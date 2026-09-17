"""Governador de erros e orçamento (roadmap §5.6, Fase 2).

Toda tentativa passa por aqui: `before_attempt` recusa chave pausada,
congelada ou sem orçamento; `on_error` classifica o erro e decide entre
repetir (transitório, finito), cair para outro engine (técnico) ou parar
(429, proxy, challenge, restrição, orçamento, cancelamento).

Chave = (conta, operação). Parar nunca significa "tentar outra conta":
quem chama deve interromper a cascata e devolver o parcial.

Estados que só saem com liberação manual e sessão validada:
`needs_attention` (challenge) e `restricted`. `paused` (429) expira pelo
relógio. Limites são operacionais, não limites seguros do Instagram.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

try:
    from instat.block_detector import BlockDetector
    from instat.exceptions import (
        AccountBlockedError,
        BlockedError,
        ChallengeError,
        ProxyError,
        RateLimitError,
        RestrictedError,
        TransientError,
    )
except ImportError:
    from block_detector import BlockDetector  # type: ignore
    from exceptions import (  # type: ignore
        AccountBlockedError,
        BlockedError,
        ChallengeError,
        ProxyError,
        RateLimitError,
        RestrictedError,
        TransientError,
    )

Key = Tuple[str, str]

# DataImpulse (docs.dataimpulse.com/errors). O campo em que o token chega
# não é documentado: procuramos em corpo, cabeçalhos e frase de status.
PROXY_TOKENS: Dict[str, Tuple[int, ...]] = {
    'NO_USER': (407,), 'TRAFFIC_EXHAUSTED': (407,), 'THREADS_EXHAUSTED': (407,),
    'PORT_NOT_ALLOWED': (407,), 'USER_BLOCKED': (407,),
    'PORT_BLOCKED': (403,), 'SITE_PERMANENTLY_BLOCKED': (403,),
    'NO_HOST_CONNECTION': (502,), 'NO_RAY': (503,),
}
CHALLENGE_MARKERS = ('checkpoint_required', 'challenge_required', 'two_factor_required')
RESTRICTION_MARKERS = ('feedback_required',)


class Signal(str, Enum):
    TRANSIENT = 'transient'
    TECHNICAL = 'technical'
    RATE_LIMITED = 'rate_limited'
    PROXY = 'proxy'
    CHALLENGE = 'challenge'
    RESTRICTED = 'restricted'
    CANCELLED = 'cancelled'


class KeyState(str, Enum):
    ACTIVE = 'active'
    PAUSED = 'paused'
    NEEDS_ATTENTION = 'needs_attention'
    RESTRICTED = 'restricted'


TERMINAL_SIGNALS = frozenset({
    Signal.RATE_LIMITED, Signal.PROXY, Signal.CHALLENGE, Signal.RESTRICTED,
})


@dataclass(frozen=True)
class Classification:
    signal: Signal
    cause: Optional[str] = None
    retry_after: Optional[float] = None

    @property
    def reason(self) -> str:
        if self.signal is Signal.PROXY:
            return f'proxy:{self.cause or "unknown"}'
        return self.signal.value


def parse_retry_after(value: Any, now_epoch: Optional[float] = None) -> Optional[float]:
    """RFC 9110: segundos inteiros não negativos ou HTTP-date."""
    if value is None:
        return None
    text = str(value).strip()
    if text.isdigit():
        return float(int(text))
    try:
        when = parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        return None
    if when is None:
        return None
    now = time.time() if now_epoch is None else now_epoch
    return max(0.0, when.timestamp() - now)


def _proxy_token(status: int, haystack: str) -> Optional[str]:
    for token, statuses in PROXY_TOKENS.items():
        if status in statuses and token in haystack:
            return token
    return None


def classify_http(status: int, headers: Optional[Mapping[str, Any]] = None,
                  body: str = '', reason_phrase: str = '',
                  now_epoch: Optional[float] = None) -> Optional[Classification]:
    """None para sucesso (2xx/3xx e 404 fica com o chamador)."""
    headers = dict(headers or {})
    header_text = ' '.join(f'{k}: {v}' for k, v in headers.items())
    haystack = f'{body or ""} {header_text} {reason_phrase or ""}'
    token = _proxy_token(status, haystack)
    if token:
        return Classification(Signal.PROXY, cause=token)
    if status == 407:
        return Classification(Signal.PROXY, cause='unknown_407')
    retry_after = parse_retry_after(
        next((v for k, v in headers.items() if k.lower() == 'retry-after'), None),
        now_epoch,
    )
    if status == 429:
        return Classification(Signal.RATE_LIMITED, retry_after=retry_after)
    if status >= 500:
        return Classification(Signal.TRANSIENT, cause=str(status), retry_after=retry_after)
    lowered = (body or '').lower()
    if status in (400, 403):
        if any(m in lowered for m in CHALLENGE_MARKERS):
            return Classification(Signal.CHALLENGE)
        if any(m in lowered for m in RESTRICTION_MARKERS):
            return Classification(Signal.RESTRICTED, cause='feedback_required')
        if status == 403:
            return Classification(Signal.RESTRICTED, cause='403')
        return Classification(Signal.TECHNICAL, cause='400')
    return None


def classify_exception(exc: BaseException) -> Classification:
    if isinstance(exc, ProxyError):
        return Classification(Signal.PROXY, cause=exc.cause)
    if isinstance(exc, RateLimitError):
        return Classification(Signal.RATE_LIMITED, retry_after=exc.retry_after)
    if isinstance(exc, (AccountBlockedError, ChallengeError)):
        return Classification(Signal.CHALLENGE)
    if isinstance(exc, RestrictedError):
        return Classification(Signal.RESTRICTED)
    if isinstance(exc, TransientError):
        return Classification(Signal.TRANSIENT, cause=str(exc.status or ''),
                              retry_after=exc.retry_after)
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return Classification(Signal.TRANSIENT, cause=type(exc).__name__)
    if isinstance(exc, BlockedError):
        text = str(exc).lower()
        if any(m in text for m in RESTRICTION_MARKERS):
            return Classification(Signal.RESTRICTED)
        if any(ind in text for ind in BlockDetector.URL_INDICATORS):
            return Classification(Signal.CHALLENGE)
    return Classification(Signal.TECHNICAL, cause=type(exc).__name__)


@dataclass
class Policy:
    max_transient_retries: int = 3
    backoff_base_s: float = 1.0
    backoff_cap_s: float = 60.0
    jitter: float = 0.2
    rate_limit_pause_s: float = 900.0
    max_attempts: int = 50
    max_bytes: Optional[int] = None
    max_duration_s: Optional[float] = None
    sleep_slice_s: float = 1.0


class Clock:
    def monotonic(self) -> float:
        return time.monotonic()

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)


class FakeClock(Clock):
    """Relógio controlado para testes: sleep só avança o tempo."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def monotonic(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self._now += max(0.0, seconds)

    def advance(self, seconds: float) -> None:
        self._now += seconds


