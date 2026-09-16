"""Política de versão de Python (roadmap F0, decisão de 16/09/2026: mínimo 3.12).

Mantém coerentes pyproject, CI, README e Dockerfile. Versões acima de 3.12 só
aparecem nos classificadores se também estiverem na matriz de CI.
"""
import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[1]
PYPROJECT = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
CI = (ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8")
MIN = (3, 12)


def _v(text):
    return tuple(int(x) for x in text.split("."))


def _classifier_versions():
    return [c.rsplit("::", 1)[1].strip() for c in PYPROJECT["project"]["classifiers"]
            if re.fullmatch(r"Programming Language :: Python :: 3\.\d+", c)]


def _ci_matrix_versions():
    m = re.search(r"python-version:\s*\[([^\]]+)\]", CI)
    assert m, "matriz de python-version ausente no CI"
    return [v.strip().strip("'\"") for v in m.group(1).split(",")]


def test_requires_python_is_312():
    assert PYPROJECT["project"]["requires-python"] == ">=3.12"


def test_classifiers_start_at_312():
    versions = _classifier_versions()
    assert "3.12" in versions
    assert all(_v(v) >= MIN for v in versions), versions


def test_declared_versions_are_tested_in_ci():
    assert set(_classifier_versions()) <= set(_ci_matrix_versions())


def test_ci_versions_respect_minimum():
    matrix = _ci_matrix_versions()
    singles = re.findall(r"python-version:\s*[\"'](\d+\.\d+)[\"']", CI)
    assert "3.12" in matrix
    assert all(_v(v) >= MIN for v in matrix + singles), (matrix, singles)


def test_ci_installs_optional_extras_used_by_tests():
    assert re.search(r'pip install -e "\.\[dev,httpx,playwright\]"', CI)


def test_ci_smoke_tests_installed_wheel_outside_checkout():
    assert "instat.mobile.engines" in CI


def test_tool_targets_312():
    assert PYPROJECT["tool"]["ruff"]["target-version"] == "py312"
    assert PYPROJECT["tool"]["mypy"]["python_version"] == "3.12"


def test_readme_states_312():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "python-3.12%2B" in readme
    assert "Python ≥ 3.12" in readme
    assert "3.9" not in readme


def test_dockerfile_uses_312_or_newer():
    m = re.search(r"^FROM python:(\d+\.\d+)", (ROOT / "Dockerfile").read_text(encoding="utf-8"), re.M)
    assert m and _v(m.group(1)) >= MIN
