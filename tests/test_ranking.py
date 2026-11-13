"""형상 순위(ranking.csv): 세트마다 다른 변수, 발산 판정 케이스."""
import csv
import json
import textwrap

from aero_automation.cases import build_cases
from aero_automation.config import load_config
from aero_automation.results import collect, geometry_keys, rank_geometries


def make_project(tmp_path):
    """2단 세트가 먼저, 3단 세트에만 flap2_* 변수가 있는 프로젝트 (예제 flap_optimization과 같은 구조)."""
    (tmp_path / "journal.jou").write_text("AOA=${aoa}\n", encoding="utf-8")
    (tmp_path / "project.toml").write_text(textwrap.dedent("""
        [project]
        template = "journal.jou"

        [solver]
        kind = "custom"
        command = ["unused"]

        [constants]
        main_airfoil = "2412"
        main_chord = 300
        flap1_airfoil = "4412"
        flap1_chord = 100
        flap1_gap = 6
        flap1_overlap = 5

        [cases.sweep]
        aoa = [0, 4]

        [[cases.set]]
        label = "2el"
        name = "2el_d{flap1_deflection}_a{aoa}"
        [cases.set.sweep]
        flap1_deflection = [20, 30]

        [[cases.set]]
        label = "3el"
        name = "3el_d{flap1_deflection}_f{flap2_deflection}_a{aoa}"
        [cases.set.constants]
        flap2_airfoil = "4412"
        flap2_chord = 80
        flap2_gap = 5
        flap2_overlap = 3
        [cases.set.sweep]
        flap1_deflection = [20]
        flap2_deflection = [35, 45]

        [results]
        objective = "max_cl"
    """), encoding="utf-8")
    return tmp_path / "project.toml"


def finish(case, cl: float, cd: float = 0.02) -> None:
    case.dir.mkdir(parents=True, exist_ok=True)
    (case.dir / "coef-rfile.out").write_text(
        '"r"\n("Iteration" "cl" "cd")\n' + "".join(f"{i} {cl} {cd}\n" for i in range(1, 4)), encoding="utf-8")
    (case.dir / "status.json").write_text(json.dumps({"state": "done"}), encoding="utf-8")


def test_ranking_separates_variables_only_some_sets_have(tmp_path):
    cfg = load_config(make_project(tmp_path))
    cases = build_cases(cfg)
    assert "flap2_deflection" not in cases[0].params  # 첫 케이스(2단)에는 없는 변수
    keys = geometry_keys(cfg, cases)
    assert "flap2_deflection" in keys
    # 받음각에서 자동으로 정해지는 유입 각도는 형상 변수가 아님 (넣으면 받음각마다 순위가 한 줄씩 나온다)
    assert {c.params["flow_angle"] for c in cases} == {0, 4} and "flow_angle" not in keys and "aoa" not in keys

    for c in cases:
        # 3단은 flap2 각도가 클수록 다운포스가 큰 가상의 결과
        finish(c, cl=1.0 + 0.01 * c.params["aoa"] + 0.02 * c.params.get("flap2_deflection", 0))
    r = collect(cfg, cases, plots=False)
    with open(r["ranking"], encoding="utf-8-sig") as f:
        ranking = list(csv.DictReader(f))
    # 형상 4개(2단 2 + 3단 2)가 따로 나오고, 형상마다 받음각 2개씩
    assert len(ranking) == 4 and {e["n_aoa"] for e in ranking} == {"2"}
    three = {e["flap2_deflection"]: e for e in ranking if e["case_set"] == "3el"}
    assert set(three) == {"35", "45"}
    assert three["45"]["rank"] == "1" and float(three["45"]["cl_max"]) > float(three["35"]["cl_max"])
    # 형상 이름도 그 형상의 것 (합쳐지면 다른 형상 이름이 찍힌다)
    assert "d45" in three["45"]["geometry"] and "d35" in three["35"]["geometry"]


def test_diverged_cases_do_not_rank(tmp_path):
    rows = [{"state": "done", "conv_state": "converged", "g": "A", "aoa": 4, "cl": 1.2, "cd": 0.03, "l/d": 40.0},
            {"state": "done", "conv_state": "converged", "g": "B", "aoa": 4, "cl": 1.5, "cd": 0.04, "l/d": 37.5},
            {"state": "done", "conv_state": "diverged", "g": "A", "aoa": 8, "cl": 57.3, "cd": 9.1, "l/d": 6.3},
            {"state": "done", "conv_state": "converged", "g": "B", "aoa": 8, "cl": float("nan"), "cd": 0.04}]
    ranking = rank_geometries(rows, ["g"], "aoa", "max_cl")
    assert [(e["g"], e["cl_max"], e["n_aoa"]) for e in ranking] == [("B", 1.5, 1), ("A", 1.2, 1)]

    # collect: 계수가 비정상적으로 큰 케이스(Fluent 발산 메시지 없이 끝남)는 diverged 판정 → 순위에서 빠지고 경고
    cfg = load_config(make_project(tmp_path))
    cases = build_cases(cfg)
    for c in cases:
        finish(c, cl=1.0 + 0.01 * c.params["aoa"])
    bad = next(c for c in cases if c.name == "2el_d30_a4")
    finish(bad, cl=57.3, cd=9.1)
    r = collect(cfg, cases, plots=True)
    with open(r["summary"], encoding="utf-8-sig") as f:
        summary = {row["case"]: row for row in csv.DictReader(f)}
    assert summary[bad.name]["state"] == "done" and summary[bad.name]["conv_state"] == "diverged"
    assert max(e["cl_max"] for e in r["top"]) < 2
    d30 = next(e for e in r["top"] if e["case_set"] == "2el" and e["flap1_deflection"] == 30)
    assert d30["n_aoa"] == 1 and d30["aoa@cl_max"] == 0
    assert any("발산 판정" in w and "1개" in w for w in r["warnings"])
