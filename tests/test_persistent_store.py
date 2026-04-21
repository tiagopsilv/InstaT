"""PersistentStore: multi-run union + metadata. No expiry."""
import os
import sqlite3
import stat
import tempfile
import time
import unittest

from instat.persistent_store import PersistentStore


IS_POSIX = os.name == 'posix'


class TestPersistentStoreBasics(unittest.TestCase):

    def _tmp(self):
        return os.path.join(
            tempfile.mkdtemp(), "ps.sqlite",
        )

    def test_creates_file_and_sets_perms(self):
        path = self._tmp()
        PersistentStore(path)
        self.assertTrue(os.path.exists(path))
        if IS_POSIX:
            mode = stat.S_IMODE(os.stat(path).st_mode)
            self.assertEqual(mode, 0o600)

    def test_schema_present(self):
        path = self._tmp()
        PersistentStore(path)
        with sqlite3.connect(path) as conn:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            self.assertIn(('profiles_seen',), rows)

    def test_add_batch_returns_new_count_only(self):
        path = self._tmp()
        s = PersistentStore(path)
        new = s.add_batch('target', 'followers',
                          ['a', 'b', 'c'], 'acc1')
        self.assertEqual(new, 3)
        new = s.add_batch('target', 'followers',
                          ['b', 'c', 'd'], 'acc1')
        self.assertEqual(new, 1)  # only 'd' is new
        new = s.add_batch('target', 'followers',
                          ['a', 'b', 'c', 'd'], 'acc2')
        self.assertEqual(new, 0)

    def test_get_all_is_deduped_and_sorted(self):
        path = self._tmp()
        s = PersistentStore(path)
        s.add_batch('t', 'followers', ['charlie', 'alice', 'bob'], 'a1')
        s.add_batch('t', 'followers', ['bob', 'dave'], 'a2')
        self.assertEqual(s.get_all('t', 'followers'),
                         ['alice', 'bob', 'charlie', 'dave'])

    def test_list_type_isolation(self):
        path = self._tmp()
        s = PersistentStore(path)
        s.add_batch('t', 'followers', ['a', 'b'], 'acc')
        s.add_batch('t', 'following', ['c', 'd'], 'acc')
        self.assertEqual(s.get_all('t', 'followers'), ['a', 'b'])
        self.assertEqual(s.get_all('t', 'following'), ['c', 'd'])

    def test_profile_id_isolation(self):
        path = self._tmp()
        s = PersistentStore(path)
        s.add_batch('p1', 'followers', ['a', 'b'], 'acc')
        s.add_batch('p2', 'followers', ['c'], 'acc')
        self.assertEqual(s.get_all('p1', 'followers'), ['a', 'b'])
        self.assertEqual(s.get_all('p2', 'followers'), ['c'])

    def test_count(self):
        path = self._tmp()
        s = PersistentStore(path)
        self.assertEqual(s.count('t', 'followers'), 0)
        s.add_batch('t', 'followers', ['a', 'b', 'c'], 'acc')
        self.assertEqual(s.count('t', 'followers'), 3)

    def test_delta_since(self):
        path = self._tmp()
        s = PersistentStore(path)
        s.add_batch('t', 'followers', ['a', 'b'], 'acc')
        # Windows time.time() can have ~1-15ms granularity; sleep well
        # past that so the "mark" is strictly between the two batches.
        time.sleep(0.05)
        mark = time.time()
        time.sleep(0.05)
        s.add_batch('t', 'followers', ['b', 'c', 'd'], 'acc')
        delta = s.get_delta_since('t', 'followers', mark)
        self.assertEqual(sorted(delta), ['c', 'd'])

    def test_stats(self):
        path = self._tmp()
        s = PersistentStore(path)
        s.add_batch('t', 'followers', ['a', 'b'], 'acc1')
        s.add_batch('t', 'followers', ['c'], 'acc2')
        st = s.stats('t', 'followers')
        self.assertEqual(st['total'], 3)
        self.assertIn('acc1', st['source_accounts'])
        self.assertIn('acc2', st['source_accounts'])
        self.assertIsNotNone(st['first_seen_at'])
        self.assertIsNotNone(st['last_seen_at'])

    def test_source_breakdown(self):
        path = self._tmp()
        s = PersistentStore(path)
        s.add_batch('t', 'followers', ['a', 'b', 'c'], 'acc1')
        s.add_batch('t', 'followers', ['d', 'e'], 'acc2')
        # Update existing — does NOT count as new contribution to acc2
        s.add_batch('t', 'followers', ['a', 'b'], 'acc2')
        breakdown = s.source_breakdown('t', 'followers')
        self.assertEqual(breakdown['acc1'], 3)
        self.assertEqual(breakdown['acc2'], 2)  # only d, e are new to acc2

    def test_skips_non_string_and_empty(self):
        path = self._tmp()
        s = PersistentStore(path)
        new = s.add_batch('t', 'followers',
                          ['ok', '', None, 123, 'also_ok'], 'acc')
        self.assertEqual(new, 2)

    def test_reopen_preserves_data(self):
        path = self._tmp()
        s = PersistentStore(path)
        s.add_batch('t', 'followers', ['a', 'b'], 'acc')
        # Close (implicit) — re-open
        s2 = PersistentStore(path)
        self.assertEqual(s2.get_all('t', 'followers'), ['a', 'b'])


if __name__ == '__main__':
    unittest.main()
