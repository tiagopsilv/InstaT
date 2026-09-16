"""Tests for the 5 InstaT gaps closed in the 2026-05-14 session:

  1. Bio populated in Profile (get_profile)
  2. should_stop callback propagation
  3. with_metadata=True returning ProfileSummary list
  4. use_stdlib_logging flag
  5. ExtractionResult plumbing (covered in test_extraction_result.py)

Each test pins the contract a downstream consumer can rely on."""
import logging
import unittest
from unittest.mock import MagicMock, patch


class TestBioInGetProfile(unittest.TestCase):

    def test_bio_extracted_via_js_probe(self):
        from instat.extractor import InstaExtractor

        with patch('instat.extractor.InstaLogin') as MockLogin:
            mock_inst = MockLogin.return_value
            mock_inst.driver = MagicMock()
            mock_inst.close_keywords = []
            ext = InstaExtractor('u', 'p', headless=True)

        # Mock the engine's driver — get_profile reads via execute_script
        # for the bio probe.
        driver = MagicMock()
        driver.find_element.return_value.get_attribute.return_value = ''
        # The bio probe: execute_script returns the bio text; verified
        # checks return False; private check returns False.
        driver.execute_script.side_effect = [
            'Corredora de São Paulo. Treinando para São Silvestre 2026.',  # bio
            False,                                                          # is_verified
            False,                                                          # is_private
        ]
        ext._engine._driver = driver

        try:
            with patch('instat.profile.parse_profile_from_meta', return_value={}):
                profile = ext.get_profile('runner_sp')
            self.assertIsNotNone(profile.bio)
            self.assertIn('São Paulo', profile.bio)
        finally:
            ext.quit()

    def test_bio_none_when_js_returns_empty_string(self):
        # JSON inline probe can return "" when biography field exists
        # but is empty. Treat as None for consistency with the
        # no-bio-found case.
        from instat.extractor import InstaExtractor

        with patch('instat.extractor.InstaLogin') as MockLogin:
            mock_inst = MockLogin.return_value
            mock_inst.driver = MagicMock()
            mock_inst.close_keywords = []
            ext = InstaExtractor('u', 'p', headless=True)

        driver = MagicMock()
        driver.find_element.return_value.get_attribute.return_value = ''
        driver.execute_script.side_effect = ['   ', False, False]
        ext._engine._driver = driver

        try:
            with patch('instat.profile.parse_profile_from_meta', return_value={}):
                profile = ext.get_profile('empty_bio_user')
            self.assertIsNone(profile.bio)
        finally:
            ext.quit()

    def test_bio_none_when_js_returns_null(self):
        from instat.extractor import InstaExtractor

        with patch('instat.extractor.InstaLogin') as MockLogin:
            mock_inst = MockLogin.return_value
            mock_inst.driver = MagicMock()
            mock_inst.close_keywords = []
            ext = InstaExtractor('u', 'p', headless=True)

        driver = MagicMock()
        driver.find_element.return_value.get_attribute.return_value = ''
        driver.execute_script.side_effect = [None, False, False]
        ext._engine._driver = driver

        try:
            with patch('instat.profile.parse_profile_from_meta', return_value={}):
                profile = ext.get_profile('private_user')
            self.assertIsNone(profile.bio)
        finally:
            ext.quit()


class TestShouldStopPropagation(unittest.TestCase):

    def test_extractor_passes_should_stop_to_engine_manager(self):
        from instat.extractor import InstaExtractor

        with patch('instat.extractor.InstaLogin') as MockLogin:
            mock_inst = MockLogin.return_value
            mock_inst.driver = MagicMock()
            mock_inst.close_keywords = []
            ext = InstaExtractor('u', 'p', headless=True)

        captured = {}

        def capture(*args, **kwargs):
            captured.update(kwargs)
            return ['x']

        def stop_fn():
            return True

        with patch.object(ext._engine_manager, 'extract', side_effect=capture):
            ext.get_followers('target', should_stop=stop_fn)

        self.assertIn('should_stop', captured)
        self.assertIs(captured['should_stop'], stop_fn)
        ext.quit()

    def test_no_should_stop_keeps_kwarg_absent(self):
        from instat.extractor import InstaExtractor

        with patch('instat.extractor.InstaLogin') as MockLogin:
            mock_inst = MockLogin.return_value
            mock_inst.driver = MagicMock()
            mock_inst.close_keywords = []
            ext = InstaExtractor('u', 'p', headless=True)

        captured = {}

        def capture(*args, **kwargs):
            captured.update(kwargs)
            return []

        with patch.object(ext._engine_manager, 'extract', side_effect=capture):
            ext.get_followers('target')

        # Default omits should_stop entirely (no None pollution).
        self.assertNotIn('should_stop', captured)
        ext.quit()


