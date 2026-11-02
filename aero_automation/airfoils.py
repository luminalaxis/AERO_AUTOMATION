"""익형 좌표 자동 수집 (UIUC Airfoil Coordinates Database).

- fetch_series(): NACA / S(Selig) / E(Eppler) 등 시리즈 전체를 airfoils/ 폴더로 일괄 다운로드
- resolve(): 설정의 익형 이름(예: "s1223", "naca23012", "e423")을 좌표 파일 경로로 바꾼다.
  폴더에 없으면 그 익형 하나만 받아 온다.

받은 파일은 좌표를 읽어 검증하고(점 개수, 정규화, 두께), 결과를 airfoils/index.csv 에 기록한다.
"""

from __future__ import annotations

import csv
import os
import re
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

import numpy as np

from .config import ConfigError

# 앞쪽부터 시도. Selig 형식 폴더(디렉터리 목록)가 형식이 통일돼 있어 우선 사용한다.
UIUC_SOURCES = [
    "https://m-selig.ae.illinois.edu/ads/coord_seligFmt/",
    "https://m-selig.ae.illinois.edu/ads/coord_database.html",
]
SERIES = {
    "naca": r"naca.*",   # NACA 전체 (4자리, 5자리, 6계열 등)
    "s": r"s\d.*",       # Selig S 시리즈 (s1223, s1223rtl ...). SD/SA 등은 제외
    "e": r"e\d.*",       # Eppler E 시리즈 (e387, e423 ...)
}
AIRFOIL_DIR = "airfoils"
INDEX_FILE = "index.csv"
_HREF = re.compile(r"""href\s*=\s*["']?([^"'\s>]+\.dat)""", re.IGNORECASE)
_USER_AGENT = "aero-automation/0.1 (airfoil coordinate fetch)"


def _get(url: str, timeout: float = 30.0, retries: int = 3) -> bytes:
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return resp.read()
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            time.sleep(1.0 * (attempt + 1))
    raise ConfigError(f"다운로드 실패: {url} ({last})")


def list_remote(sources: list[str] | None = None) -> dict[str, str]:
    """원격 데이터베이스의 {익형 이름: 좌표 파일 URL}. 첫 번째로 응답한 소스를 쓴다."""
    errors = []
    for src in sources or UIUC_SOURCES:
        try:
            html = _get(src).decode("utf-8", errors="replace")
        except ConfigError as exc:
            errors.append(str(exc))
            continue
        found = {}
        for href in _HREF.findall(html):
            name = Path(href.split("?")[0]).stem.lower()
            found.setdefault(name, urljoin(src, href))
        if found:
            return found
        errors.append(f"{src}: .dat 링크를 찾지 못함")
    raise ConfigError("익형 목록을 가져오지 못했습니다 (인터넷 연결 확인):\n  " + "\n  ".join(errors))


def select(names, series: list[str]) -> list[str]:
    patterns = []
    for s in series:
        key = s.lower()
        patterns.append(re.compile(SERIES.get(key, key), re.IGNORECASE))
    return sorted(n for n in names if any(p.fullmatch(n) for p in patterns))


def inspect(path: Path) -> dict:
    """좌표 파일 검증 + 간단한 형상 정보. 문제가 있으면 ConfigError."""
    from .geometry import normalize, read_airfoil_file

    xy, le = normalize(read_airfoil_file(path))
    if len(xy) < 20:
        raise ConfigError(f"점이 너무 적음 ({len(xy)}개)")
    upper, lower = xy[: le + 1][::-1], xy[le:]
    xs = np.linspace(0.02, 0.98, 97)
    yu = np.interp(xs, upper[:, 0], upper[:, 1])
    yl = np.interp(xs, lower[:, 0], lower[:, 1])
    thick = yu - yl
    if thick.min() < -1e-3:
        raise ConfigError("윗면과 아랫면이 교차함 (좌표 순서 이상)")
    camber = 0.5 * (yu + yl)
    return {
        "points": len(xy),
        "thickness": round(float(thick.max()), 4),
        "x_thickness": round(float(xs[thick.argmax()]), 3),
        "camber": round(float(camber[np.abs(camber).argmax()]), 4),
        "te_gap": round(float(np.linalg.norm(xy[0] - xy[-1])), 5),
    }


