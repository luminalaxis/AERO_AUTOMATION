# AERO_AUTOMATION

아 귀찮은 케이스 분류 해석 자동화 할겁니다

2D **다요소 날개(메인 + 슬롯 플랩 1~2개)** 형상 최적화를 위한 ANSYS Fluent 배치 해석 자동화 도구입니다. 예제는 **다운포스용 뒤집힌 날개**입니다.
2단(메인 + 플랩 1개)·3단(메인 + 플랩 2개) 구성의 형상(익형, 요소별 코드 길이, 플랩 각도, 갭, 오버랩) × 받음각 × 속도 케이스를 만들고, **저충실도 해석으로 가망 없는 케이스를 미리 걸러낸 뒤**, 형상마다 메쉬를 자동 생성해 Fluent로 돌리고 Cl/Cd 정리, 형상 순위, 이미지 저장까지 한 번에 처리합니다.

```
project.toml (2단/3단 세트, 형상·받음각·속도 스윕)
   │ screen 패널법 + 경계층 근사로 전 케이스 추정
   │        → 민감도 그래프, 하위 형상 계열·실속 이후 받음각 제외 (세트·속도별, selection.json)
   │ mesh   남은 형상만 gmsh 자동 메쉬 → Fluent .msh  (받음각·속도만 다른 케이스는 메쉬 공유)
   │        + geometry.png, mesh.png, mesh_slot1/2.png, mesh_le.png
   │ run    케이스별 저널 생성 → fluent 2ddp -t4 -hidden -i run.jou
   │        (base_2el/3el.cas.h5 읽기 → 메쉬 교체 → aoa/mach/기준값 변경 → 계산 → 컨투어 이미지)
   │ collect
   ▼
runs/screening/      저충실도 추정, 민감도 그래프, 선별 결과
runs/summary.csv     케이스별 조건 + Cl, Cd, L/D (요소별 포함), 단위 폭당 양력·항력 [N/m]
runs/ranking.csv     형상별 최대 Cl, 최대 L/D 순위 (속도별로 따로)
runs/plots/          속도별 폴더에 Cl-α, Cd-α, L/D-α, 극곡선 + 속도에 따른 형상별 최대 L/D
runs/<그룹>/<케이스>/ convergence.png, img_*.png (Fluent 컨투어)
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
aero-auto plan --dry-run   # 케이스 목록 확인 (6336개 = 형상 264개 × 받음각 6 × 속도 4)
```

```bash
aero-auto screen           # 세트·속도별 저충실도 선별 (예제: 6336개 → 730개, 약 3분)
```

