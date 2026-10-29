# AERO_AUTOMATION

아 귀찮은 케이스 분류 해석 자동화 할겁니다

ANSYS Fluent 해석을 **케이스 매트릭스(마하수 × 받음각 등) → 케이스별 폴더/저널 생성 → 배치 실행 → 결과 요약 CSV**까지 자동으로 처리하는 파이썬 도구입니다.
외부 라이브러리 없이 파이썬 3.11+ 표준 라이브러리만 사용합니다.

## 흐름

```
project.toml + cases.csv + journal.jou(템플릿)
        │  plan
        ▼
runs/M0.3/M0.3_AoA4/run.jou   ← 케이스마다 변수 치환된 저널
runs/M0.3/M0.3_AoA8/run.jou
        │  run   (fluent 2ddp -t8 -g -i run.jou, 완료 케이스는 건너뜀)
        ▼
runs/.../coef-rfile.out, solver.log, status.json
        │  collect
        ▼
runs/summary.csv              ← 케이스별 조건 + Cl/Cd 등 (엑셀에서 바로 열림)
```

## 설치

```bash
pip install -e .
```

설치 없이 레포 폴더에서 `python -m aero_automation ...`로 실행해도 됩니다.

## 사용법

```bash
cd examples/airfoil_sweep
```

```bash
aero-auto plan --dry-run      # 케이스 목록만 확인
```

```bash
aero-auto plan                # 케이스 폴더 + 저널 생성 (실행 전 저널 검토용)
```

```bash
aero-auto run                 # 배치 실행
```

```bash
aero-auto run -j 2 --only "M0.7*"   # 일부 케이스만, 2개씩 동시 실행
```

```bash
aero-auto status              # 진행 상태 (pending / running / done / failed)
```

```bash
aero-auto collect             # runs/summary.csv 생성
```

- 실패하거나 중단돼도 다시 `run` 하면 **완료된 케이스는 건너뛰고** 나머지만 돌립니다. 전부 다시 돌리려면 `--force`.
- 종료 코드가 0이 아니거나, 로그에 `Error:`, `Divergence detected` 등이 있으면 `failed`로 표시합니다.
- `timeout_minutes`를 넘기면 Fluent 프로세스 트리 전체를 종료합니다.

## 설정 (`project.toml`)

| 섹션 | 내용 |
|---|---|
| `[project]` | `template`(저널 템플릿), `output_dir`, `max_parallel`(동시 실행 수) |
| `[solver]` | `executable`(비우면 `AWP_ROOTxxx` 환경변수로 자동 탐색), `mode`(2ddp/3ddp), `processes`, `timeout_minutes`, `extra_args` |
| `[constants]` | 모든 케이스 공통 값 |
| `[cases]` | `csv`(행 단위 케이스), `name`(폴더명 패턴), `group`(분류 폴더 패턴) |
| `[cases.sweep]` | 전체 조합으로 늘릴 변수 목록 (CSV 각 행 × 조합) |
| `[derived]` | 계산 변수, 예: `flow_x = "cos(radians(aoa))"` |
| `[results]` | `report_files`(읽을 .out 패턴), `average_last`(마지막 N iteration 평균) |

값 우선순위: `constants` < CSV < `sweep` < `derived`. CSV에 `name` 컬럼이 있으면 그 값을 케이스 이름으로 씁니다.

## 저널 템플릿 만들기

1. Fluent에서 베이스 케이스(`base.cas.h5`)를 세팅합니다. 바뀌는 조건은 **Named Expression**(`aoa`, `mach` 등)으로 만들어 경계조건에 연결해 두면 저널이 단순해집니다.
2. `File > Write > Start Journal`로 한 케이스 작업을 녹화합니다.
3. 케이스마다 바뀌는 숫자를 `${aoa}`, `${mach}`처럼 바꿉니다. 포맷 지정도 됩니다: `${aoa:.2f}`.
4. 기본 제공 변수: `${case_name}`, `${group}`, `${case_dir}`, `${project_dir}`, `${nprocs}`.
5. Report File 출력 경로는 상대경로로 두세요 (실행 위치 = 케이스 폴더).
6. 마지막 줄은 `/exit yes`.

TUI 명령의 프롬프트 순서는 Fluent 버전과 모델 설정마다 다르니, 예제 저널을 그대로 쓰기보다 직접 녹화한 저널을 쓰는 걸 권장합니다.

## 다른 솔버 (MAPDL, CFX 등)

`kind = "custom"`으로 명령어를 직접 지정하면 어떤 배치 솔버든 같은 방식으로 돌릴 수 있습니다. `[project] template`이 각 케이스 폴더에 `run.jou`로 치환·저장됩니다.

```toml
[solver]
kind = "custom"
executable = 'C:\Program Files\ANSYS Inc\v242\ansys\bin\winx64\MAPDL.exe'
command = ["${exe}", "-b", "-np", "${nprocs}", "-i", "${journal}", "-o", "solve.txt"]
```

## 테스트

```bash
pip install -e ".[dev]" && pytest
```

실제 Fluent 대신 가짜 솔버(`tests/fake_solver.py`)로 실행·실패 판정·타임아웃·결과 수집을 검증합니다.