def _download_one(name: str, url: str, out_dir: Path, force: bool) -> dict:
    path = out_dir / f"{name}.dat"
    row = {"name": name, "url": url}
    try:
        if force or not path.is_file():
            data = _get(url)
            tmp = path.with_suffix(".tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)  # 동시에 받아도 반쯤 쓴 파일이 남지 않게
            row["downloaded"] = True
        row.update(inspect(path), status="ok")
    except ConfigError as exc:
        row.update(status="bad", message=str(exc))
    return row


def fetch_series(series: list[str], out_dir: Path, force: bool = False, workers: int = 4,
                 sources: list[str] | None = None, log=print) -> list[dict]:
    """시리즈 전체 다운로드 + 검증. 반환: 익형별 결과 행."""
    out_dir.mkdir(parents=True, exist_ok=True)
    remote = list_remote(sources)
    names = select(remote, series)
    if not names:
        raise ConfigError(f"시리즈 {series}에 해당하는 익형이 없습니다.")
    todo = [n for n in names if force or not (out_dir / f"{n}.dat").is_file()]
    log(f"원격 목록 {len(remote)}개 중 {', '.join(series)} 시리즈 {len(names)}개 "
        f"(새로 받을 것 {len(todo)}개)")

    rows = []
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for i, row in enumerate(pool.map(lambda n: _download_one(n, remote[n], out_dir, force), names), 1):
            rows.append(row)
            if i % 50 == 0 or i == len(names):
                log(f"  {i}/{len(names)}")
    write_index(out_dir)
    return rows


def write_index(out_dir: Path) -> Path:
    """폴더의 모든 .dat를 검사해 index.csv 작성 (수동으로 넣은 파일 포함)."""
    rows = []
    for path in sorted(out_dir.glob("*.dat")):
        row = {"name": path.stem}
        try:
            row.update(inspect(path), status="ok")
        except (ConfigError, ValueError, IndexError) as exc:
            row.update(status="bad", message=str(exc))
        rows.append(row)
    cols = ["name", "status", "thickness", "x_thickness", "camber", "te_gap", "points", "message"]
    index = out_dir / INDEX_FILE
    with index.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        w.writerows(rows)
    return index


def resolve(spec: str, root: Path) -> Path:
    """익형 이름/경로 → 좌표 파일 경로.

    순서: (1) 경로로 존재 (2) <root>/airfoils/<이름>.dat (3) UIUC에서 그 익형만 받아 저장.
    "uiuc:s1223"처럼 접두어를 붙여도 된다.
    """
    spec = spec.strip()
    name = spec.split(":", 1)[1] if spec.lower().startswith("uiuc:") else spec
    direct = (root / name)
    if direct.suffix and direct.is_file():
        return direct
    local = root / AIRFOIL_DIR / f"{Path(name).stem.lower()}.dat"
    if local.is_file():
        return local
    if direct.suffix:  # 경로처럼 쓴 파일이 없으면 다운로드하지 않는다
        raise ConfigError(f"익형 좌표 파일을 찾을 수 없습니다: {direct}")
    key = Path(name).stem.lower()
    remote = list_remote()
    if key not in remote:
        raise ConfigError(f"UIUC 데이터베이스에 '{key}' 익형이 없습니다. 이름을 확인하세요 "
                          f"(airfoils 폴더의 index.csv 또는 aero-auto airfoils list 참고).")
    local.parent.mkdir(parents=True, exist_ok=True)
    row = _download_one(key, remote[key], local.parent, force=False)
    if row["status"] != "ok":
        raise ConfigError(f"'{key}' 좌표 이상: {row.get('message')}")
    return local


def plot_previews(out_dir: Path, per_page: int = 48) -> list[Path]:
    """시리즈별 미리보기 그림 (한 장에 48개). 받은 좌표를 눈으로 확인하는 용도."""
    from .geometry import normalize, read_airfoil_file
    from .plots import _save, plt

    groups: dict[str, list[Path]] = {}
    for path in sorted(out_dir.glob("*.dat")):
        key = next((k for k, pat in SERIES.items() if re.fullmatch(pat, path.stem, re.IGNORECASE)), "other")
        groups.setdefault(key, []).append(path)
    saved = []
    for key, paths in groups.items():
        for page in range(0, len(paths), per_page):
            chunk = paths[page: page + per_page]
            cols = 6
            rows = -(-len(chunk) // cols)
            fig, axes = plt.subplots(rows, cols, figsize=(cols * 2.4, rows * 0.9), squeeze=False)
            for ax, path in zip(axes.flat, chunk):
                try:
                    xy, _ = normalize(read_airfoil_file(path))
                    ax.plot(xy[:, 0], xy[:, 1], lw=0.8)
                except (ConfigError, ValueError, IndexError):
                    ax.text(0.5, 0, "bad", ha="center", color="r")
                ax.set_title(path.stem, fontsize=7)
                ax.set_aspect("equal")
                ax.axis("off")
            for ax in list(axes.flat)[len(chunk):]:
                ax.axis("off")
            saved.append(_save(fig, out_dir / f"_preview_{key}_{page // per_page + 1}.png"))
    return saved
