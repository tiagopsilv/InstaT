import unittest
from typing import Callable, Optional, Set

from instat.engines.base import BaseEngine
from instat.engines.engine_manager import EngineManager
from instat.exceptions import AllEnginesBlockedError, BlockedError


class MockEngine(BaseEngine):
    """Concrete mock implementation of BaseEngine for testing."""

    def __init__(self, engine_name='mock', available=True,
                 profiles=None, count=100, block_on_extract=False,
                 block_on_count=False):
        self._name = engine_name
        self._available = available
        self._profiles = profiles or {'user1', 'user2'}
        self._count = count
        self._block_on_extract = block_on_extract
        self._block_on_count = block_on_count
        self._quit_called = False

    def login(self, username: str, password: str, **kwargs) -> bool:
        return True

    def extract(self, profile_id: str, list_type: str,
                existing_profiles: Optional[Set[str]] = None,
                max_duration: Optional[float] = None,
                on_batch: Optional[Callable] = None) -> Set[str]:
        if self._block_on_extract:
            raise BlockedError(f"{self._name} is blocked")
        return self._profiles

    def get_total_count(self, profile_id: str, list_type: str) -> Optional[int]:
        if self._block_on_count:
            raise BlockedError(f"{self._name} blocked on count")
        return self._count

    def quit(self) -> None:
        self._quit_called = True

    @property
    def name(self) -> str:
        return self._name

    @property
    def is_available(self) -> bool:
        return self._available


class TestEngineManager(unittest.TestCase):

    def test_engine_manager_uses_first_available(self):
        e1 = MockEngine('engine1', profiles={'a', 'b'})
        e2 = MockEngine('engine2', profiles={'c', 'd'})
        mgr = EngineManager([e1, e2])
        result = mgr.extract('user', 'followers')
        self.assertEqual(set(result), {'a', 'b'})

    def test_engine_manager_falls_back_on_blocked_error(self):
        e1 = MockEngine('engine1', block_on_extract=True)
        e2 = MockEngine('engine2', profiles={'x', 'y'})
        mgr = EngineManager([e1, e2])
        result = mgr.extract('user', 'followers')
        self.assertEqual(set(result), {'x', 'y'})

    def test_engine_manager_raises_all_blocked_when_exhausted(self):
        e1 = MockEngine('engine1', block_on_extract=True)
        e2 = MockEngine('engine2', block_on_extract=True)
        mgr = EngineManager([e1, e2])
        with self.assertRaises(AllEnginesBlockedError):
            mgr.extract('user', 'followers')

    def test_engine_manager_skips_unavailable_engines(self):
        e1 = MockEngine('unavailable', available=False)
        e2 = MockEngine('available', profiles={'ok'})
        mgr = EngineManager([e1, e2])
        self.assertEqual(len(mgr.engines), 1)
        self.assertEqual(mgr.engines[0].name, 'available')

    def test_base_engine_not_instantiable(self):
        with self.assertRaises(TypeError):
            BaseEngine()

    def test_quit_all_closes_all_engines(self):
        e1 = MockEngine('e1')
        e2 = MockEngine('e2')
        mgr = EngineManager([e1, e2])
        mgr.quit_all()
        self.assertTrue(e1._quit_called)
        self.assertTrue(e2._quit_called)

    def test_get_total_count_fallback(self):
        e1 = MockEngine('e1', block_on_count=True)
        e2 = MockEngine('e2', count=500)
        mgr = EngineManager([e1, e2])
        result = mgr.get_total_count('user', 'followers')
        self.assertEqual(result, 500)


if __name__ == "__main__":
    unittest.main()
