"""테스트용 가짜 솔버: 저널에서 aoa를 읽어 Fluent 형식 report-file을 쓴다."""
import re
import sys
from pathlib import Path

journal = Path(sys.argv[1]).read_text(encoding="utf-8")
aoa = float(re.search(r"AOA=(\S+)", journal).group(1))
if aoa >= 99:
    print("Error: Divergence detected in AMG solver")
    sys.exit(0)
if aoa <= -99:
    sys.exit(3)
lines = ['"coef-rfile"', '"Iteration" "cl" "cd"', '("Iteration" "cl" "cd")']
for it in range(1, 11):
    lines.append(f"{it} {0.1 * aoa + it * 0.001:.6e} {0.01 + it * 0.0001:.6e}")
Path("coef-rfile.out").write_text("\n".join(lines) + "\n")
print("calculation complete")
