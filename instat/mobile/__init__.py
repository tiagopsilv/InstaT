"""
instat.mobile — esqueleto do modo mobile (Android/Docker).

Ver docs/ROADMAP_MOBILE.md. Nesta fase (Fase 0) o pacote entrega apenas
os CONTRATOS: a identidade por conta (DeviceProfile/AccountSlot) e os
stubs de engine (MobileApiEngine/AndroidUiEngine) que travam a API pública
e servem de alvo para o TDD das fases seguintes.
"""
from .account_slot import (
    SESSTTL_MAX,
    SESSTTL_MIN,
    STICKY_PORT_MAX,
    STICKY_PORT_MIN,
    AccountSlot,
    DeviceProfile,
    validate_slots,
)
from .engines import MOBILE_ENGINE_NAMES, AndroidUiEngine, MobileApiEngine

__all__ = [
    "AccountSlot",
    "DeviceProfile",
    "validate_slots",
    "MobileApiEngine",
    "AndroidUiEngine",
    "MOBILE_ENGINE_NAMES",
    "STICKY_PORT_MIN",
    "STICKY_PORT_MAX",
    "SESSTTL_MIN",
    "SESSTTL_MAX",
]
