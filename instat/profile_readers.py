"""Leitores de metadados de perfil.

`read_profile_from_driver` é o código que vivia em `InstaExtractor.get_profile`
(navega 1 vez e lê og tags + heurísticas do DOM), movido sem mudar a lógica.
Usado por `SeleniumEngine.get_profile_info` e pelo caminho legado de
`get_profile` para engines/objetos que só expõem `_driver`.
"""
import re
from typing import Any, Optional

try:
    from instat.profile import parse_profile_from_meta
    from instat.profile_info import ProfileInfo
except ImportError:  # pragma: no cover
    from profile import parse_profile_from_meta  # type: ignore

    from profile_info import ProfileInfo  # type: ignore

_BIO_JS = r"""
    // Strategy 1: header section structural scan
    const h = document.querySelector('header section');
    if (h) {
        const candidates = h.querySelectorAll('div, span, h1');
        for (const el of candidates) {
            const t = (el.textContent || '').trim();
            if (t.length < 20 || t.length > 1000) continue;
            if (el.querySelector('a')) continue;
            if (/followers|following|posts|seguidores|seguindo|publicaç/i.test(t)) continue;
            return t;
        }
    }
    // Strategy 2: inline JSON via window.__additionalDataLoaded
    // / SharedData fallback. IG stores user.biography directly.
    const scripts = document.querySelectorAll('script');
    for (const s of scripts) {
        const txt = s.textContent || '';
        const m = txt.match(/"biography":\s*"((?:[^"\\]|\\.){0,1000})"/);
        if (m) {
            try {
                const raw = m[1].replace(/\\n/g, '\n').replace(/\\"/g, '"').replace(/\\\\/g, '\\');
                if (raw.length >= 1) return raw;
            } catch (e) { /* ignore */ }
        }
    }
    // Strategy 3: permissive spans
    const spans = document.querySelectorAll('span[dir="auto"]');
    for (const s of spans) {
        const t = (s.textContent || '').trim();
        if (t.length < 10 || t.length > 1000) continue;
        if (s.closest('a')) continue;
        if (/^\d/.test(t) && /followers|following|posts/i.test(t)) continue;
        return t;
    }
    return null;
"""


def read_profile_from_driver(driver: Any, profile_id: str) -> ProfileInfo:
    url = f"https://www.instagram.com/{profile_id}/"
    driver.get(url)

    def _meta(prop: str) -> str:
        try:
            el = driver.find_element('css selector', f'meta[property="{prop}"]')
            return el.get_attribute('content') or ''
        except Exception:
            return ''

    og_desc = _meta('og:description')
    og_title = _meta('og:title')
    og_image = _meta('og:image')

    # Bio: 3 estratégias (IG muda o layout); todas degradam para None.
    bio: Optional[str] = None
    try:
        bio = driver.execute_script(_BIO_JS)
        if bio is not None and not isinstance(bio, str):
            bio = None
        elif isinstance(bio, str) and not bio.strip():
            bio = None
    except Exception:
        bio = None

    counts = parse_profile_from_meta(og_desc)

    # og:title: "Full Name (@username) • Instagram photos and videos"
    full_name = None
    if og_title:
        m = re.match(r'^(.*?)\s*\(@', og_title)
        if m:
            full_name = m.group(1).strip() or None

    is_verified = None
    try:
        is_verified = bool(driver.execute_script(
            "return !!document.querySelector('svg[aria-label=\"Verified\"]');"
        ))
    except Exception:
        pass

    is_private = None
    try:
        is_private = bool(driver.execute_script(
            "return document.body.innerText.toLowerCase().includes"
            "('this account is private') || "
            "document.body.innerText.toLowerCase().includes('conta privada');"
        ))
    except Exception:
        pass

    return ProfileInfo(
        username=profile_id,
        url=url,
        full_name=full_name,
        bio=bio,
        followers_count=counts.get('followers_count'),
        following_count=counts.get('following_count'),
        posts_count=counts.get('posts_count'),
        is_private=is_private,
        is_verified=is_verified,
        profile_pic_url=og_image or None,
    )


__all__ = ["read_profile_from_driver"]
