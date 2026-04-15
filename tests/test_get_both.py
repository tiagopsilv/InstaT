"""Testes para InstaExtractor.get_both (paralelo followers+following)."""
import unittest
from unittest.mock import MagicMock, patch


class TestHttpxLoginWithCookies(unittest.TestCase):
    def test_success_with_valid_cookies(self):
        from instat.engines.httpx_engine import HttpxEngine
        eng = HttpxEngine()
        if not eng.is_available:
            self.skipTest('httpx not installed')
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {'status': 'ok'}
        mock_client.get.return_value = mock_resp
        mock_client.cookies.get.side_effect = lambda k: {'sessionid': 'S', 'csrftoken': 'C'}.get(k)
        with patch.object(eng, '_build_client', return_value=mock_client):
            assert eng.login_with_cookies([
                {'name': 'sessionid', 'value': 'S', 'domain': '.instagram.com'},
                {'name': 'csrftoken', 'value': 'C', 'domain': '.instagram.com'},
            ]) is True

    def test_raises_on_invalid_cookies(self):
        from instat.engines.httpx_engine import HttpxEngine
        from instat.exceptions import BlockedError
        eng = HttpxEngine()
        if not eng.is_available:
            self.skipTest('httpx not installed')
        mock_client = MagicMock()
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        mock_client.get.return_value = mock_resp
        with patch.object(eng, '_build_client', return_value=mock_client):
            with self.assertRaises(BlockedError):
                eng.login_with_cookies([{'name': 'sessionid', 'value': 'X'}])


class TestGetBoth(unittest.TestCase):
    """Fluxo paralelo. Não instancia InstaExtractor real — monta um fake."""

    def _make_extractor(self):
        from instat.extractor import InstaExtractor
        ext = InstaExtractor.__new__(InstaExtractor)
        ext.username = 'u'
        ext.password = 'p'
        ext.timeout = 10
        ext._exporter = None
        ext._engine = MagicMock()
        ext._engine._driver = MagicMock()
        ext._engine._driver.get_cookies.return_value = [
            {'name': 'sessionid', 'value': 'S', 'domain': '.instagram.com'}
        ]
        ext._engine_manager = MagicMock()
        return ext

    def test_happy_path_httpx_following_succeeds(self):
        ext = self._make_extractor()
        ext._engine_manager.extract.return_value = ['f1', 'f2']
        with patch('instat.engines.httpx_engine.HttpxEngine') as MockHttpx:
            inst = MockHttpx.return_value
            inst.is_available = True
            inst.login_with_cookies.return_value = True
            inst.extract.return_value = {'g1', 'g2', 'g3'}
            out = ext.get_both('target')
        self.assertEqual(set(out['followers']), {'f1', 'f2'})
        self.assertEqual(set(out['following']), {'g1', 'g2', 'g3'})
        inst.login_with_cookies.assert_called_once()

    def test_falls_back_to_selenium2_when_httpx_fails(self):
        ext = self._make_extractor()
        ext._engine_manager.extract.return_value = ['f1']
        with patch('instat.engines.httpx_engine.HttpxEngine') as MockHttpx, \
             patch('instat.extractor.SeleniumEngine') as MockSel:
            MockHttpx.return_value.is_available = True
            MockHttpx.return_value.login_with_cookies.side_effect = Exception('403')
            sel2 = MockSel.return_value
            sel2.extract.return_value = {'g1'}
            out = ext.get_both('target')
        self.assertEqual(out['following'], ['g1'])
        MockSel.assert_called_once()

    def test_sequential_fallback_when_both_parallel_paths_fail(self):
        ext = self._make_extractor()
        call_log = []
        def extract_side_effect(pid, ltype, **kw):
            call_log.append(ltype)
            return ['f1'] if ltype == 'followers' else ['seq_g1']
        ext._engine_manager.extract.side_effect = extract_side_effect
        with patch('instat.engines.httpx_engine.HttpxEngine') as MockHttpx, \
             patch('instat.extractor.SeleniumEngine') as MockSel:
            MockHttpx.return_value.is_available = True
            MockHttpx.return_value.login_with_cookies.side_effect = Exception('403')
            MockSel.return_value.login.side_effect = Exception('blocked')
            out = ext.get_both('target')
        self.assertEqual(out['followers'], ['f1'])
        self.assertEqual(out['following'], ['seq_g1'])
        self.assertIn('following', call_log)


if __name__ == '__main__':
    unittest.main()
