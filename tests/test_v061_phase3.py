"""Fast offline unit tests for v0.6.1 Phase 3 Atlas 14 temporal distribution helpers."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import numpy as np


def _load_phase3_mod():
    path = Path(__file__).resolve().parent.parent / "pipeline" / "03_phase3_v061.py"
    spec = importlib.util.spec_from_file_location("p3_v061", path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


SAMPLE_ATLAS14_CSV = """Tabulated temporal distribution data
CUMULATIVE PERCENTAGES OF TOTAL PRECIPITATION FOR FIRST-QUARTILE CASES
hours,90%,50%,10%
0,0,0,0
0.5,1,5,10

CUMULATIVE PERCENTAGES OF TOTAL PRECIPITATION FOR ALL CASES
Time,Percent of occurrence
hours,90%,80%,50%,20%,10%
0,0,0,0,0,0
0.5,1.0,2.0,10.0,20.0,30.0
1.0,5.0,10.0,50.0,70.0,80.0
1.5,50.0,60.0,100.0,100.0,100.0
"""


def test_parse_atlas14_all_cases_median_and_build_hyetograph():
    mod = _load_phase3_mod()
    hours, pcts = mod.parse_atlas14_all_cases_median(SAMPLE_ATLAS14_CSV)
    assert np.allclose(hours, [0.0, 0.5, 1.0, 1.5])
    assert np.allclose(pcts, [0.0, 10.0, 50.0, 100.0])

    rows = mod.build_atlas14_hyetograph_rows(hours, pcts, total_depth_mm=100.0)
    assert len(rows) == 3
    assert rows[0]["time_min"] == 0.0
    assert np.isclose(rows[0]["depth_mm"], 10.0)
    assert np.isclose(rows[0]["intensity_mm_hr"], 20.0)
    assert np.isclose(rows[1]["depth_mm"], 40.0)
    assert np.isclose(rows[1]["intensity_mm_hr"], 80.0)
    assert np.isclose(sum(r["depth_mm"] for r in rows), 100.0)
