"""PostMetrics dataclass + parsing helpers — covers TEP feed flow.

The IG private API shape drifts; these tests pin the parser against
known-good shapes and document every tolerated deviation."""
import unittest
from datetime import datetime, timezone

from instat.post_metrics import (
    PostMetrics,
    parse_hashtags,
    parse_post_from_api,
)


class TestParseHashtags(unittest.TestCase):

    def test_empty_caption_returns_empty(self):
        self.assertEqual(parse_hashtags(None), [])
        self.assertEqual(parse_hashtags(""), [])

    def test_extracts_simple_tags(self):
        self.assertEqual(
            parse_hashtags("hello #foo and #bar"),
            ["foo", "bar"],
        )

    def test_lowercases_and_dedupes(self):
        # Order preserved on first sight. Duplicate (#FOO == #foo) drops.
        self.assertEqual(
            parse_hashtags("#FOO #bar #foo #BAR #baz"),
            ["foo", "bar", "baz"],
        )

    def test_unicode_accented_tags(self):
        # Brazilian Portuguese tags must survive — TCC sample is BR-PT.
        self.assertEqual(
            parse_hashtags("treino de hoje #corrida #saúde #atletismo"),
            ["corrida", "saúde", "atletismo"],
        )

    def test_ignores_isolated_hash(self):
        # A # alone or with whitespace after isn't a tag.
        self.assertEqual(parse_hashtags("preço # 50 reais"), [])

    def test_ignores_tags_with_only_punctuation(self):
        # # followed by punctuation only must not match.
        self.assertEqual(parse_hashtags("#!!! #..."), [])


class TestParsePostFromAPI(unittest.TestCase):

    def _api_item(self, **overrides):
        base = {
            'code': 'B2lkP7Op_xy',
            'like_count': 137,
            'comment_count': 8,
            'taken_at': 1716220800,  # 2024-05-20 16:00 UTC
            'caption': {'text': 'corrida matinal #treino #saúde'},
            'media_type': 1,
            'image_versions2': {
                'candidates': [
                    {'url': 'https://scontent.cdninstagram.com/img.jpg',
                     'width': 1080, 'height': 1080},
                ],
            },
        }
        base.update(overrides)
        return base

    def test_full_payload_parsed(self):
        m = parse_post_from_api(self._api_item())
        self.assertEqual(m.shortcode, 'B2lkP7Op_xy')
        self.assertEqual(m.likes_count, 137)
        self.assertEqual(m.comments_count, 8)
        self.assertEqual(
            m.timestamp,
            datetime(2024, 5, 20, 16, 0, tzinfo=timezone.utc),
        )
        self.assertEqual(m.caption, 'corrida matinal #treino #saúde')
        self.assertEqual(m.hashtags, ['treino', 'saúde'])
        self.assertEqual(m.media_type, 'image')
        self.assertEqual(m.media_url, 'https://scontent.cdninstagram.com/img.jpg')

    def test_missing_shortcode_raises(self):
        with self.assertRaises(ValueError):
            parse_post_from_api({'like_count': 10})

    def test_non_dict_raises(self):
        with self.assertRaises(ValueError):
            parse_post_from_api("not a dict")

    def test_missing_likes_or_comments_become_none(self):
        # IG hides like_count for some Reels. Code must tolerate.
        m = parse_post_from_api(self._api_item(
            like_count=None, comment_count=None,
        ))
        self.assertIsNone(m.likes_count)
        self.assertIsNone(m.comments_count)

    def test_invalid_timestamp_becomes_none(self):
        # Feed sometimes ships taken_at as a string in old endpoints.
        m = parse_post_from_api(self._api_item(taken_at='not-a-number'))
        self.assertIsNone(m.timestamp)

    def test_carousel_media_type_label(self):
        m = parse_post_from_api(self._api_item(media_type=8))
        self.assertEqual(m.media_type, 'carousel')

    def test_video_media_type_label(self):
        m = parse_post_from_api(self._api_item(media_type=2))
        self.assertEqual(m.media_type, 'video')

    def test_unknown_media_type_becomes_none(self):
        m = parse_post_from_api(self._api_item(media_type=99))
        self.assertIsNone(m.media_type)

    def test_carousel_falls_back_to_first_child_image(self):
        # Carousel root has no image_versions2 — pull from carousel_media[0].
        item = self._api_item(media_type=8)
        del item['image_versions2']
        item['carousel_media'] = [
            {'image_versions2': {'candidates': [
                {'url': 'https://scontent.cdninstagram.com/c0.jpg',
                 'width': 1080, 'height': 1080},
            ]}},
            {'image_versions2': {'candidates': [
                {'url': 'https://scontent.cdninstagram.com/c1.jpg',
                 'width': 1080, 'height': 1080},
            ]}},
        ]
        m = parse_post_from_api(item)
        self.assertEqual(m.media_url, 'https://scontent.cdninstagram.com/c0.jpg')

    def test_no_caption_object(self):
        m = parse_post_from_api(self._api_item(caption=None))
        self.assertIsNone(m.caption)
        self.assertEqual(m.hashtags, [])

    def test_caption_dict_without_text(self):
        # Some Reels have caption = {} with no 'text' key.
        m = parse_post_from_api(self._api_item(caption={}))
        self.assertIsNone(m.caption)
        self.assertEqual(m.hashtags, [])

    def test_no_image_url_safe(self):
        item = self._api_item()
        del item['image_versions2']
        m = parse_post_from_api(item)
        self.assertIsNone(m.media_url)


class TestTEPCalculationConsumerPattern(unittest.TestCase):
    """Pin the consumer-side TEP formula against the dataclass shape.

    PostMetrics doesn't compute TEP itself (analytics is pipeline's
    job), but downstream code shouldn't break when fields are None.
    This covers the math the pipeline will run."""

    def test_tep_formula_works_with_full_data(self):
        post = PostMetrics(
            shortcode='X', likes_count=200, comments_count=50,
        )
        followers = 5000
        tep = (post.likes_count + post.comments_count) / followers * 100
        self.assertAlmostEqual(tep, 5.0)

    def test_tep_skipped_when_likes_hidden(self):
        # Pipeline pattern: skip post when likes hidden, don't crash.
        post = PostMetrics(
            shortcode='X', likes_count=None, comments_count=10,
        )
        is_complete = (
            post.likes_count is not None and post.comments_count is not None
        )
        self.assertFalse(is_complete)


if __name__ == '__main__':
    unittest.main()
