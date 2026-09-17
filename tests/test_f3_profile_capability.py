"""F3 — metadados de perfil sem Selenium. Critérios em docs/phase-evidence/fase-3/README.md."""
import json
from typing import Optional, Set
from unittest.mock import MagicMock, patch

import pytest

from instat import InstaExtractor
from instat.engines.base import BaseEngine
from instat.exceptions import ChallengeError, ExtractionStoppedError, ProfileNotFoundError
from instat.profile import Profile
from instat.profile_info import ProfileInfo


class LegacyEngine(BaseEngine):
    """Escrita contra a interface antiga: só os métodos abstratos."""

    def __init__(self, name="legacy"):
        self._name = name
        self.extract_calls = 0

    def login(self, username, password, **kw):
        return True

    def extract(self, profile_id, list_type, existing_profiles: Optional[Set[str]] = None,
                max_duration=None, on_batch=None):
        self.extract_calls += 1
        return {"x"}

    def get_total_count(self, profile_id, list_type):
        return 1

    def quit(self):
        pass

    @property
    def name(self):
        return self._name

    @property
    def is_available(self):
        return True


class CapableEngine(LegacyEngine):
    def __init__(self, name="capable", info=None, exc=None):
        super().__init__(name)
        self.info_calls = 0
        self._info = info
        self._exc = exc

    def get_profile_info(self, profile_id):
        self.info_calls += 1
        if self._exc is not None:
            raise self._exc
        return self._info or ProfileInfo(username=profile_id, url=f"https://www.instagram.com/{profile_id}/",
                                         full_name="Nome Capaz", followers_count=42, following_count=7,
                                         posts_count=3, is_private=False, is_verified=True)


def ext_with(*engines):
    return InstaExtractor(username="u", password="p", headless=True, engines=list(engines))


# ------------------------------------------------------------ 2. subclasse legada
def test_legacy_subclass_still_instantiable_and_declares_no_profile_info():
    e = LegacyEngine()
    assert "profile_info" not in e.capabilities
    assert {"extract", "total_count"} <= e.capabilities
    with pytest.raises(NotImplementedError):
        e.get_profile_info("alvo")


def test_get_profile_with_only_legacy_engine_explains_missing_capability():
    ext = ext_with(LegacyEngine("minha-engine"))
    with pytest.raises(RuntimeError) as err:
        ext.get_profile("alvo")
    msg = str(err.value)
    assert "minha-engine" in msg and "profile_info" in msg
    assert "selenium" in msg and "httpx" in msg


# ------------------------------------------------------------ 4. cascata e governador
def test_primary_without_capability_falls_to_capable_secondary():
    legacy, capable = LegacyEngine(), CapableEngine()
    ext = ext_with(legacy, capable)
    p = ext.get_profile("alvo")
    assert isinstance(p, Profile) and p._extractor is ext
    assert (p.full_name, p.followers_count, p.is_verified) == ("Nome Capaz", 42, True)
    assert capable.info_calls == 1


def test_challenge_on_profile_info_stops_cascade():
    first = CapableEngine("c1", exc=ChallengeError("checkpoint_required"))
    second = CapableEngine("c2")
    ext = ext_with(first, second)
    with pytest.raises(ExtractionStoppedError) as err:
        ext.get_profile("alvo")
    assert err.value.reason == "challenge"
    assert second.info_calls == 0


def test_not_implemented_and_technical_errors_go_to_next_engine():
    from instat.exceptions import BlockedError
    first = CapableEngine("c1", exc=BlockedError("parse falhou"))
    second = CapableEngine("c2")
    assert ext_with(first, second).get_profile("alvo").full_name == "Nome Capaz"
    assert second.info_calls == 1


# ------------------------------------------------------------ 5. httpx
WEB_PROFILE_INFO = {"data": {"user": {
    "id": "123", "username": "alvo", "full_name": "Alvo da Silva", "biography": "bio\nlinha 2",
    "edge_followed_by": {"count": 1894}, "edge_follow": {"count": 1892},
    "edge_owner_to_timeline_media": {"count": 123}, "is_private": False, "is_verified": True,
    "profile_pic_url_hd": "https://cdn.example/hd.jpg", "profile_pic_url": "https://cdn.example/sd.jpg",
}}, "status": "ok"}


