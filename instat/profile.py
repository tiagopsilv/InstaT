"""
Profile: handle leve para um perfil-alvo.

Criado por InstaExtractor.get_profile(profile_id): navega ao perfil
uma única vez, extrai metadados baratos (og tags + contadores) e
expõe get_followers/get_following que delegam ao extractor.
"""
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from .extractor import InstaExtractor


_COUNT_RE = re.compile(
    r'([\d.,KMBkmb]+)\s+Followers,\s+([\d.,KMBkmb]+)\s+Following,\s+([\d.,KMBkmb]+)\s+Posts',
    re.IGNORECASE,
)


@dataclass
class Profile:
    """
    Snapshot leve do cabeçalho do perfil + métodos de extração.

    Atributos populados em get_profile() com 1 navegação:
      - username, url
      - full_name, bio (se presentes)
      - followers_count, following_count, posts_count
      - is_private, is_verified
      - profile_pic_url
    Tudo opcional — campo ausente vira None.
    """
    username: str
    url: str
    full_name: Optional[str] = None
    bio: Optional[str] = None
    followers_count: Optional[int] = None
    following_count: Optional[int] = None
    posts_count: Optional[int] = None
    is_private: Optional[bool] = None
    is_verified: Optional[bool] = None
    profile_pic_url: Optional[str] = None

    # Não entra no __repr__ nem comparação; injetado pelo extractor.
    _extractor: Optional["InstaExtractor"] = None

    def __post_init__(self):
        # dataclass não tem hook, mas __post_init__ é chamado pós-__init__
        pass

    def get_followers(self, max_duration: Optional[float] = None) -> List[str]:
        """Delega ao extractor associado."""
        if self._extractor is None:
            raise RuntimeError("Profile not bound to an InstaExtractor")
        return self._extractor.get_followers(self.username, max_duration=max_duration)

    def get_following(self, max_duration: Optional[float] = None) -> List[str]:
        """Delega ao extractor associado."""
        if self._extractor is None:
            raise RuntimeError("Profile not bound to an InstaExtractor")
        return self._extractor.get_following(self.username, max_duration=max_duration)


def _parse_shorthand_count(text: str) -> Optional[int]:
    """Parse '1,894' / '1.9K' / '2M' para int. Retorna None se falhar."""
    if not text:
        return None
    t = text.strip().replace(',', '').replace(' ', '')
    m = re.fullmatch(r'([\d.]+)\s*([KMBkmb]?)', t)
    if not m:
        return None
    num_str, suffix = m.groups()
    try:
        n = float(num_str)
    except ValueError:
        return None
    mult = {'k': 1_000, 'K': 1_000, 'm': 1_000_000, 'M': 1_000_000,
            'b': 1_000_000_000, 'B': 1_000_000_000, '': 1}.get(suffix, 1)
    return int(n * mult)


def parse_profile_from_meta(og_description: str) -> dict:
    """
    Extrai counts do meta og:description do Instagram.

    Formato típico (varia por idioma):
      '1,894 Followers, 1,892 Following, 123 Posts - @tiagopsilv'
    Retorna dict com followers_count/following_count/posts_count (None se falhar).
    """
    if not og_description:
        return {}
    m = _COUNT_RE.search(og_description)
    if not m:
        return {}
    followers, following, posts = m.groups()
    return {
        'followers_count': _parse_shorthand_count(followers),
        'following_count': _parse_shorthand_count(following),
        'posts_count': _parse_shorthand_count(posts),
    }
