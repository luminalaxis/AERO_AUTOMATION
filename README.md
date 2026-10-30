# AERO_AUTOMATION

아 귀찮은 케이스 분류 해석 자동화 할겁니다

2D **메인익 + 슬롯 플랩** 형상 최적화를 위한 ANSYS Fluent 배치 해석 자동화 도구입니다.
형상(익형, 플랩 각도, 갭, 오버랩) × 받음각 케이스를 만들고, **저충실도 해석으로 가망 없는 케이스를 미리 걸러낸 뒤**, 형상마다 메쉬를 자동 생성해 Fluent로 돌리고 Cl/Cd 정리, 형상 순위, 이미지 저장까지 한 번에 처리합니다.

```
project.toml (형상·받음각 스윕)
   │ screen 패널법 + 경계층 근사로 전 케이스 추정 (형상당 0.3초)
   │        → 민감도 그래프, 하위 형상 계열·실속 이후 받음각 제외 (selection.json)
   │ mesh   남은 형상만 gmsh 자동 메쉬 → Fluent .msh  (받음각만 다른 케이스는 메쉬 공유)
   │        + geometry.png, mesh.png, mesh_slot.png, mesh_le.png
   │ run    케이스별 저널 생성 → fluent 2ddp -t4 -hidden -i run.jou
   │        (base.cas.h5 읽기 → 메쉬 교체 → aoa/mach 변경 → 계산 → 컨투어 이미지 저장)
   │ collect
   ▼
runs/screening/      저충실도 추정, 민감도 그래프, 선별 결과
runs/summary.csv     케이스별 조건 + Cl, Cd, L/D (+ 메인익/플랩 각각)
runs/ranking.csv     형상별 최대 Cl, 최대 L/D 순위
runs/plots/          Cl-α, Cd-α, L/D-α, 극곡선
runs/<형상>/<케이스>/ convergence.png, img_*.png (Fluent 컨투어)
```

## 설치

파이썬 3.11 이상.

```bash
pip install -e .
```

numpy, matplotlib, gmsh가 함께 설치됩니다. gmsh는 pip 패키지에 실행 파일이 포함돼 있어 따로 설치할 필요 없습니다.

## 빠른 시작 (예제)

```bash
cd examples/flap_optimization
```

```bash
aero-auto plan --dry-run   # 케이스 목록 확인 (288개 = 메인익 4 × 플랩 2 × 플랩각 2 × 갭 3 × 받음각 6)
```

```bash
aero-auto screen           # 저충실도 선별 (예제: 288개 → 92개, 약 15초)
```

```bash
aero-auto mesh             # 남은 형상만 메쉬 생성 (각 10초 내외) + 이미지
```

```bash
aero-auto run              # 메쉬 확인 → 남은 케이스만 Fluent 배치 실행 → 결과 정리
```

```bash
aero-auto status           # 진행 상태
```

```bash
aero-auto collect          # 결과 다시 정리 (summary, ranking, 그래프)
```

- `--only "2412-4412_d20*"`: 일부 케이스만 실행 (와일드카드, 그룹 이름도 가능)
- `-j 2`: 동시 실행 수 (라이선스와 코어 수 고려)
- 중단돼도 다시 `run` 하면 완료된 케이스와 이미 만든 메쉬는 건너뜁니다. 전부 다시 하려면 `run --force`, `mesh --force`.
- `screen`을 한 번 돌리면 이후 `mesh`, `run`은 제외된 케이스를 자동으로 건너뜁니다(상태 `screened`). 스크리닝을 무시하려면 `--all`.
- 실패 판정: 종료 코드, 로그의 `Error:` / `Divergence detected`, 시간 초과(`timeout_minutes`), 메쉬 생성 실패.

## CFD 전 스크리닝 (`screen`)

에어포일 종류를 여러 개 넣으면 조합이 금방 수백 개가 됩니다. `screen`은 Fluent를 돌리기 전에 모든 형상을 저충실도로 해석해서 민감도를 보여주고, 가망 없는 케이스를 미리 제외합니다.

**해석 방법 (형상당 약 0.3초)**
- 다요소 패널법(Hess-Smith): 비점성 Cl, Cp. NACA 0012 α=4°에서 Cl 0.478 등 이론값과 일치
- 적분 경계층(Thwaites → Head)으로 Cd(Squire-Young)와 윗면 박리 위치 추정. 기본은 완전 난류(`transition = "turbulent"`)로 Fluent SST 설정과 맞춤
- 실속각 추정: 메인익 윗면 박리 구간이 `sep_limit`(기본 10%)를 넘는 받음각

