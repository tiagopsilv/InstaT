"""Testes para PERF-02: partial coverage BlockedError + reset page state."""
import unittest
from unittest.mock import MagicMock, patch


class TestCompletionThreshold(unittest.TestCase):
    """Solução E: cobertura abaixo de threshold levanta BlockedError."""

    def test_default_threshold_is_90_percent(self):
        from instat.engines.selenium_engine import SeleniumEngine
        eng = SeleniumEngine()
        self.assertEqual(eng.completion_threshold, 0.90)

    def test_threshold_is_configurable(self):
        from instat.engines.selenium_engine import SeleniumEngine
        eng = SeleniumEngine()
        eng.completion_threshold = 0.95
        self.assertEqual(eng.completion_threshold, 0.95)


class TestPartialCoverageBlockedError(unittest.TestCase):
    """_get_profiles levanta BlockedError quando coverage < threshold."""

    @patch('instat.engines.selenium_engine.human_delay', return_value=0)
    def test_partial_coverage_raises_blocked_error(self, _hd):
        from instat.engines.selenium_engine import SeleniumEngine
        from instat.exceptions import BlockedError
        eng = SeleniumEngine()
        eng._driver = MagicMock()
        eng._selectors = MagicMock()
        eng._selectors.get.return_value = 'span._ap3a'

        # Simula batch_read_text retornando sempre mesmos 50 perfis
        with patch('instat.engines.selenium_engine.Utils.batch_read_text',
                   return_value={f'u{i}' for i in range(50)}):
            with self.assertRaises(BlockedError) as cm:
                eng._get_profiles(expected_count=100, max_duration=5.0)
            self.assertIn('partial coverage', str(cm.exception))
            self.assertIn('50/100', str(cm.exception))

    @patch('instat.engines.selenium_engine.human_delay', return_value=0)
    def test_full_coverage_returns_normally(self, _hd):
        from instat.engines.selenium_engine import SeleniumEngine
        eng = SeleniumEngine()
        eng._driver = MagicMock()
        eng._selectors = MagicMock()
        eng._selectors.get.return_value = 'span._ap3a'

        # 100/100 profiles → sem BlockedError
        with patch('instat.engines.selenium_engine.Utils.batch_read_text',
                   return_value={f'u{i}' for i in range(100)}):
            result = eng._get_profiles(expected_count=100, max_duration=5.0)
            self.assertEqual(len(result), 100)

    @patch('instat.engines.selenium_engine.human_delay', return_value=0)
    def test_coverage_exactly_at_threshold_returns_normally(self, _hd):
        from instat.engines.selenium_engine import SeleniumEngine
        eng = SeleniumEngine()
        eng.completion_threshold = 0.50
        eng._driver = MagicMock()
        eng._selectors = MagicMock()
        eng._selectors.get.return_value = 'span._ap3a'

        # 50/100 = 0.50 (exatamente o threshold) → NÃO levanta
        with patch('instat.engines.selenium_engine.Utils.batch_read_text',
                   return_value={f'u{i}' for i in range(50)}):
            result = eng._get_profiles(expected_count=100, max_duration=5.0)
            self.assertEqual(len(result), 50)

    @patch('instat.engines.selenium_engine.human_delay', return_value=0)
    def test_zero_profiles_does_not_raise(self, _hd):
        """Se coletou 0, não levanta BlockedError — retorna [] para permitir
        outras engines tentarem."""
        from instat.engines.selenium_engine import SeleniumEngine
        eng = SeleniumEngine()
        eng._driver = MagicMock()
        eng._selectors = MagicMock()
        eng._selectors.get.return_value = 'span._ap3a'

        with patch('instat.engines.selenium_engine.Utils.batch_read_text',
                   return_value=set()):
            result = eng._get_profiles(expected_count=100, max_duration=2.0)
            self.assertEqual(result, [])

    @patch('instat.engines.selenium_engine.human_delay', return_value=0)
    def test_partial_coverage_saves_checkpoint_before_raising(self, _hd):
        from instat.engines.selenium_engine import SeleniumEngine
        from instat.exceptions import BlockedError
        eng = SeleniumEngine()
        eng._driver = MagicMock()
        eng._selectors = MagicMock()
        eng._selectors.get.return_value = 'span._ap3a'
        mock_ckpt = MagicMock()

        with patch('instat.engines.selenium_engine.Utils.batch_read_text',
                   return_value={f'u{i}' for i in range(50)}):
            with self.assertRaises(BlockedError):
                eng._get_profiles(expected_count=100, max_duration=2.0,
                                  checkpoint=mock_ckpt)
        # Checkpoint deve ter sido salvo com os 50 perfis antes do raise
        mock_ckpt.save.assert_called()
        last_call_args = mock_ckpt.save.call_args.args
        saved_profiles = last_call_args[0]
        self.assertEqual(len(saved_profiles), 50)