```bash
aero-auto mesh             # 남은 형상만 메쉬 생성 (각 5~7초) + 이미지
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

- `--only "2el_4412c375*"`: 일부 케이스만 실행 (와일드카드, 그룹 이름도 가능)
- `-j 2`: 동시 실행 수 (라이선스와 코어 수 고려)
- 중단돼도 다시 `run` 하면 완료된 케이스와 이미 만든 메쉬는 건너뜁니다. 전부 다시 하려면 `run --force`, `mesh --force`.
- `screen`을 한 번 돌리면 이후 `mesh`, `run`은 제외된 케이스를 자동으로 건너뜁니다(상태 `screened`). 스크리닝을 무시하려면 `--all`.
- 실패 판정: 종료 코드, 로그의 `Error:` / `Divergence detected`, 시간 초과(`timeout_minutes`), 메쉬 생성 실패.

## 형상 정의 (2단 / 3단)

길이(코드, 갭, 오버랩)는 모두 **mm**로 받습니다. 플랩은 `flap1_*`, `flap2_*`(최대 `flap3_*`)로 지정하고, 앞의 요소에 이어 붙입니다.

| 변수 | 의미 |
|---|---|
| `main_airfoil` | 메인 단면. NACA 4자리(`"2412"`) 또는 좌표 파일 경로(`"airfoils/main.dat"`, Selig/Lednicer 형식) |
| `main_chord` | 메인 코드 길이 [mm] |
| `flapN_airfoil` | N번째 플랩 단면 (같은 형식). 없으면 그 앞까지만 (flap1만 있으면 2단, flap2까지 있으면 3단) |
| `flapN_chord` | N번째 플랩 코드 길이 [mm] |
| `flapN_deflection` | 플랩 각도 [deg]. **메인 코드선 기준 절대각**, 뒷전이 내려가는 방향 + |
| `flapN_gap` | 앞 요소(메인 또는 flap{N-1}) 뒷전(아랫면 끝점) ~ 플랩 표면 최소 거리 [mm] |
| `flapN_overlap` | 앞 요소 뒷전 x − 플랩 앞쪽 끝 x [mm]. +면 플랩이 앞 요소 아래로 들어감 |
| `chord` | 메인 코드 [m]. 메쉬 스케일, Re, 계수 기준 길이에 쓰임. 예제는 `[derived]`에서 `main_chord / 1000`으로 계산 |
| `downforce` | `true`면 다운포스용 뒤집힌 날개 (아래 참고) |

### 다운포스 날개 (`downforce = true`)

- 전체 형상(메인 + 플랩)을 위아래로 뒤집습니다. 플랩은 앞 요소 뒷전의 **위쪽**에 놓이고, 갭·오버랩·플랩 각도 정의는 거울상 그대로입니다. 메쉬 이름 끝에 `_inv`가 붙습니다.
- **받음각 `aoa`는 다운포스가 커지는 방향이 +**입니다. Fluent에 넣는 실제 유입 각도는 `flow_angle = -aoa`로 자동 계산되고, 저널은 `${flow_angle}`을 씁니다.
- 결과의 `cl`은 **다운포스 계수(아래 방향 +)**, `l/d`는 다운포스/항력, 힘은 `downforce_N_per_m`입니다. 요소별(`cl-main`, `cl-flap1` 등)도 같은 부호입니다.
- 스크리닝은 자유류 조건이라 뒤집힌 날개가 똑바른 날개의 정확한 거울상입니다. 그래서 똑바른 형상을 같은 받음각으로 풀어 그 양력을 다운포스로 씁니다.
- 지면 효과(지면과 가까운 프런트 윙 등)는 아직 넣지 않았습니다. 지금은 자유류(지면 없음) 해석입니다.

주어진 overlap에서 gap이 정확히 맞도록 플랩 높이를 자동으로 계산하고, 요소끼리 겹치거나 불가능한 조합이면 해당 형상만 실패 처리합니다. 앞선 모든 요소와의 실제 최소 간격은 `mesh_info.json`의 `flaps[].clearance`에 기록합니다.

### 케이스 세트: 2단과 3단을 한 프로젝트에서

`[[cases.set]]`로 구성을 여러 개 정의합니다. 공통 `[cases.sweep]`(익형, 메인 코드, 받음각, 속도)은 모든 세트에 적용되고, 세트마다 자기 상수와 스윕을 더합니다.

```toml
[cases.sweep]                 # 공통
main_airfoil = ["2412", "4412"]
main_chord = [300, 375, 450]  # [mm]
aoa = [0, 4, 8, 12, 16, 20]
speed_kmh = [40, 60, 80, 100]

[[cases.set]]
label = "2el"                 # 케이스 변수 case_set 으로 사용 가능
name = "2el_{main_airfoil}c{main_chord}_f{flap1_chord}d{flap1_deflection}g{flap1_gap}_v{speed_kmh}_a{aoa}"
[cases.set.constants]
base_case = "base_2el.cas.h5"
flap1_airfoil = "4412"
flap1_overlap = 5
[cases.set.sweep]
flap1_chord = [80, 115, 150]  # [mm]
flap1_deflection = [20, 30]
flap1_gap = [6, 10]

