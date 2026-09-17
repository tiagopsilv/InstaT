"""F5 — prova de sensibilidade dos testes (substitui a fraqueza do vermelho por ausência de módulo).

Para cada mutação: copia o repositório para um diretório temporário, aplica UMA
alteração defeituosa na implementação e roda os testes indicados. Esperado: falham.
"""
import os
import shutil
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))

MUTATIONS = [
    ("M1 fencing: commit sem checar validade da concessão", "instat/jobstore/store.py",
     "WHERE a.account=? AND a.lease_owner=? AND a.lease_gen=? AND a.lease_until>?\n                      AND r.run_id=? AND j.job_id=?\"\"\", (acct, owner, gen, t, run, job)",
     "WHERE a.account=? AND a.lease_owner=? AND a.lease_gen=? AND ?>=0\n                      AND r.run_id=? AND j.job_id=?\"\"\", (acct, owner, gen, t, run, job)",
     "tests/jobstore/test_e3.py tests/jobstore/test_f5_impl.py -k T06 or D05 or rejections"),
    ("M2 I7: horário lido antes do BEGIN IMMEDIATE", "instat/jobstore/store.py",
     '        self.c.execute("BEGIN IMMEDIATE")\n        return self.clock() if now is None else now',
     '        t = self.clock() if now is None else now\n        self.c.execute("BEGIN IMMEDIATE")\n        return t',
     "tests/jobstore/test_f5_impl.py -k now_is_read_after"),
    ("M3 idempotência: sem verificação de attempt_id repetido", "instat/jobstore/store.py",
     "            if prev is not None:\n                if prev[0] != run:",
     "            if prev is not None and False:\n                if prev[0] != run:",
     "tests/jobstore/test_e3.py tests/jobstore/test_f5_faults.py -k T01 or N04 or X01 or killed or fault_injections"),
    ("M4 spool: ack antes do commit", "instat/jobstore/worker.py",
     "            result = self._commit_entry(entry)\n            self._account(result)\n            if result.startswith((\"committed:\", \"duplicate:\")):\n                self.spool.ack(attempt_id)\n                continue",
     "            self.spool.ack(attempt_id)\n            result = self._commit_entry(entry)\n            self._account(result)\n            if result.startswith((\"committed:\", \"duplicate:\")):\n                continue",
     "tests/jobstore/test_f5_faults.py -k fault_injections or killed"),
    ("M5 limite de releituras desligado", "instat/jobstore/store.py",
     'if prior > POLICY["max_rereads_per_pos"]:', "if prior > 99:",
     "tests/jobstore/test_e3.py tests/jobstore/test_f5_impl.py -k L01 or L02 or rejections"),
    ("M6 liberação da conta sem sessão validada", "instat/jobstore/store.py",
     "        if session_validated is not True:\n            return \"rejected:sessao_nao_validada\"",
     "        if False:\n            return \"rejected:sessao_nao_validada\"",
     "tests/jobstore/test_e3.py tests/jobstore/test_f5_impl.py -k L04 or T12 or rejections"),
    ("M7 heartbeat: renova conta revogada", "instat/jobstore/store.py",
     "WHERE account=? AND lease_owner=? AND lease_gen=? AND lease_until>?\n                                      AND auth_state='ok'\"\"\"",
     "WHERE account=? AND lease_owner=? AND lease_gen=? AND lease_until>?\n                                      \"\"\"",
     "tests/jobstore/test_e3.py tests/jobstore/test_f5_impl.py -k T05 or heartbeat or rejections"),
    ("M8 backup permitido com transação aberta", "instat/jobstore/store.py",
     "        if src_conn.in_transaction:\n            raise BackupMisuse",
     "        if src_conn.in_transaction and False:\n            raise BackupMisuse",
     "tests/jobstore/test_f5_impl.py -k backup_refused"),
]


def main():
    only = sys.argv[1:]
    py = sys.executable
    rows = []
    for title, rel, old, new, selection in MUTATIONS:
        if only and title.split()[0] not in only:
            continue
        tmp = tempfile.mkdtemp(prefix="f5mut_")
        for d in ("instat", "tests"):
            shutil.copytree(os.path.join(ROOT, d), os.path.join(tmp, d),
                            ignore=shutil.ignore_patterns("__pycache__", "logs"))
        shutil.copy(os.path.join(ROOT, "pyproject.toml"), tmp)
        p = os.path.join(tmp, rel)
        src = open(p, encoding="utf-8").read()
        if src.count(old) != 1:
            rows.append((title, "MUTAÇÃO NÃO APLICADA", src.count(old)))
            continue
        open(p, "w", encoding="utf-8").write(src.replace(old, new))
        files, _, k = selection.partition(" -k ")
        cmd = [py, "-m", "pytest", *files.split(), "-k", k, "-p", "no:cacheprovider", "-q", "--tb=no"]
        try:
            r = subprocess.run(cmd, cwd=tmp, capture_output=True, text=True, timeout=180,
                               env={**os.environ, "PYTHONPATH": tmp})
        except subprocess.TimeoutExpired:
            rows.append((title, "DETECTADA (travou)", "teste não terminou em 180 s: o travamento do backup reapareceu"))
            shutil.rmtree(tmp, ignore_errors=True)
            continue
        summary = [ln for ln in r.stdout.splitlines() if " passed" in ln or " failed" in ln or "error" in ln]
        detected = r.returncode != 0
        rows.append((title, "DETECTADA" if detected else "NÃO DETECTADA", summary[-1] if summary else r.stdout[-200:]))
        shutil.rmtree(tmp, ignore_errors=True)
    for t, s, d in rows:
        print(f"{s:22} {t}\n    {d}")
    print(f"--- detectadas: {sum(1 for _, s, _ in rows if s.startswith('DETECTADA'))}/{len(rows)}")


if __name__ == "__main__":
    main()
