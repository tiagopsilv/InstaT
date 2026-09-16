"""Diagnóstico isolado: Connection.backup() com transação de escrita aberta.

Cada variante roda num subprocesso com limite de 10 s, para um travamento não
bloquear as demais.
"""
import os
import sqlite3
import subprocess
import sys
import tempfile

VARIANT = sys.argv[1] if len(sys.argv) > 1 else None


def prep():
    d = tempfile.mkdtemp(prefix="diag_")
    p = os.path.join(d, "src.sqlite")
    c = sqlite3.connect(p, isolation_level=None)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE t(x)")
    c.executemany("INSERT INTO t VALUES(?)", [(i,) for i in range(50)])
    c.close()
    return p


def run_variant(name):
    p = prep()
    dst = sqlite3.connect(p + ".bak")
    if name == "mesma_conexao_begin_immediate":
        c = sqlite3.connect(p, isolation_level=None)
        c.execute("BEGIN IMMEDIATE")
        n = c.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        c.backup(dst, pages=-1)
        c.execute("COMMIT")
    elif name == "coordenador_immediate_backup_por_leitor":
        coord = sqlite3.connect(p, isolation_level=None)
        coord.execute("BEGIN IMMEDIATE")             # exclui escritores
        n = coord.execute("SELECT COUNT(*) FROM t").fetchone()[0]
        reader = sqlite3.connect(p, isolation_level=None)
        reader.backup(dst, pages=-1)                  # outra conexão, só leitura
        coord.execute("COMMIT")
    elif name == "leitor_snapshot_begin_deferred":
        reader = sqlite3.connect(p, isolation_level=None)
        reader.execute("BEGIN")
        n = reader.execute("SELECT COUNT(*) FROM t").fetchone()[0]   # fixa o snapshot WAL
        reader.backup(dst, pages=-1)
        reader.execute("COMMIT")
    else:
        raise SystemExit(f"variante desconhecida {name}")
    m = dst.execute("SELECT COUNT(*) FROM t").fetchone()[0]
    print(f"OK origem={n} backup={m} iguais={n == m}")


if VARIANT:
    run_variant(VARIANT)
else:
    for v in ["mesma_conexao_begin_immediate",
              "coordenador_immediate_backup_por_leitor",
              "leitor_snapshot_begin_deferred"]:
        try:
            r = subprocess.run([sys.executable, __file__, v], capture_output=True, text=True, timeout=10)
            out = (r.stdout.strip() or r.stderr.strip().splitlines()[-1]) if (r.stdout or r.stderr) else "(sem saída)"
            print(f"{v:42} → {out}")
        except subprocess.TimeoutExpired:
            print(f"{v:42} → TRAVOU (timeout 10 s)")
