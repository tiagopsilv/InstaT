"""F4 — sensibilidade dos testes por mutação (o vermelho só provou API ausente).

Copia instat+tests para diretório temporário, aplica UMA alteração defeituosa, roda os testes
selecionados com limite de tempo. Esperado: falham.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
STORE, SCHED, PAR = "instat/jobstore/store.py", "instat/scheduler.py", "instat/parallel.py"
IMPL = "tests/test_f4_scheduler.py"
DISP = "tests/test_f4_dispute.py"

MUTATIONS = [
    ("M1 acquire concede com concessão vigente", STORE,
     "                     AND (lease_until IS NULL OR lease_until<=?)\n                     AND NOT EXISTS",
     "                     AND (lease_until IS NULL OR lease_until<=? OR 1=1)\n                     AND NOT EXISTS",
     f"{DISP} -k 2-167"),
    ("M2 release_lease sem conferir dono e geração", STORE,
     "WHERE account=? AND lease_owner=? AND lease_gen=? AND lease_until>?\"\"\",\n                                 (t, acct, owner, gen, t))",
     "WHERE account=? AND ?<>'' AND ?>=0 AND lease_until>?\"\"\",\n                                 (t, acct, owner, gen, t))",
     f"{IMPL} -k release_lease_only"),
    ("M3 challenge não marca a conta", SCHED,
     "            self._mark(st, account, \"needs_attention\", e)\n",
     "            pass\n",
     f"{IMPL} -k challenge_or_restriction or challenge_stops"),
    ("M4 plan_workers sem limite de contas", SCHED,
     "        return max(0, min(int(requested), len(self.eligible_accounts(now))))",
     "        return max(0, int(requested))",
     f"{IMPL} -k plan_workers or one_account_per_worker"),
    ("M5 parallel_extract sem limite de workers", PAR,
     "        workers = capacity\n",
     "        pass\n",
     f"{IMPL} -k never_shares"),
    ("M6 commit sem checar validade da concessão (fencing)", STORE,
     "WHERE a.account=? AND a.lease_owner=? AND a.lease_gen=? AND a.lease_until>?\n                      AND r.run_id=? AND j.job_id=?\"\"\", (acct, owner, gen, t, run, job)",
     "WHERE a.account=? AND a.lease_owner=? AND a.lease_gen=? AND ?>=0\n                      AND r.run_id=? AND j.job_id=?\"\"\", (acct, owner, gen, t, run, job)",
     f"{DISP} -k 2-167"),
    ("M7 conta em needs_attention concedida (sem checar estado nem liberação)", STORE,
     "                   WHERE account=? AND auth_state='ok'\n"
     "                     AND (restricted_until IS NULL OR restricted_until<=?)\n"
     "                     AND (restricted_at IS NULL OR (auth_validated_at IS NOT NULL AND auth_validated_at>=restricted_at))\n"
     "                     AND (lease_until IS NULL OR lease_until<=?)\n"
     "                     AND NOT EXISTS",
     "                   WHERE account=? AND 1=1\n"
     "                     AND (restricted_until IS NULL OR restricted_until<=?)\n"
     "                     AND 1=1\n"
     "                     AND (lease_until IS NULL OR lease_until<=?)\n"
     "                     AND NOT EXISTS",
     f"{IMPL} {DISP} -k challenge_or_restriction or 2-167"),
    ("M8 scheduler não libera a concessão ao sair", SCHED,
     "            st.release_lease(tok, now=None)\n",
     "            pass\n",
     f"{IMPL} -k lease_releases_on_success"),
]


def main():
    only = sys.argv[1:]
    rows = []
    for title, rel, old, new, selection in MUTATIONS:
        if only and title.split()[0] not in only:
            continue
        tmp = tempfile.mkdtemp(prefix="f4mut_")
        for d in ("instat", "tests"):
            shutil.copytree(os.path.join(ROOT, d), os.path.join(tmp, d),
                            ignore=shutil.ignore_patterns("__pycache__", "logs", "e2e"))
        shutil.copy(os.path.join(ROOT, "pyproject.toml"), tmp)
        p = os.path.join(tmp, rel)
        src = open(p, encoding="utf-8").read()
        if src.count(old) != 1:
            rows.append((title, "MUTAÇÃO NÃO APLICADA", f"ocorrências={src.count(old)}"))
            continue
        open(p, "w", encoding="utf-8").write(src.replace(old, new))
        files, _, k = selection.partition(" -k ")
        cmd = [sys.executable, "-m", "pytest", *files.split(), "-k", k, "-p", "no:cacheprovider", "-q", "--tb=no"]
        try:
            r = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, timeout=300,
                               env={**os.environ, "PYTHONPATH": tmp})
            summary = [ln for ln in r.stdout.splitlines() if " passed" in ln or " failed" in ln or "error" in ln]
            rows.append((title, "DETECTADA" if r.returncode != 0 else "NÃO DETECTADA",
                         summary[-1] if summary else r.stdout[-200:]))
        except subprocess.TimeoutExpired:
            rows.append((title, "DETECTADA (travou)", "não terminou em 300 s"))
        shutil.rmtree(tmp, ignore_errors=True)
    for t, s, d in rows:
        print(f"{s:22} {t}\n    {d}")
    print(f"--- detectadas: {sum(1 for _, s, _ in rows if s.startswith('DETECTADA'))}/{len(rows)}")


if __name__ == "__main__":
    main()
