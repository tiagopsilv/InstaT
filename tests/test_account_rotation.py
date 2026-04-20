"""P5: get_{followers,following}_with_rotation — serial fallback across
accounts + optional per-account proxy when shadow-rate-limit detected."""
import unittest
from unittest.mock import MagicMock, patch


def _make_extractor_skeleton(**kwargs):
    from instat.extractor import InstaExtractor
    ext = InstaExtractor.__new__(InstaExtractor)
    ext._exporter = None
    ext._engine = MagicMock()
    ext._engine_manager = MagicMock()
    ext._imap_config = kwargs.get('imap_config')
    ext._headless = True
    ext._engine_names = ['selenium']
    ext.timeout = 10
    ext._completion_threshold_override = None
    ext.username = 'primary'
    ext.password = 'pw'
    ext._engine_manager.engines = [
        type('E', (), {'name': 'selenium'})()
    ]
    return ext


class TestFallbackAccountValidation(unittest.TestCase):

    def test_empty_or_none_returns_empty(self):
        from instat.extractor import InstaExtractor
        self.assertEqual(InstaExtractor._validate_fallback_accounts(None), [])
        self.assertEqual(InstaExtractor._validate_fallback_accounts([]), [])

    def test_rejects_non_dict(self):
        from instat.extractor import InstaExtractor
        with self.assertRaises(ValueError):
            InstaExtractor._validate_fallback_accounts(['not a dict'])
        with self.assertRaises(ValueError):
            InstaExtractor._validate_fallback_accounts([('u', 'p')])

    def test_rejects_missing_or_empty_fields(self):
        from instat.extractor import InstaExtractor
        for bad in [
            [{'username': 'u'}],
            [{'password': 'p'}],
            [{'username': '', 'password': 'p'}],
            [{'username': 'u', 'password': ''}],
            [{'username': None, 'password': 'p'}],
        ]:
            with self.assertRaises(ValueError, msg=str(bad)):
                InstaExtractor._validate_fallback_accounts(bad)

    def test_accepts_valid(self):
        from instat.extractor import InstaExtractor
        accs = [
            {'username': 'alt1', 'password': 'p1'},
            {'username': 'alt2', 'password': 'p2', 'extra': 'ignored'},
        ]
        out = InstaExtractor._validate_fallback_accounts(accs)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0], {'username': 'alt1', 'password': 'p1'})
        self.assertEqual(out[1], {'username': 'alt2', 'password': 'p2'})