class TestResetPageState(unittest.TestCase):
    """Solução D: _reset_page_state navega para about:blank."""

    @patch('instat.engines.selenium_engine.human_delay', return_value=0)
    def test_reset_navigates_to_about_blank(self, _hd):
        from instat.engines.selenium_engine import SeleniumEngine
        eng = SeleniumEngine()
        eng._driver = MagicMock()
        eng._reset_page_state()
        eng._driver.get.assert_called_once_with('about:blank')

    @patch('instat.engines.selenium_engine.human_delay', return_value=0)
    def test_reset_handles_driver_error_silently(self, _hd):
        from instat.engines.selenium_engine import SeleniumEngine
        eng = SeleniumEngine()
        eng._driver = MagicMock()
        eng._driver.get.side_effect = Exception("driver dead")
        # Não deve levantar
        eng._reset_page_state()


class TestReopenModal(unittest.TestCase):
    """PERF-03 Solução G: reopen modal para contornar rate limit."""

    @patch('instat.engines.selenium_engine.human_delay', return_value=0)
    @patch('instat.engines.selenium_engine.Utils')
    @patch('instat.engines.selenium_engine.WebDriverWait')
    def test_reopen_modal_success(self, MockWait, MockUtils, _hd):
        from instat.engines.selenium_engine import SeleniumEngine
        eng = SeleniumEngine()
        eng._driver = MagicMock()
        eng._selectors = MagicMock()
        eng._selectors.get.return_value = '//button'
        eng._selectors.get_all.return_value = ["//a[@href*='/followers/']"]
        MockUtils.find_element_with_fallback.return_value = MagicMock()
        with patch.object(eng, '_click_link_element', return_value=True):
            result = eng._reopen_modal('tiagopsilv', 'followers')
        self.assertTrue(result)

    @patch('instat.modal_interaction.human_delay', return_value=0)
    @patch('instat.modal_interaction.Utils')
    def test_reopen_modal_returns_false_when_link_not_found(self, MockUtils, _hd):
        # Reopen now delegates to ModalInteraction — patch there.
        from instat.engines.selenium_engine import SeleniumEngine
        eng = SeleniumEngine()
        eng._driver = MagicMock()
        eng._selectors = MagicMock()
        eng._selectors.get.return_value = '//button'
        eng._selectors.get_all.return_value = ['//a']
        MockUtils.find_element_with_fallback.return_value = None
        result = eng._reopen_modal('p', 'followers')
        self.assertFalse(result)

    @patch('instat.engines.selenium_engine.human_delay', return_value=0)
    def test_reopen_modal_handles_exception_gracefully(self, _hd):
        from instat.engines.selenium_engine import SeleniumEngine
        eng = SeleniumEngine()
        eng._driver = MagicMock()
        eng._driver.get.side_effect = Exception("driver dead")
        eng._selectors = MagicMock()
        result = eng._reopen_modal('p', 'followers')
        self.assertFalse(result)


class TestGetProfilesTriggersReopenOnRateLimit(unittest.TestCase):
    """PERF-03: quando bate rate limit (stale rounds), chama _reopen_modal."""

    @patch('instat.engines.selenium_engine.human_delay', return_value=0)
    def test_reopen_called_on_stale_rounds_threshold(self, _hd):
        from instat.engines.selenium_engine import SeleniumEngine
        eng = SeleniumEngine()
        eng._driver = MagicMock()
        eng._selectors = MagicMock()
        eng._selectors.get.return_value = 'span._ap3a'

        # Simula 150 perfis em burst, depois nada — rate limit
        call_count = {'n': 0}
        def mock_read(*args, **kw):
            call_count['n'] += 1
            if call_count['n'] <= 3:
                return {f'u{i}' for i in range(50 * call_count['n'])}
            return {f'u{i}' for i in range(150)}  # nada novo após 3 calls

        with patch('instat.engines.selenium_engine.Utils.batch_read_text', side_effect=mock_read), \
             patch.object(eng, '_reopen_modal', return_value=True) as mock_reopen:
            # Expected_count alto para forçar continuar tentando
            try:
                eng._get_profiles(
                    expected_count=1000, max_duration=1.5,
                    profile_id='p', list_type='followers',
                )
            except Exception:
                pass  # BlockedError por cobertura parcial é esperado
            # _reopen_modal DEVE ter sido chamado ao menos 1x
            self.assertTrue(mock_reopen.called)

    @patch('instat.engines.selenium_engine.human_delay', return_value=0)
    def test_reopen_not_called_without_profile_id(self, _hd):
        """Se profile_id/list_type não passados, não tenta reopen."""
        from instat.engines.selenium_engine import SeleniumEngine
        eng = SeleniumEngine()
        eng._driver = MagicMock()
        eng._selectors = MagicMock()
        eng._selectors.get.return_value = 'span._ap3a'

        with patch('instat.engines.selenium_engine.Utils.batch_read_text',
                   return_value={f'u{i}' for i in range(50)}), \
             patch.object(eng, '_reopen_modal', return_value=True) as mock_reopen:
            try:
                eng._get_profiles(expected_count=1000, max_duration=1.0)
            except Exception:
                pass
            mock_reopen.assert_not_called()


if __name__ == '__main__':
    unittest.main()