**선별 규칙**
1. **계열 선별:** `protect` 변수(기본 `gap`, `overlap`)를 제외한 나머지(익형 조합, 플랩각 등)가 같은 형상들을 한 계열로 묶고, 계열 안 최고 점수로 비교합니다. 상위 `keep_top`개 계열과 최고 대비 `keep_within` 이내 계열만 남깁니다. 점수는 `[results] objective`(최대 L/D 또는 최대 Cl)를 따릅니다.
2. **보호 변수:** 슬롯 갭과 오버랩은 합류 경계층 같은 점성 효과가 커서 저충실도 예측이 부정확합니다. 그래서 이 값만으로는 제외하지 않고, 남은 계열 안에서는 모든 갭/오버랩을 CFD로 돌립니다.
3. **받음각 선별:** 추정 실속각 + `aoa_margin`을 넘는 받음각은 제외합니다. 실속 이후 거동 확인용으로 넘는 첫 받음각 1개는 남깁니다.
4. **스팟 체크:** 제외된 계열 중 최상위 `spot_check`개의 최고 형상은 받음각 1~2개만 CFD로 돌립니다. `collect`에서 이 형상이 CFD에서 남긴 형상보다 좋게 나오면 경고합니다. 이 경우 `keep_within`을 늘려 다시 `screen` 하세요.

**결과물 (`runs/screening/`)**

| 파일 | 내용 |
|---|---|
| `sensitivity_main_effects.png` | 변수 값별 점수 분포 (평균선, 최소~최대 범위, 형상별 점: 파랑=유지, 회색=제외) |
| `sensitivity_importance.png` | 변수 중요도: 변수 값에 따른 평균 점수 변화 폭 |
| `screening_polars.png` | 형상별 추정 Cl-α, L/D-α와 추정 실속각 |
| `screening_scatter.png` | 형상별 추정 최대 Cl vs 최대 L/D |
| `geometries.csv` | 형상별 추정 실속각, 최대 Cl, 최대 L/D, 계열 순위, 유지 여부와 이유 |
| `cases.csv` | 케이스별 추정 Cl, Cd, L/D, 유지 여부와 이유 |
| `selection.json` | `run`/`mesh`가 읽는 선별 결과 |

CFD가 끝난 뒤 `collect`는 `plots/screening_vs_cfd_cl.png`, `screening_vs_cfd_ld.png`에 추정값과 CFD 결과의 상관을 그립니다. 첫 배치 결과로 스크리닝을 얼마나 믿을 수 있는지 확인하세요.

**설정 (`[screening]`)**

| 항목 | 기본값 | 내용 |
|---|---|---|
| `protect` | `["gap", "overlap"]` | 이 변수 값만으로는 제외하지 않음 |
| `keep_top` | 3 | 항상 유지할 상위 계열 수 |
| `keep_within` | 0.05 | 최고 계열 대비 이 비율 이내면 유지. 늘리면 안전하지만 절감이 줄어듦 |
| `spot_check` | 1 | 제외 계열 검증용으로 대표 케이스만 돌릴 계열 수 |
| `prune_aoa`, `aoa_margin` | true, 2.0 | 실속 이후 받음각 제외 여부와 여유각 |
| `sep_limit` | 0.10 | 실속 판정 박리 구간 비율 |
| `transition` | `"turbulent"` | `"free"`면 Michel 기준 자연 천이 |
| `reynolds`, `mach` | 비움 | 비우면 케이스 변수 `reynolds`/`mach` → `[mesh] reynolds` 순으로 사용 |
| `metric` | 비움 | `max_ld`/`max_cl`. 비우면 `[results] objective` |

**한계:** 경계층과 비점성 유동의 상호작용, 슬롯의 합류 경계층, 실속 이후 유동은 고려하지 않습니다. 절대값(특히 고양력 형상의 Cl)은 CFD보다 높게 나오므로 **형상 간 상대 비교와 경향 확인용**으로만 쓰세요.

## 형상 정의

케이스 변수 이름으로 형상을 지정합니다. `[constants]`에 고정값을, `[cases.sweep]`이나 CSV에 바꿀 값을 넣으면 됩니다.

| 변수 | 의미 |
|---|---|
| `main_airfoil` | 메인익 단면. NACA 4자리(`"2412"`) 또는 좌표 파일 경로(`"airfoils/main.dat"`, Selig/Lednicer 형식) |
| `flap_airfoil` | 플랩 단면 (같은 형식). 비우면 단일 익형 |
| `flap_chord` | 플랩 시위 / 메인익 시위 |
| `flap_deflection` | 플랩 각도 [deg], 뒷전이 내려가는 방향 + |
| `gap` | 메인익 뒷전(아랫면 끝점) ~ 플랩 표면 최소 거리 [c] |
| `overlap` | 메인익 뒷전 x − 플랩 앞쪽 끝 x [c], +면 플랩이 메인익 아래로 들어감 |