[[cases.set]]
label = "3el"
...                           # flap2_* 추가
```

값 우선순위: `[constants]` < 세트 `constants` < CSV 행 < 공통 `sweep` < 세트 `sweep`.
세트마다 `name`, `group` 패턴을 따로 줄 수 있습니다(없으면 `[cases]`의 것).

예제 규모: 2단 72개 + 3단 192개 = 형상 264개, × 받음각 6 × 속도 4 = 6336 케이스.

## 속도와 레이놀즈수

예제는 주행 속도 40/60/80/100 km/h를 스윕하고, 속도와 메인 코드에서 유속, 마하수, 레이놀즈수를 `[derived]`로 계산합니다(Fluent 기본 공기 물성: ρ = 1.225 kg/m³, μ = 1.7894×10⁻⁵ Pa·s, 음속 340.29 m/s).

```toml
[derived]
chord = "main_chord / 1000"                          # [m]
velocity = "speed_kmh / 3.6"
mach = "velocity / 340.29"
reynolds = "1.225 * velocity * chord / 1.7894e-5"   # 메인 코드 기준

[results]
split_by = ["speed_kmh"]     # 속도별로 순위·그래프·스크리닝 선별
```

| 속도 | 유속 | 마하수 | Re (메인 300 mm) | Re (375 mm) | Re (450 mm) |
|---|---|---|---|---|---|
| 40 km/h | 11.1 m/s | 0.033 | 228,000 | 285,000 | 342,000 |
| 60 km/h | 16.7 m/s | 0.049 | 342,000 | 428,000 | 513,000 |
| 80 km/h | 22.2 m/s | 0.065 | 456,000 | 570,000 | 685,000 |
| 100 km/h | 27.8 m/s | 0.082 | 570,000 | 713,000 | 856,000 |

- Re는 입력값이 아니라 속도와 코드 길이로 정해지는 결과입니다(Re = ρVc/μ).
- **메쉬:** 형상(모든 요소의 코드·각도·갭 포함)별로 하나만 만들고 속도·받음각과는 공유합니다. 첫 셀 높이는 그 메쉬를 쓰는 케이스 중 가장 높은 Re 기준이라 모든 속도에서 y+ ≤ 목표입니다.
- **계수와 힘:** Cl, Cd, L/D는 메인 코드로 무차원화됩니다. 메인 코드나 구성(2단/3단)이 다른 형상끼리 실제 힘을 비교하려면 `summary.csv`의 `downforce_N_per_m`(똑바른 날개면 `lift_N_per_m`), `drag_N_per_m`(단위 폭 1 m당 힘 = ½ρV²·c·Cl)을 보세요.
- **Fluent:** 저널이 케이스마다 `mach` Named Expression과 기준 속도·길이·면적을 갱신합니다. 기준 속도를 갱신하지 않으면 계수가 베이스 케이스 속도 기준으로 잘못 계산됩니다.
- **`split_by`:** 스크리닝 선별, `ranking.csv` 순위, 그래프를 속도별로 따로 만듭니다. 어떤 형상이 특정 속도에서만 좋아도 그 속도에서는 살아남습니다. `plots/by_speed_kmh.png`는 상위 형상의 최대 L/D가 속도에 따라 어떻게 변하는지 보여줍니다.

## CFD 전 스크리닝 (`screen`)

익형·코드·각도 조합을 여러 개 넣으면 케이스가 금방 수천 개가 됩니다. `screen`은 Fluent를 돌리기 전에 모든 형상을 저충실도로 해석해서 민감도를 보여주고, 가망 없는 케이스를 미리 제외합니다.

**해석 방법**
- 다요소 패널법(Hess-Smith): 요소 개수와 무관하게 비점성 Cl, Cp 계산. NACA 0012 α=4°에서 Cl 0.478 등 이론값과 일치
- 적분 경계층(Thwaites → Head)으로 Cd(Squire-Young)와 윗면 박리 위치 추정. `transition = "turbulent"`(기본)는 완전 난류로 Fluent SST와 맞추고, `"free"`(예제 설정)는 자연 천이로 Fluent Transition SST와 맞춥니다
- 실속각 추정: 메인 윗면 박리 구간이 `sep_limit`(기본 10%)를 넘는 받음각
- 같은 형상의 비점성 해는 속도가 달라도 재사용합니다

**선별 규칙**
1. **선별 단위:** 케이스 세트(2단/3단)와 `[results] split_by`(속도)의 조합마다 따로 선별합니다. 3단은 양력이 크지만 항력도 커서 L/D로는 2단보다 항상 불리하게 나오기 때문에, 구성 간 비교는 CFD 결과로 합니다.
2. **계열 선별:** `protect` 변수를 제외한 나머지(익형, 플랩 코드, 플랩 각도 등)가 같은 형상들을 한 계열로 묶고, 계열 안 최고 점수로 비교합니다. 상위 `keep_top`개 계열과 최고 대비 `keep_within` 이내 계열만 남깁니다. 점수는 `[results] objective`(최대 L/D 또는 최대 Cl)를 따릅니다.
3. **보호 변수 (기본 `*gap`, `*overlap`, `main_chord`, `chord`):** 슬롯 갭·오버랩은 합류 경계층 같은 점성 효과가 커서 저충실도 예측이 부정확하고, 메인 코드는 길수록 Re가 커져 L/D가 항상 유리합니다. 그래서 이 값만으로는 제외하지 않고, 남은 계열 안에서는 모든 값을 CFD로 돌립니다. 와일드카드를 쓸 수 있습니다(`*gap`은 `flap1_gap`, `flap2_gap` 모두).
4. **받음각 선별:** 추정 실속각 + `aoa_margin`을 넘는 받음각은 제외합니다. 실속 이후 거동 확인용으로 넘는 첫 받음각 1개는 남깁니다.
5. **스팟 체크:** 선별 단위마다 제외된 계열 중 최상위 `spot_check`개의 최고 형상은 받음각 1~2개만 CFD로 돌립니다. `collect`에서 이 형상이 같은 조건의 남긴 형상보다 좋게 나오면 경고합니다. 이 경우 `keep_within`을 늘려 다시 `screen` 하세요.

**결과물 (`runs/screening/`)**

| 파일 | 내용 |
|---|---|
| `<세트·속도>/sensitivity_main_effects.png` | 변수 값별 점수 분포 (평균선, 최소~최대 범위, 형상별 점: 파랑=유지, 회색=제외) |
| `<세트·속도>/sensitivity_importance.png` | 변수 중요도: 변수 값에 따른 평균 점수 변화 폭 |
| `<세트·속도>/screening_polars.png` | 형상별 추정 Cl-α, L/D-α와 추정 실속각 |
| `<세트·속도>/screening_scatter.png` | 형상별 추정 최대 Cl vs 최대 L/D |
| `geometries.csv` | 형상·조건별 추정 실속각, 최대 Cl, 최대 L/D, 계열 순위, 유지 여부와 이유 |
| `cases.csv` | 케이스별 추정 Cl, Cd, L/D, 유지 여부와 이유 |
| `selection.json` | `run`/`mesh`가 읽는 선별 결과 |

CFD가 끝난 뒤 `collect`는 `plots/screening_vs_cfd_cl.png`, `screening_vs_cfd_ld.png`에 추정값과 CFD 결과의 상관을 그립니다. 첫 배치 결과로 스크리닝을 얼마나 믿을 수 있는지 확인하세요.

**설정 (`[screening]`)**

| 항목 | 기본값 | 내용 |
|---|---|---|
| `protect` | `["*gap", "*overlap", "main_chord", "chord"]` | 이 변수 값만으로는 제외하지 않음 (와일드카드 가능) |
| `keep_top` | 3 | 항상 유지할 상위 계열 수 |
| `keep_within` | 0.05 | 최고 계열 대비 이 비율 이내면 유지. 늘리면 안전하지만 절감이 줄어듦 |
| `spot_check` | 1 | 제외 계열 검증용으로 대표 케이스만 돌릴 계열 수 |
| `prune_aoa`, `aoa_margin` | true, 2.0 | 실속 이후 받음각 제외 여부와 여유각 |
| `sep_limit` | 0.10 | 실속 판정 박리 구간 비율 |
| `transition` | `"turbulent"` | `"free"`면 Michel 기준 자연 천이 |
| `reynolds`, `mach` | 비움 | 비우면 케이스 변수 `reynolds`/`mach` → `[mesh] reynolds` 순으로 사용 |
| `metric` | 비움 | `max_ld`/`max_cl`. 비우면 `[results] objective` |

**한계:** 경계층과 비점성 유동의 상호작용, 슬롯의 합류 경계층, 실속 이후 유동은 고려하지 않습니다. 절대값(특히 고양력 형상의 Cl)은 CFD보다 높게 나오므로 **형상 간 상대 비교와 경향 확인용**으로만 쓰세요.

## 메쉬 설정 (`[mesh]`)

| 항목 | 기본값 | 내용 |
|---|---|---|
| `chord` | 1.0 | 메인 코드 [m]. 메쉬 좌표 스케일 (케이스 변수 `chord`가 있으면 그 값) |
| `reynolds`, `y_plus` | 3e6, 1.0 | 첫 셀 높이 계산 (평판 난류 마찰계수 추정). 케이스 변수 `reynolds`가 있으면 형상별 최댓값 |
| `growth_rate` | 1.15 | 경계층 성장률 |
| `bl_thickness` | 0.02 | 경계층 사각형 영역 최대 두께 [메인 코드] |
| `gap_bl_fraction` | 0.3 | 슬롯에서 경계층 두께 ≤ 최소 간격 × 이 값 (갭이 좁으면 자동으로 얇아짐) |
| `surface_cells`, `flap_surface_cells` | 200, 150 | 메인 / 플랩마다 윗면·아랫면 각각의 표면 셀 수 (앞전·뒷전에 집중) |
| `near_size`, `wake_size` | 0.01, 0.01 | 물체 주변 / 후류 셀 크기 [메인 코드] |
| `wake_length`, `wake_angles` | 3.0, [-10, 0, 10, 20] | 후류 세분화 길이와 방향 (받음각 범위를 덮도록) |
| `farfield_radius`, `farfield_size` | 50, 5.0 | 원거리 경계 반지름과 셀 크기 [메인 코드] |
| `min_quality` | 0.05 | 최소 셀 품질 (삼각형 SICN, 사각형 scaled Jacobian). 미달 시 경계층을 얇게 해서 재시도 |

경계 이름은 `main`, `flap1`, `flap2`(wall), `farfield`(pressure-far-field), 셀 영역은 `fluid`입니다. 같은 구성(2단 또는 3단) 안에서는 이름이 같아서 Fluent에서 메쉬만 교체해도 설정이 유지됩니다. 슬롯마다(메인↔플랩1, 플랩1↔플랩2) 그 슬롯의 최소 간격 기준으로 세분화합니다.
`[mesh]` 항목과 같은 이름의 케이스 변수가 있으면 케이스 값이 우선합니다. 단 `reynolds`는 형상 안에서 가장 큰 값을 써서 속도가 달라도 메쉬를 공유합니다.

메쉬 독립성은 기준 형상 하나로 `surface_cells`, `near_size` 등을 2~3단계 바꿔 Cl/Cd가 수렴하는지 확인한 뒤, 최적화 동안에는 설정을 고정하는 것을 권장합니다. 설정이 같아야 형상 간 비교에서 메쉬 차이로 생기는 노이즈가 줄어듭니다.

## Fluent 베이스 케이스 만들기 (최초 1회, 2단·3단 각각)

해석 설정은 베이스 케이스에 해 두고, 케이스마다 메쉬만 바꿔 끼웁니다. 2단과 3단은 경계 구성이 달라서 **`base_2el.cas.h5`와 `base_3el.cas.h5`를 따로** 만듭니다(세트별 `base_case` 변수로 저널이 골라 읽음).

1. 해당 구성의 메쉬 하나를 Fluent 2D 배정밀도(2ddp)에서 엽니다: `File > Read > Mesh` → `runs/_meshes/<형상>/mesh.msh`
2. `Mesh > Check`가 통과하는지 확인합니다. 단위는 m입니다.
3. **Models**: Viscous → Transition SST (Re 수십만 대역 권장. 층류 박리 기포와 천이를 잡음) 또는 k-omega SST(완전 난류)
4. **Materials**: air, Density = **ideal-gas**. pressure-far-field 경계조건은 ideal-gas가 필요합니다.
5. **Named Expressions**: `flow_angle` = `0 [deg]`, `mach` = `0.08` (값은 저널이 케이스마다 바꿈. `flow_angle`은 실제 유입 각도로, 다운포스 날개는 `-aoa`가 들어감)
6. **Boundary Conditions**
   - `farfield` (pressure-far-field): Mach Number = `mach`, X-Component of Flow Direction = `cos(flow_angle)`, Y-Component = `sin(flow_angle)`
   - `main`, `flap1`(, `flap2`): wall
7. **Reference Values**: Compute from `farfield`, Length = 메인 코드(m), Area = 메인 코드 × 1 m (2D). 속도·길이·면적은 저널이 케이스마다 다시 설정합니다
8. **Report Definitions**: Force 리포트를 Coefficient로 만듭니다.
   - `cx`: Zones = 모든 wall / Force Vector = (1, 0)
   - `cy`: Zones = 모든 wall / Force Vector = (0, 1)
   - `cx-main`, `cy-main`, `cx-flap1`, `cy-flap1`(, `cx-flap2`, `cy-flap2`): 각 요소만
   - Report File `coef-rfile`에 위 리포트를 모두 넣습니다.

   cl/cd 대신 물체축 cx/cy를 쓰는 이유: 받음각마다 Fluent의 force vector를 바꿀 필요 없이 도구가 유입 각도 θ(`flow_angle`)로 `cl = cy·cosθ − cx·sinθ`, `cd = cx·cosθ + cy·sinθ`를 계산합니다. 다운포스 날개는 `cl` 부호를 바꿔 아래 방향을 +로 출력합니다 (`cl-flap1`, `cd-flap2` 등 요소별도 자동).
9. **이미지용 그래픽 객체와 뷰**
   - Contour `contour-pressure` (Pressure Coefficient), `contour-velocity` (Velocity Magnitude)
   - 날개 전체가 보이게 확대 → `View > Save View` 이름 `near`
   - 슬롯 부분 확대 → 이름 `slot`
10. `File > Write > Case`로 프로젝트 폴더(`examples/flap_optimization/`)에 `base_2el.cas.h5` / `base_3el.cas.h5`로 저장합니다.

### 파일럿 확인 순서

1. `sample_meshes.zip`(예제 폴더)의 메쉬 2개를 Fluent에서 열어 Mesh Check를 확인합니다.
   - 2단: `4412c375_4412c115d20g6o5_inv` (메인 375 mm + 플랩 115 mm, 뒤집힌 날개)
   - 3단: `4412c375_4412c80d25g6o5_4412c80d35g8o3_inv` (메인 375 mm + 플랩 80 mm × 2, 뒤집힌 날개)
2. 위 순서로 베이스 케이스를 만든 뒤 `File > Replace Mesh`로 같은 구성의 다른 메쉬로 바꿔도 경계조건, 리포트, 뷰가 유지되는지 확인합니다.
3. 케이스 하나씩 배치로 돌려봅니다:

   ```bash
   aero-auto run --only "2el_4412c375_f115d20g6_v100_a4"
   ```

   ```bash
   aero-auto run --only "3el_4412c375_f80d25_f80d35g8_v100_a4"
   ```

4. `runs/2el_4412c375/2el_4412c375_f115d20g6_v100_a4/solver.log`에서 저널 명령이 오류 없이 실행됐는지, `img_*.png`가 저장됐는지 확인합니다.

저널(`journal.jou`)의 TUI 명령은 Fluent 버전에 따라 프롬프트가 다를 수 있습니다. 오류가 나면 GUI에서 `File > Write > Start Journal`로 같은 작업을 녹화해 해당 줄을 교체하세요. 특히 `/file/replace-mesh`, `/define/named-expressions/edit`, `/report/reference-values/...`, `/display/save-picture` 부분을 확인하면 됩니다.

## 결과물

| 파일 | 내용 |
|---|---|
| `runs/summary.csv` | 케이스별 형상·받음각·속도, 상태, cx/cy, **cl/cd, l/d** (요소별 포함, 다운포스 날개는 cl = 다운포스 계수), `downforce_N_per_m`(또는 `lift_N_per_m`)/`drag_N_per_m`, 반복 수 |
| `runs/ranking.csv` | 형상별 최대 Cl과 그때 받음각, 최대 L/D와 그때 받음각·Cl·Cd. `objective` 기준 정렬 (`split_by` 조건별 순위, 2단·3단 함께 비교) |
| `runs/plots/*.png` | 형상별 Cl-α, Cd-α, L/D-α, 극곡선 (`split_by`가 있으면 조건별 폴더), `by_<조건>.png` |
| `runs/_meshes/<형상>/` | `geometry.png`, `mesh.png`, `mesh_slot1.png`(, `mesh_slot2.png`), `mesh_le.png`, `mesh_info.json` |
| `runs/<그룹>/<케이스>/` | `convergence.png`(리포트 수렴 이력), `img_*.png`(Fluent 컨투어), `solver.log`, `result.cas.h5` |

결과값은 마지막 `average_last`개 iteration의 평균입니다. 진동하며 수렴하는 경우를 대비한 것으로, `convergence.png`로 수렴 여부를 함께 확인하세요.

## 설정 파일 전체 구조 (`project.toml`)

| 섹션 | 내용 |
|---|---|
| `[project]` | `template`(저널), `output_dir`, `max_parallel` |
| `[solver]` | `executable`(비우면 `AWP_ROOTxxx` 환경변수로 자동 탐색), `mode`(2ddp), `processes`, `graphics`(이미지 저장), `timeout_minutes`, `extra_args` |
| `[constants]` | 공통 값 |
| `[cases]` | `csv`(행 단위 케이스, 선택), `name`(케이스 폴더 이름 패턴), `group`(분류 폴더 패턴) |
| `[cases.sweep]` | 전체 조합 변수 (세트가 있으면 모든 세트에 공통 적용) |
| `[[cases.set]]` | 케이스 세트: `label`, `constants`, `sweep`, `name`, `group` |
| `[derived]` | 계산 변수, 예: `velocity = "speed_kmh / 3.6"` |
| `[mesh]` | 위 메쉬 설정. 섹션이 없으면 메쉬 생성 없이 기존 방식으로 동작 |
| `[results]` | `report_files`, `average_last`, `x_param`, `wind_axes`, `objective`(`max_ld`/`max_cl`), `split_by`(조건별 분석 변수) |
| `[screening]` | 스크리닝 설정 (위 표 참고) |

저널 템플릿에서 쓸 수 있는 변수는 모든 케이스 변수(`case_set`, `base_case` 등 포함)에 더해 `${case_name}`, `${group}`, `${case_dir}`, `${project_dir}`, `${nprocs}`, `${mesh_file}`, `${mesh_name}`입니다. 포맷 지정도 됩니다: `${aoa:.2f}`.

## 테스트

```bash
pip install -e ".[dev]"
```

```bash
pytest
```

실제 Fluent 대신 가짜 솔버(`tests/fake_solver.py`)로 2단·3단 형상 배치 정확도(mm 단위 갭), Fluent 메쉬 면 방향 규칙, 패널법 이론값, 케이스 세트, 세트·속도별 스크리닝 선별과 실행 반영, 메쉬 생성부터 해석, cl/cd 변환, 순위, 그래프까지의 전체 흐름을 검증합니다.
