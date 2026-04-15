import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch


class TestAsyncInstaExtractorBasics(unittest.IsolatedAsyncioTestCase):
    """Testa API async delegando via asyncio.to_thread."""

    async def test_import_from_package(self):
        from instat import AsyncInstaExtractor
        self.assertTrue(hasattr(AsyncInstaExtractor, 'get_followers'))
        self.assertTrue(hasattr(AsyncInstaExtractor, '__aenter__'))

    @patch('instat.async_extractor.InstaExtractor')
    async def test_get_followers_delegates_to_sync(self, MockSync):
        instance = MockSync.return_value
        instance.get_followers.return_value = ['a', 'b', 'c']
        from instat.async_extractor import AsyncInstaExtractor
        ext = AsyncInstaExtractor('u', 'p')
        result = await ext.get_followers('target', max_duration=60.0)
        self.assertEqual(result, ['a', 'b', 'c'])
        instance.get_followers.assert_called_once_with('target', 60.0)

    @patch('instat.async_extractor.InstaExtractor')
    async def test_get_following_delegates(self, MockSync):
        MockSync.return_value.get_following.return_value = ['x']
        from instat.async_extractor import AsyncInstaExtractor
        ext = AsyncInstaExtractor('u', 'p')
        self.assertEqual(await ext.get_following('t'), ['x'])

    @patch('instat.async_extractor.InstaExtractor')
    async def test_get_total_count_delegates(self, MockSync):
        MockSync.return_value.get_total_count.return_value = 404
        from instat.async_extractor import AsyncInstaExtractor
        ext = AsyncInstaExtractor('u', 'p')
        self.assertEqual(await ext.get_total_count('t', 'followers'), 404)

    async def test_parse_count_text_is_static(self):
        from instat.async_extractor import AsyncInstaExtractor
        self.assertEqual(AsyncInstaExtractor.parse_count_text('1.2k'), 1200)


class TestAsyncContextManager(unittest.IsolatedAsyncioTestCase):

    @patch('instat.async_extractor.InstaExtractor')
    async def test_async_context_manager_calls_close(self, MockSync):
        instance = MockSync.return_value
        from instat.async_extractor import AsyncInstaExtractor
        async with AsyncInstaExtractor('u', 'p') as ext:
            await ext.get_followers('t')
        instance.quit.assert_called_once()

    @patch('instat.async_extractor.InstaExtractor')
    async def test_close_without_init_is_safe(self, MockSync):
        from instat.async_extractor import AsyncInstaExtractor
        ext = AsyncInstaExtractor('u', 'p')
        await ext.close()  # nunca inicializou → não deve levantar
        MockSync.return_value.quit.assert_not_called()


class TestLazyInit(unittest.IsolatedAsyncioTestCase):

    @patch('instat.async_extractor.InstaExtractor')
    async def test_constructor_does_not_call_sync(self, MockSync):
        from instat.async_extractor import AsyncInstaExtractor
        ext = AsyncInstaExtractor('u', 'p')
        MockSync.assert_not_called()
        MockSync.return_value.get_followers.return_value = ['a']
        await ext.get_followers('t')
        MockSync.assert_called_once()

    @patch('instat.async_extractor.InstaExtractor')
    async def test_init_happens_only_once(self, MockSync):
        MockSync.return_value.get_followers.return_value = []
        MockSync.return_value.get_following.return_value = []
        from instat.async_extractor import AsyncInstaExtractor
        ext = AsyncInstaExtractor('u', 'p')
        await ext.get_followers('t')
        await ext.get_following('t')
        await ext.get_followers('other')
        MockSync.assert_called_once()

    @patch('instat.async_extractor.InstaExtractor')
    async def test_concurrent_init_is_thread_safe(self, MockSync):
        MockSync.return_value.get_followers.return_value = []
        from instat.async_extractor import AsyncInstaExtractor
        ext = AsyncInstaExtractor('u', 'p')
        await asyncio.gather(
            ext.get_followers('p1'),
            ext.get_followers('p2'),
            ext.get_followers('p3'),
        )
        MockSync.assert_called_once()


class TestParallelExtraction(unittest.IsolatedAsyncioTestCase):

    @patch('instat.async_extractor.InstaExtractor')
    async def test_parallel_with_separate_instances(self, MockSync):
        """Modo recomendado: 1 instance por profile → paralelismo real."""
        MockSync.return_value.get_followers.side_effect = [
            ['a', 'b'], ['c', 'd'], ['e'],
        ]
        from instat.async_extractor import AsyncInstaExtractor

        async def fetch(pid):
            async with AsyncInstaExtractor('u', 'p') as e:
                return await e.get_followers(pid)

        r1, r2, r3 = await asyncio.gather(fetch('p1'), fetch('p2'), fetch('p3'))
        self.assertEqual(MockSync.call_count, 3)
        self.assertEqual(
            {tuple(r1), tuple(r2), tuple(r3)},
            {('a', 'b'), ('c', 'd'), ('e',)},
        )


class TestExporters(unittest.IsolatedAsyncioTestCase):

    @patch('instat.async_extractor.InstaExtractor')
    async def test_to_csv_delegates(self, MockSync):
        instance = MockSync.return_value
        instance.to_csv.return_value = ['alice', 'bob']
        from instat.async_extractor import AsyncInstaExtractor
        with tempfile.TemporaryDirectory() as td:
            path = str(Path(td) / 'out.csv')
            ext = AsyncInstaExtractor('u', 'p')
            result = await ext.to_csv('t', 'followers', path)
        self.assertEqual(result, ['alice', 'bob'])
        instance.to_csv.assert_called_once()

    @patch('instat.async_extractor.InstaExtractor')
    async def test_to_json_delegates(self, MockSync):
        instance = MockSync.return_value
        instance.to_json.return_value = ['x']
        from instat.async_extractor import AsyncInstaExtractor
        ext = AsyncInstaExtractor('u', 'p')
        await ext.to_json('t', 'followers', 'out.json', indent=4)
        instance.to_json.assert_called_once_with('t', 'followers', 'out.json', None, 4)

    @patch('instat.async_extractor.InstaExtractor')
    async def test_to_sqlite_delegates(self, MockSync):
        instance = MockSync.return_value
        instance.to_sqlite.return_value = ['z']
        from instat.async_extractor import AsyncInstaExtractor
        ext = AsyncInstaExtractor('u', 'p')
        with tempfile.TemporaryDirectory() as td:
            db = str(Path(td) / 'out.db')
            await ext.to_sqlite('t', 'followers', db, table='my')
        instance.to_sqlite.assert_called_once_with('t', 'followers', db, 'my', None)


class TestSyncStillWorks(unittest.TestCase):
    """Backward compat: adicionar AsyncInstaExtractor não quebra InstaExtractor."""

    @patch('instat.extractor.InstaLogin')
    def test_sync_extractor_still_works(self, MockLogin):
        MockLogin.return_value.driver = MagicMock()
        MockLogin.return_value.close_keywords = ['not now']
        from instat.extractor import InstaExtractor
        ext = InstaExtractor('u', 'p', headless=True)
        self.assertIsNotNone(ext._engine)


if __name__ == '__main__':
    unittest.main()
