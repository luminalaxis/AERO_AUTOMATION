"""단계적 실행: run --stage1 (스팟 체크 + 대표 형상 전체 받음각) → narrow-aoa (나머지는 최대 각도 주변만)."""
import json
import textwrap

from aero_automation.cases import build_cases
from aero_automation.cli import main
from aero_automation.config import load_config
from aero_automation.runner import read_status
from aero_automation.staging import stage1_cases

# 가짜 솔버: 받음각 8°에서 Cl이 최대인 극곡선
FAKE = '''
import re, sys
from pathlib import Path
journal = Path(sys.argv[1]).read_text(encoding="utf-8")
aoa = float(re.search(r"AOA=(\\S+)", journal).group(1))
cl, cd = 1.2 - (aoa - 8) ** 2 / 100, 0.02 + 0.001 * aoa
rows = ['"coef-rfile"', '("Iteration" "cl" "cd")'] + [f"{i} {cl:.8e} {cd:.8e}" for i in range(1, 6)]
Path("coef-rfile.out").write_text("\\n".join(rows) + "\\n")
'''


def make_project(tmp_path):
    (tmp_path / "fake.py").write_text(FAKE, encoding="utf-8")
    (tmp_path / "journal.jou").write_text("AOA=${aoa}\n", encoding="utf-8")
    (tmp_path / "project.toml").write_text(textwrap.dedent(f"""
        [project]
        template = "journal.jou"

        [solver]
        kind = "custom"
        command = ["${{python}}", "{(tmp_path / 'fake.py').as_posix()}", "${{journal}}"]

        [cases]
        name = "{{main_airfoil}}_c{{main_chord}}_a{{aoa}}"

        [cases.sweep]
        main_airfoil = ["0012", "2412", "4412"]
        main_chord = [300, 450]
        aoa = [0, 4, 8, 12, 16]

        [derived]
        chord = "main_chord / 1000"

        [screening]
        keep_top = 1
        keep_within = 0.0
        spot_check = 0
        spot_check_values = false
        prune_aoa = false
        n_side = 50

        [results]
        objective = "max_cl"
    """), encoding="utf-8")
    return tmp_path / "project.toml"


def cli(cfg_path, *args):
    return main(["-c", str(cfg_path), "--no-banner", *args])


def test_stage1_then_narrow_aoa(tmp_path, capsys):
    cfg_path = make_project(tmp_path)
    assert cli(cfg_path, "screen") == 0
    cfg = load_config(cfg_path)
    cases = build_cases(cfg)
    sel = json.loads((cfg.output_dir / "screening" / "selection.json").read_text(encoding="utf-8"))
    kept = [c for c in cases if c.name in sel["kept"]]
    assert len(kept) == 10  # 남은 계열(익형 1개) × 코드 2개 × 받음각 5개

    stage1 = stage1_cases(cfg, kept)
    assert len(stage1) == 5 and len({c.params["main_chord"] for c in stage1}) == 1  # 대표 형상의 전체 받음각
    assert cli(cfg_path, "run", "--stage1") == 0
    assert {c.name for c in cases if read_status(c)["state"] == "done"} == {c.name for c in stage1}

    # 미리보기는 selection.json을 바꾸지 않는다
    assert cli(cfg_path, "narrow-aoa", "--dry-run") == 0
    assert json.loads((cfg.output_dir / "screening" / "selection.json").read_text(encoding="utf-8"))["kept"] == sel["kept"]

    assert cli(cfg_path, "narrow-aoa", "--keep", "3") == 0
    out = capsys.readouterr().out
    assert "최대 8° (cl 1.200) → 4, 8, 12°  제외 2개" in out and "10개 → 8개" in out
    sel2 = json.loads((cfg.output_dir / "screening" / "selection.json").read_text(encoding="utf-8"))
    removed = set(sel["kept"]) - set(sel2["kept"])
    other = [c for c in kept if c not in stage1]
    assert removed == {c.name for c in other if c.params["aoa"] in (0, 16)}
    assert all("받음각 범위 밖" in sel2["pruned"][n] for n in removed)

    assert cli(cfg_path, "run", "--priority") == 0
    states = {c.name: read_status(c)["state"] for c in cases}
    assert sum(s == "done" for s in states.values()) == 8
    assert all(states[n] == "screened" for n in removed)


def test_narrow_aoa_needs_stage1_results(tmp_path, capsys):
    cfg_path = make_project(tmp_path)
    assert cli(cfg_path, "screen") == 0
    assert cli(cfg_path, "narrow-aoa") == 0
    out = capsys.readouterr().out
    assert "결과가 부족해 그대로 둔 그룹 1개" in out and "제외한 케이스 0개" in out


def test_window_moves_inside_at_range_edge(tmp_path, capsys):
    cfg_path = make_project(tmp_path)
    fake = (tmp_path / "fake.py").read_text(encoding="utf-8").replace("1.2 - (aoa - 8) ** 2 / 100", "0.1 * aoa")
    (tmp_path / "fake.py").write_text(fake, encoding="utf-8")  # 최대가 범위 끝(16°)
    assert cli(cfg_path, "screen") == 0
    assert cli(cfg_path, "run", "--stage1") == 0
    assert cli(cfg_path, "narrow-aoa") == 0
    out = capsys.readouterr().out
    assert "최대 16° (cl 1.600) → 8, 12, 16°" in out and "범위 끝인 그룹 1개" in out
