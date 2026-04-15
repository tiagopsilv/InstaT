import csv
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from instat.exporters import BaseExporter, CallbackExporter, CSVExporter, JSONExporter, SQLiteExporter


def _make_metadata(count=3, list_type='followers', profile_id='target'):
    return {
        'profile_id': profile_id,
        'list_type': list_type,
        'count': count,
        'timestamp': 1700000000.0,
        'duration_seconds': 42.5,
    }


class TestBaseExporter(unittest.TestCase):

    def test_base_exporter_is_abstract(self):
        with self.assertRaises(TypeError):
            BaseExporter()

    def test_validate_metadata_missing_key(self):
        class Dummy(BaseExporter):
            def export(self, profiles, metadata):
                self._validate_metadata(metadata)
        d = Dummy()
        with self.assertRaises(ValueError):
            d.export([], {'profile_id': 'x'})

    def test_iso_from_timestamp(self):
        iso = BaseExporter._iso_from_timestamp(1700000000.0)
        self.assertIn('2023-11-14', iso)
        self.assertTrue(iso.endswith('+00:00'))


class TestCSVExporter(unittest.TestCase):

    def test_csv_creates_valid_file(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'out.csv'
            CSVExporter(str(path)).export(['alice', 'bob', 'carol'], _make_metadata(count=3))

            with open(path, encoding='utf-8-sig') as f:
                rows = list(csv.reader(f))
            self.assertEqual(rows[0], ['username', 'profile_id', 'list_type', 'extracted_at'])
            self.assertEqual(len(rows), 4)
            self.assertEqual(rows[1][0], 'alice')
            self.assertEqual(rows[1][1], 'target')
            self.assertEqual(rows[1][2], 'followers')

    def test_csv_uses_utf8_sig_for_excel(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'out.csv'
            CSVExporter(str(path)).export(['user'], _make_metadata(count=1))
            raw = path.read_bytes()
            self.assertEqual(raw[:3], b'\xef\xbb\xbf')

    def test_csv_empty_profiles_still_writes_header(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'out.csv'
            CSVExporter(str(path)).export([], _make_metadata(count=0))
            with open(path, encoding='utf-8-sig') as f:
                rows = list(csv.reader(f))
            self.assertEqual(len(rows), 1)

    def test_csv_missing_metadata_raises(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'out.csv'
            with self.assertRaises(ValueError):
                CSVExporter(str(path)).export(['x'], {'profile_id': 'x'})


class TestJSONExporter(unittest.TestCase):

    def test_json_has_metadata_and_profiles(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'out.json'
            JSONExporter(str(path)).export(['a', 'b'], _make_metadata(count=2))
            with open(path, encoding='utf-8') as f:
                payload = json.load(f)
            self.assertIn('metadata', payload)
            self.assertIn('profiles', payload)
            self.assertEqual(payload['profiles'], ['a', 'b'])
            self.assertEqual(payload['metadata']['profile_id'], 'target')
            self.assertEqual(payload['metadata']['count'], 2)

    def test_json_custom_indent(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'out.json'
            JSONExporter(str(path), indent=None).export(['x'], _make_metadata(count=1))
            content = path.read_text()
            self.assertNotIn('\n    ', content)


class TestSQLiteExporter(unittest.TestCase):

    def test_sqlite_creates_table_and_rows(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / 'out.db'
            SQLiteExporter(str(db)).export(['u1', 'u2'], _make_metadata(count=2))
            conn = sqlite3.connect(str(db))
            rows = conn.execute("SELECT username FROM profiles").fetchall()
            conn.close()
            self.assertEqual({r[0] for r in rows}, {'u1', 'u2'})

    def test_sqlite_dedup_on_reexport(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / 'out.db'
            exp = SQLiteExporter(str(db))
            exp.export(['a', 'b'], _make_metadata(count=2))
            exp.export(['a', 'b', 'c'], _make_metadata(count=3))
            conn = sqlite3.connect(str(db))
            count = conn.execute("SELECT COUNT(*) FROM profiles").fetchone()[0]
            conn.close()
            self.assertEqual(count, 3)

    def test_sqlite_custom_table(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / 'out.db'
            SQLiteExporter(str(db), table='my_custom').export(['x'], _make_metadata(count=1))
            conn = sqlite3.connect(str(db))
            rows = conn.execute("SELECT username FROM my_custom").fetchall()
            conn.close()
            self.assertEqual(rows[0][0], 'x')

    def test_sqlite_invalid_table_name_raises(self):
        with self.assertRaises(ValueError):
            SQLiteExporter('x.db', table='drop; table--')


class TestCallbackExporter(unittest.TestCase):

    def test_callback_receives_args(self):
        received = []
        def cb(profiles, metadata):
            received.append((list(profiles), dict(metadata)))
        CallbackExporter(cb).export(['a'], _make_metadata(count=1))
        self.assertEqual(received[0][0], ['a'])
        self.assertEqual(received[0][1]['profile_id'], 'target')

    def test_callback_not_callable_raises(self):
        with self.assertRaises(TypeError):
            CallbackExporter(123)


class TestInstaExtractorIntegration(unittest.TestCase):
    """Integration: InstaExtractor delega ao exporter + métodos convenience."""

    def setUp(self):
        self._login_patcher = patch('instat.extractor.InstaLogin')
        MockLogin = self._login_patcher.start()
        MockLogin.return_value.driver = MagicMock()
        MockLogin.return_value.close_keywords = ['not now']

    def tearDown(self):
        self._login_patcher.stop()

    def test_exporter_called_after_extract(self):
        from instat.extractor import InstaExtractor
        mock_exporter = MagicMock(spec=BaseExporter)
        ext = InstaExtractor('u', 'p', headless=True, exporter=mock_exporter)
        with patch.object(ext._engine_manager, 'extract', return_value=['a', 'b']):
            result = ext.get_followers('target')
        self.assertEqual(result, ['a', 'b'])
        mock_exporter.export.assert_called_once()
        args, _ = mock_exporter.export.call_args
        profiles, metadata = args
        self.assertEqual(profiles, ['a', 'b'])
        self.assertEqual(metadata['profile_id'], 'target')
        self.assertEqual(metadata['list_type'], 'followers')
        self.assertEqual(metadata['count'], 2)

    def test_exporter_exception_does_not_break_extract(self):
        from instat.extractor import InstaExtractor
        broken = MagicMock(spec=BaseExporter)
        broken.export.side_effect = RuntimeError("boom")
        ext = InstaExtractor('u', 'p', exporter=broken)
        with patch.object(ext._engine_manager, 'extract', return_value=['x']):
            result = ext.get_followers('target')
        self.assertEqual(result, ['x'])

    def test_to_csv_convenience_method(self):
        from instat.extractor import InstaExtractor
        ext = InstaExtractor('u', 'p')
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'out.csv'
            with patch.object(ext._engine_manager, 'extract',
                              return_value=['alpha', 'beta']):
                result = ext.to_csv('target', 'followers', str(path))
            self.assertEqual(result, ['alpha', 'beta'])
            self.assertTrue(path.exists())
            rows = list(csv.reader(open(path, encoding='utf-8-sig')))
            self.assertEqual(len(rows), 3)

    def test_to_json_convenience_method(self):
        from instat.extractor import InstaExtractor
        ext = InstaExtractor('u', 'p')
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'out.json'
            with patch.object(ext._engine_manager, 'extract',
                              return_value=['a', 'b']):
                result = ext.to_json('target', 'following', str(path))
            self.assertEqual(result, ['a', 'b'])
            payload = json.loads(path.read_text(encoding='utf-8'))
            self.assertEqual(payload['profiles'], ['a', 'b'])
            self.assertEqual(payload['metadata']['list_type'], 'following')

    def test_to_sqlite_convenience_method(self):
        from instat.extractor import InstaExtractor
        ext = InstaExtractor('u', 'p')
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / 'out.db'
            with patch.object(ext._engine_manager, 'extract', return_value=['x']):
                ext.to_sqlite('target', 'followers', str(db))
            conn = sqlite3.connect(str(db))
            rows = conn.execute("SELECT username FROM profiles").fetchall()
            conn.close()
            self.assertEqual(rows[0][0], 'x')

    def test_convenience_does_not_overwrite_exporter(self):
        from instat.extractor import InstaExtractor
        mock_exp = MagicMock(spec=BaseExporter)
        ext = InstaExtractor('u', 'p', exporter=mock_exp)
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / 'out.csv'
            with patch.object(ext._engine_manager, 'extract', return_value=['a']):
                ext.to_csv('t', 'followers', str(path))
        # self._exporter ainda é o original
        self.assertIs(ext._exporter, mock_exp)
        # Convenience não chamou self._exporter (exporter efêmero)
        mock_exp.export.assert_not_called()

    def test_no_exporter_no_side_effects(self):
        from instat.extractor import InstaExtractor
        ext = InstaExtractor('u', 'p')
        self.assertIsNone(ext._exporter)
        with patch.object(ext._engine_manager, 'extract', return_value=['z']):
            result = ext.get_followers('target')
        self.assertEqual(result, ['z'])


if __name__ == "__main__":
    unittest.main()
