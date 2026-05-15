"""ProfileSummary — per-follower metadata snapshot."""
import unittest

from instat.profile_summary import ProfileSummary


class TestProfileSummary(unittest.TestCase):

    def test_from_username_minimal(self):
        ps = ProfileSummary.from_username("someone")
        self.assertEqual(ps.username, "someone")
        self.assertIsNone(ps.user_id)
        self.assertIsNone(ps.full_name)
        self.assertIsNone(ps.is_private)
        self.assertIsNone(ps.is_verified)
        self.assertIsNone(ps.is_business)
        self.assertIsNone(ps.profile_pic_url)
        self.assertIsNone(ps.follower_count)

    def test_from_api_user_full(self):
        api_user = {
            'pk': 12345678,
            'username': 'brazilianrunner',
            'full_name': 'Maria Corrida',
            'is_private': False,
            'is_verified': True,
            'is_business': True,
            'profile_pic_url': 'https://scontent.cdninstagram.com/p.jpg',
        }
        ps = ProfileSummary.from_api_user(api_user)
        self.assertEqual(ps.username, 'brazilianrunner')
        self.assertEqual(ps.user_id, '12345678')
        self.assertEqual(ps.full_name, 'Maria Corrida')
        self.assertFalse(ps.is_private)
        self.assertTrue(ps.is_verified)
        self.assertTrue(ps.is_business)
        self.assertEqual(
            ps.profile_pic_url,
            'https://scontent.cdninstagram.com/p.jpg',
        )

    def test_from_api_user_missing_username_raises(self):
        with self.assertRaises(ValueError):
            ProfileSummary.from_api_user({'pk': 1, 'full_name': 'X'})

    def test_from_api_user_non_dict_raises(self):
        with self.assertRaises(ValueError):
            ProfileSummary.from_api_user("not a dict")

    def test_from_api_user_pk_none_keeps_user_id_none(self):
        ps = ProfileSummary.from_api_user({'username': 'x', 'pk': None})
        self.assertIsNone(ps.user_id)

    def test_from_api_user_tolerates_missing_fields(self):
        # API drift — only username present, everything else absent.
        ps = ProfileSummary.from_api_user({'username': 'x'})
        self.assertEqual(ps.username, 'x')
        self.assertIsNone(ps.user_id)
        self.assertIsNone(ps.full_name)
        self.assertIsNone(ps.is_verified)


if __name__ == '__main__':
    unittest.main()
