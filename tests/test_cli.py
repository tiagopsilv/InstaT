import os
import tempfile
import unittest
from io import StringIO
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestCliParser(unittest.TestCase):

    def test_help_exits_zero(self):
        from instat.__main__ import _build_parser
        parser = _build_parser()
        with self.assertRaises(SystemExit) as cm:
            parser.parse_args(['--help'])
        self.assertEqual(cm.exception.code, 0)

    def test_missing_subcommand_errors(self):
        from instat.__main__ import _build_parser
        parser = _build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([])

    def test_extract_requires_profile(self):
        from instat.__main__ import _build_parser
        parser = _build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args(['extract'])

    def test_extract_parses_basic_args(self):
        from instat.__main__ import _build_parser
        parser = _build_parser()
        args = parser.parse_args([
            'extract', '--profile', 'target', '--type', 'followers',
            '--output', 'out.csv',
        ])
        self.assertEqual(args.command, 'extract')
        self.assertEqual(args.profile, 'target')
        self.assertEqual(args.type, 'followers')
        self.assertEqual(args.output, 'out.csv')

    def test_extract_multiple_engines(self):
        from instat.__main__ import _build_parser
        parser = _build_parser()
        args = parser.parse_args([
            'extract', '--profile', 't', '--type', 'followers',
            '--engine', 'selenium', '--engine', 'playwright',
        ])
        self.assertEqual(args.engine, ['selenium', 'playwright'])

    def test_invalid_type_rejected(self):
        from instat.__main__ import _build_parser
        parser = _build_parser()
        with self.assertRaises(SystemExit):
            parser.parse_args([
                'extract', '--profile', 't', '--type', 'invalid',
            ])

    def test_no_headless_flag(self):
        from instat.__main__ import _build_parser
        parser = _build_parser()
        args = parser.parse_args([
            'extract', '--profile', 't', '--type', 'followers', '--no-headless',
        ])
        self.assertFalse(args.headless)


class TestExporterFromOutput(unittest.TestCase):

    def test_csv_from_extension(self):
        from instat.__main__ import _exporter_from_output
        from instat.exporters import CSVExporter
        self.assertIsInstance(_exporter_from_output('out.csv', None), CSVExporter)

    def test_json_from_extension(self):
        from instat.__main__ import _exporter_from_output
        from instat.exporters import JSONExporter
        self.assertIsInstance(_exporter_from_output('out.json', None), JSONExporter)

    def test_sqlite_from_extension(self):
        from instat.__main__ import _exporter_from_output
        from instat.exporters import SQLiteExporter
        self.assertIsInstance(_exporter_from_output('out.db', None), SQLiteExporter)
        self.assertIsInstance(_exporter_from_output('x.sqlite', None), SQLiteExporter)
        self.assertIsInstance(_exporter_from_output('x.sqlite3', None), SQLiteExporter)

    def test_none_output_returns_none(self):
        from instat.__main__ import _exporter_from_output
        self.assertIsNone(_exporter_from_output(None, None))

    def test_unknown_extension_exits_1(self):
        from instat.__main__ import _exporter_from_output
        with self.assertRaises(SystemExit) as cm:
            _exporter_from_output('out.xyz', None)
        self.assertEqual(cm.exception.code, 1)

    def test_format_override(self):
        from instat.__main__ import _exporter_from_output
        from instat.exporters import JSONExporter
        exp = _exporter_from_output('out.csv', 'json')
        self.assertIsInstance(exp, JSONExporter)


class TestCredentials(unittest.TestCase):

    def test_flags_override_env(self):
        from instat.__main__ import _resolve_credentials
        args = MagicMock(username='flag_user', password='flag_pw')
        with patch.dict(os.environ, {'INSTAT_USERNAME': 'env_u', 'INSTAT_PASSWORD': 'env_p'}):
            u, p = _resolve_credentials(args)
        self.assertEqual(u, 'flag_user')
        self.assertEqual(p, 'flag_pw')

    def test_env_fallback(self):
        from instat.__main__ import _resolve_credentials
        args = MagicMock(username=None, password=None)
        with patch.dict(os.environ, {'INSTAT_USERNAME': 'env_u', 'INSTAT_PASSWORD': 'env_p'}):
            u, p = _resolve_credentials(args)
        self.assertEqual(u, 'env_u')
        self.assertEqual(p, 'env_p')

    def test_missing_credentials_exits_1(self):
        from instat.__main__ import _resolve_credentials
        args = MagicMock(username=None, password=None)
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(SystemExit) as cm:
                _resolve_credentials(args)
            self.assertEqual(cm.exception.code, 1)


