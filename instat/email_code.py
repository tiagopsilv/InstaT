"""
IMAP fetcher para códigos de verificação do Instagram/Meta.

Polling em caixa IMAP procurando e-mail recente do Instagram,
extraindo código numérico de 6 dígitos.

Usa apenas stdlib (imaplib + email).
"""
import email as _email
import imaplib
import re
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parsedate_to_datetime
from typing import Optional

from loguru import logger

# Regex conservador: 6 dígitos isolados, típico do IG.
_CODE_RE = re.compile(r'(?<!\d)(\d{6})(?!\d)')

# Remetentes conhecidos do Instagram/Meta.
_INSTAGRAM_SENDERS = (
    'instagram',
    'mail.instagram.com',
    'security@mail.instagram.com',
    'no-reply@mail.instagram.com',
    'facebookmail.com',
)

# Frases que aparecem em e-mails de verificação (multi-idioma).
_VERIFICATION_KEYWORDS = (
    'verification code',
    'confirmation code',
    'código de verificação',
    'código de confirmação',
    'security code',
    'código de segurança',
    'verify your account',
    'verifique sua conta',
    'confirm your identity',
    'confirme sua identidade',
    'use the following code',
    'use o código a seguir',
    'use o seguinte código',
    'tried to log in',
    'tentou fazer login',
)


@dataclass
class ImapConfig:
    """
    Config IMAP. Senha em `password` (recomendado: senha de app do provedor
    — Gmail/Outlook geram senhas de app específicas, não use a senha real).
    """
    host: str
    user: str
    password: str
    port: int = 993
    mailbox: str = 'INBOX'
    timeout: int = 60          # total em segundos para polling
    poll_interval: float = 3.0  # segundos entre tentativas
    since_minutes: int = 10     # busca e-mails dos últimos N minutos

    @classmethod
    def from_dict(cls, d) -> "ImapConfig":
        if d is None:
            return None
        if isinstance(d, ImapConfig):
            return d
        return cls(**d)


def _decode_str(raw) -> str:
    """Decode RFC 2047 headers com fallback seguro."""
    if raw is None:
        return ''
    try:
        parts = decode_header(raw)
    except Exception:
        return str(raw)
    out = []
    for text, enc in parts:
        if isinstance(text, bytes):
            try:
                out.append(text.decode(enc or 'utf-8', errors='ignore'))
            except Exception:
                out.append(text.decode('utf-8', errors='ignore'))
        else:
            out.append(text)
    return ''.join(out)


def _get_body(msg) -> str:
    """
    Extrai todo o texto de um email.Message — concatena text/plain + text/html
    (todas as partes), maximizando chance de pegar o código. Chamadores ainda
    podem aplicar regex de 6 dígitos sobre o retorno sem ambiguidade.
    """
    parts_text = []
    if msg.is_multipart():
        for part in msg.walk():
            ct = part.get_content_type()
            if ct not in ('text/plain', 'text/html'):
                continue
            try:
                payload = part.get_payload(decode=True)
                if payload is None:
                    continue
                charset = part.get_content_charset() or 'utf-8'
                parts_text.append(payload.decode(charset, errors='ignore'))
            except Exception:
                continue
    else:
        try:
            payload = msg.get_payload(decode=True)
            if payload:
                charset = msg.get_content_charset() or 'utf-8'
                parts_text.append(payload.decode(charset, errors='ignore'))
        except Exception:
            pass
    return '\n'.join(parts_text)


def _raw_message_text(raw_bytes: bytes) -> str:
    """Último recurso: decodifica os bytes brutos (útil quando parsing MIME falha)."""
    try:
        return raw_bytes.decode('utf-8', errors='ignore')
    except Exception:
        return ''


def _is_from_instagram(from_header: str) -> bool:
    f = (from_header or '').lower()
    return any(sender in f for sender in _INSTAGRAM_SENDERS)


def _looks_like_verification(subject: str, body: str) -> bool:
    text = (subject + '\n' + body).lower()
    return any(kw in text for kw in _VERIFICATION_KEYWORDS) or bool(_CODE_RE.search(body))


def _extract_code(subject: str, body: str) -> Optional[str]:
    """Procura primeiro no subject (comum: 'Your code: 123456'), depois no body."""
    m = _CODE_RE.search(subject or '')
    if m:
        return m.group(1)
    m = _CODE_RE.search(body or '')
    return m.group(1) if m else None


def _message_is_recent(msg, cutoff: datetime) -> bool:
    """Compara Date header com cutoff; se não parseável, assume recente."""
    date_header = msg.get('Date')
    if not date_header:
        return True
    try:
        msg_date = parsedate_to_datetime(date_header)
    except Exception:
        return True
    if msg_date.tzinfo is None:
        msg_date = msg_date.replace(tzinfo=timezone.utc)
    return msg_date >= cutoff


def fetch_instagram_code(
    config: ImapConfig,
    started_at: Optional[datetime] = None,
    _imaplib=imaplib,
    _time=time,
) -> Optional[str]:
    """
    Poll IMAP até encontrar um e-mail recente do Instagram com código.

    Args:
      config: credenciais IMAP.
      started_at: só considera e-mails recebidos após este timestamp (UTC).
        Default: now - config.since_minutes.
      _imaplib / _time: injetáveis para teste.

    Returns:
      String de 6 dígitos, ou None se timeout sem encontrar.
    """
    if started_at is None:
        started_at = datetime.now(timezone.utc) - timedelta(minutes=config.since_minutes)
    elif started_at.tzinfo is None:
        started_at = started_at.replace(tzinfo=timezone.utc)

    deadline = _time.time() + config.timeout
    since_date = started_at.strftime('%d-%b-%Y')
    logger.info(f"IMAP: polling {config.host} for Instagram code (since {since_date}, "
                f"timeout {config.timeout}s)")

    attempts = 0
    while _time.time() < deadline:
        attempts += 1
        try:
            conn = _imaplib.IMAP4_SSL(config.host, config.port)
            try:
                conn.login(config.user, config.password)
                conn.select(config.mailbox)
                # SINCE é de dia-granularidade; refinamos por Date header na iteração
                typ, data = conn.search(None, 'SINCE', since_date)
                if typ == 'OK' and data and data[0]:
                    ids = data[0].split()
                    # mais recentes primeiro
                    for mid in reversed(ids):
                        typ, msg_data = conn.fetch(mid, '(RFC822)')
                        if typ != 'OK' or not msg_data or not msg_data[0]:
                            continue
                        raw = msg_data[0][1]
                        msg = _email.message_from_bytes(raw)
                        from_h = _decode_str(msg.get('From'))
                        if not _is_from_instagram(from_h):
                            continue
                        if not _message_is_recent(msg, started_at):
                            continue
                        subject = _decode_str(msg.get('Subject'))
                        body = _get_body(msg)
                        # Último recurso: bytes brutos da mensagem (captura casos
                        # em que o parser MIME não extraiu o corpo corretamente).
                        raw_text = _raw_message_text(raw) if not body else ''
                        if not _looks_like_verification(subject, body + '\n' + raw_text):
                            continue
                        code = _extract_code(subject, body) or _extract_code('', raw_text)
                        if code:
                            logger.info(f"IMAP: code found after {attempts} attempt(s)")
                            return code
            finally:
                try:
                    conn.logout()
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"IMAP: attempt {attempts} failed: {e}")
        _time.sleep(config.poll_interval)

    logger.warning(f"IMAP: no code found after {attempts} attempt(s) / {config.timeout}s")
    return None
