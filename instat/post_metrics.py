"""PostMetrics — per-post snapshot for engagement scoring.

Purpose: feed TEP (Taxa de Engajamento por Post) and similar formulas
without the consumer scraping post pages itself. TEP is defined in the
TCC (Tiago, 2023) as:

    TEP_i = (likes_i + comments_i) / followers * 100

The averaged form (TME) is the mean of TEP_i across N posts — N=5 in the
TCC. This module exposes the per-post primitive; the consumer
(`instagram-data-pipeline`) does the math and stores in BigQuery.

Scope: extraction only. No analytics, no scoring, no thresholds.

Field availability depends on engine. The dataclass uses Optional for
fields the IG private API may omit on some media types (Reels often
hide like_count for new posts; carousels mark media_type=8).
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, List, Optional


# Hashtag pattern: # followed by letters/digits/underscores; supports
# accented characters and emoji-adjacent forms. Lowercased for dedup.
_HASHTAG_RE = re.compile(r'#([\wÀ-ſ][\wÀ-ſ]*)', re.UNICODE)


# IG private API media_type code → human label.
_MEDIA_TYPE_LABELS = {1: 'image', 2: 'video', 8: 'carousel'}


@dataclass
class PostMetrics:
    """Engagement metrics for a single Instagram post.

    Fields:
      shortcode: 11-char URL slug (`instagram.com/p/<shortcode>/`). Stable.
      likes_count / comments_count: None when IG hides them (rare on
        public profiles; common on Reels under "like count hidden").
      timestamp: post creation time, UTC. None if API omitted it.
      caption: raw caption text. May be empty string for media-only posts.
      hashtags: lowercased list parsed from caption. Deduplicated, ordered.
      media_type: 'image' | 'video' | 'carousel' | None.
      media_url: URL for the first image of the post (carousel cover or
        image post). For videos, the thumbnail URL. Useful for downstream
        CNN visual analysis without re-fetching the post.
    """
    shortcode: str
    likes_count: Optional[int] = None
    comments_count: Optional[int] = None
    timestamp: Optional[datetime] = None
    caption: Optional[str] = None
    hashtags: List[str] = field(default_factory=list)
    media_type: Optional[str] = None
    media_url: Optional[str] = None


def parse_hashtags(caption: Optional[str]) -> List[str]:
    """Extract lowercased hashtags from a caption.

    Deduplicated while preserving first-occurrence order — matters when
    consumers want to know which tag the author used first. Empty/None
    caption returns []."""
    if not caption:
        return []
    seen: dict = {}
    for match in _HASHTAG_RE.finditer(caption):
        tag = match.group(1).lower()
        if tag not in seen:
            seen[tag] = None
    return list(seen.keys())


def parse_post_from_api(item: Any) -> PostMetrics:
    """Decode one entry from `/feed/user/{user_id}/` into PostMetrics.

    Tolerant of missing fields — IG's private API shape drifts; the goal
    is a best-effort PostMetrics with as many fields populated as
    possible, never a hard failure. The shortcode is the only field we
    refuse to fabricate; if `code` is missing the API result is
    unusable for downstream lookup and we raise."""
    if not isinstance(item, dict):
        raise ValueError(f"Expected dict, got {type(item).__name__}")

    shortcode = item.get('code')
    if not shortcode:
        raise ValueError("Post item missing 'code' (shortcode) field")

    caption_obj = item.get('caption')
    caption_text: Optional[str] = None
    if isinstance(caption_obj, dict):
        caption_text = caption_obj.get('text')

    taken_at = item.get('taken_at')
    ts: Optional[datetime] = None
    if isinstance(taken_at, (int, float)):
        try:
            ts = datetime.fromtimestamp(taken_at, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            ts = None

    media_type_code = item.get('media_type')
    media_label = _MEDIA_TYPE_LABELS.get(media_type_code)

    # Image URL: image_versions2.candidates[0].url; carousels nest under
    # carousel_media[0].image_versions2.candidates[0].url. Videos use
    # thumbnail in the same image_versions2 path.
    media_url = _extract_media_url(item)

    return PostMetrics(
        shortcode=shortcode,
        likes_count=_safe_int(item.get('like_count')),
        comments_count=_safe_int(item.get('comment_count')),
        timestamp=ts,
        caption=caption_text,
        hashtags=parse_hashtags(caption_text),
        media_type=media_label,
        media_url=media_url,
    )


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _extract_media_url(item: dict) -> Optional[str]:
    """Drill into the nested image_versions2.candidates path.

    Falls back to first carousel item when the top-level has no image
    (carousels keep child media under `carousel_media`)."""
    candidates = (
        item.get('image_versions2', {}).get('candidates')
        if isinstance(item.get('image_versions2'), dict)
        else None
    )
    if not candidates and isinstance(item.get('carousel_media'), list):
        first = item['carousel_media'][0] if item['carousel_media'] else None
        if isinstance(first, dict):
            candidates = (
                first.get('image_versions2', {}).get('candidates')
                if isinstance(first.get('image_versions2'), dict)
                else None
            )
    if isinstance(candidates, list) and candidates:
        first = candidates[0]
        if isinstance(first, dict):
            url = first.get('url')
            if isinstance(url, str):
                return url
    return None


__all__ = ['PostMetrics', 'parse_hashtags', 'parse_post_from_api']
