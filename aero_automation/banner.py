"""시작 배너: 큰 글씨 로고 + 지면 효과 날개 아스키 그림.

외부 라이브러리 없이 글꼴을 직접 들고 있습니다. 터미널(TTY)일 때만 보이고,
파이프·리다이렉트·테스트에서는 출력하지 않습니다.

    AERO_AUTO_NO_BANNER=1   배너 끄기 (또는 --no-banner)
    NO_COLOR=1              색 끄기 (https://no-color.org)
"""

from __future__ import annotations

import os
import shutil
import sys

from . import __version__

# ANSI Shadow 스타일 글꼴 (글자마다 6줄)
_FONT = {
    "A": [" █████╗ ",
          "██╔══██╗",
          "███████║",
          "██╔══██║",
          "██║  ██║",
          "╚═╝  ╚═╝"],
    "E": ["███████╗",
          "██╔════╝",
          "█████╗  ",
          "██╔══╝  ",
          "███████╗",
          "╚══════╝"],
    "R": ["██████╗ ",
          "██╔══██╗",
          "██████╔╝",
          "██╔══██╗",
          "██║  ██║",
          "╚═╝  ╚═╝"],
    "O": [" ██████╗ ",
          "██╔═══██╗",
          "██║   ██║",
          "██║   ██║",
          "╚██████╔╝",
          " ╚═════╝ "],
    "U": ["██╗   ██╗",
          "██║   ██║",
          "██║   ██║",
          "██║   ██║",
          "╚██████╔╝",
          " ╚═════╝ "],
    "T": ["████████╗",
          "╚══██╔══╝",
          "   ██║   ",
          "   ██║   ",
          "   ██║   ",
          "   ╚═╝   "],
    "-": ["      ",
          "      ",
          "█████╗",
          "╚════╝",
          "      ",
          "      "],
    " ": ["  "] * 6,
}

# 뒤집힌 다요소 날개(메인 + 플랩) + 움직이는 바닥. 각 줄은 (색 이름, 문자열) 조각 목록.
_WING = [
    [("flow", "  ───▶                                                 "), ("wing", "▄▄██▀")],
    [("flow", "  ───▶     "), ("wing", "▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄▄     ▄▄██▀▀")],
    [("flow", "  ───▶   "), ("wing", "▄████████████████████████████████████▀▀  ▄██▀▀")],
    [("flow", "  ───▶   "), ("wing", "▀███████████████████████████████▀▀▀     ▀▀")],
    [("flow", "  ───▶     "), ("wing", "▀▀▀███████████████████▀▀▀▀")],
    [("dim", "                ↓ ↓ ↓   downforce   ↓ ↓ ↓")],
    [("ground", "  ▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔▔")],
    [("dim", "  ═══▶ ═══▶ ═══▶       moving ground       ═══▶ ═══▶ ═══▶")],
]

_TAGLINE = "2D 다요소 날개 · Fluent 배치 해석 자동화"

# 큰 글씨 줄마다 위→아래로 바뀌는 색 (256색: 하늘색 → 파랑)
_GRADIENT = [51, 45, 39, 33, 27, 21]
_COLORS = {"flow": "38;5;81", "wing": "38;5;255", "ground": "38;5;244",
           "dim": "38;5;244", "accent": "1;38;5;45"}


def render_text(text: str) -> list[str]:
    """글꼴에 있는 글자로 큰 글씨 6줄을 만듭니다."""
    rows = [""] * 6
    for ch in text.upper():
        glyph = _FONT.get(ch, _FONT[" "])
        for i in range(6):
            rows[i] += glyph[i]
    return [r.rstrip() for r in rows]


def _supports_unicode(stream) -> bool:
    enc = getattr(stream, "encoding", None) or "ascii"
    try:
        "█╗═▀▄▔▶↓·다".encode(enc)
        return True
    except (UnicodeEncodeError, LookupError):
        return False


def _use_color(stream) -> bool:
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("FORCE_COLOR"):
        return True
    if not (hasattr(stream, "isatty") and stream.isatty()):
        return False
    if os.name == "nt":
        os.system("")  # Windows 콘솔에서 ANSI 이스케이프 처리 켜기
    return True


def build_banner(*, color: bool = True, unicode: bool = True, width: int = 80) -> str:
    """배너 문자열. 폭이 좁거나 유니코드를 못 쓰면 한 줄짜리로 줄입니다."""
    def paint(code: str, s: str) -> str:
        return f"\033[{code}m{s}\033[0m" if color else s

    version = f"v{__version__}"
    if not unicode:
        return f"== AERO-AUTO {version} :: 2D multi-element wing / Fluent batch automation ==\n"

    title = render_text("AERO-AUTO")
    if width < max(len(r) for r in title) + 2:
        return paint(_COLORS["accent"], "✈ AERO-AUTO") + f" {version}  {paint(_COLORS['dim'], _TAGLINE)}\n"

    lines = [""]
    for row, c in zip(title, _GRADIENT):
        lines.append(" " + paint(f"1;38;5;{c}", row))
    lines.append("")
    for parts in _WING:
        lines.append("".join(paint(_COLORS[k], s) for k, s in parts).rstrip())
    lines.append("")
    lines.append("  " + paint(_COLORS["accent"], _TAGLINE) + "  " + paint(_COLORS["dim"], version))
    lines.append("")
    return "\n".join(lines) + "\n"


def print_banner(stream=None, *, force: bool = False) -> bool:
    """TTY일 때만 배너를 출력합니다. 출력했으면 True."""
    stream = stream or sys.stdout
    if not force:
        if os.environ.get("AERO_AUTO_NO_BANNER"):
            return False
        if not (hasattr(stream, "isatty") and stream.isatty()):
            return False
    width = shutil.get_terminal_size((80, 24)).columns
    stream.write(build_banner(color=_use_color(stream), unicode=_supports_unicode(stream), width=width))
    stream.flush()
    return True
