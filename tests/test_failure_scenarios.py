"""Passo 3 — cenários de falha controlada (mocks).

Cobre gaps do test-suite existente:
- stale element durante wait_for_new_profiles
- modal não clicável (dismiss_save_login_modal fallback)
- checkpoint: retomar após interrupção simulada
- sessão expirada no fluxo de login (restore → fallback form)
"""
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from selenium.common.exceptions import (
    ElementClickInterceptedException,
    StaleElementReferenceException,
    TimeoutException,
)


class TestStaleElementHandling(unittest.TestCase):
    """wait_for_new_profiles deve absorver stale e ainda terminar."""

    @patch('instat.utils.human_delay', return_value=0)
    def test_stale_then_recovery_exits_loop(self, _hd):
        from instat.utils import Utils

        driver = MagicMock()
        # spinner check: find_elements returns [] (no spinner)
        driver.find_elements.return_value = []

        # 1ª chamada batch_read_text: stale; 2ª: snapshot com novos;
        # 3ª (snapshot_after_scroll): mesmos perfis → break normal.
        call_count = {'n': 0}

        def fake_batch(_driver, _sel):
            call_count['n'] += 1
            if call_count['n'] == 1:
                raise StaleElementReferenceException("stale")
            return {'alice', 'bob'}

        with patch.object(Utils, 'batch_read_text', side_effect=fake_batch), \
             patch.object(Utils, 'dynamic_scroll_element'):
            # Não deve entrar em loop infinito
            Utils.wait_for_new_profiles(
                driver, scrollable_element=MagicMock(),
                profile_selector='span._ap3a',
                existing_profiles=set(),
                wait_interval=0, additional_scroll_attempts=1,
            )

        # Garantimos múltiplas chamadas (stale + recovery + post-scroll)
        self.assertGreaterEqual(call_count['n'], 2)


class TestModalNotClickable(unittest.TestCase):
    """dismiss_save_login_modal deve tolerar modal presente mas não-clicável."""

    def _make_selectors(self):
        m = MagicMock()
        m.get_all.return_value = [
            "//div[@role='dialog']//button[1]",  # targeted (timeout)
            "//div[@role='dialog']//button[2]",  # targeted (timeout)
            "//button",                          # generic (keyword filter)
        ]
        return m

    @patch('instat.utils.human_delay', return_value=0)
    def test_click_intercepted_falls_back_to_js_click(self, _hd):
        """Phase 1: native .click() raises ElementClickIntercepted → fallback via execute_script."""
        from instat.utils import Utils

        with patch.object(Utils, 'selectors', new=self._make_selectors()):
            driver = MagicMock()
            button = MagicMock()
            button.click.side_effect = ElementClickInterceptedException("overlay blocks click")

            # WebDriverWait.until returns truthy; find_elements returns our button
            with patch('instat.utils.WebDriverWait') as MockWait:
                MockWait.return_value.until.return_value = True
                driver.find_elements.return_value = [button]

                result = Utils.dismiss_save_login_modal(
                    driver, close_keywords=['not now'], timeout=1
                )

        self.assertTrue(result, "Should succeed via JS-click fallback")
        driver.execute_script.assert_called()

    @patch('instat.utils.human_delay', return_value=0)
    def test_all_strategies_fail_returns_false_without_raise(self, _hd):
        """When targeted times out AND generic finds nothing, returns False silently."""
        from instat.utils import Utils

        with patch.object(Utils, 'selectors', new=self._make_selectors()):
            driver = MagicMock()
            driver.find_elements.return_value = []  # nothing matches generic

            with patch('instat.utils.WebDriverWait') as MockWait:
                MockWait.return_value.until.side_effect = TimeoutException()

                result = Utils.dismiss_save_login_modal(
                    driver, close_keywords=['not now'], timeout=1
                )

        self.assertFalse(result)


