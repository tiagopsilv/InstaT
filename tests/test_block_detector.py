"""BlockDetector: pure detection logic, zero driver state mutation."""
import unittest
from unittest.mock import MagicMock

from instat.block_detector import BlockDetector, BlockInfo


def _mk_driver(url="", title="", page_source=""):
    d = MagicMock()
    d.current_url = url
    d.title = title
    d.page_source = page_source
    return d


class TestURLDetection(unittest.TestCase):

    def test_clean_url_returns_none(self):
        detector = BlockDetector()
        d = _mk_driver(url="https://www.instagram.com/")
        self.assertIsNone(detector.check(d))

    def test_challenge_url(self):
        detector = BlockDetector()
        d = _mk_driver(url="https://www.instagram.com/challenge/foo")
        info = detector.check(d)
        self.assertIsNotNone(info)
        self.assertEqual(info.kind, 'url')
        self.assertEqual(info.indicator, 'challenge')
        self.assertIn('Desafio', info.reason)

    def test_checkpoint_url(self):
        detector = BlockDetector()
        d = _mk_driver(url="https://www.instagram.com/checkpoint/x")
        info = detector.check(d)
        self.assertEqual(info.indicator, 'checkpoint')

    def test_auth_platform_codeentry(self):
        detector = BlockDetector()
        d = _mk_driver(
            url="https://www.instagram.com/auth_platform/codeentry/?apc=XYZ"
        )
        info = detector.check(d)
        # First match wins — 'auth_platform' comes before 'codeentry' in
        # the dict, but either is fine; just assert detection.
        self.assertIsNotNone(info)
        self.assertIn(info.indicator, ('auth_platform', 'codeentry'))

    def test_two_factor(self):
        detector = BlockDetector()
        d = _mk_driver(url="https://www.instagram.com/two_factor/login")
        info = detector.check(d)
        self.assertEqual(info.indicator, 'two_factor')

    def test_case_insensitive(self):
        detector = BlockDetector()
        d = _mk_driver(url="https://www.instagram.com/CHALLENGE/x")
        self.assertIsNotNone(detector.check(d))


class TestHTMLDetection(unittest.TestCase):

    def test_meta_verified_detected(self):
        detector = BlockDetector()
        d = _mk_driver(
            url="https://www.instagram.com/foo",
            page_source="some text Meta Verified more text",
        )
        info = detector.check(d)
        self.assertIsNotNone(info)
        self.assertEqual(info.kind, 'html')
        self.assertIn('Meta Verified', info.reason)

    def test_meta_verified_portuguese(self):
        detector = BlockDetector()
        d = _mk_driver(
            url="https://www.instagram.com/foo",
            page_source=(
                "pagina inicial o Meta Verified está disponível para "
                "o Facebook e o Instagram etc"
            ),
        )
        info = detector.check(d)
        self.assertIsNotNone(info)
        self.assertEqual(info.kind, 'html')

    def test_html_only_on_non_challenge_url(self):
        """URL takes precedence — if URL matches, don't even check HTML."""
        detector = BlockDetector()
        d = _mk_driver(
            url="https://www.instagram.com/challenge/foo",
            page_source="meta verified",
        )
        info = detector.check(d)
        self.assertEqual(info.kind, 'url')  # URL wins


class TestLoginLoopDetection(unittest.TestCase):

    def test_stuck_on_login_page(self):
        detector = BlockDetector()
        d = _mk_driver(url="https://www.instagram.com/accounts/login/")
        info = detector.check(d)
        self.assertIsNotNone(info)
        self.assertEqual(info.kind, 'login_loop')

    def test_login_loop_doesnt_trigger_if_html_signature_matches(self):
        """HTML check runs before login_loop → should prefer html."""
        detector = BlockDetector()
        d = _mk_driver(
            url="https://www.instagram.com/accounts/login/",
            page_source="meta verified",
        )
        info = detector.check(d)
        self.assertEqual(info.kind, 'html')


class TestRobustness(unittest.TestCase):

    def test_empty_url_returns_none(self):
        detector = BlockDetector()
        d = _mk_driver(url="")
        self.assertIsNone(detector.check(d))

    def test_driver_raising_is_safe(self):
        """A broken driver shouldn't crash the detector."""
        detector = BlockDetector()
        d = MagicMock()
        type(d).current_url = property(
            lambda self: (_ for _ in ()).throw(RuntimeError("dead"))
        )
        self.assertIsNone(detector.check(d))

    def test_none_page_source(self):
        detector = BlockDetector()
        d = _mk_driver(url="https://www.instagram.com/foo")
        d.page_source = None
        self.assertIsNone(detector.check(d))


class TestExtension(unittest.TestCase):
    """Subclasses can add rules via extra_checks() without edits here."""

    def test_extra_checks_invoked(self):

        class CookieAwareDetector(BlockDetector):
            def extra_checks(self, driver):
                cookie = driver.get_cookie('banned_flag')
                if cookie:
                    return BlockInfo(
                        reason='Custom cookie banned',
                        action='Reset cookies',
                        kind='cookie',
                        indicator='banned_flag',
                        url=driver.current_url,
                    )
                return None

        d = _mk_driver(url="https://www.instagram.com/feed")
        d.get_cookie.return_value = {'name': 'banned_flag', 'value': '1'}
        info = CookieAwareDetector().check(d)
        self.assertEqual(info.kind, 'cookie')

    def test_extra_checks_runs_after_html(self):
        """HTML signatures have priority over custom extras."""

        class EchoDetector(BlockDetector):
            def extra_checks(self, driver):
                return BlockInfo(
                    reason='e', action='e', kind='extra',
                    indicator='e', url=driver.current_url,
                )

        d = _mk_driver(
            url="https://www.instagram.com/foo",
            page_source="meta verified",
        )
        self.assertEqual(EchoDetector().check(d).kind, 'html')


class TestInstaLoginBackCompat(unittest.TestCase):
    """InstaLogin.BLOCK_INDICATORS still resolves — external users
    sometimes reached into it."""

    def test_block_indicators_alias(self):
        from instat.login import InstaLogin
        self.assertIs(InstaLogin.BLOCK_INDICATORS,
                      BlockDetector.URL_INDICATORS)


if __name__ == '__main__':
    unittest.main()
