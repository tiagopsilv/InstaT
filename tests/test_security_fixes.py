"""Tests for security fixes (sessions permissions, log redaction, input validation).

Covers:
- SessionCache.save sets file mode 0600 (POSIX) / silently skips on Windows
- InstaLogin._redact_url strips query + fragment
- InstaExtractor.get_* validates profile_id against IG username rules
- login.py _save_block_evidence also chmods artifacts
"""
import os
import stat
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from instat.session_cache import SessionCache, _restrict_permissions
from instat.login import InstaLogin


IS_POSIX = os.name == 'posix'


class TestSessionCacheFilePermissions(unittest.TestCase):

    def test_save_sets_0600_on_posix(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = SessionCache(cache_dir=tmp)
            cache.save('alice', [{'name': 'sessionid', 'value': 'x'}])
            path = os.path.join(tmp, 'alice.json')
            self.assertTrue(os.path.exists(path))
            if IS_POSIX:
                mode = stat.S_IMODE(os.stat(path).st_mode)
                self.assertEqual(
                    mode, 0o600,
                    f"Expected 0600 on cookie file, got {oct(mode)}"
                )

    def test_restrict_permissions_tolerates_chmod_failure(self):
        """On Windows / exotic FS, chmod may raise — must be swallowed."""
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "probe.json")
            with open(path, "w") as f:
                f.write("{}")
            with patch("instat.session_cache.os.chmod",
                       side_effect=OSError("nope")):
                _restrict_permissions(path)  # must not raise


class TestUrlRedactionInLogs(unittest.TestCase):
    """_redact_url must strip query (apc=…) and fragment before logging."""

    def test_strips_query_params(self):
        url = ("https://www.instagram.com/auth_platform/codeentry/"
               "?apc=SECRET_TOKEN_abc123")
        self.assertEqual(
            InstaLogin._redact_url(url),
            "https://www.instagram.com/auth_platform/codeentry/",
        )
        self.assertNotIn("SECRET_TOKEN", InstaLogin._redact_url(url))

    def test_strips_fragment(self):
        url = "https://www.instagram.com/challenge/#secret"
        self.assertEqual(
            InstaLogin._redact_url(url),
            "https://www.instagram.com/challenge/",
        )

    def test_keeps_path(self):
        url = "https://www.instagram.com/accounts/login/?next=/foo"
        red = InstaLogin._redact_url(url)
        self.assertIn("/accounts/login/", red)
        self.assertNotIn("?", red)

    def test_malformed_url_returns_original(self):
        # urlsplit accepts almost anything; worst case returns unchanged
        self.assertEqual(InstaLogin._redact_url(""), "")

    def test_check_account_blocked_logs_redacted(self):
        """_check_account_blocked emits URL without ?apc= in the log record."""
        from instat.block_detector import BlockDetector
        login = InstaLogin.__new__(InstaLogin)
        login._block_detector = BlockDetector()
        driver = MagicMock()
        driver.current_url = (
            "https://www.instagram.com/auth_platform/codeentry/"
            "?apc=SECRET_TOKEN_zzzz"
        )
        driver.title = "Facebook"
        driver.page_source = ""
        captured = []
        with patch("instat.login.logger") as mock_logger, \
             patch.object(login, "_save_block_evidence", return_value="ev.png"):
            mock_logger.error.side_effect = lambda msg: captured.append(msg)
            with self.assertRaises(Exception):  # AccountBlockedError
                login._check_account_blocked(driver)
        joined = "\n".join(captured)
        self.assertNotIn("SECRET_TOKEN", joined)
        self.assertIn("/auth_platform/codeentry/", joined)


class TestProfileIdValidation(unittest.TestCase):
    """InstaExtractor.get_* must reject malformed profile_ids early."""

    def _make_ext(self):
        from instat.extractor import InstaExtractor
        ext = InstaExtractor.__new__(InstaExtractor)
        ext._engine = MagicMock()
        ext._engine_manager = MagicMock()
        ext._exporter = None
        return ext

    def test_rejects_path_traversal(self):
        ext = self._make_ext()
        for bad in ("../admin", "foo/bar", "a\\b"):
            with self.assertRaises(ValueError, msg=bad):
                ext.get_followers(bad)

    def test_rejects_query_injection(self):
        ext = self._make_ext()
        for bad in ("user?q=1", "user#frag", "user&x=1"):
            with self.assertRaises(ValueError, msg=bad):
                ext.get_following(bad)

    def test_rejects_overlong(self):
        ext = self._make_ext()
        with self.assertRaises(ValueError):
            ext.get_followers("a" * 31)

    def test_rejects_empty(self):
        ext = self._make_ext()
        with self.assertRaises(ValueError):
            ext.get_followers("")

    def test_rejects_non_str(self):
        ext = self._make_ext()
        with self.assertRaises(ValueError):
            ext.get_followers(12345)  # type: ignore[arg-type]

    def test_accepts_legit(self):
        """Valid IG usernames must pass and reach the downstream call."""
        ext = self._make_ext()
        ext._engine_manager.extract.return_value = ["a"]
        # Should not raise
        ext.get_followers("tiagopsilv")
        ext.get_followers("user_123")
        ext.get_followers("a.b.c")
        ext.get_followers("X")
        ext.get_followers("a" * 30)


class TestCliCredentialResolution(unittest.TestCase):
    """CLI must warn on --password on argv and prompt via getpass otherwise."""

    def test_password_flag_emits_warning(self):
        from instat.__main__ import _resolve_credentials

        class Args:
            username = "u"
            password = "p"

        import io
        buf = io.StringIO()
        with patch("sys.stderr", buf), \
             patch.dict(os.environ, {}, clear=True):
            u, p = _resolve_credentials(Args())
        self.assertEqual((u, p), ("u", "p"))
        self.assertIn("--password", buf.getvalue())
        self.assertIn("visible", buf.getvalue())

    def test_env_var_used_when_no_flag(self):
        from instat.__main__ import _resolve_credentials

        class Args:
            username = None
            password = None

        with patch.dict(os.environ,
                        {"INSTAT_USERNAME": "envu", "INSTAT_PASSWORD": "envp"},
                        clear=True):
            u, p = _resolve_credentials(Args())
        self.assertEqual((u, p), ("envu", "envp"))

    def test_no_creds_no_tty_exits(self):
        from instat.__main__ import _resolve_credentials

        class Args:
            username = "u"
            password = None

        with patch.dict(os.environ, {}, clear=True), \
             patch("sys.stdin") as mock_stdin:
            mock_stdin.isatty.return_value = False
            with self.assertRaises(SystemExit) as cm:
                _resolve_credentials(Args())
            self.assertEqual(cm.exception.code, 1)

    def test_no_password_tty_prompts_getpass(self):
        from instat.__main__ import _resolve_credentials

        class Args:
            username = "u"
            password = None

        with patch.dict(os.environ, {}, clear=True), \
             patch("sys.stdin") as mock_stdin, \
             patch("instat.__main__.getpass.getpass",
                   return_value="typed-pass") as mock_prompt:
            mock_stdin.isatty.return_value = True
            u, p = _resolve_credentials(Args())
        mock_prompt.assert_called_once()
        self.assertEqual((u, p), ("u", "typed-pass"))


if __name__ == "__main__":
    unittest.main()