class GovernorStop(Exception):
    """Parada decidida pelo governador. Nunca deve levar a outra conta."""

    def __init__(self, reason: str, key: Optional[Key] = None,
                 signal: Optional[Signal] = None) -> None:
        super().__init__(reason)
        self.reason = reason
        self.key = key
        self.signal = signal

    @property
    def is_terminal(self) -> bool:
        """Terminal para a conta: não repetir, não rotacionar."""
        return self.signal in TERMINAL_SIGNALS or self.reason.startswith('budget:') \
            or self.reason == 'cancelled' or self.reason == 'paused'


@dataclass
class Decision:
    action: str  # 'retry' | 'fallback' | 'stop'
    classification: Classification
    delay_s: float = 0.0
    reason: Optional[str] = None


@dataclass
class _KeyData:
    state: KeyState = KeyState.ACTIVE
    paused_until: float = 0.0
    attempts: int = 0
    bytes: int = 0
    started: float = 0.0
    transient: int = 0


class Governor:
    def __init__(self, policy: Optional[Policy] = None, clock: Optional[Clock] = None,
                 rng: Optional[Callable[[], float]] = None) -> None:
        self.policy = policy or Policy()
        self.clock = clock or Clock()
        self._rng = rng or random.random
        self._keys: Dict[Key, _KeyData] = {}
        self.timeline: List[Dict[str, Any]] = []

    # ----------------------------------------------------------- estado
    def _data(self, key: Key) -> _KeyData:
        if key not in self._keys:
            self._keys[key] = _KeyData(started=self.clock.monotonic())
        return self._keys[key]

    def state(self, key: Key) -> KeyState:
        d = self._data(key)
        if d.state is KeyState.PAUSED and self.clock.monotonic() >= d.paused_until:
            d.state = KeyState.ACTIVE
        return d.state

    def usage(self, key: Key) -> Dict[str, Any]:
        d = self._data(key)
        return {'attempts': d.attempts, 'bytes': d.bytes,
                'elapsed_s': self.clock.monotonic() - d.started,
                'state': self.state(key).value}

    def _log(self, key: Key, event: str, **detail: Any) -> None:
        self.timeline.append({'t': self.clock.monotonic(), 'key': key, 'event': event,
                              'state': self._data(key).state.value, **detail})

    def begin(self, key: Key) -> None:
        """Novo orçamento para uma chamada de extração (estado é mantido)."""
        d = self._data(key)
        d.attempts = d.bytes = d.transient = 0
        d.started = self.clock.monotonic()
        self._log(key, 'begin')

    def release(self, key: Key, *, session_validated: bool) -> None:
        """Liberação manual. Exige sessão validada; tempo sozinho não libera."""
        d = self._data(key)
        if d.state in (KeyState.NEEDS_ATTENTION, KeyState.RESTRICTED) and not session_validated:
            raise ValueError('release requires a positively validated session')
        d.state = KeyState.ACTIVE
        d.paused_until = 0.0
        self._log(key, 'release')

    # ----------------------------------------------------------- orçamento
    def _stop(self, key: Key, reason: str, signal: Optional[Signal] = None) -> GovernorStop:
        self._log(key, 'stop', reason=reason, signal=signal.value if signal else None)
        return GovernorStop(reason, key, signal)

    def before_attempt(self, key: Key) -> None:
        d = self._data(key)
        state = self.state(key)
        if state is not KeyState.ACTIVE:
            raise self._stop(key, state.value if state is not KeyState.PAUSED else 'paused')
        p = self.policy
        if d.attempts >= p.max_attempts:
            raise self._stop(key, 'budget:attempts')
        if p.max_bytes is not None and d.bytes > p.max_bytes:
            raise self._stop(key, 'budget:bytes')
        if p.max_duration_s is not None and self.clock.monotonic() - d.started >= p.max_duration_s:
            raise self._stop(key, 'budget:duration')
        d.attempts += 1
        self._log(key, 'attempt', n=d.attempts)

    def record_bytes(self, key: Key, n: int) -> None:
        self._data(key).bytes += max(0, int(n or 0))

    def check_budget(self, key: Key) -> None:
        """Verificação no meio de uma tentativa (ex.: entre páginas do httpx)."""
        p, d = self.policy, self._data(key)
        if p.max_bytes is not None and d.bytes > p.max_bytes:
            raise self._stop(key, 'budget:bytes')
        if p.max_duration_s is not None and self.clock.monotonic() - d.started >= p.max_duration_s:
            raise self._stop(key, 'budget:duration')

    def on_success(self, key: Key) -> None:
        self._data(key).transient = 0
        self._log(key, 'success')

    # ----------------------------------------------------------- decisão
    def _backoff(self, n: int) -> float:
        p = self.policy
        base = min(p.backoff_cap_s, p.backoff_base_s * (2 ** (n - 1)))
        return base * (1 + p.jitter * (2 * self._rng() - 1))

    def on_error(self, key: Key, exc: BaseException) -> Decision:
        c = classify_exception(exc)
        d = self._data(key)
        p = self.policy
        if c.signal is Signal.TRANSIENT:
            d.transient += 1
            if d.transient > p.max_transient_retries:
                decision = Decision('fallback', c, reason='transient:exhausted')
            elif c.retry_after is not None and c.retry_after > p.backoff_cap_s:
                # Espera longa demais para fazer aqui: pausa a chave pelo
                # Retry-After em vez de dormir.
                d.state = KeyState.PAUSED
                d.paused_until = self.clock.monotonic() + c.retry_after
                decision = Decision('stop', c, reason='transient:retry_after_above_cap')
            else:
                delay = c.retry_after if c.retry_after is not None else self._backoff(d.transient)
                decision = Decision('retry', c, delay_s=delay)
        elif c.signal is Signal.TECHNICAL:
            decision = Decision('fallback', c)
        else:
            if c.signal is Signal.RATE_LIMITED:
                d.state = KeyState.PAUSED
                d.paused_until = self.clock.monotonic() + max(p.rate_limit_pause_s,
                                                              c.retry_after or 0.0)
            elif c.signal is Signal.CHALLENGE:
                d.state = KeyState.NEEDS_ATTENTION
            elif c.signal is Signal.RESTRICTED:
                d.state = KeyState.RESTRICTED
            decision = Decision('stop', c, reason=c.reason)
        self._log(key, 'error', signal=c.signal.value, cause=c.cause, action=decision.action,
                  delay_s=round(decision.delay_s, 3), reason=decision.reason,
                  error=type(exc).__name__)
        return decision

    def wait(self, key: Key, seconds: float,
             should_stop: Optional[Callable[[], bool]] = None) -> None:
        """Espera fatiada; levanta GovernorStop('cancelled') se pedirem parada."""
        remaining = max(0.0, seconds)
        while remaining > 0:
            if should_stop is not None and should_stop():
                raise self._stop(key, 'cancelled', Signal.CANCELLED)
            step = min(self.policy.sleep_slice_s, remaining)
            self.clock.sleep(step)
            remaining -= step
        if should_stop is not None and should_stop():
            raise self._stop(key, 'cancelled', Signal.CANCELLED)

    def call(self, key: Key, fn: Callable[[], Any],
             should_stop: Optional[Callable[[], bool]] = None) -> Any:
        """Executa `fn` sob o governador.

        retry → espera e repete; fallback → re-levanta o erro original
        (o chamador tenta o próximo engine); stop → GovernorStop.
        """
        while True:
            if should_stop is not None and should_stop():
                raise self._stop(key, 'cancelled', Signal.CANCELLED)
            self.before_attempt(key)
            try:
                result = fn()
            except GovernorStop:
                raise
            except Exception as exc:
                decision = self.on_error(key, exc)
                if decision.action == 'retry':
                    self.wait(key, decision.delay_s, should_stop)
                    continue
                if decision.action == 'fallback':
                    raise
                raise self._stop(key, decision.reason or decision.classification.reason,
                                 decision.classification.signal) from exc
            self.on_success(key)
            return result


__all__ = [
    'Classification', 'Clock', 'Decision', 'FakeClock', 'Governor', 'GovernorStop',
    'KeyState', 'Policy', 'Signal', 'TERMINAL_SIGNALS',
    'classify_exception', 'classify_http', 'parse_retry_after',
]