class TestProxyLoading(unittest.TestCase):

    def test_load_proxies_from_file(self):
        from instat.__main__ import _load_proxies
        with tempfile.TemporaryDirectory() as td:
            f = Path(td) / 'proxies.txt'
            f.write_text('http://p1:8080\nhttp://p2:8080\n\n  http://p3:8080  \n')
            proxies = _load_proxies(str(f))
        self.assertEqual(proxies, ['http://p1:8080', 'http://p2:8080', 'http://p3:8080'])

    def test_load_proxies_none_returns_none(self):
        from instat.__main__ import _load_proxies
        self.assertIsNone(_load_proxies(None))

    def test_load_proxies_missing_file_exits_1(self):
        from instat.__main__ import _load_proxies
        with self.assertRaises(SystemExit) as cm:
            _load_proxies('/nonexistent/path/proxies.txt')
        self.assertEqual(cm.exception.code, 1)


class TestMainExecution(unittest.TestCase):
    """End-to-end de main() com InstaExtractor mockado."""

    @patch('instat.__main__.InstaExtractor')
    def test_extract_command_success(self, MockExtractor):
        instance = MockExtractor.return_value
        instance.get_followers.return_value = ['alice', 'bob']
        from instat.__main__ import main
        with patch.dict(os.environ, {'INSTAT_USERNAME': 'u', 'INSTAT_PASSWORD': 'p'}):
            with patch('sys.stdout', new=StringIO()) as fake_out:
                rc = main(['extract', '--profile', 'target', '--type', 'followers'])
        self.assertEqual(rc, 0)
        output = fake_out.getvalue()
        self.assertIn('alice', output)
        self.assertIn('bob', output)

    @patch('instat.__main__.InstaExtractor')
    def test_count_command_success(self, MockExtractor):
        instance = MockExtractor.return_value
        instance.get_total_count.return_value = 404
        from instat.__main__ import main
        with patch.dict(os.environ, {'INSTAT_USERNAME': 'u', 'INSTAT_PASSWORD': 'p'}):
            with patch('sys.stdout', new=StringIO()) as fake_out:
                rc = main(['count', '--profile', 'target', '--type', 'followers'])
        self.assertEqual(rc, 0)
        self.assertIn('404', fake_out.getvalue())

    @patch('instat.__main__.InstaExtractor')
    def test_login_error_returns_exit_2(self, MockExtractor):
        from instat.exceptions import LoginError
        MockExtractor.side_effect = LoginError("bad credentials")
        from instat.__main__ import main
        with patch.dict(os.environ, {'INSTAT_USERNAME': 'u', 'INSTAT_PASSWORD': 'p'}):
            rc = main(['extract', '--profile', 't', '--type', 'followers'])
        self.assertEqual(rc, 2)

    @patch('instat.__main__.InstaExtractor')
    def test_all_engines_blocked_returns_exit_3(self, MockExtractor):
        from instat.exceptions import AllEnginesBlockedError
        instance = MockExtractor.return_value
        instance.get_followers.side_effect = AllEnginesBlockedError("blocked")
        from instat.__main__ import main
        with patch.dict(os.environ, {'INSTAT_USERNAME': 'u', 'INSTAT_PASSWORD': 'p'}):
            rc = main(['extract', '--profile', 't', '--type', 'followers'])
        self.assertEqual(rc, 3)

    @patch('instat.__main__.InstaExtractor')
    def test_extract_with_csv_output(self, MockExtractor):
        instance = MockExtractor.return_value
        instance.get_followers.return_value = ['x', 'y']
        from instat.__main__ import main
        with tempfile.TemporaryDirectory() as td:
            output_path = str(Path(td) / 'out.csv')
            with patch.dict(os.environ, {'INSTAT_USERNAME': 'u', 'INSTAT_PASSWORD': 'p'}):
                rc = main([
                    'extract', '--profile', 't', '--type', 'followers',
                    '--output', output_path,
                ])
            self.assertEqual(rc, 0)
            # exporter param passed to InstaExtractor constructor
            call_kwargs = MockExtractor.call_args.kwargs
            self.assertIsNotNone(call_kwargs.get('exporter'))


if __name__ == '__main__':
    unittest.main()
