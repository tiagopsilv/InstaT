"""Scroll-loop strategy — extracts the main profile-collection loop
that SeleniumEngine._get_profiles used to own.

Why this module exists:
  The loop mixes four concerns that all change independently when IG
  tweaks its list UI:
    1. DOM scroll mechanics (IPC JS into the dialog container)
    2. Rate-limit detection (stale-round counting with a warmup window)
    3. Recovery (close + reopen the modal to reset pagination cursor)
    4. Progress reporting (checkpoint writes, on_batch callbacks,
       BlockPredictor telemetry, partial-coverage BlockedError)
  Keeping them in one ~150-line method made each IG regression a
  surgical strike into a dense blob. Now scroll mechanics, recovery,
  and escalation are isolated methods on ScrollLoop — an IG change
  touches a narrow surface.

Public entry point:
  ScrollLoop(driver, selectors, modal, ...config...).run(
      expected_count, max_duration,
      initial_profiles=..., checkpoint=..., on_batch=...,
      profile_id=..., list_type=..., should_stop=...,
  ) -> List[str]

  Raises BlockedError when coverage falls below completion_threshold
  with some profiles collected (EngineManager uses this to trigger
  fallback to the next engine while preserving checkpoint state).

Collaborators:
  - reopen_modal (injected callable) — used for the reopen cascade;
    typically SeleniumEngine._reopen_modal, which itself delegates to
    ModalInteraction
  - BlockPredictor (optional) — passive stale-round telemetry
"""
import time
from typing import Any, Callable, List, Optional, Set

from loguru import logger

try:
    from instat.constants import human_delay
    from instat.exceptions import BlockedError
    from instat.utils import Utils
except ImportError:
    from constants import human_delay  # type: ignore
    from exceptions import BlockedError  # type: ignore
    from utils import Utils  # type: ignore


