"""
Engines mobile — stubs que já implementam o contrato BaseEngine.

Ainda NÃO funcionais: `login`/`extract`/`get_total_count` levantam
NotImplementedError citando a fase do roadmap que os implementa. A escolha
de falhar alto (em vez de retornar dado falso) é o princípio nº 3 do
roadmap: "falhar para humano, não para retry".

Por que já existem como stubs:
  - travam o contrato público: os nomes 'mobile_api'/'android_ui'
    passam a ser reconhecidos por InstaExtractor._build_engines;
  - dão o alvo do TDD das fases F7 (UI Android) e F8 (API móvel).

`is_available` é True (a classe é importável, sem dependências pesadas),
então a cascata reconhece o nome; a indisponibilidade real aparece só ao
tentar usar, com mensagem clara. Usados como engine primária, o
InstaExtractor falha já na construção. Enquanto não implementadas, use-as
sempre ATRÁS de um engine funcional na cascata, p.ex.
`engines=["selenium", "httpx", "mobile_api", "android_ui"]`.
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
    # Stubs não oferecem nenhuma capacidade até a fase correspondente.
    capabilities = frozenset()  # type: ignore[assignment]

    def not_implemented_error(self, what: str) -> NotImplementedError:
        return NotImplementedError(
            f"{type(self).__name__}.{what} ainda não implementado — "
            f"ver Fase {self._PHASE} do docs/ROADMAP_MOBILE.md. "
            f"Não use '{self._NAME}' como engine primária; até lá, coloque-o "
            f"atrás de um engine funcional na cascata (ex.: selenium/httpx)."
        )

    def login(self, username: str, password: str, **kwargs) -> bool:
        raise self.not_implemented_error("login")

    def extract(self, profile_id: str, list_type: str,
                existing_profiles: Optional[Set[str]] = None,
                max_duration: Optional[float] = None,
                on_batch: Optional[Callable] = None) -> Set[str]:
        raise self.not_implemented_error("extract")

    def get_total_count(self, profile_id: str, list_type: str) -> Optional[int]:
        raise self.not_implemented_error("get_total_count")

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
    """Extração pela API privada móvel por adapter mantido.

    Experimento condicional da fase F8 do roadmap, só com necessidade
    demonstrada pelos resultados da F7.
    """
    _NAME = "mobile_api"
    _PHASE = "F8"


class AndroidUiEngine(_StubMobileEngine):
    """Automação do app oficial no Android em container, somente leitura.

    Implementada na fase F7 do roadmap, a partir da fatia vertical F6b.
    """
    _NAME = "android_ui"
    _PHASE = "F7"


# Nomes reconhecidos por InstaExtractor._build_engines como engines mobile.
MOBILE_ENGINE_NAMES = {
    MobileApiEngine._NAME: MobileApiEngine,
    AndroidUiEngine._NAME: AndroidUiEngine,
}
