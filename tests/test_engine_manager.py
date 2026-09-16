import unittest
from typing import Callable, Optional, Set

from instat.engines.base import BaseEngine
from instat.engines.engine_manager import EngineManager
from instat.exceptions import AllEnginesBlockedError, BlockedError


class MockEngine(BaseEngine):
    """Concrete mock implementation of BaseEngine for testing."""

    def __init__(self, engine_name='mock', available=True,
                 profiles=None, count=100, block_on_extract=False,
                 block_on_count=False):
        self._name = engine_name
        self._available = available
        self._profiles = profiles or {'user1', 'user2'}
        self._count = count
        self._block_on_extract = block_on_extract
        self._block_on_count = block_on_count
        self._quit_called = False

    def login(self, username: str, password: str, **kwargs) -> bool:
        return True

    def extract(self, profile_id: str, list_type: str,
                existing_profiles: Optional[Set[str]] = None,
                max_duration: Optional[float] = None,
                on_batch: Optional[Callable] = None) -> Set[str]:
        if self._block_on_extract:
            raise BlockedError(f"{self._name} is blocked")
        return self._profiles

    def get_total_count(self, profile_id: str, list_type: str) -> Optional[int]:
        if self._block_on_count:
            raise BlockedError(f"{self._name} blocked on count")
        return self._count

    def quit(self) -> None:
        self._quit_called = True

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_available(self) -> bool:
        return self._available


class TestEngineManager(unittest.TestCase):

    def test_engine_manager_uses_first_available(self):
        e1 = MockEngine('engine1', profiles={'a', 'b'})
        e2 = MockEngine('engine2', profiles={'c', 'd'})
        mgr = EngineManager([e1, e2])
        result = mgr.extract('user', 'followers')
        self.assertEqual(set(result), {'a', 'b'})

    def test_engine_manager_falls_back_on_blocked_error(self):
        e1 = MockEngine('engine1', block_on_extract=True)
        e2 = MockEngine('engine2', profiles={'x', 'y'})
        mgr = EngineManager([e1, e2])
        result = mgr.extract('user', 'followers')
        self.assertEqual(set(result), {'x', 'y'})

    def test_engine_manager_raises_all_blocked_when_exhausted(self):
        e1 = MockEngine('engine1', block_on_extract=True)
        e2 = MockEngine('engine2', block_on_extract=True)
        mgr = EngineManager([e1, e2])
        with self.assertRaises(AllEnginesBlockedError):
            mgr.extract('user', 'followers')

    def test_engine_manager_skips_unavailable_engines(self):
        e1 = MockEngine('unavailable', available=False)
        e2 = MockEngine('available', profiles={'ok'})
        mgr = EngineManager([e1, e2])
        self.assertEqual(len(mgr.engines), 1)
        self.assertEqual(mgr.engines[0].name, 'available')

    def test_base_engine_not_instantiable(self):
        with self.assertRaises(TypeError):
            BaseEngine()

    def test_quit_all_closes_all_engines(self):
        e1 = MockEngine('e1')
        e2 = MockEngine('e2')
        mgr = EngineManager([e1, e2])
        mgr.quit_all()
        self.assertTrue(e1._quit_called)
        self.assertTrue(e2._quit_called)

    def test_get_total_count_fallback(self):
        e1 = MockEngine('e1', block_on_count=True)
        e2 = MockEngine('e2', count=500)
        mgr = EngineManager([e1, e2])
        result = mgr.get_total_count('user', 'followers')
        self.assertEqual(result, 500)


