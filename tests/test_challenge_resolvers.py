"""ChallengeResolver / ChallengeResolverChain + EmailChallengeResolver."""
import unittest
from datetime import datetime, timedelta, timezone
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


class TestEmailChallengeRetroactiveWindow(unittest.TestCase):
    """started_at must be retroactive to tolerate emails IG sent
    automatically when the challenge page first loaded (before we
    had a chance to click 'Get a new code')."""

    _FAKE_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=timezone.utc)

    def _mk(self):
        selectors = MagicMock()
        selectors.get_all.side_effect = lambda key: {
            'EMAIL_CHALLENGE_HEADING': ["h2"],
            'EMAIL_CHALLENGE_GET_NEW_CODE': ["//span[@label='Get a new code']"],
            'EMAIL_CHALLENGE_INPUT': ["input"],
            'EMAIL_CHALLENGE_CONTINUE': ["//div[@role='button']"],
        }.get(key, [])
        return EmailChallengeResolver(
            selector_loader=selectors,
            imap_config={'host': 'x', 'user': 'u', 'password': 'p'},
            clock=lambda: self._FAKE_NOW,
        )

    @patch('instat.challenge_resolvers.human_delay', return_value=0)
    def test_click_branch_window_is_ten_minutes(self, _hd):
        r = self._mk()
        driver = MagicMock()
        driver.find_element.return_value = MagicMock()  # button is there
        started_at = r._click_get_new_code_then_mark_time(driver)
        self.assertEqual(started_at, self._FAKE_NOW - timedelta(minutes=10))

    @patch('instat.challenge_resolvers.human_delay', return_value=0)
    def test_no_click_branch_window_is_ten_minutes(self, _hd):
        from selenium.common.exceptions import NoSuchElementException
        r = self._mk()
        driver = MagicMock()
        driver.find_element.side_effect = NoSuchElementException()
        started_at = r._click_get_new_code_then_mark_time(driver)
        self.assertEqual(started_at, self._FAKE_NOW - timedelta(minutes=10))


class TestInstaLoginIntegration(unittest.TestCase):

    def test_default_chain_contains_email_and_bloks_resolvers(self):
        # Default chain ships with both resolvers — Bloks first (URL-
        # specific, runs cheap can_handle), Email second.
        from instat.challenge_resolvers import (
            BloksCodeEntryResolver, EmailChallengeResolver,
        )
        from instat.login import InstaLogin
        login = InstaLogin.__new__(InstaLogin)
        login.selectors = MagicMock()
        login._imap_config = {'host': 'x'}
        login.timeout = 10
        chain = login._default_challenge_chain()
        self.assertEqual(len(chain), 2)
        self.assertIsInstance(chain._resolvers[0], BloksCodeEntryResolver)
        self.assertIsInstance(chain._resolvers[1], EmailChallengeResolver)

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


class TestChainIteratesMultiStep(unittest.TestCase):
    """The chain loops up to max_iterations to handle multi-step
    challenges (email → bloks codeentry). Pin the loop semantics so a
    future refactor doesn't silently regress to single-pass."""

    def test_loops_until_no_resolver_matches(self):
        # Stateful resolver: first pass it matches, second pass doesn't.
        class Stateful(FakeResolver):
            def __init__(self):
                super().__init__('s', handle=True, resolve_result=True)
                self.calls = 0
            def can_handle(self, driver):
                return self.calls == 0
            def resolve(self, driver):
                self.calls += 1
                return True

        s = Stateful()
        chain = ChallengeResolverChain([s])
        self.assertTrue(chain.try_resolve(MagicMock()))
        self.assertEqual(s.calls, 1)

    def test_multi_step_two_resolvers(self):
        # First-pass: A matches, advances state. Second-pass: B matches,
        # advances state. Third-pass: nothing.
        state = {'step': 0}

        class A(FakeResolver):
            def can_handle(self, driver): return state['step'] == 0
            def resolve(self, driver):
                state['step'] = 1
                return True

        class B(FakeResolver):
            def can_handle(self, driver): return state['step'] == 1
            def resolve(self, driver):
                state['step'] = 2
                return True

        a, b = A('a'), B('b')
        chain = ChallengeResolverChain([a, b])
        self.assertTrue(chain.try_resolve(MagicMock()))
        self.assertEqual(state['step'], 2)

    def test_max_iterations_caps_loop(self):
        # Resolver that always matches and always returns False.
        # Loop must terminate via max_iterations rather than spinning.
        class AlwaysMatches(FakeResolver):
            def __init__(self):
                super().__init__('x', handle=True, resolve_result=False)
                self.calls = 0
            def resolve(self, driver):
                self.calls += 1
                return False

        x = AlwaysMatches()
        chain = ChallengeResolverChain([x])
        result = chain.try_resolve(MagicMock(), max_iterations=3)
        self.assertFalse(result)
        self.assertEqual(x.calls, 3)


