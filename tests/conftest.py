"""Configuração comum dos testes.

Testes marcados `real` tocam o Instagram de verdade com a conta de teste
local (docs/ROADMAP_MOBILE.md §7.1). Eles só rodam com INSTAT_REAL_TESTS=1;
sem isso são pulados, e o CI ainda os exclui por `-m "not real"`.
"""
import os

import pytest


def pytest_collection_modifyitems(config, items):
    if os.environ.get("INSTAT_REAL_TESTS") == "1":
        return
    skip = pytest.mark.skip(reason="teste real: defina INSTAT_REAL_TESTS=1 (opt-in)")
    for item in items:
        if "real" in item.keywords:
            item.add_marker(skip)
