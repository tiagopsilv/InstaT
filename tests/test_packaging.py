"""Empacotamento (roadmap F0).

O wheel precisa conter todos os subpacotes de `instat` e nenhum dado de
diagnóstico. O build roda numa cópia do projeto com um `cookies.json` falso
plantado em `instat/logs/diagnostics/`, para provar a exclusão mesmo quando a
cópia de trabalho tem esses arquivos.
"""
import pathlib
import shutil
import subprocess
import sys
import zipfile

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
IGNORE = shutil.ignore_patterns(".git", "build", "dist", "*.egg-info", ".venv*", "venv*",
                                "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache")


def _source_packages(root):
    return sorted(
        str(p.parent.relative_to(root)).replace("\\", "/")
        for p in (root / "instat").rglob("__init__.py")
    )


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    pytest.importorskip("build")
    src = tmp_path_factory.mktemp("src") / "InstaT"
    shutil.copytree(ROOT, src, ignore=IGNORE)
    planted = src / "instat" / "logs" / "diagnostics" / "20260101_000000_fake"
    planted.mkdir(parents=True)
    (planted / "cookies.json").write_text('[{"name": "sessionid", "value": "FAKE"}]', encoding="utf-8")
    (planted / "metadata.json").write_text("{}", encoding="utf-8")
    out = tmp_path_factory.mktemp("dist")
    proc = subprocess.run([sys.executable, "-m", "build", "--wheel", "--outdir", str(out)],
                          cwd=src, capture_output=True, text=True)
    assert proc.returncode == 0, (proc.stdout + proc.stderr)[-3000:]
    wheels = list(out.glob("*.whl"))
    assert len(wheels) == 1, wheels
    return src, zipfile.ZipFile(wheels[0])


def test_wheel_contains_every_source_package(built):
    src, wheel = built
    in_wheel = sorted({n.rsplit("/", 1)[0] for n in wheel.namelist() if n.endswith("__init__.py")})
    assert in_wheel == _source_packages(src)


def test_wheel_ships_selectors_json(built):
    _, wheel = built
    assert "instat/config/selectors.json" in wheel.namelist()


def test_wheel_excludes_logs_and_cookies(built):
    _, wheel = built
    leaked = [n for n in wheel.namelist()
              if n.startswith("instat/logs/") or n.endswith("cookies.json") or "/diagnostics/" in n]
    assert leaked == []


def test_wheel_requires_python_312(built):
    _, wheel = built
    meta = next(n for n in wheel.namelist() if n.endswith(".dist-info/METADATA"))
    assert "Requires-Python: >=3.12" in wheel.read(meta).decode("utf-8").splitlines()
