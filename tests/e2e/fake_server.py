"""
Servidor HTTP fake que simula o Instagram para testes E2E.
Usa apenas stdlib (http.server + threading + socket).

Endpoints:
  GET  /accounts/login/           -> form de login
  POST /accounts/login/           -> valida creds, set cookie sessionid, redirect /
  GET  /                          -> home
  GET  /{username}/               -> perfil com links followers/following
  GET  /{username}/followers/     -> modal com perfis
  GET  /{username}/following/     -> idem para following
  GET  /challenge/...             -> página de challenge
  GET  /_fake/set_mode?mode=X     -> override global
                                     (normal|ratelimit|block|challenge)
  STATE.revoked_sessions               -> sessionids que o servidor rejeita
"""
import http.server
import socket
import socketserver
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlparse

TEMPLATES_DIR = Path(__file__).parent / 'templates'


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


class FakeInstagramState:
    """Estado mutável do servidor (singleton por instância)."""

    def __init__(self):
        self.valid_credentials = {'testuser': 'testpass'}
        self.profiles_db = {
            'target': {f'user_{i:03d}' for i in range(100)},
        }
        self.mode = 'normal'  # 'normal' | 'ratelimit' | 'block' | 'challenge'
        self.session_cookies = {}
        self.revoked_sessions = set()
        self.login_posts = 0


STATE = FakeInstagramState()


class FakeInstagramHandler(http.server.SimpleHTTPRequestHandler):

    def log_message(self, *args, **kw):
        # Silencia logs do servidor para não poluir pytest output
        pass

    def _send_html(self, body: str, status: int = 200, headers=None):
        raw = body.encode('utf-8')
        self.send_response(status)
        self.send_header('Content-Type', 'text/html; charset=utf-8')
        self.send_header('Content-Length', str(len(raw)))
        for k, v in (headers or {}).items():
            self.send_header(k, v)
        self.end_headers()
        self.wfile.write(raw)

    def _render(self, name: str, **kw) -> str:
        tpl = (TEMPLATES_DIR / name).read_text(encoding='utf-8')
        return tpl.format(**kw)

    def _generate_profile_spans(self, usernames: list) -> str:
        return "\n".join(
            f'<span class="_ap3a _aaco _aacw _aacx _aad7 _aade">{u}</span>'
            for u in usernames
        )

    def _cookie(self, name: str):
        from http.cookies import SimpleCookie
        jar = SimpleCookie(self.headers.get('Cookie', ''))
        return jar[name].value if name in jar else None

    def _redirect(self, location: str):
        self.send_response(302)
        self.send_header('Location', location)
        self.send_header('Content-Length', '0')
        self.end_headers()

    def do_GET(self):
        url = urlparse(self.path)
        path = url.path

        # Mode control endpoint
        if path == '/_fake/set_mode':
            qs = parse_qs(url.query)
            STATE.mode = qs.get('mode', ['normal'])[0]
            self._send_html('ok')
            return

        # Mode: block (everything except login returns 403)
        if STATE.mode == 'block' and path != '/accounts/login/':
            self._send_html('<html><body>Blocked</body></html>', status=403)
            return

        # Mode: ratelimit (everything returns 429)
        if STATE.mode == 'ratelimit':
            self._send_html('<html><body>Rate limited</body></html>', status=429)
            return

        if path == '/accounts/login/':
            self._send_html(self._render('login.html'))
            return

        # Sessão revogada/expirada no servidor → volta ao login
        if path == '/' and self._cookie('sessionid') in STATE.revoked_sessions:
            self._redirect('/accounts/login/')
            return

        # Mode: challenge (sessão restaurada cai num challenge)
        if STATE.mode == 'challenge' and path == '/':
            self._redirect('/challenge/abc123/')
            return

        if path.startswith('/challenge/'):
            self._send_html(
                '<html><head><title>Challenge</title></head><body>'
                '<h1>Confirme que é você</h1>'
                '<p>Precisamos verificar esta conta (fake server).</p>'
                '</body></html>'
            )
            return

        if path == '/':
            self._send_html('<html><body><h1>Feed</h1></body></html>')
            return

        parts = [p for p in path.strip('/').split('/') if p]

        # GET /{profile_id}/
        if len(parts) == 1:
            profile_id = parts[0]
            usernames = STATE.profiles_db.get(profile_id, set())
            if not usernames:
                # Perfil não existe → 404 mas ainda render minimal page
                self._send_html(
                    f'<html><body><h2>{profile_id}</h2>'
                    '<p>Profile not found</p></body></html>',
                    status=404
                )
                return
            body = self._render(
                'profile.html',
                profile_id=profile_id,
                followers_count=len(usernames),
                following_count=len(usernames),
            )
            self._send_html(body)
            return

        # GET /{profile_id}/followers/ or /{profile_id}/following/
        if len(parts) == 2 and parts[1] in ('followers', 'following'):
            profile_id = parts[0]
            usernames = sorted(STATE.profiles_db.get(profile_id, set()))
            spans = self._generate_profile_spans(usernames)
            body = self._render('modal.html', profile_spans=spans)
            self._send_html(body)
            return

        self._send_html('<html><body>Not found</body></html>', status=404)

    def do_POST(self):
        url = urlparse(self.path)
        if url.path == '/accounts/login/':
            length = int(self.headers.get('Content-Length', '0'))
            body = self.rfile.read(length).decode('utf-8')
            params = parse_qs(body)
            username = params.get('username', [''])[0]
            password = params.get('password', [''])[0]
            STATE.login_posts += 1
            if STATE.valid_credentials.get(username) == password:
                sessionid = f'sess_{username}_{STATE.login_posts}'
                STATE.session_cookies[sessionid] = username
                self.send_response(302)
                # SameSite explícito: em HTTP puro o Firefox devolve
                # SameSite=None sem `secure` e recusa re-adicionar o cookie.
                self.send_header('Set-Cookie', f'sessionid={sessionid}; Path=/; SameSite=Lax')
                self.send_header('Set-Cookie', f'ds_user_id={sum(map(ord, username))}; Path=/; SameSite=Lax')
                self.send_header('Location', '/')
                self.end_headers()
                return
            # Invalid creds: stay on login page
            self._send_html(self._render('login.html'), status=200)
            return
        self._send_html('Not found', status=404)


class FakeInstagramServer:
    """Wrapper que roda o servidor em thread e expõe URL base."""

    def __init__(self):
        self.port = _free_port()
        socketserver.ThreadingTCPServer.allow_reuse_address = True
        self.httpd = socketserver.ThreadingTCPServer(
            ('127.0.0.1', self.port), FakeInstagramHandler
        )
        self._thread = None

    @property
    def base_url(self) -> str:
        return f'http://127.0.0.1:{self.port}'

    def start(self):
        self._thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self):
        try:
            self.httpd.shutdown()
        except Exception:
            pass
        try:
            self.httpd.server_close()
        except Exception:
            pass
