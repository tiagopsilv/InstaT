"""Os 43 cenários [E3] contra a implementação real (F5, critério 1).

API_AUSENTE conta como falha: a implementação precisa expor a API inteira.
"""
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import e3_scenarios as E3  # noqa: E402


@pytest.mark.parametrize("tid,title,fn", E3.TESTS, ids=[t[0] for t in E3.TESTS])
def test_e3_scenario(tid, title, fn):
    from instat.jobstore.store import JobStore
    try:
        ok, detail = fn(JobStore)
    except E3.ApiAusente as e:
        pytest.fail(f"{tid} API_AUSENTE: {e}")
    assert ok, f"{tid} {title}: {detail}"


def test_all_43_scenarios_present():
    assert len(E3.TESTS) == 43
