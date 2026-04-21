"""ChallengeResolver / ChallengeResolverChain + EmailChallengeResolver."""
import unittest
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from selenium.common.exceptions import NoSuchElementException, TimeoutException

from instat.challenge_resolvers import (
    ChallengeResolver, ChallengeResolverChain, EmailChallengeResolver,
)


class FakeResolver(ChallengeResolver):
    def __init__(self, name, handle=False, resolve_result=True, raises=None):
        self.name = name
        self._handle = handle
        self._resolve = resolve_result
        self._raises = raises
        self.resolve_called = False

    def can_handle(self, driver):
        if self._raises and self._handle:
            raise self._raises
        return self._handle

    def resolve(self, driver):
        self.resolve_called = True
        return self._resolve


class TestChain(unittest.TestCase):

    def test_empty_chain_returns_false(self):
        chain = ChallengeResolverChain()
        self.assertFalse(chain.try_resolve(MagicMock()))

    def test_first_matching_wins(self):
        a = FakeResolver('a', handle=False)
        b = FakeResolver('b', handle=True, resolve_result=True)
        c = FakeResolver('c', handle=True, resolve_result=True)
        chain = ChallengeResolverChain([a, b, c])
        self.assertTrue(chain.try_resolve(MagicMock()))
        self.assertFalse(a.resolve_called)
        self.assertTrue(b.resolve_called)
        self.assertFalse(c.resolve_called)

    def test_matching_resolver_fails_chain_stops(self):
        """If can_handle=True but resolve returns False, chain returns
        False immediately (driver state already mutated)."""
        a = FakeResolver('a', handle=True, resolve_result=False)
        b = FakeResolver('b', handle=True, resolve_result=True)
        chain = ChallengeResolverChain([a, b])
        self.assertFalse(chain.try_resolve(MagicMock()))
        self.assertTrue(a.resolve_called)
        self.assertFalse(b.resolve_called)

    def test_resolver_raising_is_contained(self):
        a = FakeResolver('a', handle=True, raises=RuntimeError("boom"))
        b = FakeResolver('b', handle=True, resolve_result=True)
        chain = ChallengeResolverChain([a, b])
        self.assertFalse(chain.try_resolve(MagicMock()))

    def test_register_and_unregister(self):
        chain = ChallengeResolverChain()
        self.assertEqual(len(chain), 0)
        r = FakeResolver('x', handle=True, resolve_result=True)
        chain.register(r)
        self.assertEqual(len(chain), 1)
        chain.unregister_all()
        self.assertEqual(len(chain), 0)


class TestEmailChallengeResolverDetection(unittest.TestCase):

    _SENTINEL = object()

    def _mk(self, heading_sels=None, imap_config=_SENTINEL):
        if imap_config is self._SENTINEL:
            imap_config = {'host': 'x', 'user': 'u', 'password': 'p'}
        selectors = MagicMock()
        selectors.get_all.side_effect = lambda key: {
            'EMAIL_CHALLENGE_HEADING': heading_sels or ["h2[aria-label='Check your email']"],
            'EMAIL_CHALLENGE_GET_NEW_CODE': ["//span[@label='Get a new code']"],
            'EMAIL_CHALLENGE_INPUT': ["input[aria-label='Enter code']"],
            'EMAIL_CHALLENGE_CONTINUE': ["//div[@role='button']"],
        }.get(key, [])
        return EmailChallengeResolver(
            selector_loader=selectors,
            imap_config=imap_config,
        )

    def test_cannot_handle_without_imap(self):
        r = self._mk(imap_config=None)
        driver = MagicMock()
        self.assertFalse(r.can_handle(driver))

    def test_cannot_handle_without_heading(self):
        r = self._mk()
        driver = MagicMock()
        driver.find_element.side_effect = NoSuchElementException()
        self.assertFalse(r.can_handle(driver))

    def test_can_handle_when_heading_present(self):
        r = self._mk()
        driver = MagicMock()
        driver.find_element.return_value = MagicMock()
        self.assertTrue(r.can_handle(driver))


class TestEmailChallengeResolverResolve(unittest.TestCase):

    def _mk(self):
        selectors = MagicMock()
        selectors.get_all.side_effect = lambda key: {
            'EMAIL_CHALLENGE_HEADING': ["h2[aria-label='Check your email']"],
            'EMAIL_CHALLENGE_GET_NEW_CODE': ["//span[@label='Get a new code']"],
            'EMAIL_CHALLENGE_INPUT': ["input[aria-label='Enter code']"],
            'EMAIL_CHALLENGE_CONTINUE': ["//div[@role='button']"],
        }.get(key, [])
        r = EmailChallengeResolver(
            selector_loader=selectors,
            imap_config={'host': 'x', 'user': 'u', 'password': 'p'},
            clock=lambda: datetime(2026, 1, 1, tzinfo=timezone.utc),
        )
        return r

    @patch('instat.challenge_resolvers.human_delay', return_value=0)
    @patch('instat.challenge_resolvers.fetch_instagram_code',
           return_value='123456')
    def test_full_resolution_happy_path(self, _fetch, _hd):
        r = self._mk()
        driver = MagicMock()
        driver.find_element.return_value = MagicMock()
        # Patch _challenge_gone to succeed on first post-click check:
        # the production code calls it after each click strategy AND
        # finally in _wait_challenge_gone via WebDriverWait.until.
        with patch.object(r, '_challenge_gone', return_value=True), \
             patch('instat.challenge_resolvers.WebDriverWait') as wait:
            wait.return_value.until.return_value = True
            result = r.resolve(driver)
        self.assertTrue(result)

    @patch('instat.challenge_resolvers.human_delay', return_value=0)
    @patch('instat.challenge_resolvers.fetch_instagram_code',
           return_value=None)
    def test_imap_returns_nothing(self, _fetch, _hd):
        r = self._mk()
        driver = MagicMock()
        driver.find_element.return_value = MagicMock()
        self.assertFalse(r.resolve(driver))

    @patch('instat.challenge_resolvers.human_delay', return_value=0)
    @patch('instat.challenge_resolvers.fetch_instagram_code',
           return_value='123456')
    def test_input_not_found(self, _fetch, _hd):
        r = self._mk()
        driver = MagicMock()

        def fe(by, sel):
            if 'input' in sel.lower() or 'enter' in sel.lower():
                raise NoSuchElementException()
            return MagicMock()
        driver.find_element.side_effect = fe
        self.assertFalse(r.resolve(driver))


class TestInstaLoginIntegration(unittest.TestCase):

    def test_default_chain_contains_email_resolver(self):
        from instat.login import InstaLogin
        login = InstaLogin.__new__(InstaLogin)
        login.selectors = MagicMock()
        login._imap_config = {'host': 'x'}
        login.timeout = 10
        chain = login._default_challenge_chain()
        self.assertEqual(len(chain), 1)

    def test_custom_chain_accepted(self):
        from instat.login import InstaLogin
        custom = ChallengeResolverChain([
            FakeResolver('custom', handle=True, resolve_result=True),
        ])
        # Just verify the stored attribute — we don't run login() here
        login = InstaLogin.__new__(InstaLogin)
        login._challenge_chain = custom
        driver = MagicMock()
        self.assertTrue(login._try_handle_email_challenge(driver))


if __name__ == '__main__':
    unittest.main()