class TestCheckpointResumeFlow(unittest.TestCase):
    """Simula: extração salva checkpoint, processo morre, 2ª chamada retoma."""

    def test_second_extract_resumes_from_saved_profiles(self):
        from instat.checkpoint import ExtractionCheckpoint

        with tempfile.TemporaryDirectory() as tmpdir:
            # Run 1: salva 3 perfis e "crasha"
            ckpt1 = ExtractionCheckpoint(
                'tiagopsilv', 'followers', checkpoint_dir=tmpdir
            )
            ckpt1.save({'u1', 'u2', 'u3'})
            # Simula crash — não limpa
            del ckpt1

            # Run 2: novo processo abre o mesmo arquivo
            ckpt2 = ExtractionCheckpoint(
                'tiagopsilv', 'followers', checkpoint_dir=tmpdir
            )
            resumed = ckpt2.load()

        self.assertEqual(resumed, {'u1', 'u2', 'u3'},
                         "Second run must resume exactly what was saved")

    def test_corrupt_checkpoint_is_cleared_silently(self):
        """Se o JSON ficou corrompido num crash mid-write, load retorna None
        e remove o arquivo, para não derrubar a próxima extração."""
        from instat.checkpoint import ExtractionCheckpoint

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'u_followers.json')
            with open(path, 'w', encoding='utf-8') as f:
                f.write('{"profiles": [INVALID')  # JSON truncado/corrompido

            ckpt = ExtractionCheckpoint('u', 'followers', checkpoint_dir=tmpdir)
            self.assertIsNone(ckpt.load())
            self.assertFalse(os.path.exists(path),
                             "Corrupt file should be cleared by load()")

    def test_missing_keys_checkpoint_is_cleared_silently(self):
        """JSON válido mas sem os campos esperados também é inválido."""
        from instat.checkpoint import ExtractionCheckpoint

        with tempfile.TemporaryDirectory() as tmpdir:
            path = os.path.join(tmpdir, 'u_followers.json')
            with open(path, 'w', encoding='utf-8') as f:
                f.write('{"unexpected": 1}')

            ckpt = ExtractionCheckpoint('u', 'followers', checkpoint_dir=tmpdir)
            self.assertIsNone(ckpt.load())
            self.assertFalse(os.path.exists(path))


class TestExpiredSessionFallsBackToForm(unittest.TestCase):
    """Login deve detectar cookie expirado e cair no form login normal."""

    def _patch_firefox_and_driver(self):
        patches = []
        p1 = patch("instat.login.webdriver.Firefox")
        patches.append(p1)
        mock_driver = p1.start().return_value
        p2 = patch("instat.login.GeckoDriverManager")
        patches.append(p2)
        p2.start()
        p3 = patch("instat.login.Service")
        patches.append(p3)
        p3.start()

        selmap = {
            "LOGIN_USERNAME_INPUT": "input[name='username']",
            "LOGIN_PASSWORD_INPUT": "input[name='password']",
            "LOGIN_BUTTON_CANDIDATE": "//button",
        }
        p4 = patch("instat.login.SelectorLoader")
        patches.append(p4)
        mock_loader = p4.start().return_value
        mock_loader.get.side_effect = lambda k: selmap[k]
        return mock_driver, patches

    def test_stale_cache_redirects_to_login_triggers_form_fallback(self):
        from instat.login import InstaLogin

        mock_driver, patches = self._patch_firefox_and_driver()
        try:
            # Cache "expirado" do ponto de vista do IG: cookies add com sucesso
            # mas refresh acaba em /accounts/login/.
            # _try_restore_session deve retornar False, limpar cookies,
            # e o fluxo principal segue para form login.
            mock_cache = MagicMock()
            mock_cache.load.return_value = [
                {'name': 'sessionid', 'value': 'stale_value'}
            ]

            # current_url sequence (read after driver.refresh() in restore,
            # then after form fill):
            #   1. restore check: '/accounts/login/' (expired → triggers fail path)
            #   2. after form login: '/' (success, _check_account_blocked passes)
            urls = iter([
                'https://www.instagram.com/accounts/login/',
                'https://www.instagram.com/',
                'https://www.instagram.com/',
                'https://www.instagram.com/',
            ])
            type(mock_driver).current_url = property(lambda self: next(urls))
            mock_driver.page_source = "<html></html>"
            mock_driver.title = "Instagram"
            # Singular get_cookie (used by line 284) — set so restore path
            # reaches the URL check first (fail there); but also set to None
            # so if URL check misses, sessionid check catches it.
            mock_driver.get_cookie.return_value = None
            mock_driver.get_cookies.return_value = [
                {'name': 'sessionid', 'value': 'fresh123'}
            ]

            with patch("instat.login_flow.WebDriverWait") as mock_wait:
                username_mock = MagicMock()
                password_mock = MagicMock()
                mock_wait.return_value.until.side_effect = [
                    username_mock, password_mock, True
                ]

                client = InstaLogin(
                    'tester', 'pw', headless=True, session_cache=mock_cache
                )
                result = client.login()

            self.assertTrue(result)
            # Stale cookies devem ter sido limpos durante _try_restore_session
            mock_driver.delete_all_cookies.assert_called()
            # Form foi preenchido (fallback)
            username_mock.send_keys.assert_any_call('tester')
            password_mock.send_keys.assert_any_call('pw')
            # Cookies novos devem ter sido salvos
            mock_cache.save.assert_called_once_with(
                'tester', [{'name': 'sessionid', 'value': 'fresh123'}]
            )
        finally:
            for p in patches:
                p.stop()


if __name__ == '__main__':
    unittest.main()