길이 단위는 모두 메인익 시위(c) 기준입니다. 주어진 overlap에서 gap이 정확히 맞도록 플랩 높이를 자동으로 계산하고, 두 물체가 겹치거나 불가능한 조합이면 해당 형상만 실패 처리합니다.
메인익 아랫면이 플랩에 gap보다 더 가까워지는 경우도 있어서 실제 최소 간격을 `mesh_info.json`의 `min_clearance`에 기록합니다.

## 메쉬 설정 (`[mesh]`)

| 항목 | 기본값 | 내용 |
|---|---|---|
| `chord` | 1.0 | 메인익 시위 [m]. 메쉬 좌표 스케일 |
| `reynolds`, `y_plus` | 3e6, 1.0 | 첫 셀 높이 계산 (평판 난류 마찰계수 추정) |
| `growth_rate` | 1.15 | 경계층 성장률 |
| `bl_thickness` | 0.02 | 경계층 사각형 영역 최대 두께 [c] |
| `gap_bl_fraction` | 0.3 | 슬롯에서 경계층 두께 ≤ 최소 간격 × 이 값 (갭이 좁으면 자동으로 얇아짐) |
| `surface_cells`, `flap_surface_cells` | 200, 150 | 윗면/아랫면 각각의 표면 셀 수 (앞전·뒷전에 집중) |
| `near_size`, `wake_size` | 0.01, 0.01 | 물체 주변 / 후류 셀 크기 [c] |
| `wake_length`, `wake_angles` | 3.0, [-10, 0, 10, 20] | 후류 세분화 길이와 방향 (받음각 범위를 덮도록) |
| `farfield_radius`, `farfield_size` | 50, 5.0 | 원거리 경계 반지름과 셀 크기 [c] |
| `min_quality` | 0.05 | 최소 셀 품질 (삼각형 SICN, 사각형 scaled Jacobian). 미달 시 경계층을 얇게 해서 재시도 |

경계 이름은 항상 `main`, `flap`(wall), `farfield`(pressure-far-field), 셀 영역은 `fluid`입니다. 모든 형상에서 이름이 같아서 Fluent에서 메쉬만 교체해도 설정이 유지됩니다.
`[mesh]` 항목과 같은 이름의 케이스 변수가 있으면 케이스 값이 우선합니다. 예를 들어 `reynolds`를 스윕하면 케이스마다 첫 셀 높이가 달라집니다.

메쉬 독립성은 기준 형상 하나로 `surface_cells`, `near_size` 등을 2~3단계 바꿔 Cl/Cd가 수렴하는지 확인한 뒤, 최적화 동안에는 설정을 고정하는 것을 권장합니다. 설정이 같아야 형상 간 비교에서 메쉬 차이로 생기는 노이즈가 줄어듭니다.

## Fluent 베이스 케이스 만들기 (최초 1회)

해석 설정은 `base.cas.h5` 하나에 해 두고, 케이스마다 메쉬만 바꿔 끼웁니다.

1. `aero-auto mesh`로 만든 메쉬 하나를 Fluent 2D 배정밀도(2ddp)에서 엽니다: `File > Read > Mesh` → `runs/_meshes/<형상>/mesh.msh`
2. `Mesh > Check`가 통과하는지 확인합니다. 단위는 m입니다.
3. **Models**: Viscous → k-omega SST (권장)
4. **Materials**: air, Density = **ideal-gas**. pressure-far-field 경계조건은 ideal-gas가 필요합니다.
5. **Named Expressions**: `aoa` = `0 [deg]`, `mach` = `0.15`
6. **Boundary Conditions**
   - `farfield` (pressure-far-field): Mach Number = `mach`, X-Component of Flow Direction = `cos(aoa)`, Y-Component = `sin(aoa)`
   - `main`, `flap`: wall
7. **Reference Values**: Compute from `farfield`, Length = 시위(m), Area = 시위 × 1 m (2D)
8. **Report Definitions**: Force 리포트를 Coefficient로 만듭니다.
   - `cx`: Zones = main, flap / Force Vector = (1, 0)
   - `cy`: Zones = main, flap / Force Vector = (0, 1)
   - `cx-main`, `cy-main`, `cx-flap`, `cy-flap`: 각 물체만
   - Report File `coef-rfile`에 위 리포트를 모두 넣습니다.

   cl/cd 대신 물체축 cx/cy를 쓰는 이유: 받음각마다 Fluent의 force vector를 바꿀 필요 없이 도구가 `cl = cy·cosα − cx·sinα`, `cd = cx·cosα + cy·sinα`로 변환합니다 (`cl-flap`, `cd-flap` 등도 자동).
