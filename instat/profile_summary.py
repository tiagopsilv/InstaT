"""ProfileSummary — per-follower metadata snapshot.

Returned by `extract(..., with_metadata=True)` instead of bare username
strings. Captures every field IG's private API ships per user in the
`/friendships/{user_id}/{list_type}/` response, all `Optional` because
the API shape drifts and Selenium/Playwright DOM scraping fills only
`username` (everything else stays None).

Honest scope:
  IG's bulk followers/following endpoint does NOT include
  `follower_count` per follower — that would require an N+1 lookup
  (one `/users/web_profile_info/` call per username). The pipeline
  still needs `get_total_count` separately for counts; ProfileSummary
  delivers the cheap fields:

    - user_id (`pk` numeric, stable across username changes — primary
      win for SNA / `follower_following_relationship` table)
    - full_name, profile_pic_url
    - is_verified, is_private, is_business

Why a separate dataclass instead of extending `Profile`:
  - Profile is a "per-target" handle bound to an extractor (see
    profile.py) with bound methods like `get_followers()`. A summary
    of a discovered follower is structural data, not a handle.
  - Keeping them separate avoids loading method-resolution overhead
    on lists of thousands of summaries.
"""
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class ProfileSummary:
    """Cheap-to-fill per-follower metadata.

    `username` is the only mandatory field. Everything else is what the
    engine could capture; `None` means "engine did not provide", not
    "absent on the actual profile". To resolve missing counts call
    `extractor.get_total_count(username, ...)` or `get_profile(...)`
    explicitly.
    """
    username: str
    user_id: Optional[str] = None
    full_name: Optional[str] = None
    is_private: Optional[bool] = None
    is_verified: Optional[bool] = None
    is_business: Optional[bool] = None
    profile_pic_url: Optional[str] = None
    # follower_count NOT populated from the bulk endpoint by IG.
    # Field exists because some downstream consumers want a single
    # type they can hydrate later via get_total_count(); engines
    # never set this.
    follower_count: Optional[int] = None

    @classmethod
    def from_username(cls, username: str) -> "ProfileSummary":
        """Build a summary that only carries the username — what
        Selenium/Playwright DOM scraping returns. Lets pipelines treat
        every result uniformly as ProfileSummary regardless of engine."""
        return cls(username=username)

    @classmethod
    def from_api_user(cls, user: Any) -> "ProfileSummary":
        """Decode one entry from `/friendships/{id}/followers/` users
        list (or `/following/`). Tolerates field absence — the IG
        private API drops keys silently between version bumps."""
        if not isinstance(user, dict):
            raise ValueError(
                f"Expected dict, got {type(user).__name__}"
            )
        username = user.get('username')
        if not username or not isinstance(username, str):
            raise ValueError("API user item missing 'username'")
        pk = user.get('pk')
        return cls(
            username=username,
            user_id=str(pk) if pk is not None else None,
            full_name=_safe_str(user.get('full_name')),
            is_private=_safe_bool(user.get('is_private')),
            is_verified=_safe_bool(user.get('is_verified')),
            is_business=_safe_bool(user.get('is_business')),
            profile_pic_url=_safe_str(user.get('profile_pic_url')),
        )


def _safe_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    return str(value) if not isinstance(value, str) else value


def _safe_bool(value: Any) -> Optional[bool]:
    if value is None:
        return None
    return bool(value)


__all__ = ['ProfileSummary']
