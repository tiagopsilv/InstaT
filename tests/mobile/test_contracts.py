"""
Contratos do esqueleto mobile (roadmap Fase 0, passo 4).

Estes testes são unitários puros (sem container Android), então NÃO
levam o marcador `mobile` — rodam no CI normalmente. O marcador `mobile`
fica reservado para testes que precisam de um device real (Fase 1+).
"""
import inspect

import pytest

from instat.engines.base import BaseEngine
from instat.mobile import (
    MOBILE_ENGINE_NAMES,
    AccountSlot,
    AndroidUiEngine,
    DeviceProfile,
    MobileApiEngine,
    validate_slots,
)


# --------------------------------------------------------------------------
# Engines novas: implementam o contrato BaseEngine, mas ainda são stubs
# --------------------------------------------------------------------------

@pytest.mark.parametrize("cls,expected_name", [
    (MobileApiEngine, "mobile_api"),
    (AndroidUiEngine, "android_ui"),
])
def test_mobile_engines_are_base_engines(cls, expected_name):
    assert issubclass(cls, BaseEngine)
    eng = cls()
    assert eng.name == expected_name
    # O nome vive no conjunto reconhecido pela cascata.
    assert expected_name in MOBILE_ENGINE_NAMES


@pytest.mark.parametrize("cls", [MobileApiEngine, AndroidUiEngine])
def test_mobile_engines_are_stubs_that_fail_loud(cls):
    """Enquanto não implementadas, login/extract falham com mensagem clara
    citando a fase — nunca silenciosamente com dado falso."""
    eng = cls()
    with pytest.raises(NotImplementedError, match="Fase"):
        eng.login("u", "p")
    with pytest.raises(NotImplementedError, match="Fase"):
        eng.extract("target", "followers")


@pytest.mark.parametrize("cls,phase", [(AndroidUiEngine, "F7"), (MobileApiEngine, "F8")])
def test_mobile_stub_messages_cite_current_roadmap_phase(cls, phase):
    """A mensagem aponta a fase atual do roadmap (F7/F8), não a numeração da v10."""
    with pytest.raises(NotImplementedError, match=rf"\b{phase}\b"):
        cls().login("u", "p")


def test_mobile_stub_as_primary_engine_fails_fast_citing_phase():
    """Stub como engine primária falha na construção, antes de qualquer login,
    com NotImplementedError citando a fase — não com LoginError genérico."""
    from instat.extractor import InstaExtractor
    with pytest.raises(NotImplementedError, match=r"\bF7\b"):
        InstaExtractor("u", "p", engines=["android_ui", "selenium"])


def test_mobile_engines_implement_full_abstract_surface():
    """Não deve sobrar método abstrato (senão a classe nem instancia)."""
    for cls in (MobileApiEngine, AndroidUiEngine):
        assert not inspect.isabstract(cls)


# --------------------------------------------------------------------------
# _build_engines reconhece os novos nomes (opt-in) sem quebrar a cascata
# --------------------------------------------------------------------------

def test_build_engines_recognizes_mobile_names():
    """Os nomes 'mobile_api'/'android_ui' são reconhecidos e construídos
    como as classes certas — não caem no ramo 'Unknown engine name'."""
    ext = InstaExtractorNoInit()
    built = ext._build_engines(["mobile_api", "android_ui"], headless=True, timeout=10)
    types = {type(e) for e in built}
    assert MobileApiEngine in types
    assert AndroidUiEngine in types


def test_build_engines_mixed_cascade_keeps_existing_engines():
    """Mistura mobile + httpx: os nomes novos não atrapalham os antigos."""
    ext = InstaExtractorNoInit()
    built = ext._build_engines(
        ["mobile_api", "android_ui", "httpx"], headless=True, timeout=10
    )
    names = [e.name for e in built]
    assert "mobile_api" in names
    assert "android_ui" in names
    assert "httpx" in names  # engine antiga preservada


class InstaExtractorNoInit:
    """Acessa _build_engines sem rodar __init__ (que faria login real)."""

    def __new__(cls):
        from instat.extractor import InstaExtractor
        obj = object.__new__(InstaExtractor)
        return obj


# --------------------------------------------------------------------------
# AccountSlot: invariantes de 1 conta = 1 device = 1 porta sticky
# --------------------------------------------------------------------------

def _slot(account="acct01", port=10001, country="br", sessttl=120):
    return AccountSlot(
        account=account,
        device=DeviceProfile.for_account(account, country=country),
        proxy_port=port,
        country=country,
        sessttl=sessttl,
    )


def test_accountslot_valid():
    s = _slot()
    assert s.account == "acct01"
    assert 10000 <= s.proxy_port <= 20000
    assert s.device.account == "acct01"


@pytest.mark.parametrize("port", [0, 9999, 20001, 65535])
def test_accountslot_rejects_port_outside_sticky_range(port):
    with pytest.raises(ValueError, match="porta"):
        _slot(port=port)


@pytest.mark.parametrize("ttl", [0, -1, 121, 999])
def test_accountslot_rejects_sessttl_out_of_range(ttl):
    with pytest.raises(ValueError, match="sessttl"):
        _slot(sessttl=ttl)


def test_accountslot_rejects_empty_account():
    with pytest.raises(ValueError, match="account"):
        _slot(account="")


def test_deviceprofile_is_deterministic_per_account():
    """Mesma conta → mesmo device (IDs estáveis entre execuções)."""
    a = DeviceProfile.for_account("acct01", country="br")
    b = DeviceProfile.for_account("acct01", country="br")
    assert a == b
    assert a.android_id.startswith("android-")
    # contas diferentes → devices diferentes
    c = DeviceProfile.for_account("acct02", country="br")
    assert c != a


def test_validate_slots_detects_duplicate_account():
    slots = [_slot(account="dup", port=10001), _slot(account="dup", port=10002)]
    with pytest.raises(ValueError, match="conta"):
        validate_slots(slots)


def test_validate_slots_detects_duplicate_port():
    slots = [_slot(account="a", port=10001), _slot(account="b", port=10001)]
    with pytest.raises(ValueError, match="porta"):
        validate_slots(slots)


def test_validate_slots_accepts_unique_slots():
    slots = [_slot(account="a", port=10001), _slot(account="b", port=10002)]
    validate_slots(slots)  # não levanta