9. **이미지용 그래픽 객체와 뷰**
   - Contour `contour-pressure` (Pressure Coefficient), `contour-velocity` (Velocity Magnitude)
   - 익형 전체가 보이게 확대 → `View > Save View` 이름 `near`
   - 슬롯 부분 확대 → 이름 `slot`
10. `File > Write > Case`로 `examples/flap_optimization/base.cas.h5`(프로젝트 폴더)에 저장합니다.

### 파일럿 확인 순서

1. `sample_meshes.zip`(예제 폴더)의 메쉬 2개(갭 0.01, 0.03)를 Fluent에서 열어 Mesh Check를 확인합니다.
2. 위 순서로 베이스 케이스를 만든 뒤 `File > Replace Mesh`로 다른 메쉬로 바꿔도 경계조건, 리포트, 뷰가 유지되는지 확인합니다.
3. 케이스 하나만 배치로 돌려봅니다:

   ```bash
   aero-auto run --only "2412-4412_d20_g0.01_a4"
   ```

4. `runs/2412-4412_d20_g0.01/2412-4412_d20_g0.01_a4/solver.log`에서 저널 명령이 오류 없이 실행됐는지, `img_*.png`가 저장됐는지 확인합니다.

저널(`journal.jou`)의 TUI 명령은 Fluent 버전에 따라 프롬프트가 다를 수 있습니다. 오류가 나면 GUI에서 `File > Write > Start Journal`로 같은 작업을 녹화해 해당 줄을 교체하세요. 특히 `/file/replace-mesh`, `/display/save-picture` 부분을 확인하면 됩니다.

## 결과물

| 파일 | 내용 |
|---|---|
| `runs/summary.csv` | 케이스별 형상·받음각, 상태, cx/cy, **cl/cd, l/d** (요소별 포함), 반복 수 |
| `runs/ranking.csv` | 형상별 최대 Cl과 그때 받음각, 최대 L/D와 그때 받음각·Cl·Cd. `objective` 기준 정렬 |
| `runs/plots/*.png` | 형상별 Cl-α, Cd-α, L/D-α, 극곡선 |
| `runs/_meshes/<형상>/` | `geometry.png`, `mesh.png`, `mesh_slot.png`, `mesh_le.png`, `mesh_info.json` |
| `runs/<그룹>/<케이스>/` | `convergence.png`(리포트 수렴 이력), `img_*.png`(Fluent 컨투어), `solver.log`, `result.cas.h5` |

결과값은 마지막 `average_last`개 iteration의 평균입니다. 진동하며 수렴하는 경우를 대비한 것으로, `convergence.png`로 수렴 여부를 함께 확인하세요.

## 설정 파일 전체 구조 (`project.toml`)

| 섹션 | 내용 |
|---|---|
| `[project]` | `template`(저널), `output_dir`, `max_parallel` |
| `[solver]` | `executable`(비우면 `AWP_ROOTxxx` 환경변수로 자동 탐색), `mode`(2ddp), `processes`, `graphics`(이미지 저장), `timeout_minutes`, `extra_args` |
| `[constants]` | 공통 값 |
| `[cases]` | `csv`(행 단위 케이스, 선택), `name`(케이스 폴더 이름 패턴), `group`(분류 폴더 패턴) |
| `[cases.sweep]` | 전체 조합 변수 |
| `[derived]` | 계산 변수, 예: `flow_x = "cos(radians(aoa))"` |
| `[mesh]` | 위 메쉬 설정. 섹션이 없으면 메쉬 생성 없이 기존 방식으로 동작 |
| `[results]` | `report_files`, `average_last`, `x_param`, `wind_axes`, `objective`(`max_ld`/`max_cl`) |

저널 템플릿에서 쓸 수 있는 변수는 모든 케이스 변수에 더해 `${case_name}`, `${group}`, `${case_dir}`, `${project_dir}`, `${nprocs}`, `${mesh_file}`, `${mesh_name}`입니다. 포맷 지정도 됩니다: `${aoa:.2f}`.

## 테스트

```bash
pip install -e ".[dev]"
```

```bash
pytest
```

실제 Fluent 대신 가짜 솔버(`tests/fake_solver.py`)로 형상 배치 정확도, Fluent 메쉬 면 방향 규칙, 패널법 이론값, 스크리닝 선별과 실행 반영, 메쉬 생성부터 해석, cl/cd 변환, 순위, 그래프까지의 전체 흐름을 검증합니다.
