"""Testes unitários para instat.email_code (IMAP fetcher mockado)."""
import unittest
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from unittest.mock import MagicMock


def _make_msg(from_addr: str, subject: str, body: str,
              date: datetime = None, html: str = None) -> bytes:
    msg = EmailMessage()
    msg['From'] = from_addr
    msg['Subject'] = subject
    msg['Date'] = (date or datetime.now(timezone.utc)).strftime('%a, %d %b %Y %H:%M:%S %z')
    if html:
        msg.set_content(body)
        msg.add_alternative(html, subtype='html')
    else:
        msg.set_content(body)
    return msg.as_bytes()


class _FakeImapConn:
    def __init__(self, messages):
        # messages: list of bytes
        self._messages = list(messages)
        self.logged_in = False

    def login(self, user, pw):
        self.logged_in = True
        return ('OK', [b''])

    def select(self, mailbox):
        return ('OK', [b'1'])

    def search(self, charset, *args):
        ids = b' '.join(str(i + 1).encode() for i in range(len(self._messages)))
        return ('OK', [ids])

    def fetch(self, mid, parts):
        idx = int(mid) - 1
        if 0 <= idx < len(self._messages):
            return ('OK', [(b'1', self._messages[idx])])
        return ('NO', [])

    def logout(self):
        return ('OK', [b''])


class _FakeImaplib:
    def __init__(self, conn):
        self._conn = conn

    def IMAP4_SSL(self, host, port):
        return self._conn


class _FakeTime:
    def __init__(self, start=1_000_000.0):
        self.now = start

    def time(self):
        return self.now

    def sleep(self, s):
        self.now += s


class TestExtractCode(unittest.TestCase):
    def test_prefers_subject(self):
        from instat.email_code import _extract_code
        self.assertEqual(_extract_code('Your code is 123456', 'ignore 999999 please'), '123456')

    def test_body_fallback(self):
        from instat.email_code import _extract_code
        self.assertEqual(_extract_code('', 'Enter 654321 to verify'), '654321')

    def test_none_if_no_6digit(self):
        from instat.email_code import _extract_code
        self.assertIsNone(_extract_code('abc', 'no numbers here'))

    def test_ignores_longer_digit_runs(self):
        from instat.email_code import _extract_code
        self.assertIsNone(_extract_code('', 'order 1234567890'))


class TestIsFromInstagram(unittest.TestCase):
    def test_matches_canonical(self):
        from instat.email_code import _is_from_instagram
        self.assertTrue(_is_from_instagram('Instagram <security@mail.instagram.com>'))
        self.assertTrue(_is_from_instagram('no-reply@mail.instagram.com'))
        self.assertTrue(_is_from_instagram('notification@facebookmail.com'))

    def test_rejects_spoof(self):
        from instat.email_code import _is_from_instagram
        self.assertFalse(_is_from_instagram('spammer@phishing.ru'))


