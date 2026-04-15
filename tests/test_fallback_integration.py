import unittest
from typing import Callable, Optional, Set
from unittest.mock import MagicMock, patch

from instat.engines.base import BaseEngine
from instat.engines.engine_manager import EngineManager
from instat.exceptions import AccountBlockedError, AllEnginesBlockedError, BlockedError, RateLimitError
from instat.session_pool import SessionPool


class MockEngine(BaseEngine):
    """Configurable mock engine for integration tests."""

    def __init__(self, engine_name='mock', available=True,
                 profiles=None, count=100, exc_on_extract=None,
                 exc_on_login=None):
        self._name = engine_name
        self._available = available
        self._profiles = profiles if profiles is not None else {'u1', 'u2'}
        self._count = count
        self._exc_on_extract = exc_on_extract
        self._exc_on_login = exc_on_login
        self.login_calls = []
        self.extract_calls = []

    def login(self, username, password, **kw):
        self.login_calls.append({'username': username, 'proxy': kw.get('proxy')})
        if self._exc_on_login:
            raise self._exc_on_login
        return True

    def extract(self, profile_id, list_type, existing_profiles=None,
                max_duration=None, on_batch=None):
        self.extract_calls.append({
            'profile_id': profile_id,
            'list_type': list_type,
            'existing': set(existing_profiles) if existing_profiles else set(),
        })
        if self._exc_on_extract:
            if on_batch:
                on_batch(self._profiles)
            raise self._exc_on_extract
        if on_batch:
            on_batch(self._profiles)
        return self._profiles

    def get_total_count(self, profile_id, list_type):
        return self._count

    def quit(self):
        pass

    @property
    def name(self):
        return self._name

    @property
    def is_available(self):
        return self._available


class TestFallbackIntegration(unittest.TestCase):

    def setUp(self):
        # Mock ExtractionCheckpoint inside engine_manager (avoid disk pollution)
        self._ckpt_patcher = patch(
            'instat.engines.engine_manager.ExtractionCheckpoint'
        )
        MockCkpt = self._ckpt_patcher.start()
        self._ckpt_instance = MagicMock()
        self._ckpt_instance.load.return_value = None
        MockCkpt.return_value = self._ckpt_instance

        # Mock SmartBackoff to avoid real sleeps
        self._backoff_patcher = patch(
            'instat.engines.engine_manager.SmartBackoff'
        )
        MockBackoff = self._backoff_patcher.start()
        self._backoff_instance = MagicMock()
        MockBackoff.return_value = self._backoff_instance

    def tearDown(self):
        self._ckpt_patcher.stop()
        self._backoff_patcher.stop()

    def test_selenium_blocked_playwright_succeeds(self):
        """Engine 1 blocked (sem progresso) → Engine 2 succeeds."""
        # MockEngine com profiles=set() não chama on_batch com dados
        # (garantindo que manager.profiles fica vazio após fail da engine1)
        mock_sel = MockEngine('selenium',
                              profiles=set(),
                              exc_on_extract=BlockedError('sel blocked'))
        mock_pw = MockEngine('playwright', profiles={'a', 'b'})
        mgr = EngineManager([mock_sel, mock_pw])
        result = mgr.extract('target', 'followers')
        self.assertEqual(set(result), {'a', 'b'})

    def test_session1_blocked_session2_succeeds(self):
        """Session 1 raises RateLimitError → Session 2 succeeds."""
        mock_engine = MockEngine('selenium', profiles={'x', 'y'})
        call_count = {'n': 0}
        orig_extract = mock_engine.extract

        def extract_once_then_success(*args, **kw):
            call_count['n'] += 1
            if call_count['n'] == 1:
                raise RateLimitError('session 1 rate limited')
            return orig_extract(*args, **kw)
        mock_engine.extract = extract_once_then_success

        sp = SessionPool([
            {'username': 'acc1', 'password': 'p1'},
            {'username': 'acc2', 'password': 'p2'},
        ])
        mgr = EngineManager([mock_engine], session_pool=sp)
        result = mgr.extract('target', 'followers')
        self.assertEqual(set(result), {'x', 'y'})
        # Login was called twice (once per session)
        self.assertEqual(len(mock_engine.login_calls), 2)
        self.assertEqual(mock_engine.login_calls[0]['username'], 'acc1')
        self.assertEqual(mock_engine.login_calls[1]['username'], 'acc2')

    def test_checkpoint_resume_after_crash(self):
        """Existing checkpoint profiles are passed to extract as existing_profiles."""
        self._ckpt_instance.load.return_value = {'a', 'b', 'c'}
        mock_engine = MockEngine('selenium', profiles={'d', 'e'})
        mgr = EngineManager([mock_engine])
        result = mgr.extract('target', 'followers')
        self.assertEqual(set(result), {'a', 'b', 'c', 'd', 'e'})
        self.assertEqual(
            mock_engine.extract_calls[0]['existing'], {'a', 'b', 'c'}
        )

    def test_all_blocked_returns_partial(self):
        """All engines fail but checkpoint has data → returns partial."""
        self._ckpt_instance.load.return_value = {'cached1', 'cached2'}
        # profiles=set() para não contaminar via on_batch
        mock_engine = MockEngine('selenium', profiles=set(),
                                 exc_on_extract=BlockedError('blocked'))
        mgr = EngineManager([mock_engine])
        result = mgr.extract('target', 'followers')
        self.assertEqual(set(result), {'cached1', 'cached2'})

    def test_all_blocked_no_progress_raises(self):
        """All engines fail AND no checkpoint AND no partial progress
        → AllEnginesBlockedError."""
        self._ckpt_instance.load.return_value = None
        # profiles=set() = nenhum progresso via on_batch
        mock_engine = MockEngine('selenium', profiles=set(),
                                 exc_on_extract=BlockedError('blocked'))
        mgr = EngineManager([mock_engine])
        with self.assertRaises(AllEnginesBlockedError):
            mgr.extract('target', 'followers')

    def test_partial_progress_returned_even_on_blocked_error(self):
        """Engine levanta BlockedError mas já havia notificado batch via on_batch
        → retorna parcial em vez de levantar AllEnginesBlockedError.
        Este é o fluxo PERF-02: Selenium cobertura parcial → retorna parcial."""
        self._ckpt_instance.load.return_value = None
        # profiles com dados → on_batch é chamado antes do raise
        mock_engine = MockEngine('selenium', profiles={'p1', 'p2'},
                                 exc_on_extract=BlockedError('partial coverage'))
        mgr = EngineManager([mock_engine])
        result = mgr.extract('target', 'followers')
        # Mesmo com BlockedError, retorna parcial graças ao on_batch
        self.assertEqual(set(result), {'p1', 'p2'})

    def test_account_blocked_uses_longer_cooldown(self):
        """AccountBlockedError triggers 6h cooldown (META_INTERSTITIAL_COOLDOWN)."""
        acc_exc = AccountBlockedError('meta', reason='meta', url='x')
        # Engine that raises AccountBlockedError — triggers cooldown
        # Followed by engine that succeeds — but extract still finishes even
        # if all sessions are consumed by the first engine (returns partial/empty)
        mock_engine_block = MockEngine('selenium', exc_on_extract=acc_exc)
        # Extra session so we capture the cooldown before exhaustion
        sp = SessionPool([
            {'username': 'acc1', 'password': 'p1'},
        ])
        # Pre-load checkpoint so extract doesn't raise AllEnginesBlockedError
        self._ckpt_instance.load.return_value = {'existing'}
        with patch.object(sp, 'mark_blocked', wraps=sp.mark_blocked) as spy:
            mgr = EngineManager([mock_engine_block], session_pool=sp)
            mgr.extract('target', 'followers')
            # mark_blocked called with META cooldown for AccountBlockedError
            cooldowns_used = [call.args[1] for call in spy.call_args_list if len(call.args) > 1]
            self.assertIn(SessionPool.META_INTERSTITIAL_COOLDOWN, cooldowns_used)

    def test_on_batch_saves_checkpoint(self):
        """engine.extract's on_batch callback triggers checkpoint.save."""
        mock_engine = MockEngine('selenium', profiles={'p1', 'p2'})
        mgr = EngineManager([mock_engine])
        mgr.extract('target', 'followers')
        # on_batch was invoked with profiles → checkpoint.save called
        self._ckpt_instance.save.assert_called()

    def test_checkpoint_cleared_on_success(self):
        """Successful extraction clears the checkpoint."""
        mock_engine = MockEngine('selenium', profiles={'a'})
        mgr = EngineManager([mock_engine])
        mgr.extract('target', 'followers')
        self._ckpt_instance.clear.assert_called_once()