def _httpx_with(status, body):
    pytest.importorskip("httpx")
    from instat.engines.httpx_engine import HttpxEngine
    eng = HttpxEngine()
    r = MagicMock(status_code=status, headers={}, text=json.dumps(body), content=json.dumps(body).encode())
    r.json.return_value = body
    eng._client = MagicMock(get=MagicMock(return_value=r))
    return eng


def test_httpx_profile_info_from_web_profile_info():
    info = _httpx_with(200, WEB_PROFILE_INFO).get_profile_info("alvo")
    assert info == ProfileInfo(username="alvo", url="https://www.instagram.com/alvo/", full_name="Alvo da Silva",
                               bio="bio\nlinha 2", followers_count=1894, following_count=1892, posts_count=123,
                               is_private=False, is_verified=True, profile_pic_url="https://cdn.example/hd.jpg")


def test_httpx_profile_info_404():
    with pytest.raises(ProfileNotFoundError):
        _httpx_with(404, {}).get_profile_info("sumiu")


# ------------------------------------------------------------ 6. selenium mantém o resultado
META = {
    "og:description": "1,894 Followers, 1,892 Following, 123 Posts - @target",
    "og:title": "Target Name (@target) • Instagram photos and videos",
    "og:image": "https://example.test/pic.jpg",
}


def _fake_driver(meta=META):
    import re

    class El:
        def __init__(self, v):
            self.v = v

        def get_attribute(self, _):
            return self.v

    d = MagicMock()

    def find_element(_by, selector):
        prop = re.search(r'property="([^"]+)"', selector).group(1)
        if prop in meta:
            return El(meta[prop])
        raise RuntimeError("not found")
    d.find_element.side_effect = find_element
    d.execute_script.return_value = None
    return d


def test_selenium_profile_info_matches_legacy_reader():
    from instat.engines.selenium_engine import SeleniumEngine
    eng = SeleniumEngine(headless=True)
    eng._driver = _fake_driver()
    info = eng.get_profile_info("target")
    assert (info.username, info.full_name, info.followers_count, info.following_count, info.posts_count,
            info.profile_pic_url) == ("target", "Target Name", 1894, 1892, 123, "https://example.test/pic.jpg")


def test_profile_readers_module_is_shared_by_selenium_and_legacy_path():
    from instat import profile_readers
    info = profile_readers.read_profile_from_driver(_fake_driver(), "target")
    assert info.followers_count == 1894 and info.full_name == "Target Name"


# ------------------------------------------------------------ 7. matriz de capacidades
def test_capability_matrix():
    from instat.engines.playwright_engine import PlaywrightEngine
    from instat.engines.selenium_engine import SeleniumEngine
    from instat.mobile.engines import AndroidUiEngine, MobileApiEngine
    assert SeleniumEngine(headless=True).capabilities == frozenset({"extract", "total_count", "profile_info"})
    assert PlaywrightEngine().capabilities == frozenset({"extract", "total_count"})
    assert AndroidUiEngine().capabilities == frozenset()
    assert MobileApiEngine().capabilities == frozenset()
    pytest.importorskip("httpx")
    from instat.engines.httpx_engine import HttpxEngine
    assert HttpxEngine().capabilities == frozenset({"extract", "total_count", "profile_info", "recent_posts"})


def test_explicit_capabilities_opt_in_overrides_derivation():
    class Declared(CapableEngine):
        capabilities = frozenset({"extract"})
    ext = ext_with(Declared("declarada"))
    with pytest.raises(RuntimeError):
        ext.get_profile("alvo")


# ------------------------------------------------------------ 8. extra não instalado
def test_message_cites_missing_extra():
    with patch("instat.engines.httpx_engine.HttpxEngine.is_available", new=property(lambda self: False)):
        ext = ext_with(LegacyEngine("primaria"), "httpx")
    with pytest.raises(RuntimeError) as err:
        ext.get_profile("alvo")
    assert "pip install instat[httpx]" in str(err.value)
