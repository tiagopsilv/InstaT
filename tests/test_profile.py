"""Testes para instat.profile e InstaExtractor.get_profile."""
import unittest
from unittest.mock import MagicMock, patch


class TestParseShorthandCount(unittest.TestCase):
    def test_plain_number(self):
        from instat.profile import _parse_shorthand_count
        self.assertEqual(_parse_shorthand_count('1,894'), 1894)
        self.assertEqual(_parse_shorthand_count('42'), 42)

    def test_k_suffix(self):
        from instat.profile import _parse_shorthand_count
        self.assertEqual(_parse_shorthand_count('1.9K'), 1900)
        self.assertEqual(_parse_shorthand_count('10K'), 10000)

    def test_m_suffix(self):
        from instat.profile import _parse_shorthand_count
        self.assertEqual(_parse_shorthand_count('2.5M'), 2_500_000)

    def test_invalid_returns_none(self):
        from instat.profile import _parse_shorthand_count
        self.assertIsNone(_parse_shorthand_count(''))
        self.assertIsNone(_parse_shorthand_count('abc'))


class TestParseProfileFromMeta(unittest.TestCase):
    def test_parses_standard_description(self):
        from instat.profile import parse_profile_from_meta
        desc = '1,894 Followers, 1,892 Following, 123 Posts - @tiagopsilv'
        out = parse_profile_from_meta(desc)
        self.assertEqual(out['followers_count'], 1894)
        self.assertEqual(out['following_count'], 1892)
        self.assertEqual(out['posts_count'], 123)

    def test_parses_k_m_suffixes(self):
        from instat.profile import parse_profile_from_meta
        desc = '1.9K Followers, 500 Following, 2M Posts - @x'
        out = parse_profile_from_meta(desc)
        self.assertEqual(out['followers_count'], 1900)
        self.assertEqual(out['following_count'], 500)
        self.assertEqual(out['posts_count'], 2_000_000)

    def test_empty_or_invalid_returns_empty(self):
        from instat.profile import parse_profile_from_meta
        self.assertEqual(parse_profile_from_meta(''), {})
        self.assertEqual(parse_profile_from_meta('garbage text'), {})


class TestProfileBinding(unittest.TestCase):
    def test_get_followers_delegates_to_extractor(self):
        from instat.profile import Profile
        ext = MagicMock()
        ext.get_followers.return_value = ['a', 'b']
        p = Profile(username='u', url='https://ig/u/', _extractor=ext)
        result = p.get_followers(max_duration=60)
        ext.get_followers.assert_called_once_with('u', max_duration=60)
        self.assertEqual(result, ['a', 'b'])

    def test_get_following_delegates(self):
        from instat.profile import Profile
        ext = MagicMock()
        ext.get_following.return_value = ['x']
        p = Profile(username='u', url='https://ig/u/', _extractor=ext)
        self.assertEqual(p.get_following(), ['x'])
        ext.get_following.assert_called_once_with('u', max_duration=None)

    def test_unbound_raises(self):
        from instat.profile import Profile
        p = Profile(username='u', url='x')
        with self.assertRaises(RuntimeError):
            p.get_followers()


class TestGetProfileOnExtractor(unittest.TestCase):
    def _make_ext(self):
        from instat.extractor import InstaExtractor
        ext = InstaExtractor.__new__(InstaExtractor)
        ext.username = 'me'
        ext.password = 'p'
        ext.timeout = 10
        ext._exporter = None
        ext._engine = MagicMock()
        return ext

    def test_returns_profile_with_parsed_counts(self):
        ext = self._make_ext()
        driver = ext._engine._driver

        meta_values = {
            'og:description': '1,894 Followers, 1,892 Following, 123 Posts - @t',
            'og:title': 'Tiago Silva (@tiagopsilv) • Instagram',
            'og:image': 'https://cdn/pic.jpg',
        }

        def find_element(by, sel):
            for prop, val in meta_values.items():
                if prop in sel:
                    el = MagicMock()
                    el.get_attribute.return_value = val
                    return el
            raise Exception('not found')

        driver.find_element.side_effect = find_element
        driver.execute_script.return_value = False

        p = ext.get_profile('tiagopsilv')
        self.assertEqual(p.username, 'tiagopsilv')
        self.assertEqual(p.followers_count, 1894)
        self.assertEqual(p.following_count, 1892)
        self.assertEqual(p.posts_count, 123)
        self.assertEqual(p.full_name, 'Tiago Silva')
        self.assertEqual(p.profile_pic_url, 'https://cdn/pic.jpg')
        self.assertIs(p._extractor, ext)

    def test_raises_when_no_selenium_driver(self):
        ext = self._make_ext()
        ext._engine._driver = None
        with self.assertRaises(RuntimeError):
            ext.get_profile('x')


if __name__ == '__main__':
    unittest.main()
