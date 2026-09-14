"""
Engines mobile — stubs que já implementam o contrato BaseEngine.

Ainda NÃO funcionais: `login`/`extract`/`get_total_count` levantam
NotImplementedError citando a fase que os implementa. A escolha de falhar
alto (em vez de retornar dado falso) é o princípio nº 3 do roadmap:
"falhar para humano, não para retry".

Por que já existem como stubs:
  - travam o contrato público (§3.1): os nomes 'mobile_api'/'android_ui'
    passam a ser reconhecidos por InstaExtractor._build_engines;
  - dão o alvo do TDD das Fases 1 e 5.

`is_available` é True (a classe é importável, sem dependências pesadas),
então a cascata reconhece o nome; a indisponibilidade real aparece só ao
tentar usar, com mensagem clara. Enquanto não implementadas, use-as sempre
ATRÁS de um engine funcional na cascata, p.ex.
`engines=["mobile_api", "android_ui", "selenium", "httpx"]`.
"""
from __future__ import annotations

from typing import Callable, Optional, Set

try:
    from instat.engines.base import BaseEngine
except ImportError:  # execução fora do pacote instalado
    from engines.base import BaseEngine  # type: ignore


class _StubMobileEngine(BaseEngine):
    """Base comum dos stubs mobile. Subclasses definem _NAME e _PHASE."""

    _NAME: str = "mobile"
    _PHASE: str = "?"

    def _not_yet(self, what: str) -> "NotImplementedError":
        return NotImplementedError(
            f"{type(self).__name__}.{what} ainda não implementado — "
            f"ver Fase {self._PHASE} do docs/ROADMAP_MOBILE.md. "
            f"Até lá, coloque '{self._NAME}' atrás de um engine funcional "
            f"na cascata (ex.: selenium/httpx)."
        )

    def login(self, username: str, password: str, **kwargs) -> bool:
        raise self._not_yet("login")

    def extract(self, profile_id: str, list_type: str,
                existing_profiles: Optional[Set[str]] = None,
                max_duration: Optional[float] = None,
                on_batch: Optional[Callable] = None) -> Set[str]:
        raise self._not_yet("extract")

    def get_total_count(self, profile_id: str, list_type: str) -> Optional[int]:
        raise self._not_yet("get_total_count")

    def quit(self) -> None:
        # Nada a liberar num stub.
        return None

    @property
    def name(self) -> str:
        return self._NAME

    @property
    def is_available(self) -> bool:
        return True


class MobileApiEngine(_StubMobileEngine):
    """Extração em volume pela API privada móvel (curl_cffi, TLS do app).

    Implementada na Fase 5 (handoff de sessão app→API) sobre a identidade
    do DeviceProfile e o fingerprint TLS da Fase 4.
    """
    _NAME = "mobile_api"
    _PHASE = "5"


class AndroidUiEngine(_StubMobileEngine):
    """Automação do app oficial no container Android via uiautomator2.

    Implementada nas Fases 1 (boot/login) e 6 (comportamento humano).
    """
    _NAME = "android_ui"
    _PHASE = "1"


# Nomes reconhecidos por InstaExtractor._build_engines como engines mobile.
MOBILE_ENGINE_NAMES = {
    MobileApiEngine._NAME: MobileApiEngine,
    AndroidUiEngine._NAME: AndroidUiEngine,
}
