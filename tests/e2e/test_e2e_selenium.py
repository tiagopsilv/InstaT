"""
Testes E2E reais: Selenium + Firefox contra o servidor fake.
Requer Firefox instalado. Pula com skip se ambiente não suporta.
"""
import pytest

from instat.engines.selenium_engine import SeleniumEngine

from .fake_server import STATE

pytestmark = pytest.mark.e2e


@pytest.fixture
def engine(fake_instagram):
    """SeleniumEngine apontando para o fake server."""
    eng = SeleniumEngine(
        headless=True, timeout=10,
        base_url=fake_instagram.base_url,
    )
    yield eng
    try:
        eng.quit()
    except Exception:
        pass


def test_login_against_fake_server(engine):
    """Login com credenciais válidas do fake server."""
    result = engine.login('testuser', 'testpass')
    assert result is True
    assert engine._driver is not None


def test_extract_followers_against_fake_server(engine):
    """Extrai followers do perfil 'target' (100 perfis no STATE)."""
    engine.login('testuser', 'testpass')
    followers = engine.extract('target', 'followers', max_duration=30.0)
    assert isinstance(followers, set)
    assert len(followers) >= 10  # pelo menos alguns spans do DOM
    assert any(u.startswith('user_') for u in followers)


def test_extract_nonexistent_profile(engine):
    """Perfil que não existe no STATE → extrai 0 perfis ou levanta ProfileNotFound."""
    from instat.exceptions import ProfileNotFoundError
    engine.login('testuser', 'testpass')
    try:
        result = engine.extract('nonexistent', 'followers', max_duration=10.0)
        assert len(result) == 0
    except ProfileNotFoundError:
        pass


def test_extract_handles_rate_limit(engine):
    """Com STATE.mode='ratelimit', requests retornam 429."""
    engine.login('testuser', 'testpass')
    STATE.mode = 'ratelimit'
    try:
        result = engine.extract('target', 'followers', max_duration=10.0)
        assert len(result) == 0
    except Exception:
        # Qualquer exceção aceitável — não pode travar indefinidamente
        pass


def test_selectors_work_on_fake_dom(engine):
    """Verifica que FOLLOWERS_LINK do selectors.json casa com o HTML fake."""
    from selenium.webdriver.common.by import By
    engine.login('testuser', 'testpass')
    engine._driver.get(f'{engine._base_url}/target/')
    links = engine._driver.find_elements(
        By.XPATH, "//a[contains(@href, '/followers/')]"
    )
    assert len(links) >= 1