class ScrollLoop:
    """Phase 3: extract profiles from an open IG list modal by
    scrolling until `expected_count` is reached, the list is
    exhausted, or a time/stop signal fires.

    Stale-round handling:
      A "stale round" is a scroll iteration where no new usernames
      appeared. The loop tolerates MAX_STALE_ROUNDS consecutive stale
      rounds before concluding either the list ended or IG rate-limited
      us. During the warmup window (first `warmup_threshold` profiles),
      we allow `warmup_stale_rounds` (higher) instead, because popular
      targets may take several scrolls before returning items.

    Recovery (reopen cascade):
      When the stale-round threshold is hit, instead of giving up we
      close+reopen the modal (up to MAX_REOPEN_ATTEMPTS). This resets
      IG's internal pagination cursor and often gets more items. Each
      successful reopen resets the stale counter.

    Partial-coverage escalation:
      If we collected > 0 but < completion_threshold of expected, we
      raise BlockedError with the checkpoint already saved, so the
      engine manager can fall back to another engine and resume.
    """

    MAX_STALE_ROUNDS = 4
    MAX_REOPEN_ATTEMPTS = 3

    PROFILE_USERNAME_SPAN_KEY = "PROFILE_USERNAME_SPAN"

    # JS snippet kept here (not on class) because it only makes sense
    # inside a scroll-loop context — moving it keeps the refactor
    # cohesive and lets SeleniumEngine drop the method entirely.
    _SCROLL_JS = """
    const selectors = [
        'div[role="dialog"] div[style*="overflow"]',
        'div[role="dialog"] ul',
        'div[role="dialog"] div[style*="height"]'
    ];
    for (const s of selectors) {
        const el = document.querySelector(s);
        if (el && el.scrollHeight > el.clientHeight) {
            el.scrollTop = el.scrollHeight;
            return true;
        }
    }
    const dlg = document.querySelector('div[role="dialog"]');
    if (dlg) {
        dlg.scrollTop = dlg.scrollHeight;
        return true;
    }
    window.scrollTo(0, document.body.scrollHeight);
    return false;
    """

    def __init__(
        self,
        driver: Any,
        selectors: Any,
        reopen_modal: Callable[[str, str], bool],
        *,
        pause_time: float = 0.5,
        wait_interval: float = 2.0,
        warmup_threshold: int = 200,
        warmup_stale_rounds: int = 10,
        checkpoint_interval: int = 100,
        completion_threshold: float = 0.90,
        engine_name: str = "selenium",
        block_predictor: Any = None,
    ) -> None:
        self._driver = driver
        self._selectors = selectors
        self._reopen_modal = reopen_modal
        self.pause_time = pause_time
        self.wait_interval = wait_interval
        self.warmup_threshold = warmup_threshold
        self.warmup_stale_rounds = warmup_stale_rounds
        self.checkpoint_interval = checkpoint_interval
        self.completion_threshold = completion_threshold
        self._engine_name = engine_name
        self._block_predictor = block_predictor

    def run(
        self,
        expected_count: int,
        max_duration: Optional[float],
        *,
        initial_profiles: Optional[Set[str]] = None,
        checkpoint: Any = None,
        on_batch: Optional[Callable] = None,
        profile_id: Optional[str] = None,
        list_type: Optional[str] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> List[str]:
        start_time = time.perf_counter()
        unique: Set[str] = set(initial_profiles) if initial_profiles else set()
        if unique:
            logger.info(
                f"Starting with {len(unique)} profiles from checkpoint."
            )

        state = _LoopState(last_checkpoint_count=len(unique))
        profile_selector = self._selectors.get(self.PROFILE_USERNAME_SPAN_KEY)

        while True:
            if self._is_max_duration_exceeded(start_time, max_duration):
                logger.info("Max duration reached, stopping extraction.")
                break
            if should_stop and should_stop():
                logger.info("should_stop signal received, stopping extraction.")
                break

            new_added = self._one_scroll_iteration(
                unique, profile_selector, checkpoint, on_batch, state,
            )

            if len(unique) >= expected_count:
                logger.info(
                    f"Expected profile count reached "
                    f"({len(unique)}/{expected_count})."
                )
                break

            if new_added == 0:
                if self._handle_stale_round(
                    unique, state, profile_id, list_type,
                ):
                    break
            else:
                state.stale_rounds = 0
                logger.info(
                    f"Collected {len(unique)} out of {expected_count} "
                    f"expected profiles (+{new_added})."
                )

        elapsed = time.perf_counter() - start_time
        logger.info(
            f"Profile extraction completed in {elapsed:.2f}s. "
            f"Total unique profiles: {len(unique)}/{expected_count}."
        )

        self._maybe_raise_partial_coverage(
            unique, expected_count, checkpoint, on_batch,
        )
        return list(unique)

    # --------------------------- iteration ---------------------------

    def _one_scroll_iteration(
        self,
        unique: Set[str],
        profile_selector: str,
        checkpoint: Any,
        on_batch: Optional[Callable],
        state: "_LoopState",
    ) -> int:
        count_before = len(unique)
        self._scroll_modal_js()
        human_delay(self.pause_time, variance=0.2)

        snapshot = Utils.batch_read_text(self._driver, profile_selector)
        unique |= snapshot
        new_added = len(unique) - count_before

        self._maybe_checkpoint(unique, checkpoint, state)
        self._notify_batch(unique, on_batch, new_added)
        return new_added

    def _maybe_checkpoint(
        self, unique: Set[str], checkpoint: Any, state: "_LoopState",
    ) -> None:
        if not checkpoint:
            return
        if (len(unique) - state.last_checkpoint_count) >= self.checkpoint_interval:
            checkpoint.save(unique)
            state.last_checkpoint_count = len(unique)
            logger.info(f"Checkpoint saved: {len(unique)} profiles")

    @staticmethod
    def _notify_batch(
        unique: Set[str], on_batch: Optional[Callable], new_added: int,
    ) -> None:
        if on_batch and new_added > 0:
            try:
                on_batch(unique)
            except Exception as e:
                logger.debug(f"on_batch failed silently: {e}")

    # ---------------------- stale / reopen cascade -------------------

    def _handle_stale_round(
        self,
        unique: Set[str],
        state: "_LoopState",
        profile_id: Optional[str],
        list_type: Optional[str],
    ) -> bool:
        """Called when an iteration added no new profiles. Returns
        True if the outer loop should break (exhausted or rate-limited
        beyond recovery); False to continue."""
        state.stale_rounds += 1
        in_warmup = len(unique) < self.warmup_threshold
        effective_limit = (
            self.warmup_stale_rounds if in_warmup else self.MAX_STALE_ROUNDS
        )
        logger.debug(
            f"No new profiles in this round. Stale rounds: "
            f"{state.stale_rounds}/{effective_limit}"
            f"{' (warmup)' if in_warmup else ''}"
        )
        self._record_stale(state.stale_rounds, effective_limit, reopen_failed=False)

        if state.stale_rounds < effective_limit:
            human_delay(self.wait_interval, variance=0.2)
            return False

        if (profile_id and list_type
                and state.reopen_attempts < self.MAX_REOPEN_ATTEMPTS):
            state.reopen_attempts += 1
            logger.info(
                f"Rate limit suspected after {len(unique)} profiles. "
                f"Reopening modal "
                f"(attempt {state.reopen_attempts}/{self.MAX_REOPEN_ATTEMPTS})..."
            )
            reopen_ok = self._reopen_modal(profile_id, list_type)
            if not reopen_ok:
                self._record_stale(
                    state.stale_rounds, effective_limit, reopen_failed=True,
                )
                logger.warning("Reopen failed — stopping extraction.")
                return True
            state.stale_rounds = 0
            human_delay(3.0, variance=1.0)
            return False

        logger.info(
            f"No new profiles after {self.MAX_STALE_ROUNDS} rounds "
            f"+ {state.reopen_attempts} reopen attempts — end of list."
        )
        return True

    def _record_stale(
        self, stale_count: int, max_stale: int, *, reopen_failed: bool,
    ) -> None:
        predictor = self._block_predictor
        if predictor is None:
            return
        try:
            predictor.record_stale(
                stale_count=stale_count,
                max_stale=max_stale,
                reopen_failed=reopen_failed,
                engine=self._engine_name,
            )
        except Exception as e:
            logger.debug(f"block_predictor record_stale failed: {e}")

    # ------------------------ scroll primitive -----------------------

    def _scroll_modal_js(self) -> None:
        try:
            self._driver.execute_script(self._SCROLL_JS)
        except Exception as e:
            logger.debug(f"scroll_modal_js error (ignored): {e}")

    # ------------------------ housekeeping ---------------------------

    @staticmethod
    def _is_max_duration_exceeded(
        start_time: float, max_duration: Optional[float],
    ) -> bool:
        if max_duration is None:
            return False
        elapsed = time.perf_counter() - start_time
        if elapsed > max_duration:
            logger.warning("Max duration ({:.1f}s) exceeded.", max_duration)
            return True
        return False

    def _maybe_raise_partial_coverage(
        self,
        unique: Set[str],
        expected_count: int,
        checkpoint: Any,
        on_batch: Optional[Callable],
    ) -> None:
        if expected_count <= 0 or not unique:
            return
        coverage = len(unique) / expected_count
        if coverage >= self.completion_threshold:
            return
        if checkpoint:
            checkpoint.save(unique)
        if on_batch:
            try:
                on_batch(unique)
            except Exception as e:
                logger.debug(f"on_batch final failed silently: {e}")
        logger.warning(
            f"Coverage {100*coverage:.0f}% below threshold "
            f"{100*self.completion_threshold:.0f}% "
            f"({len(unique)}/{expected_count}). "
            f"Raising BlockedError to trigger engine fallback."
        )
        raise BlockedError(
            f"{self._engine_name} partial coverage: "
            f"{len(unique)}/{expected_count} ({100*coverage:.0f}%)"
        )


class _LoopState:
    """Mutable counters carried across iterations. Kept private to
    this module; exposed attributes are only for internal methods."""

    __slots__ = ("stale_rounds", "reopen_attempts", "last_checkpoint_count")

    def __init__(self, last_checkpoint_count: int = 0) -> None:
        self.stale_rounds = 0
        self.reopen_attempts = 0
        self.last_checkpoint_count = last_checkpoint_count


__all__ = ["ScrollLoop"]
