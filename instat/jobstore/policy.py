"""Política de sanidade `sanity-v1` (roadmap §6.3.10).

Limites operacionais iniciais escolhidos pelo projeto, não documentados pelo
Instagram nem calibrados. Calibração gera `sanity-v2`; páginas antigas mantêm
a versão gravada.
"""
from typing import Any, Dict

SANITY_V1: Dict[str, Any] = {
    "version": "sanity-v1",
    "K": 3,                         # telas (scan) consecutivas sem membro novo, após a releitura
    "S": 3,                         # telas consecutivas com screen_hash idêntico
    "replay_ratio": 0.95,           # fração dos membros confiáveis anteriores ao segmento
    "frontier_confirm_screens": 2,  # telas consecutivas com membro novo para encerrar a releitura
    "counter_tol_abs_min": 2,       # membros
    "counter_tol_rel": 0.01,        # fração do limite superior do contador
    "counter_stale_s": 1800,        # segundos
    "max_rereads_per_pos": 2,       # releituras ADICIONAIS por posição
    "max_runs": 3,                  # execuções totais por job
}

AUTO_RESUME_REASONS = ("lease_lost", "technical_error")
HISTORY_LABEL = "histórico observado: não é lista atual nem prova de completude"

__all__ = ["AUTO_RESUME_REASONS", "HISTORY_LABEL", "SANITY_V1"]