class TestRotationFlow(unittest.TestCase):

    def test_no_fallback_returns_primary_result(self):
        ext = _make_extractor_skeleton()
        ext._engine_manager.get_total_count.return_value = 1000
        with patch.object(ext, '_extract_until_complete',
                          return_value=['u1', 'u2']):
            res = ext.get_followers_with_rotation('someuser')
        self.assertEqual(sorted(res), ['u1', 'u2'])

    def test_primary_hits_target_skips_fallbacks(self):
        ext = _make_extractor_skeleton()
        ext._engine_manager.get_total_count.return_value = 100
        primary_result = [f'u{i}' for i in range(95)]  # 95% >= 0.90
        build_calls = []

        def fake_build(*args, **kwargs):
            build_calls.append(args)
            return MagicMock()

        with patch.object(ext, '_extract_until_complete',
                          return_value=primary_result), \
             patch.object(ext, '_build_rotation_extractor',
                          side_effect=fake_build):
            res = ext.get_followers_with_rotation(
                'target',
                fallback_accounts=[{'username': 'alt', 'password': 'pw'}],
            )
        self.assertEqual(len(res), 95)
        # Fallbacks never built because target was hit
        self.assertEqual(build_calls, [])

    def test_fallback_union_when_primary_shadow(self):
        """Primary gets 30, fallback1 adds 40 new, fallback2 adds 50 new.
        With target 0.90 * 100 = 90, we need fallback2 to trigger too."""
        ext = _make_extractor_skeleton()
        ext._engine_manager.get_total_count.return_value = 100

        primary_result = [f'a{i}' for i in range(30)]  # 30

        # fallback1 extractor
        alt1 = MagicMock()
        alt1._extract_until_complete.return_value = [f'b{i}' for i in range(40)]
        # fallback2 extractor — together with prev, >= 90
        alt2 = MagicMock()
        alt2._extract_until_complete.return_value = [f'c{i}' for i in range(50)]

        builds = iter([alt1, alt2])

        with patch.object(ext, '_extract_until_complete',
                          return_value=primary_result), \
             patch.object(ext, '_build_rotation_extractor',
                          side_effect=lambda *a, **k: next(builds)):
            res = ext.get_followers_with_rotation(
                'target',
                fallback_accounts=[
                    {'username': 'alt1', 'password': 'p1'},
                    {'username': 'alt2', 'password': 'p2'},
                ],
                target_fraction=0.90,
            )

        self.assertEqual(len(res), 30 + 40 + 50)
        alt1._extract_until_complete.assert_called_once()
        alt2._extract_until_complete.assert_called_once()
        alt1.quit.assert_called_once()
        alt2.quit.assert_called_once()

    def test_rotation_short_circuits_on_target(self):
        ext = _make_extractor_skeleton()
        ext._engine_manager.get_total_count.return_value = 100

        primary_result = [f'p{i}' for i in range(30)]
        alt1 = MagicMock()
        alt1._extract_until_complete.return_value = [f'a{i}' for i in range(65)]
        alt2 = MagicMock()  # should NOT be called

        builds = iter([alt1, alt2])

        with patch.object(ext, '_extract_until_complete',
                          return_value=primary_result), \
             patch.object(ext, '_build_rotation_extractor',
                          side_effect=lambda *a, **k: next(builds)):
            res = ext.get_followers_with_rotation(
                'target',
                fallback_accounts=[
                    {'username': 'alt1', 'password': 'p1'},
                    {'username': 'alt2', 'password': 'p2'},
                ],
                target_fraction=0.90,  # 30 + 65 = 95 >= 90
            )

        self.assertEqual(len(res), 95)
        alt1._extract_until_complete.assert_called_once()
        alt2._extract_until_complete.assert_not_called()

    def test_fallback_proxies_paired_positionally(self):
        ext = _make_extractor_skeleton()
        ext._engine_manager.get_total_count.return_value = 1000
        alt1 = MagicMock()
        alt1._extract_until_complete.return_value = []
        alt2 = MagicMock()
        alt2._extract_until_complete.return_value = []
        alt3 = MagicMock()
        alt3._extract_until_complete.return_value = []

        build_args = []

        def fake_build(username, password, proxy):
            build_args.append((username, proxy))
            return [alt1, alt2, alt3][len(build_args) - 1]

        with patch.object(ext, '_extract_until_complete', return_value=[]), \
             patch.object(ext, '_build_rotation_extractor',
                          side_effect=fake_build):
            ext.get_followers_with_rotation(
                'target',
                fallback_accounts=[
                    {'username': 'alt1', 'password': 'p'},
                    {'username': 'alt2', 'password': 'p'},
                    {'username': 'alt3', 'password': 'p'},
                ],
                fallback_proxies=['http://proxy1', 'http://proxy2'],
                target_fraction=0.90,
            )

        # Positional pairing: alt1→proxy1, alt2→proxy2, alt3→None
        self.assertEqual(build_args, [
            ('alt1', 'http://proxy1'),
            ('alt2', 'http://proxy2'),
            ('alt3', None),
        ])

    def test_fallback_setup_failure_skips_to_next(self):
        ext = _make_extractor_skeleton()
        ext._engine_manager.get_total_count.return_value = 1000
        alt2 = MagicMock()
        alt2._extract_until_complete.return_value = ['x']

        def fake_build(username, password, proxy):
            if username == 'alt1':
                raise RuntimeError("login blew up")
            return alt2

        with patch.object(ext, '_extract_until_complete', return_value=[]), \
             patch.object(ext, '_build_rotation_extractor',
                          side_effect=fake_build):
            res = ext.get_followers_with_rotation(
                'target',
                fallback_accounts=[
                    {'username': 'alt1', 'password': 'p'},
                    {'username': 'alt2', 'password': 'p'},
                ],
            )

        # alt1 failed build, alt2 succeeded
        self.assertEqual(res, ['x'])
        alt2.quit.assert_called_once()

    def test_fallback_extract_exception_keeps_accumulator(self):
        ext = _make_extractor_skeleton()
        ext._engine_manager.get_total_count.return_value = 1000

        alt1 = MagicMock()
        alt1._extract_until_complete.side_effect = Exception("boom")
        alt2 = MagicMock()
        alt2._extract_until_complete.return_value = ['x', 'y']

        builds = iter([alt1, alt2])

        with patch.object(ext, '_extract_until_complete',
                          return_value=['a']), \
             patch.object(ext, '_build_rotation_extractor',
                          side_effect=lambda *a, **k: next(builds)):
            res = ext.get_followers_with_rotation(
                'target',
                fallback_accounts=[
                    {'username': 'alt1', 'password': 'p'},
                    {'username': 'alt2', 'password': 'p'},
                ],
            )

        self.assertEqual(sorted(res), ['a', 'x', 'y'])
        alt1.quit.assert_called_once()  # quit fired even after exception
        alt2.quit.assert_called_once()

    def test_profile_id_validated_at_entry(self):
        ext = _make_extractor_skeleton()
        with self.assertRaises(ValueError):
            ext.get_followers_with_rotation('../admin')
        with self.assertRaises(ValueError):
            ext.get_following_with_rotation('user?q=1')

    def test_bad_fallback_accounts_rejected_early(self):
        ext = _make_extractor_skeleton()
        with self.assertRaises(ValueError):
            ext.get_followers_with_rotation(
                'target',
                fallback_accounts=[{'username': 'only_username'}],
            )


class TestBuildRotationExtractorInheritance(unittest.TestCase):
    """Rotation extractor should inherit key config from self."""

    def test_inherits_imap_config_engines_timeout(self):
        from instat.extractor import InstaExtractor
        ext = _make_extractor_skeleton()
        ext._imap_config = {'host': 'x'}
        ext._engine_names = ['selenium', 'httpx']
        ext.timeout = 42
        ext._completion_threshold_override = 0.5
        ext._headless = False

        captured = []
        orig = InstaExtractor

        def fake_init(self, **kwargs):
            captured.append(kwargs)

        with patch('instat.extractor.InstaExtractor', side_effect=orig) as mock_cls:
            # We can't fully mock __init__ without side-effects; just call
            # _build_rotation_extractor and capture via mock_cls.call_args
            try:
                ext._build_rotation_extractor('alt', 'pw', 'http://proxy')
            except Exception:
                # __init__ will fail because we can't really construct one;
                # we just want to verify the args that were passed.
                pass
        kw = mock_cls.call_args.kwargs
        self.assertEqual(kw['username'], 'alt')
        self.assertEqual(kw['password'], 'pw')
        self.assertEqual(kw['headless'], False)
        self.assertEqual(kw['timeout'], 42)
        self.assertEqual(kw['proxies'], ['http://proxy'])
        self.assertEqual(kw['engines'], ['selenium', 'httpx'])
        self.assertEqual(kw['imap_config'], {'host': 'x'})
        self.assertEqual(kw['completion_threshold'], 0.5)


if __name__ == '__main__':
    unittest.main()
