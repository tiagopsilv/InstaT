"""
DeviceProfile e AccountSlot — a identidade fixa por conta do modo mobile.

Invariante central do roadmap (§2): 1 conta = 1 DeviceProfile = 1 porta
sticky DataImpulse. Nada é compartilhado entre slots. Estas dataclasses
apenas MODELAM e VALIDAM essa identidade; a geração fiel de headers/TLS e
o uso real vêm nas Fases 3–5. `DeviceProfile.for_account` é determinístico:
a mesma conta sempre gera o mesmo device, para não "trocar de aparelho"
entre execuções (sinal forte de bot).
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Dict, Iterable, List, Tuple

# Porta sticky da DataImpulse (ver roadmap §1.3): 10000–20000.
STICKY_PORT_MIN = 10000
STICKY_PORT_MAX = 20000
# sessttl em minutos: 1–120 (máximo do sticky).
SESSTTL_MIN = 1
SESSTTL_MAX = 120

# Pequeno catálogo de aparelhos reais coerentes (modelo ↔ dpi ↔ resolução).
# Coerência importa mais que variedade (roadmap §1.5). Expandido nas Fases 3+.
_DEVICE_CATALOG: List[Dict[str, Any]] = [
    {"manufacturer": "samsung", "model": "SM-G991B", "device": "o1s",
     "dpi": 420, "resolution": (1080, 2400)},
    {"manufacturer": "Xiaomi", "model": "M2101K6G", "device": "sweet",
     "dpi": 440, "resolution": (1080, 2400)},
    {"manufacturer": "motorola", "model": "moto g(60)", "device": "hanoip",
     "dpi": 400, "resolution": (1080, 2400)},
    {"manufacturer": "Google", "model": "Pixel 6", "device": "oriole",
     "dpi": 420, "resolution": (1080, 2400)},
]

# País (cr do proxy) → locale/timezone coerentes (roadmap §1.5).
_COUNTRY_DEFAULTS: Dict[str, Tuple[str, str]] = {
    "br": ("pt_BR", "America/Sao_Paulo"),
    "us": ("en_US", "America/New_York"),
    "gb": ("en_GB", "Europe/London"),
    "pt": ("pt_PT", "Europe/Lisbon"),
}
_FALLBACK_LOCALE_TZ = ("en_US", "America/New_York")


def _digest(account: str) -> str:
    return hashlib.sha256(account.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class DeviceProfile:
    """Identidade de aparelho, imutável e determinística por conta.

    NÃO gera ainda o User-Agent/headers finais (isso é a HeaderFactory da
    Fase 3) — guarda os campos crus a partir dos quais eles serão montados.
    """
    account: str
    manufacturer: str
    model: str
    device: str
    dpi: int
    resolution: Tuple[int, int]
    locale: str
    timezone: str
    android_id: str        # 'android-<hex16>'
    device_id: str         # UUID determinístico
    phone_id: str          # UUID determinístico
    country: str

    @classmethod
    def for_account(cls, account: str, country: str = "br") -> "DeviceProfile":
        if not account:
            raise ValueError("account não pode ser vazio")
        country = (country or "br").lower()
        h = _digest(account)
        spec = _DEVICE_CATALOG[int(h[:8], 16) % len(_DEVICE_CATALOG)]
        locale, tz = _COUNTRY_DEFAULTS.get(country, _FALLBACK_LOCALE_TZ)

        def _uuid(salt: str) -> str:
            d = hashlib.sha256(f"{salt}:{account}".encode("utf-8")).hexdigest()
            return f"{d[0:8]}-{d[8:12]}-{d[12:16]}-{d[16:20]}-{d[20:32]}"

        return cls(
            account=account,
            manufacturer=str(spec["manufacturer"]),
            model=str(spec["model"]),
            device=str(spec["device"]),
            dpi=int(spec["dpi"]),
            resolution=tuple(spec["resolution"]),
            locale=locale,
            timezone=tz,
            android_id="android-" + h[:16],
            device_id=_uuid("device"),
            phone_id=_uuid("phone"),
            country=country,
        )


@dataclass
class AccountSlot:
    """Amarra conta + device + porta sticky (país/ASN fixos) num único slot.

    Um slot é a unidade que a orquestração (Fase 9) distribui entre
    containers Android. As invariantes abaixo impedem os dois erros que
    mais queimam conta no roadmap: compartilhar identidade entre contas e
    girar o IP no meio da sessão.
    """
    account: str
    device: DeviceProfile
    proxy_port: int
    country: str = "br"
    sessttl: int = SESSTTL_MAX
    # metadados opcionais do slot (nome do container, etc.)
    labels: Dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if not self.account:
            raise ValueError("account do slot não pode ser vazio")
        if not (STICKY_PORT_MIN <= self.proxy_port <= STICKY_PORT_MAX):
            raise ValueError(
                f"porta sticky {self.proxy_port} fora do intervalo "
                f"{STICKY_PORT_MIN}–{STICKY_PORT_MAX} (DataImpulse)"
            )
        if not (SESSTTL_MIN <= self.sessttl <= SESSTTL_MAX):
            raise ValueError(
                f"sessttl {self.sessttl} fora de {SESSTTL_MIN}–{SESSTTL_MAX} min"
            )
        if self.device.account != self.account:
            raise ValueError(
                f"device pertence a {self.device.account!r}, não a {self.account!r}"
            )


def validate_slots(slots: Iterable[AccountSlot]) -> None:
    """Garante que nenhum par de slots compartilha conta ou porta sticky.

    Levanta ValueError na primeira colisão. Chamado antes de subir o pool.
    """
    seen_accounts: Dict[str, int] = {}
    seen_ports: Dict[int, str] = {}
    for s in slots:
        if s.account in seen_accounts:
            raise ValueError(f"conta duplicada entre slots: {s.account!r}")
        if s.proxy_port in seen_ports:
            raise ValueError(
                f"porta sticky duplicada entre slots: {s.proxy_port} "
                f"({seen_ports[s.proxy_port]!r} e {s.account!r})"
            )
        seen_accounts[s.account] = 1
        seen_ports[s.proxy_port] = s.account