class TestWithMetadataMode(unittest.TestCase):

    def test_extractor_passes_with_metadata(self):
        from instat.extractor import InstaExtractor

        with patch('instat.extractor.InstaLogin') as MockLogin:
            mock_inst = MockLogin.return_value
            mock_inst.driver = MagicMock()
            mock_inst.close_keywords = []
            ext = InstaExtractor('u', 'p', headless=True)

        captured = {}

        def capture(*args, **kwargs):
            captured.update(kwargs)
            return []

        with patch.object(ext._engine_manager, 'extract', side_effect=capture):
            ext.get_followers('target', with_metadata=True)

        self.assertTrue(captured.get('with_metadata'))
        ext.quit()

    def test_selenium_engine_with_metadata_returns_username_only_summaries(self):
        from instat.engines.selenium_engine import SeleniumEngine
        from instat.profile_summary import ProfileSummary

        eng = SeleniumEngine()
        # Skip _extract_list internals — pretend it returned 3 usernames.
        with patch.object(eng, '_extract_list', return_value=['a', 'b', 'c']):
            out = eng.extract('p', 'followers', with_metadata=True)

        self.assertEqual(len(out), 3)
        self.assertTrue(all(isinstance(o, ProfileSummary) for o in out))
        self.assertEqual([o.username for o in out], ['a', 'b', 'c'])
        # Selenium can't fill the rest — confirm they're all None.
        self.assertTrue(all(o.user_id is None for o in out))


class TestHttpxWithMetadata(unittest.TestCase):

    def test_httpx_extract_returns_profile_summaries(self):
        from instat.engines.httpx_engine import HttpxEngine
        from instat.profile_summary import ProfileSummary

        e = HttpxEngine()
        e._client = MagicMock()
        e._resolve_user_id = MagicMock(return_value='123')

        api_response = MagicMock()
        api_response.status_code = 200
        api_response.json.return_value = {
            'users': [
                {
                    'pk': 11, 'username': 'a', 'full_name': 'Alice',
                    'is_verified': True, 'is_private': False,
                    'is_business': False,
                    'profile_pic_url': 'https://x/a.jpg',
                },
                {
                    'pk': 22, 'username': 'b', 'full_name': 'Bob',
                    'is_verified': False, 'is_private': True,
                    'is_business': False,
                    'profile_pic_url': 'https://x/b.jpg',
                },
            ],
            'next_max_id': None,
        }
        e._client.get.return_value = api_response

        out = e.extract('user', 'followers', with_metadata=True)
        self.assertEqual(len(out), 2)
        self.assertTrue(all(isinstance(o, ProfileSummary) for o in out))
        self.assertEqual(out[0].user_id, '11')
        self.assertEqual(out[0].username, 'a')
        self.assertTrue(out[0].is_verified)
        self.assertEqual(out[1].user_id, '22')
        self.assertTrue(out[1].is_private)


class TestUseStdlibLogging(unittest.TestCase):

    def test_configure_logging_use_stdlib_routes_to_stdlib(self):
        from instat.logging_config import configure_logging
        from loguru import logger

        captured = []

        class CaptureHandler(logging.Handler):
            def emit(self, record):
                captured.append(record.getMessage())

        # Loguru records carry the calling module name; we attach the
        # capture handler to the stdlib ROOT logger to catch them
        # regardless of caller namespace. This mirrors how Cloud Logging
        # is normally wired (root handler captures everything).
        root = logging.getLogger()
        prior_handlers = root.handlers[:]
        prior_level = root.level
        root.handlers = [CaptureHandler()]
        root.setLevel(logging.DEBUG)
        try:
            configure_logging(use_stdlib=True)
            logger.info("hello-from-loguru")
            self.assertTrue(any('hello-from-loguru' in m for m in captured))
        finally:
            # Reset both loguru and stdlib config to avoid bleed into
            # later tests.
            configure_logging(use_stdlib=False, file_log=False)
            root.handlers = prior_handlers
            root.setLevel(prior_level)


if __name__ == '__main__':
    unittest.main()
