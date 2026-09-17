"""
HttpxEngine: extração via API privada mobile do Instagram (i.instagram.com/api/v1).
Dependência OPCIONAL — instalar com: pip install instat[httpx]

Mais rápida que Selenium/Playwright (~2min para 5k perfis) pois não usa browser.
Mais frágil: a API privada pode mudar sem aviso — endpoints e shapes não são
documentados oficialmente pelo Instagram.

Será o último fallback na cascata Selenium → Playwright → httpx.
"""
import time
from typing import Callable, Optional, Set

from loguru import logger

try:
    from instat.constants import human_delay
    from instat.engines.base import BaseEngine
    from instat.exceptions import (
        BlockedError,
        ChallengeError,
        ProfileNotFoundError,
        ProxyError,
        RateLimitError,
        RestrictedError,
        TransientError,
    )
    from instat.governor import Signal, classify_http
    from instat.post_metrics import PostMetrics, parse_post_from_api
    from instat.profile_summary import ProfileSummary
    from instat.session_cache import SessionCache
except ImportError:
    from constants import human_delay
    from engines.base import BaseEngine
    from exceptions import (  # type: ignore
        BlockedError,
        ChallengeError,
        ProfileNotFoundError,
        ProxyError,
        RateLimitError,
        RestrictedError,
        TransientError,
    )
    from governor import Signal, classify_http  # type: ignore
    from post_metrics import PostMetrics, parse_post_from_api  # type: ignore
    from profile_summary import ProfileSummary  # type: ignore
    from session_cache import SessionCache


MOBILE_UA = (
    "Instagram 219.0.0.12.117 Android (30/11; 420dpi; 1080x2340; "
    "samsung; SM-G973F; beyond1; exynos9820; pt_BR; 346138365)"
)

IG_APP_ID = '936619743392459'  # ID público do Instagram web app
BASE_URL = 'https://i.instagram.com/api/v1'
WEB_BASE = 'https://www.instagram.com'


