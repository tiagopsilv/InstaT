"""
Guarda de compatibilidade retroativa da API pública (roadmap §3.1).

O trabalho mobile entra SÓ como novos engines na cascata. Nenhuma
assinatura pública pode mudar. Este arquivo trava o contrato: se um PR
renomear/remover um parâmetro ou mudar o tipo de retorno das chamadas
que o usuário já usa hoje, o CI quebra.

O snippet-contrato (tem que continuar funcionando byte a byte):

    from instat import InstaExtractor
    ext = InstaExtractor(
        username="your_user", password="your_pass",
        headless=True, engines=["selenium", "httpx"],
    )
    target = ext.get_profile("target_profile")
    print(target.username, target.full_name,
          target.followers_count, target.posts_count)
    followers = target.get_followers()
    following = target.get_following()
    ext.quit()
"""
import inspect
from typing import List, Optional, Set

from instat import InstaExtractor
from instat.engines.base import BaseEngine
from instat.profile import Profile


# --------------------------------------------------------------------------
# Parte A — contrato estático das assinaturas (offline, sem engine/rede)
# --------------------------------------------------------------------------

def _params(func):
    return inspect.signature(func).parameters


def test_extractor_init_keeps_documented_parameters():
    """__init__ NÃO pode perder nem renomear parâmetros públicos.

    Novos parâmetros são permitidos, desde que com default (senão
    quebram chamadas existentes que não os passam).
    """
    p = _params(InstaExtractor.__init__)
    required_public = [
        "username", "password", "headless", "timeout", "proxies",
        "accounts", "engines", "exporter", "imap_config",
        "completion_threshold", "block_predictor", "use_stdlib_logging",
    ]
    for name in required_public:
        assert name in p, f"parâmetro público sumiu de InstaExtractor.__init__: {name}"

    # username/password posicionais e obrigatórios; o resto tem default.
    assert p["username"].default is inspect.Parameter.empty
    assert p["password"].default is inspect.Parameter.empty
    for name in required_public[2:]:
        assert p[name].default is not inspect.Parameter.empty, (
            f"{name} perdeu o default — quebra chamadas que não o passam"
        )

    # Qualquer parâmetro NOVO precisa ter default (compatibilidade).
    for name, param in p.items():
        if name in ("self", "username", "password"):
            continue
        assert param.default is not inspect.Parameter.empty, (
            f"parâmetro novo {name} sem default quebra a API pública"
        )


def test_extractor_accepts_snippet_kwargs():
    """Os kwargs exatos do snippet-contrato são aceitos pela assinatura."""
    p = _params(InstaExtractor.__init__)
    for kw in ("username", "password", "headless", "engines"):
        assert kw in p


def test_public_methods_exist_and_are_callable():
    for meth in ("get_profile", "get_followers", "get_following",
                 "get_both", "get_followers_parallel", "quit"):
        assert callable(getattr(InstaExtractor, meth, None)), (
            f"método público sumiu de InstaExtractor: {meth}"
        )


def test_get_profile_signature():
    p = _params(InstaExtractor.get_profile)
    assert "profile_id" in p


def test_extractor_get_followers_signature_backward_compatible():
    for meth in ("get_followers", "get_following"):
        p = _params(getattr(InstaExtractor, meth))
        assert "profile_id" in p
        # kwargs que já existem hoje precisam continuar com default
        for kw in ("max_duration", "should_stop", "with_metadata"):
            assert kw in p and p[kw].default is not inspect.Parameter.empty


def test_profile_methods_signature_backward_compatible():
    for meth in ("get_followers", "get_following"):
        p = _params(getattr(Profile, meth))
        for kw in ("max_duration", "workers", "accounts",
                   "stop_threshold", "headless"):
            assert kw in p and p[kw].default is not inspect.Parameter.empty, (
                f"Profile.{meth} perdeu o kwarg compatível {kw}"
            )


def test_profile_get_followers_returns_list_of_str_by_default():
    """O retorno padrão (sem with_metadata) continua List[str]."""
    ann = inspect.signature(Profile.get_followers).return_annotation
    assert ann in (List[str], "List[str]"), (
        f"Profile.get_followers deixou de retornar List[str]: {ann}"
    )


def test_profile_exposes_snippet_attributes():
    """target.username / full_name / followers_count / posts_count existem."""
    fields = Profile.__dataclass_fields__
    for attr in ("username", "full_name", "followers_count",
                 "following_count", "posts_count"):
        assert attr in fields, f"Profile perdeu o atributo {attr}"


# --------------------------------------------------------------------------
# Parte B — o snippet-contrato roda ponta a ponta com engine falsa (sem rede)
#
# Reescrita na F3 (roadmap R12): a versão anterior injetava `_FakeDriver` em
# `_driver` e travava o acoplamento de get_profile() ao Selenium. Agora a
# engine fake NÃO tem `_driver`; os metadados chegam pela capacidade
# `get_profile_info`, atravessando a delegação real
# InstaExtractor.get_profile → EngineManager → engine.
# --------------------------------------------------------------------------

from instat.profile_info import ProfileInfo  # noqa: E402


class _FakeEngine(BaseEngine):
    """Engine em memória: sem rede, sem browser e sem `_driver`."""

    def __init__(self, followers, following, info):
        self._data = {"followers": list(followers), "following": list(following)}
        self._info = info
        self.profile_info_calls = []

    def login(self, username: str, password: str, **kwargs) -> bool:
        return True

    def extract(self, profile_id: str, list_type: str,
                existing_profiles: Optional[Set[str]] = None,
                max_duration: Optional[float] = None,
                on_batch=None) -> Set[str]:
        return set(self._data[list_type])

    def get_total_count(self, profile_id: str, list_type: str) -> Optional[int]:
        return len(self._data[list_type])

    def get_profile_info(self, profile_id: str) -> ProfileInfo:
        self.profile_info_calls.append(profile_id)
        return self._info

    def quit(self) -> None:
        pass

    @property
    def name(self) -> str:
        return "fake"

    @property
    def is_available(self) -> bool:
        return True


def test_contract_snippet_runs_end_to_end_with_fake_engine():
    """Executa o fluxo EXATO do snippet-contrato com uma engine falsa sem `_driver`."""
    fake = _FakeEngine(
        followers=["alice", "bob", "carol"],
        following=["dave", "erin"],
        info=ProfileInfo(username="target", url="https://www.instagram.com/target/",
                         full_name="Target Name", followers_count=1894,
                         following_count=1892, posts_count=123),
    )
    assert not hasattr(fake, "_driver")

    ext = InstaExtractor(
        username="your_user",
        password="your_pass",
        headless=True,
        engines=[fake],  # instância pré-construída == caminho suportado hoje
    )

    target = ext.get_profile("target")
    assert fake.profile_info_calls == ["target"], "get_profile não atravessou a delegação real"
    assert isinstance(target, Profile)
    assert target.username == "target"
    assert target.full_name == "Target Name"
    assert target.followers_count == 1894
    assert target.posts_count == 123

    followers = target.get_followers()
    following = target.get_following()

    assert isinstance(followers, list) and all(isinstance(x, str) for x in followers)
    assert isinstance(following, list) and all(isinstance(x, str) for x in following)
    assert len(followers) == 3
    assert len(following) == 2

    ext.quit()
