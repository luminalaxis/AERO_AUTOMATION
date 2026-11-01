"""UIUC Airfoil Coordinates Database에서 익형 좌표를 내려받아 airfoils/ 에 저장하고 airfoils.csv를 갱신한다.

    python fetch_uiuc.py             # 아래 UIUC 목록 전부 (이미 받은 파일은 건너뜀)
    python fetch_uiuc.py e387 s1223  # 지정한 것만

- 받은 파일은 aero_automation 형상 로더로 읽어 보고, CST 16계수 피팅 오차가 큰 파일(좌표 이상)은 제외한다.
- airfoils.csv = NACA 목록(source=naca) + 정상적으로 받은 UIUC 파일(source=uiuc).
- 없는 이름(404)이나 형식이 이상한 파일은 마지막에 목록으로 알려준다. 다른 이름으로 바꿔 넣으면 된다.
  (전체 목록: https://m-selig.ae.illinois.edu/ads/coord_database.html)
"""

from __future__ import annotations

import csv
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
URL = "https://m-selig.ae.illinois.edu/ads/coord/{name}.dat"
MAX_RMS = 2e-3  # CST 16계수 피팅 RMS 오차 허용치 [코드 기준]

# 저레이놀즈수(10^5대) 익형 위주, 캠버 0~10%. 고캠버(> 4.5%) 익형은 분포 밖 평가 세트로 자동 분리됨.
UIUC = [
    # Eppler
    "e168", "e169", "e171", "e174", "e178", "e180", "e182", "e186", "e193", "e195", "e197", "e201",
    "e205", "e207", "e209", "e211", "e214", "e216", "e221", "e222", "e226", "e228", "e230",
    "e374", "e387", "e393", "e395", "e423",
    # Selig, Selig/Donovan
    "s1223", "s1210", "s2027", "s2048", "s2055", "s2091", "s3010", "s3014", "s3021", "s4061", "s4083",
    "s5010", "s7012", "s7055", "s7075", "s8036", "s8037", "s809", "s822", "s823", "s9000",
    "sd7003", "sd7032", "sd7037", "sd7062", "sd7080", "sd7090", "sd8000", "sd8020",
    # 기타 (Wortmann, Clark, Drela, Hepperle, Göttingen)
    "fx63137", "fx60100", "clarky", "ag35", "ag24", "mh32", "mh45", "mh60", "rg15", "goe387",
]


def fetch(name: str, dest: Path) -> str | None:
    """내려받기. 실패하면 오류 메시지."""
    if dest.is_file():
        return None
    try:
        with urllib.request.urlopen(URL.format(name=name), timeout=30) as resp:
            data = resp.read()
    except urllib.error.HTTPError as exc:
        return f"HTTP {exc.code}"
    except OSError as exc:
        return str(exc)
    dest.write_bytes(data)
    return None


def check(path: Path) -> str | None:
    from aero_automation.surrogate.cst import shape_features

    try:
        f = shape_features(f"airfoils/{path.name}", HERE)
    except Exception as exc:  # noqa: BLE001 - 좌표 형식 문제는 모두 제외 사유로 보고
        return f"읽기 실패: {exc}"
    if f["cst16_rms"] > MAX_RMS:
        return f"CST 피팅 오차 큼 ({f['cst16_rms']:.1e})"
    return None


def main(names: list[str]) -> int:
    out = HERE / "airfoils"
    out.mkdir(exist_ok=True)
    problems = {}
    for i, name in enumerate(names, 1):
        dest = out / f"{name}.dat"
        err = fetch(name, dest) or check(dest)
        if err:
            problems[name] = err
            dest.unlink(missing_ok=True)
        print(f"[{i}/{len(names)}] {name:<10} {'실패 - ' + err if err else 'OK'}")

    csv_path = HERE / "airfoils.csv"
    with csv_path.open(newline="", encoding="utf-8") as f:
        rows = [r for r in csv.DictReader(f) if r["source"] == "naca"]
    rows += [{"main_airfoil": f"airfoils/{p.name}", "foil": p.stem, "source": "uiuc"}
             for p in sorted(out.glob("*.dat"))]
    with csv_path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["main_airfoil", "foil", "source"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    n_uiuc = sum(r["source"] == "uiuc" for r in rows)
    print(f"\nairfoils.csv: NACA {len(rows) - n_uiuc}개 + UIUC {n_uiuc}개 = {len(rows)}개")
    if problems:
        print("문제 있는 이름 (다른 익형으로 바꾸세요): " + ", ".join(f"{k} ({v})" for k, v in problems.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:] or UIUC))