class TestInstaExtractorBackwardCompat(unittest.TestCase):
    """Verifies InstaExtractor preserves backward compatibility."""

    def test_backward_compat_no_args(self):
        with patch('instat.extractor.InstaLogin') as MockLogin:
            MockLogin.return_value.driver = MagicMock()
            MockLogin.return_value.close_keywords = ['not now']
            from instat.extractor import InstaExtractor
            ext = InstaExtractor('user', 'pass', headless=True)
            self.assertIsNotNone(ext._engine)
            self.assertIsNotNone(ext._engine_manager)

    def test_engines_param_defaults_to_selenium(self):
        with patch('instat.extractor.InstaLogin') as MockLogin:
            MockLogin.return_value.driver = MagicMock()
            MockLogin.return_value.close_keywords = []
            from instat.extractor import InstaExtractor
            ext = InstaExtractor('u', 'p')
            # Default builds 1 engine (selenium)
            self.assertEqual(len(ext._engine_manager.engines), 1)
            self.assertEqual(ext._engine_manager.engines[0].name, 'selenium')

    def test_multi_account_defers_login(self):
        """With accounts provided, initial login is deferred to EngineManager."""
        with patch('instat.extractor.InstaLogin') as MockLogin:
            MockLogin.return_value.driver = MagicMock()
            MockLogin.return_value.close_keywords = []
            from instat.extractor import InstaExtractor
            ext = InstaExtractor(
                'u', 'p',
                accounts=[
                    {'username': 'a', 'password': 'pa'},
                    {'username': 'b', 'password': 'pb'},
                ]
            )
            # In multi-account mode, driver/insta_login are None (not logged in yet)
            self.assertIsNone(ext.driver)
            self.assertIsNone(ext.insta_login)
            # But engine and manager exist
            self.assertIsNotNone(ext._engine)
            self.assertIsNotNone(ext._engine_manager._session_pool)

    def test_unknown_engine_name_falls_back_to_selenium(self):
        """Unknown engine names are logged as warning but not fatal."""
        with patch('instat.extractor.InstaLogin') as MockLogin:
            MockLogin.return_value.driver = MagicMock()
            MockLogin.return_value.close_keywords = []
            from instat.extractor import InstaExtractor
            ext = InstaExtractor('u', 'p', engines=['selenium', 'nonexistent'])
            # Only selenium was built
            self.assertEqual(len(ext._engine_manager.engines), 1)


if __name__ == "__main__":
    unittest.main()
