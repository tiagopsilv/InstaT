"""Metadados do cabeçalho de um perfil, independentes de como foram lidos.

Contrato de dados entre engines (Selenium, httpx, e nas F7/F8 Android UI e API
móvel) e `InstaExtractor.get_profile`, que o converte em `Profile`.
"""
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class ProfileInfo:
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


__all__ = ["ProfileInfo"]