class TestEmailChallengeResolverExcludesAuthPlatform(unittest.TestCase):
    """EmailChallengeResolver must NOT match Bloks codeentry pages even
    though they share the 'Check your email' heading — guard against
    chain regression where both resolvers fight over the same page."""

    def test_skips_when_url_contains_auth_platform(self):
        from instat.config.selector_loader import SelectorLoader
        loader = MagicMock(spec=SelectorLoader)
        loader.get_all.return_value = ['h2[aria-label="Check your email"]']
        r = EmailChallengeResolver(
            selector_loader=loader,
            imap_config={'host': 'x', 'user': 'x', 'password': 'x'},
        )
        driver = MagicMock()
        driver.current_url = (
            'https://www.instagram.com/auth_platform/codeentry/'
        )
        # Even if heading is present, URL guard takes precedence.
        driver.find_element.return_value = MagicMock()
        self.assertFalse(r.can_handle(driver))

    def test_matches_when_url_is_normal_login(self):
        from instat.config.selector_loader import SelectorLoader
        loader = MagicMock(spec=SelectorLoader)
        loader.get_all.return_value = ['h2[aria-label="Check your email"]']
        r = EmailChallengeResolver(
            selector_loader=loader,
            imap_config={'host': 'x', 'user': 'x', 'password': 'x'},
        )
        driver = MagicMock()
        driver.current_url = (
            'https://www.instagram.com/accounts/login/'
        )
        driver.find_element.return_value = MagicMock()
        self.assertTrue(r.can_handle(driver))


class TestBloksCodeEntryResolver(unittest.TestCase):
    """BloksCodeEntryResolver — Meta auth_platform/codeentry flow."""

    _SENTINEL = object()

    def _resolver(self, imap_config=_SENTINEL):
        from instat.challenge_resolvers import BloksCodeEntryResolver
        if imap_config is self._SENTINEL:
            imap_config = {'host': 'x', 'user': 'x', 'password': 'x'}
        return BloksCodeEntryResolver(imap_config=imap_config)

    def test_can_handle_matches_codeentry_url(self):
        r = self._resolver()
        driver = MagicMock()
        driver.current_url = (
            'https://www.instagram.com/auth_platform/codeentry/'
        )
        self.assertTrue(r.can_handle(driver))

    def test_can_handle_rejects_non_codeentry_url(self):
        r = self._resolver()
        driver = MagicMock()
        driver.current_url = 'https://www.instagram.com/accounts/login/'
        self.assertFalse(r.can_handle(driver))

    def test_can_handle_requires_imap_config(self):
        r = self._resolver(imap_config=None)
        driver = MagicMock()
        driver.current_url = (
            'https://www.instagram.com/auth_platform/codeentry/'
        )
        self.assertFalse(r.can_handle(driver))

    def test_resolve_no_imap_code_returns_false(self):
        r = self._resolver()
        driver = MagicMock()
        driver.current_url = (
            'https://www.instagram.com/auth_platform/codeentry/'
        )
        with patch(
            'instat.challenge_resolvers.fetch_instagram_code',
            return_value=None,
        ):
            self.assertFalse(r.resolve(driver))

    def test_resolve_input_not_found_returns_false(self):
        r = self._resolver()
        driver = MagicMock()
        driver.current_url = (
            'https://www.instagram.com/auth_platform/codeentry/'
        )
        driver.find_element.side_effect = NoSuchElementException()
        # execute_script: first call is cooldown parse → 0 (no wait),
        # second is resend click → True (faked). After that the input
        # find fails → return False.
        driver.execute_script.side_effect = [0, True]
        with patch(
            'instat.challenge_resolvers.fetch_instagram_code',
            return_value='123456',
        ), patch(
            'instat.challenge_resolvers.human_delay'
        ):
            self.assertFalse(r.resolve(driver))

    def test_parse_cooldown_seconds_returns_value(self):
        r = self._resolver()
        driver = MagicMock()
        driver.execute_script.return_value = 45
        self.assertEqual(r._parse_cooldown_seconds(driver), 45)

    def test_parse_cooldown_seconds_zero_on_no_match(self):
        r = self._resolver()
        driver = MagicMock()
        driver.execute_script.return_value = 0
        self.assertEqual(r._parse_cooldown_seconds(driver), 0)

    def test_parse_cooldown_seconds_zero_on_exception(self):
        r = self._resolver()
        driver = MagicMock()
        driver.execute_script.side_effect = Exception("script error")
        self.assertEqual(r._parse_cooldown_seconds(driver), 0)

    def test_click_resend_returns_bool(self):
        r = self._resolver()
        driver = MagicMock()
        driver.execute_script.return_value = True
        self.assertTrue(r._click_resend(driver))
        driver.execute_script.return_value = False
        self.assertFalse(r._click_resend(driver))

    def test_request_fresh_code_waits_cooldown_then_clicks(self):
        r = self._resolver()
        driver = MagicMock()
        # First call: cooldown=10s. Second call: resend click=True.
        driver.execute_script.side_effect = [10, True]
        with patch('instat.challenge_resolvers.time.sleep') as mock_sleep, \
             patch('instat.challenge_resolvers.human_delay'):
            result = r._request_fresh_code(driver)
        self.assertTrue(result)
        # Slept for cooldown + 3s slack.
        mock_sleep.assert_called_once_with(13)

    def test_request_fresh_code_no_cooldown_skips_sleep(self):
        r = self._resolver()
        driver = MagicMock()
        # First call: cooldown=0. Second call: resend click=True.
        driver.execute_script.side_effect = [0, True]
        with patch('instat.challenge_resolvers.time.sleep') as mock_sleep, \
             patch('instat.challenge_resolvers.human_delay') as mock_delay:
            result = r._request_fresh_code(driver)
        self.assertTrue(result)
        mock_sleep.assert_not_called()
        # human_delay still called for DOM stabilisation pre-click.
        mock_delay.assert_called()


if __name__ == '__main__':
    unittest.main()
