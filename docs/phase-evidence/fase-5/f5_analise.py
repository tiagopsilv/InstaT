"""F5 passo 1 — análise medida do armazenamento atual (fakes, sem rede).

A1 união entre execuções apresentada como lista atual
A2 repetição e releitura indistinguíveis (sem identidade de tentativa)
A3 sem qualidade: página vazia/suspeita não se distingue
A4 sem posse: dois escritores sem concessão gravam
A5 sem proveniência: não se sabe de qual execução veio cada membro
"""
import os
import sqlite3
import sys
import tempfile
import threading

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.abspath(os.path.join(HERE, "..", "..", "..")))

from instat.persistent_store import PersistentStore  # noqa: E402

d = tempfile.mkdtemp()
s = PersistentStore(os.path.join(d, "p.db"))

print("=== A1 execução 1 = Ana/Bruno; execução 2 = Ana/Carla")
s.add_batch("alvo", "followers", ["ana", "bruno"], "conta1")
s.add_batch("alvo", "followers", ["ana", "carla"], "conta1")
print(f"  get_all → {s.get_all('alvo', 'followers')} (Bruno ausente na execução 2 continua na 'lista')")

print("=== A2 mesma página gravada duas vezes (retry após crash) × releitura legítima")
n1 = s.add_batch("alvo", "followers", ["dani"], "conta1")
n2 = s.add_batch("alvo", "followers", ["dani"], "conta1")
print(f"  1ª={n1} novo(s); 2ª={n2} novo(s); nenhuma identidade de tentativa: repetição e releitura são iguais")

print("=== A3 página vazia gravada")
n3 = s.add_batch("alvo", "followers", [], "conta1")
print(f"  add_batch([]) → {n3}; sem registro de página, qualidade ou motivo")

print("=== A4 dois escritores sem concessão")
errs = []


def writer(acct, names):
    try:
        PersistentStore(s.path).add_batch("alvo2", "followers", names, acct)
    except Exception as e:  # noqa: BLE001
        errs.append(e)


ts = [threading.Thread(target=writer, args=(f"conta{i}", [f"u{i}_{j}" for j in range(50)])) for i in range(2)]
[t.start() for t in ts]
[t.join() for t in ts]
print(f"  contas gravando o mesmo alvo ao mesmo tempo: {s.stats('alvo2', 'followers')['source_accounts']}, "
      f"erros={len(errs)} (nenhuma exclusividade)")

print("=== A5 proveniência")
cols = [r[1] for r in sqlite3.connect(s.path).execute("PRAGMA table_info(profiles_seen)")]
print(f"  colunas: {cols}")
print(f"  existe run/page/attempt/quality/policy_version: "
      f"{any(c in cols for c in ('run_id', 'page_id', 'attempt_id', 'quality', 'policy_version'))}")
tables = [r[0] for r in sqlite3.connect(s.path).execute("SELECT name FROM sqlite_master WHERE type='table'")]
print(f"  tabelas: {tables}")