class HttpxEngine(BaseEngine):
    """
    Engine via HTTP direto à API privada mobile do Instagram.
    Não usa browser. ~3x-4x mais rápida que Playwright.
    Frágil: endpoints podem mudar a qualquer momento.
    """

    @property
    def name(self) -> str:
        return 'httpx'

    @property
    def is_available(self) -> bool:
        try:
            import httpx  # noqa: F401
            return True
        except ImportError:
            return False

    def __init__(self, timeout: int = 30, proxy: Optional[str] = None):
        self._timeout = timeout
        self._proxy = proxy
        self._client = None
        self._csrftoken = None
        self._sessionid = None
        self._session_cache = SessionCache()

    def _build_client(self):
        """Constrói httpx.Client com UA e proxy opcional.

        Se `self._block_predictor` estiver atribuído (opt-in via
        InstaExtractor), registra um response hook que grava cada
        resposta no predictor. Zero impacto quando predictor é None.
        """
        import httpx
        headers = {
            'User-Agent': MOBILE_UA,
            'X-IG-App-ID': IG_APP_ID,
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
        }
        kwargs = {
            'headers': headers,
            'timeout': self._timeout,
            'follow_redirects': True,
        }
        if self._proxy:
            kwargs['proxies'] = self._proxy
        predictor = getattr(self, '_block_predictor', None)
        if predictor is not None:
            kwargs['event_hooks'] = {'response': [self._on_response]}
        return httpx.Client(**kwargs)

    def _on_response(self, response) -> None:
        """httpx response hook → BlockPredictor.record_request."""
        predictor = getattr(self, '_block_predictor', None)
        if predictor is None:
            return
        try:
            latency = (
                response.elapsed.total_seconds()
                if response.elapsed else 0.0
            )
            # content length — access through response.content triggers
            # the stream read; for hooks, we use headers when available
            # to avoid double-read issues.
            size = None
            cl = response.headers.get('content-length')
            if cl is not None:
                try:
                    size = int(cl)
                except (TypeError, ValueError):
                    size = None
            predictor.record_request(
                status_code=response.status_code,
                latency_s=latency,
                response_size=size,
                engine=self.name,
            )
        except Exception as e:
            logger.debug(f"block_predictor record_request failed: {e}")

    def login_with_cookies(self, cookies_list) -> bool:
        """
        Bypass form-login: accept cookies (ex.: extraídos de Selenium.get_cookies())
        e valida via /accounts/current_user/. Levanta BlockedError se inválidos.
        """
        self._client = self._build_client()
        for c in cookies_list:
            name = c.get('name')
            value = c.get('value')
            domain = c.get('domain', '.instagram.com')
            if name and value:
                self._client.cookies.set(name, value, domain=domain)
        try:
            r = self._client.get(f'{BASE_URL}/accounts/current_user/?edit=true')
        except Exception as e:
            raise BlockedError(f'{self.name}: cookie validation request failed: {e}') from e
        if r.status_code != 200:
            raise BlockedError(f'{self.name}: cookie validation status {r.status_code}')
        try:
            if r.json().get('status') != 'ok':
                raise BlockedError(f'{self.name}: cookies not authenticated')
        except BlockedError:
            raise
        except Exception as e:
            raise BlockedError(f'{self.name}: non-JSON validation response') from e
        self._sessionid = self._client.cookies.get('sessionid')
        self._csrftoken = self._client.cookies.get('csrftoken')
        logger.info(f'{self.name}: logged in via injected cookies')
        return True

    def login(self, username: str, password: str, **kwargs) -> bool:
        """
        Login via ajax endpoint. Tenta SessionCache primeiro, depois form login.
        """
        self._client = self._build_client()

        # 1. Tentar restaurar sessão via cookies cached
        cached_cookies = self._session_cache.load(username)
        if cached_cookies:
            try:
                for c in cached_cookies:
                    name = c.get('name')
                    value = c.get('value')
                    domain = c.get('domain', '.instagram.com')
                    if name and value:
                        self._client.cookies.set(name, value, domain=domain)
                r = self._client.get(f'{BASE_URL}/accounts/current_user/?edit=true')
                if r.status_code == 200:
                    try:
                        if r.json().get('status') == 'ok':
                            self._sessionid = self._client.cookies.get('sessionid')
                            self._csrftoken = self._client.cookies.get('csrftoken')
                            logger.info(f'{self.name}: session restored from cookie cache')
                            return True
                    except Exception:
                        pass
                logger.debug(f'{self.name}: cached cookies invalid, doing form login')
                self._client.cookies.clear()
            except Exception as e:
                logger.debug(f'{self.name}: cache restore failed: {e}')
                try:
                    self._client.cookies.clear()
                except Exception:
                    pass

        # 2. Form login — GET login page para pegar csrftoken
        try:
            self._client.get(f'{WEB_BASE}/accounts/login/')
        except Exception as e:
            raise BlockedError(f'{self.name}: failed to load login page: {e}') from e

        self._csrftoken = self._client.cookies.get('csrftoken')
        if not self._csrftoken:
            raise BlockedError(f'{self.name}: no csrftoken in cookies')

        # POST /accounts/login/ajax/
        ts = int(time.time())
        enc_password = f'#PWD_INSTAGRAM:0:{ts}:{password}'
        headers = {
            'X-CSRFToken': self._csrftoken,
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': f'{WEB_BASE}/accounts/login/',
            'Content-Type': 'application/x-www-form-urlencoded',
        }
        data = {
            'username': username,
            'enc_password': enc_password,
            'queryParams': '{}',
            'optIntoOneTap': 'false',
        }
        try:
            r = self._client.post(
                f'{WEB_BASE}/accounts/login/ajax/',
                data=data, headers=headers
            )
        except Exception as e:
            raise BlockedError(f'{self.name}: login request failed: {e}') from e

        if r.status_code == 429:
            raise RateLimitError(f'{self.name}: login rate limited')
        if r.status_code == 400:
            body = {}
            try:
                body = r.json()
            except Exception:
                pass
            message = (body.get('message') or '').lower() if isinstance(body, dict) else ''
            if 'checkpoint' in message or 'challenge' in message:
                raise BlockedError(f'{self.name}: checkpoint required')
            raise BlockedError(f'{self.name}: login failed: {body}')
        if r.status_code != 200:
            raise BlockedError(f'{self.name}: unexpected status {r.status_code}')

        try:
            body = r.json()
        except Exception as e:
            raise BlockedError(f'{self.name}: non-JSON login response') from e

        if not body.get('authenticated'):
            if body.get('two_factor_required'):
                raise BlockedError(f'{self.name}: 2FA required')
            raise BlockedError(f'{self.name}: authentication failed: {body}')

        self._sessionid = self._client.cookies.get('sessionid')
        logger.info(f'{self.name}: login successful')

        # Salvar cookies
        try:
            cookies_list = [
                {'name': c.name, 'value': c.value,
                 'domain': c.domain or '.instagram.com'}
                for c in self._client.cookies.jar
            ]
            self._session_cache.save(username, cookies_list)
        except Exception as e:
            logger.debug(f'{self.name}: failed to save cookies: {e}')

        return True

    def _resolve_user_id(self, username: str) -> str:
        """Resolve username → Instagram numeric user ID."""
        if not self._client:
            raise BlockedError(f'{self.name}: not logged in')
        try:
            r = self._client.get(
                f'{BASE_URL}/users/web_profile_info/',
                params={'username': username}
            )
        except Exception as e:
            raise self._request_error('profile info request failed', e) from e

        if r.status_code == 404:
            raise ProfileNotFoundError(f'{self.name}: user {username} not found')
        self._raise_for_status(r, 'user resolve')

        try:
            data = r.json()
            user_id = data.get('data', {}).get('user', {}).get('id')
            if not user_id:
                raise ProfileNotFoundError(f'{self.name}: no id in response')
            return str(user_id)
        except ProfileNotFoundError:
            raise
        except Exception as e:
            raise BlockedError(f'{self.name}: malformed profile info response') from e

    # ------------------------------------------------------ classificação
    bytes_sink: Optional[Callable[[int], None]] = None
    # Chamado antes de cada página; pode levantar GovernorStop (budget:*).
    budget_check: Optional[Callable[[], None]] = None

    _TRANSIENT_EXC_NAMES = frozenset({
        'TimeoutException', 'ConnectTimeout', 'ReadTimeout', 'WriteTimeout',
        'PoolTimeout', 'ConnectError', 'ReadError', 'WriteError',
        'RemoteProtocolError', 'NetworkError',
    })

    def _request_error(self, what: str, exc: BaseException) -> BlockedError:
        """Exceção de transporte → ProxyError / TransientError / BlockedError."""
        name = type(exc).__name__
        msg = f'{self.name}: {what}: {exc}'
        if name == 'ProxyError':
            c = classify_http(407, {}, str(exc))
            return ProxyError(msg, cause=c.cause if c else 'unknown')
        if isinstance(exc, (TimeoutError, ConnectionError)) or name in self._TRANSIENT_EXC_NAMES:
            return TransientError(msg)
        return BlockedError(msg)

    def _raise_for_status(self, r, what: str) -> None:
        """Classifica a resposta (roadmap §5.6) e informa bytes ao governador."""
        if self.bytes_sink is not None:
            try:
                size = r.headers.get('Content-Length') if r.headers else None
                n = int(size) if size is not None else len(r.content or b'')
            except Exception as e:
                logger.debug(f'{self.name}: bytes accounting failed: {e}')
            else:
                self.bytes_sink(n)
        if r.status_code == 200:
            return
        try:
            body = r.text or ''
        except Exception:
            body = ''
        c = classify_http(r.status_code, r.headers or {}, body,
                          reason_phrase=getattr(r, 'reason_phrase', '') or '')
        msg = f'{self.name}: {what}: status {r.status_code}'
        if c is None or c.signal is Signal.TECHNICAL:
            raise BlockedError(f'{msg} (unexpected)')
        if c.signal is Signal.PROXY:
            raise ProxyError(f'{msg} proxy {c.cause}', cause=c.cause or 'unknown')
        if c.signal is Signal.RATE_LIMITED:
            raise RateLimitError(f'{msg} rate limited', retry_after=c.retry_after)
        if c.signal is Signal.TRANSIENT:
            raise TransientError(msg, status=r.status_code, retry_after=c.retry_after)
        if c.signal is Signal.CHALLENGE:
            raise ChallengeError(f'{msg} challenge')
        raise RestrictedError(f'{msg} restricted ({c.cause})')

    def extract(self, profile_id: str, list_type: str,
                existing_profiles: Optional[Set[str]] = None,
                max_duration: Optional[float] = None,
                on_batch: Optional[Callable] = None,
                should_stop: Optional[Callable[[], bool]] = None,
                with_metadata: bool = False):
        """
        Pagina /friendships/{user_id}/{list_type}/ coletando usernames.
        50 perfis por request. Respeita max_duration, on_batch e
        should_stop (checado entre páginas — granularidade ~1 round-trip).

        with_metadata=False (default): retorna Set[str] de usernames
          (back-compat).
        with_metadata=True: retorna List[ProfileSummary] preservando a
          ordem da API e populando user_id, full_name, is_verified,
          is_private, is_business, profile_pic_url. follower_count NÃO
          vem deste endpoint — fica None. on_batch também recebe a lista
          de ProfileSummary no modo with_metadata.
        """
        if list_type not in ('followers', 'following'):
            raise ValueError(f"Invalid list_type: {list_type}")

        user_id = self._resolve_user_id(profile_id)
        endpoint = f'{BASE_URL}/friendships/{user_id}/{list_type}/'
        profiles: Set[str] = set(existing_profiles) if existing_profiles else set()
        # Modo with_metadata mantém uma lista ordenada paralela
        # (ordem original da API, dedup por username via `seen`).
        summaries: list = [] if with_metadata else []
        seen_usernames: Set[str] = set(profiles)
        start_time = time.perf_counter()
        max_id = None

        while True:
            if max_duration and (time.perf_counter() - start_time) > max_duration:
                logger.info(f'{self.name}: max_duration reached')
                break
            if should_stop and should_stop():
                logger.info(f'{self.name}: should_stop signal received')
                break
            if self.budget_check is not None:
                self.budget_check()

            params = {'count': 50}
            if max_id:
                params['max_id'] = max_id

            try:
                r = self._client.get(endpoint, params=params)
            except Exception as e:
                raise self._request_error('request failed', e) from e

            self._raise_for_status(r, 'followers page')

            try:
                data = r.json()
            except Exception:
                raise BlockedError(f'{self.name}: non-JSON response')

            users = data.get('users', [])
            added = 0
            for u in users:
                if not isinstance(u, dict):
                    continue
                username = u.get('username', '')
                if not username:
                    continue
                if username in seen_usernames:
                    continue
                profiles.add(username)
                seen_usernames.add(username)
                added += 1
                if with_metadata:
                    try:
                        summaries.append(ProfileSummary.from_api_user(u))
                    except ValueError:
                        # API shape drift — fall back to username-only.
                        summaries.append(
                            ProfileSummary.from_username(username)
                        )

            logger.debug(f'{self.name}: +{added} profiles (total: {len(profiles)})')

            if on_batch:
                try:
                    on_batch(summaries if with_metadata else profiles)
                except Exception as e:
                    logger.debug(f'{self.name}: on_batch error: {e}')

            max_id = data.get('next_max_id')
            if not max_id:
                logger.info(f'{self.name}: pagination complete')
                break

            human_delay(1.0, 0.3)

        return summaries if with_metadata else profiles

    def get_total_count(self, profile_id: str, list_type: str) -> Optional[int]:
        """Contagem via web_profile_info (edge_followed_by / edge_follow)."""
        if not self._client:
            return None
        if list_type not in ('followers', 'following'):
            return None
        try:
            r = self._client.get(
                f'{BASE_URL}/users/web_profile_info/',
                params={'username': profile_id}
            )
            if r.status_code != 200:
                return None
            data = r.json()
            user = data.get('data', {}).get('user', {})
            if list_type == 'followers':
                return user.get('edge_followed_by', {}).get('count')
            else:
                return user.get('edge_follow', {}).get('count')
        except Exception as e:
            logger.debug(f'{self.name}: get_total_count failed: {e}')
            return None

    def get_recent_posts(self, profile_id: str, limit: int = 5) -> "list[PostMetrics]":
        """Retorna até `limit` posts recentes via /feed/user/{user_id}/.

        Pagina a API privada (50 itens/página, mesma de extract) e
        decodifica via `parse_post_from_api`. Para na primeira página
        que satisfaz `limit` ou quando `next_max_id` desaparece.

        Levanta:
          ValueError se limit < 1
          ProfileNotFoundError se perfil não existe
          RateLimitError em 429
          BlockedError em 4xx (auth/compliance)
        """
        if limit < 1:
            raise ValueError(f"limit must be >= 1, got {limit}")
        if not self._client:
            raise BlockedError(f'{self.name}: not logged in')

        user_id = self._resolve_user_id(profile_id)
        endpoint = f'{BASE_URL}/feed/user/{user_id}/'
        out: list[PostMetrics] = []
        max_id: Optional[str] = None
        # 50 é o máximo prático da API; abaixo disso o IG ignora.
        page_size = min(50, max(limit, 12))

        while len(out) < limit:
            params: dict = {'count': page_size}
            if max_id:
                params['max_id'] = max_id
            try:
                r = self._client.get(endpoint, params=params)
            except Exception as e:
                raise self._request_error('feed request failed', e) from e
            if r.status_code != 404:
                self._raise_for_status(r, 'feed')
            if r.status_code == 404:
                raise ProfileNotFoundError(
                    f'{self.name}: user {profile_id} not found'
                )
            if r.status_code in (400, 403):
                raise BlockedError(
                    f'{self.name}: feed blocked at status {r.status_code}'
                )
            if r.status_code != 200:
                raise BlockedError(
                    f'{self.name}: feed unexpected status {r.status_code}'
                )
            try:
                data = r.json()
            except Exception:
                raise BlockedError(f'{self.name}: non-JSON feed response')

            items = data.get('items', [])
            for item in items:
                try:
                    out.append(parse_post_from_api(item))
                except ValueError as e:
                    logger.debug(
                        f'{self.name}: skipping malformed post item: {e}'
                    )
                    continue
                if len(out) >= limit:
                    break

            max_id = data.get('next_max_id')
            more_items = data.get('more_available', True)
            if not max_id or not more_items:
                break

            human_delay(0.8, 0.3)

        return out[:limit]

    def quit(self) -> None:
        """Fecha o httpx.Client. Safe se não logado."""
        try:
            if self._client:
                self._client.close()
        except Exception:
            pass