class TestFetchInstagramCode(unittest.TestCase):
    def _cfg(self, **kw):
        from instat.email_code import ImapConfig
        defaults = dict(host='imap.test', user='u', password='p',
                        timeout=10, poll_interval=1.0)
        defaults.update(kw)
        return ImapConfig(**defaults)

    def test_returns_code_from_matching_email(self):
        from instat.email_code import fetch_instagram_code
        msgs = [_make_msg(
            'Instagram <security@mail.instagram.com>',
            'Your Instagram verification code',
            'Hi, your code is 987654. Do not share.',
        )]
        fake_time = _FakeTime()
        code = fetch_instagram_code(
            self._cfg(), _imaplib=_FakeImaplib(_FakeImapConn(msgs)), _time=fake_time,
        )
        self.assertEqual(code, '987654')

    def test_ignores_old_emails(self):
        from instat.email_code import fetch_instagram_code
        old = datetime.now(timezone.utc) - timedelta(hours=3)
        msgs = [_make_msg(
            'security@mail.instagram.com',
            'Instagram code',
            'Code: 111111',
            date=old,
        )]
        fake_time = _FakeTime()
        code = fetch_instagram_code(
            self._cfg(timeout=2), _imaplib=_FakeImaplib(_FakeImapConn(msgs)),
            _time=fake_time,
            started_at=datetime.now(timezone.utc) - timedelta(minutes=5),
        )
        self.assertIsNone(code)

    def test_ignores_non_instagram_sender(self):
        from instat.email_code import fetch_instagram_code
        msgs = [_make_msg(
            'Random <random@example.com>',
            'Your code is 222222',
            'body',
        )]
        fake_time = _FakeTime()
        code = fetch_instagram_code(
            self._cfg(timeout=2), _imaplib=_FakeImaplib(_FakeImapConn(msgs)), _time=fake_time,
        )
        self.assertIsNone(code)

    def test_polls_until_found(self):
        from instat.email_code import fetch_instagram_code
        # Retorna vazio nas 2 primeiras, depois tem msg
        good_msg = _make_msg('security@mail.instagram.com', 'code', 'code: 333333')

        class ProgressiveConn(_FakeImapConn):
            def __init__(self):
                super().__init__([])
                self._calls = 0

            def search(self, charset, *args):
                self._calls += 1
                if self._calls <= 2:
                    return ('OK', [b''])
                self._messages = [good_msg]
                return ('OK', [b'1'])

        fake_time = _FakeTime()
        code = fetch_instagram_code(
            self._cfg(timeout=30, poll_interval=1),
            _imaplib=_FakeImaplib(ProgressiveConn()),
            _time=fake_time,
        )
        self.assertEqual(code, '333333')

    def test_times_out_gracefully(self):
        from instat.email_code import fetch_instagram_code
        fake_time = _FakeTime()
        code = fetch_instagram_code(
            self._cfg(timeout=3),
            _imaplib=_FakeImaplib(_FakeImapConn([])),
            _time=fake_time,
        )
        self.assertIsNone(code)

    def test_handles_imap_connection_error(self):
        from instat.email_code import fetch_instagram_code

        class BadImaplib:
            def IMAP4_SSL(self, host, port):
                raise OSError('network down')

        fake_time = _FakeTime()
        code = fetch_instagram_code(
            self._cfg(timeout=3),
            _imaplib=BadImaplib(), _time=fake_time,
        )
        self.assertIsNone(code)


class TestImapConfigFromDict(unittest.TestCase):
    def test_from_dict(self):
        from instat.email_code import ImapConfig
        cfg = ImapConfig.from_dict({
            'host': 'imap.gmail.com', 'user': 'u@x', 'password': 'pw',
        })
        self.assertEqual(cfg.host, 'imap.gmail.com')
        self.assertEqual(cfg.port, 993)

    def test_from_dict_passthrough(self):
        from instat.email_code import ImapConfig
        existing = ImapConfig(host='h', user='u', password='p')
        self.assertIs(ImapConfig.from_dict(existing), existing)

    def test_from_dict_none(self):
        from instat.email_code import ImapConfig
        self.assertIsNone(ImapConfig.from_dict(None))


class TestLoginChallengeIntegration(unittest.TestCase):
    """_try_handle_email_challenge usa imap_config, seletores e driver."""

    def _make_login(self, imap_config=None):
        from instat.challenge_resolvers import (
            ChallengeResolverChain,
            EmailChallengeResolver,
        )
        from instat.login import InstaLogin
        obj = InstaLogin.__new__(InstaLogin)
        obj._imap_config = imap_config
        obj.timeout = 1
        obj.selectors = MagicMock()
        obj.selectors.get_all.side_effect = lambda key: {
            'EMAIL_CHALLENGE_HEADING': ["h2[aria-label='Check your email']"],
            'EMAIL_CHALLENGE_GET_NEW_CODE': ["//span[@label='Get a new code']"],
            'EMAIL_CHALLENGE_INPUT': ["input[aria-label='Enter code']"],
            'EMAIL_CHALLENGE_CONTINUE': ["//div[@role='button' and @aria-label='Continue']"],
        }[key]
        # Wire up the challenge chain the same way __init__ would.
        obj._challenge_chain = ChallengeResolverChain([
            EmailChallengeResolver(
                selector_loader=obj.selectors,
                imap_config=imap_config,
                timeout=obj.timeout,
            ),
        ])
        return obj

    def test_no_imap_config_returns_false(self):
        obj = self._make_login(imap_config=None)
        self.assertFalse(obj._try_handle_email_challenge(MagicMock()))

    def test_returns_false_if_no_challenge_heading(self):
        from selenium.common.exceptions import NoSuchElementException
        obj = self._make_login(imap_config={'host': 'h', 'user': 'u', 'password': 'p'})
        driver = MagicMock()
        driver.find_element.side_effect = NoSuchElementException()
        self.assertFalse(obj._try_handle_email_challenge(driver))


if __name__ == '__main__':
    unittest.main()