class TestEngineManagerMetadataPartialPreservation(unittest.TestCase):
    """Regression for Gap 11 — with_metadata=True must NOT lose
    partial data when an engine raises BlockedError. Reproduced in
    live smoke 2026-05-15: Selenium collected 173 followers, raised
    BlockedError due to partial coverage, httpx was rate-limited as
    fallback, so AllEnginesBlockedError was raised even though 173
    usernames were known. Fix: on_batch in metadata mode tracks
    usernames; fallback synthesises ProfileSummary list."""

    def test_partial_returns_username_only_summaries_in_metadata_mode(self):
        from instat.profile_summary import ProfileSummary
        from instat.exceptions import BlockedError

        class PartialEngine(BaseEngine):
            """Calls on_batch with partial data, then raises."""
            @property
            def name(self_): return 'partial'
            @property
            def is_available(self_): return True
            def login(self_, u, p, **kw): return True
            def extract(self_, profile_id, list_type, **kwargs):
                on_batch = kwargs.get('on_batch')
                # Simulate scroll batches: 3 usernames in, then block.
                if on_batch:
                    on_batch({'a', 'b', 'c'})
                raise BlockedError("partial coverage 3/10")
            def get_total_count(self_, *a, **kw): return 10
            def quit(self_): pass

        class RateLimitedEngine(BaseEngine):
            @property
            def name(self_): return 'rl'
            @property
            def is_available(self_): return True
            def login(self_, u, p, **kw): return True
            def extract(self_, *a, **kw):
                from instat.exceptions import RateLimitError
                raise RateLimitError("rate limited")
            def get_total_count(self_, *a, **kw): return None
            def quit(self_): pass

        mgr = EngineManager(
            [PartialEngine(), RateLimitedEngine()],
            default_credentials=('u', 'p'),
        )
        mgr._logged_in_engines.add(id(mgr.engines[0]))
        mgr._logged_in_engines.add(id(mgr.engines[1]))

        # Unique profile_id per test — avoid leftover checkpoint pollution
        # from prior runs in `.instat_checkpoints/`.
        import uuid
        target = f"gap11_meta_{uuid.uuid4().hex[:8]}"
        result = mgr.extract(target, 'followers', with_metadata=True)
        # All engines failed but partial was tracked via on_batch.
        # Must return List[ProfileSummary] (username-only), NOT raise.
        self.assertEqual(len(result), 3)
        self.assertTrue(all(isinstance(r, ProfileSummary) for r in result))
        self.assertEqual({r.username for r in result}, {'a', 'b', 'c'})
        # User_id etc. are None (Selenium-like degradation).
        self.assertTrue(all(r.user_id is None for r in result))

    def test_partial_returns_strings_in_username_mode(self):
        # Sanity check: original behavior preserved when with_metadata=False.
        from instat.exceptions import BlockedError

        class PartialEngine(BaseEngine):
            @property
            def name(self_): return 'partial'
            @property
            def is_available(self_): return True
            def login(self_, u, p, **kw): return True
            def extract(self_, *a, **kw):
                on_batch = kw.get('on_batch')
                if on_batch:
                    on_batch({'x', 'y'})
                raise BlockedError("partial")
            def get_total_count(self_, *a, **kw): return 10
            def quit(self_): pass

        mgr = EngineManager([PartialEngine()], default_credentials=('u', 'p'))
        mgr._logged_in_engines.add(id(mgr.engines[0]))
        import uuid
        target = f"gap11_str_{uuid.uuid4().hex[:8]}"
        result = mgr.extract(target, 'followers')
        self.assertEqual(set(result), {'x', 'y'})
        self.assertTrue(all(isinstance(r, str) for r in result))


class TestEngineManagerGetRecentPosts(unittest.TestCase):
    """get_recent_posts cascade — engines that NotImplementedError are
    skipped silently so a Selenium-primary cascade falls through to
    httpx without ceremony."""

    def test_skips_not_implemented_and_uses_supporting_engine(self):
        # Engine A doesn't implement (default raise NotImplementedError).
        # Engine B implements and returns 3 posts.
        from unittest.mock import MagicMock
        eng_a = MockEngine(engine_name='a')
        eng_b = MockEngine(engine_name='b')
        # Override get_recent_posts only on B.
        eng_b.get_recent_posts = MagicMock(
            return_value=[{'shortcode': f'p{i}'} for i in range(3)],
        )
        mgr = EngineManager(
            [eng_a, eng_b],
            default_credentials=('u', 'p'),
        )
        # Mark both as already logged-in to skip handoff path.
        mgr._logged_in_engines.add(id(eng_a))
        mgr._logged_in_engines.add(id(eng_b))
        out = mgr.get_recent_posts('user', limit=3)
        self.assertEqual(len(out), 3)
        eng_b.get_recent_posts.assert_called_once_with('user', 3)

    def test_raises_when_no_engine_implements(self):
        # All engines fall through with NotImplementedError.
        eng_a = MockEngine(engine_name='a')
        eng_b = MockEngine(engine_name='b')
        mgr = EngineManager(
            [eng_a, eng_b],
            default_credentials=('u', 'p'),
        )
        mgr._logged_in_engines.add(id(eng_a))
        mgr._logged_in_engines.add(id(eng_b))
        with self.assertRaises(NotImplementedError):
            mgr.get_recent_posts('user', limit=5)

    def test_blocked_in_first_falls_through_to_second(self):
        from unittest.mock import MagicMock
        eng_a = MockEngine(engine_name='a')
        eng_b = MockEngine(engine_name='b')
        eng_a.get_recent_posts = MagicMock(
            side_effect=BlockedError("a blocked"),
        )
        eng_b.get_recent_posts = MagicMock(
            return_value=[{'shortcode': 'p0'}],
        )
        mgr = EngineManager(
            [eng_a, eng_b],
            default_credentials=('u', 'p'),
        )
        mgr._logged_in_engines.add(id(eng_a))
        mgr._logged_in_engines.add(id(eng_b))
        out = mgr.get_recent_posts('user', limit=1)
        self.assertEqual(out, [{'shortcode': 'p0'}])


if __name__ == "__main__":
    unittest.main()
