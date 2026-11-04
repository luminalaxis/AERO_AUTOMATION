# Fluent 베이스 케이스 만들기 (지면 효과 다운포스 날개, Fluent 2026 R1)

`aero-auto run`은 케이스마다 **베이스 케이스를 열고 → 메쉬만 교체하고 → 속도·기준값을 바꿔 → 계산**합니다.
그래서 물리 모델, 경계조건, 리포트, 이미지 설정을 담은 베이스 케이스를 처음 한 번 만들어야 합니다.
2단과 3단은 경계 구성이 달라서 `base_2el.cas.h5`, `base_3el.cas.h5` 두 개가 필요합니다.

> 메뉴 이름은 Fluent 버전에 따라 조금씩 다를 수 있습니다. 화면이 다르면 같은 이름의 항목을 Outline View(왼쪽 트리)에서 찾으세요.

## 0. 준비

1. `examples/flap_optimization/sample_meshes.zip`의 압축을 같은 폴더에 풉니다.
   - 2단: `4412c375_4412c115d20g6o5_inv_h30_a4/mesh.msh` (메인 375 mm + 플랩 115 mm, 지상고 30 mm, 받음각 4°)
   - 3단: `4412c375_4412c80d25g6o5_4412c80d35g8o3_inv_h30_a4/mesh.msh`
2. **Fluent Launcher** 설정
   - Dimension: **2D**
   - Options: **Double Precision** 체크
   - Parallel: Solver Processes **4** (PC 코어 수에 맞게)
   - General Options → Working Directory: `...\AERO_AUTOMATION\examples\flap_optimization`
   - Start

## 1. 메쉬 읽기와 확인

1. `File > Read > Mesh...` → 2단 `mesh.msh` 선택
2. `Domain > Mesh > Check` (또는 Outline View의 General → Check)
   - 콘솔 끝에 `Done.`이 나오고 **ERROR가 없어야** 합니다.
   - Domain Extents가 대략 x −1.9 ~ 4.5 m, 높이 약 2.25 m면 정상 (메인 코드 0.375 m의 앞 5배, 뒤 12배, 높이 6배, 단위 m)
3. `Domain > Mesh > Quality`: Minimum Orthogonal Quality가 0.1 이상이면 양호
4. `Boundary Conditions` 목록에서 이름과 타입 확인

| 경계 | 타입 |
|---|---|
| `inlet` | velocity-inlet |
| `outlet` | pressure-outlet |
| `top` | symmetry |
| `ground` | wall |
| `main`, `flap1` | wall |
| `interior-fluid` | interior |

타입이 다르게 읽혔으면 해당 경계를 선택하고 Type을 위 표대로 바꿉니다.
메쉬를 읽지 못하거나 Check에서 오류가 나면 **콘솔 메시지를 그대로 보내주세요** (메쉬 파일 형식 문제일 수 있음).

## 2. General

- Solver Type: **Pressure-Based**
- Time: **Steady**
- 2D Space: **Planar**

## 3. Models → Viscous

- **Transition SST** (4-equation) 선택, 나머지 기본값
  - Re 23만~86만 대역이라 층류 박리 기포와 천이를 잡을 수 있는 모델을 권장
  - 계산이 불안정하면 k-omega SST로 바꿔도 됩니다 (그 경우 `project.toml [screening] transition = "turbulent"`로 맞추면 좋음)

## 4. Materials → Fluid → air

- Density: **constant**, 1.225 kg/m³
- Viscosity: **constant**, 1.7894e-05 kg/(m·s)
- Change/Create
- 마하수 0.1 미만이라 비압축(밀도 일정)으로 충분합니다.

Cell Zone Conditions → `fluid`의 Material이 air인지 확인합니다.

## 5. Named Expression `speed`

버전에 따라 트리에 `Named Expressions` 항목이 따로 없을 수 있습니다(2026 R1). 둘 중 하나로 만듭니다.

**방법 1 (가장 확실): 콘솔에 입력**
```
/define/named-expressions/add "speed" definition "27.78 [m/s]" q
```
확인: `/define/named-expressions/list` 결과에 `speed`가 보이면 됩니다.

**방법 2: 트리에서**
`Parameters & Customization > Parameters > Input Parameters`를 오른쪽 클릭 → New... → Name `speed`, Definition `27.78 [m/s]` → OK
("Use as Input Parameter" 체크는 켜든 끄든 상관없음)

저널이 케이스마다 이 값을 실제 속도(11.1~27.8 m/s)로 바꿉니다. inlet과 지면이 모두 이 expression을 참조해야 속도가 함께 바뀝니다.

## 6. Boundary Conditions

### `inlet` (velocity-inlet)
- Velocity Specification Method: **Magnitude, Normal to Boundary**
- Velocity Magnitude: 입력란 오른쪽 드롭다운에서 **expression** 선택 → `speed`
- Turbulence Specification Method: **Intensity and Viscosity Ratio**
  - Turbulent Intensity: 0.5 %
  - Turbulent Viscosity Ratio: 10
- (Transition SST) Intermittency: 1 (기본값)

### `outlet` (pressure-outlet)
- Gauge Pressure: 0 Pa
- Backflow 난류 값: inlet과 동일 (0.5 %, 10)

### `top` (symmetry)
- 설정할 것 없음

### `ground` (wall) — 움직이는 바닥
- Wall Motion: **Moving Wall**
- Motion: **Absolute**, **Translational**
- Speed: expression → `speed`
- Direction: X = 1, Y = 0
- Shear Condition: No Slip

차량 기준 좌표에서 공기가 +x로 들어오고 바닥도 같은 속도로 +x로 움직이는 실제 주행 조건입니다.

### `main`, `flap1` (wall)
- Stationary Wall, No Slip (기본값)

## 7. Reference Values

`Setup > Reference Values`

- Compute from: **inlet**
- Area: **0.375** (m², 메인 코드 × 폭 1 m)
- Length: **0.375** (m)
- Density: 1.225, Velocity: 27.78
- Reference Zone: fluid

케이스마다 저널이 속도·길이·면적을 다시 넣으므로 여기 값은 샘플 기준이면 됩니다.

## 8. Solution Methods / Controls

`Solution > Methods`
- Pressure-Velocity Coupling: **Coupled**
- **Pseudo Time Method** 사용 (체크박스 또는 드롭다운) → 지면 근처 수렴이 안정적
- Spatial Discretization: Gradient Least Squares Cell Based, Pressure **Second Order**, 나머지 **Second Order Upwind**

Controls는 기본값으로 시작합니다.

### 잔차 기준 (꼭 바꾸기)

`Solution > Monitors > Residual`: **Absolute Criteria를 모두 1e-6**으로 낮춥니다.
- 기본값(1e-3)이면 다운포스·항력이 수렴하기 전에 계산이 멈춥니다. 지면 근처 다요소 날개는 특히 그렇습니다.
- 1e-6에 도달하면 Fluent가 "solution is converged"를 출력하고 일찍 끝나며, 도구는 그 경우도 `converged`로 기록합니다.
  그 밖에는 `iterations`(예제 2000)회를 다 돌고, 도구가 마지막 반복들의 힘계수 진폭으로 수렴 상태를 판정합니다.

### (선택) 힘계수 수렴 조건 — 계산 시간 줄이기

케이스가 수천 개라 케이스당 시간이 전체 일정을 좌우합니다. 다운포스·항력이 더 변하지 않으면 바로 멈추게 할 수 있습니다.
`Solution > Monitors > Convergence Conditions`(버전에 따라 Report Definitions 옆) → 9번에서 만든 **`cy`, `cx` 추가**

| 항목 | 값 |
|---|---|
| Stop Criterion | 1e-5 |
| Ignore Iterations Before (Initial Values to Ignore) | 300 |
| Use Iterations (Previous Values to Consider) | 50 |
| Use / Print | 체크 |

- 두 조건을 모두 만족할 때 멈추도록 **Every Iteration / All Conditions**로 둡니다.
- 멈춘 케이스도 도구가 마지막 반복 이력으로 수렴 상태를 다시 판정하므로, 너무 일찍 멈추면 `oscillating`으로 표시됩니다.
  파일럿에서 `convergence.png`를 보고 너무 이르면 Stop Criterion을 1e-6으로 낮추세요.

## 9. Report Definitions (다운포스·항력)

`Solution > Report Definitions > New > Force Report > Force...`
(생성 창 아래의 Report File / Report Plot 체크는 하지 않아도 됩니다. 10번에서 한 번에 묶습니다.)

| Name | Report Output Type | Force Vector (X, Y) | Zones |
|---|---|---|---|
| `cx` | **Force Coefficient** | 1, 0 | main, flap1 |
| `cy` | Force Coefficient | 0, 1 | main, flap1 |
| `cx-main` | Force Coefficient | 1, 0 | main |
| `cy-main` | Force Coefficient | 0, 1 | main |
| `cx-flap1` | Force Coefficient | 1, 0 | flap1 |
| `cy-flap1` | Force Coefficient | 0, 1 | flap1 |

- **Zones에 `ground`는 절대 넣지 마세요.** 바닥에 걸리는 힘이 섞입니다.
- 이름은 위 표와 **정확히 같아야** 합니다(`cx`, `cy`로 시작하는 이름을 도구가 짝지어 다운포스·항력으로 변환).
- 날개가 뒤집혀 있어서 `cy`는 음수(아래 방향)로 나오는 게 정상입니다. 도구가 부호를 바꿔 다운포스를 +로 정리합니다.

## 10. Report File

`Solution > Monitors > Report Files > New...`

- Name: **`coef-rfile`**
- 오른쪽 목록에서 9번의 6개 리포트를 모두 추가
- File Name: `coef-rfile.out`
- Get Data Every: 1 Iteration

## 11. 이미지용 그래픽 객체와 뷰

### Contour 2개
`Results > Graphics > Contours > New...`

| Name | Contours of | 옵션 |
|---|---|---|
| `contour-pressure` | Pressure → **Pressure Coefficient** | Filled 체크 |
| `contour-velocity` | Velocity → **Velocity Magnitude** | Filled 체크 |

- Surfaces는 **선택하지 않아도 됩니다** (비워 두면 영역 전체에 그림).
- 아직 계산 결과가 없어서 `Compute`는 회색입니다. **Save → Close**만 하면 됩니다.

### 저장 뷰 2개 (`near`, `slot`)
결과가 없으니 메쉬를 띄운 상태에서 화면만 맞춥니다.

1. `Results > Graphics > Mesh` 더블클릭 → Display (또는 General 패널의 Display...)
2. 마우스로 화면 맞추기 (휠: 확대·축소, 드래그: 이동). 날개 전체와 바닥이 함께 보이게.
3. 콘솔에 입력:
   ```
   /views/save-view near
   ```
4. 메인 뒷전과 플랩 사이 슬롯을 크게 확대한 뒤:
   ```
   /views/save-view slot
   ```
5. 확인: `/views/restore-view near` 를 입력하면 저장한 화면으로 돌아가야 합니다.

GUI로는 상단 리본 **Viewing** 탭의 **Views...** 버튼 → Save Name 입력 → Save 입니다 (버전에 따라 위치가 다를 수 있어 콘솔 방법을 권장).

## 12. 동작 확인 (선택, 권장)

1. `Solution > Initialization`: **Hybrid** → Initialize
2. `Run Calculation`: Number of Iterations 20 → Calculate
3. 오류 없이 돌고, 작업 폴더에 `coef-rfile.out`이 생기는지 확인
   - `cy` 값이 음수(다운포스)로 나오면 정상

## 13. 저장

`File > Write > Case...` → `examples\flap_optimization\base_2el.cas.h5`

(데이터는 필요 없습니다. 12번에서 계산했어도 Case만 저장하면 됩니다.)

## 14. 3단 베이스 케이스 (`base_3el.cas.h5`)

**방법 A (빠름):** 2단 설정을 재활용
1. `base_2el.cas.h5`가 열린 상태에서 `File > Replace Mesh...` → 3단 `mesh.msh`
2. Boundary Conditions에서 새로 생긴 `flap2`가 wall(정지)인지 확인
3. Report Definitions
   - `cx`, `cy`의 Zones에 `flap2` 추가
   - `cx-flap2`(1, 0), `cy-flap2`(0, 1) 새로 만들기 (Zones = flap2)
4. Report File `coef-rfile`에 `cx-flap2`, `cy-flap2` 추가
5. 뷰 `near`, `slot`이 3단 형상에 맞는지 확인하고 필요하면 다시 저장
6. `File > Write > Case...` → `base_3el.cas.h5`

**방법 B:** 3단 메쉬로 1~13번을 처음부터 반복 (`flap2` 추가). 방법 A에서 오류가 나면 이쪽으로.

## 15. 저널 녹화 (권장)

저널 명령 형식을 2026 R1에 정확히 맞추기 위해, 베이스 케이스 저장 후 아래 작업을 녹화해 주세요.

1. `File > Write > Start Journal...` → `record.jou`
2. 다음 작업을 GUI로 한 번씩 수행
   - `File > Replace Mesh...` (다른 메쉬, 예: 같은 폴더의 다른 형상)
   - Named Expression `speed`를 `20 [m/s]`로 수정
   - Reference Values의 Velocity를 20으로 수정
   - `contour-pressure` 표시 → `File > Save Picture...`로 png 저장
3. `File > Write > Stop Journal`
4. `record.jou`를 보내주시면 `journal.jou`를 그 형식에 맞춰 고칩니다.

## 16. 점검과 파일럿 실행

먼저 Fluent 없이 확인할 수 있는 것을 점검합니다 (베이스 케이스 파일, 메쉬, 저널 치환, 경로, 디스크·시간 추정).

```bat
cd examples\flap_optimization
```
```bat
aero-auto check
```

`[오류]`, `[할 일]`이 없어야 합니다. Fluent 실행 파일을 못 찾으면 `project.toml`의 `[solver] executable`에 경로를 적습니다.
경로에 한글이 있다는 `[경고]`가 나오면 프로젝트를 `C:\aero` 같은 영문 경로로 옮기세요 (Fluent가 파일을 못 읽는 경우가 많음).

케이스 하나씩 배치로 돌려봅니다. 스크리닝에서 남은 형상이라 결과가 본 배치에 그대로 쓰입니다
(베이스 케이스를 만든 샘플 메쉬와 형상이 달라도 같은 2단·3단 구성이면 메쉬만 바뀌어 그대로 돌아갑니다).

```bat
aero-auto run --only "2el_4412c375_f150d30g6_h30_v100_a4"
```
```bat
aero-auto run --only "3el_4412c375_f150d25_f150d45g5_h30_v100_a4"
```

확인할 것
- `runs\2el_4412c375_h30\2el_4412c375_f150d30g6_h30_v100_a4\transcript.txt`에 Error가 없는지.
  Windows에서 `-hidden`으로 실행하면 Fluent 출력은 `solver.log`가 아니라 이 파일에 남습니다(저널 첫 줄의 `/file/start-transcript`).
  도구도 이 파일에서 오류·발산·잔차를 읽습니다.
- 같은 폴더에 `coef-rfile.out`, `img_*.png`, `result.cas.h5`가 생겼는지
- `convergence.png`: 다운포스(`cy`)가 평평해졌는지, 잔차 기준 때문에 너무 일찍 멈추지 않았는지
- 저널 명령이 맞지 않으면 transcript.txt의 첫 오류 줄을 보고 15번(저널 녹화)으로 해당 줄을 고칩니다.

파일럿이 끝나면 다시 `aero-auto check`를 실행합니다. 끝난 케이스의 실제 실행 시간과 폴더 크기로 전체 시간·디스크를 다시 계산해 줍니다.
디스크가 모자라면 `journal.jou`의 `/file/write-case-data` 줄 앞에 `;`를 붙여 끄세요 (컨투어 이미지는 그대로 저장됨).

## 17. 메쉬 독립성 확인 (본 배치 전 한 번)

같은 케이스를 메쉬 3단계(성김·보통·조밀, 셀 크기 1.5배 차이)로 풀어 본 배치 메쉬(보통)의 이산화 불확실성을 구합니다.
베이스 케이스와 저널은 본 배치와 같은 것을 씁니다 (`mesh_study.toml`, 결과는 `runs_mesh/`).

```bat
aero-auto -c mesh_study.toml mesh
```
```bat
aero-auto -c mesh_study.toml run
```
```bat
aero-auto -c mesh_study.toml gci
```

- 2단·3단 각 1개 형상 × 받음각 4°, 12° × 3단계 = 12 케이스 (지상고 30 mm, 100 km/h: 가장 까다로운 조건)
- 셀 수: 성김 3.3만~4.9만, 보통 7.3만~10.9만(본 배치와 같은 메쉬), 조밀 16만~24만. 조밀 메쉬는 케이스당 시간이 2배 넘게 걸립니다.
- `gci`가 Cl, Cd(요소별 포함)마다 관측 차수 p, 셀 크기 0으로 외삽한 값, GCI(%)를 출력하고 `runs_mesh/mesh_study/gci.csv`, `gci_*.png`에 저장합니다.
- 보통 메쉬의 GCI가 Cl 2%, Cd 5% 이내면 본 배치 설정 그대로 진행합니다. 넘으면 `project.toml`의 `[mesh] refine = 1.3` 처럼 전체를 조밀하게 하고 다시 확인합니다.
- `convergence`가 `monotonic`이 아니면(진동·발산) 먼저 각 케이스의 반복 수렴(`conv_state`, `convergence.png`)을 확인하세요.

## 18. 본 배치 (단계적)

**1단계:** 스팟 체크 + 계열·조건별 대표 형상의 전체 받음각 (예제 172 케이스)

```bat
aero-auto run --stage1 --priority
```
```bat
aero-auto collect
```

- `--priority`: 스팟 체크(스크리닝이 뺀 쪽을 대표로 확인하는 케이스) → 점수 높은 계열·형상 → 받음각 순서로 실행합니다. 중간에 멈춰도 중요한 결과가 먼저 남습니다.
- `collect`에 스팟 체크 경고가 나오면(제외된 형상이 CFD에서 더 좋음) `[screening] keep_within`을 늘리거나 `protect`에 그 변수를 넣고 `screen`부터 다시 합니다.

**2단계:** 대표 형상의 최대 다운포스 받음각 주변 3개만 남기고 나머지 실행 (예제 3,484 → 1,828 케이스)

```bat
aero-auto narrow-aoa --dry-run
```
```bat
aero-auto narrow-aoa
```
```bat
aero-auto run --priority
```

- `--dry-run`으로 그룹별 최대 각도와 남길 받음각을 먼저 봅니다. "범위 끝" 경고가 나오면 받음각 범위를 넓히는 것을 검토하세요.
- 진행 상황과 남은 시간: `aero-auto status`. `--limit 100`으로 나눠 돌릴 수도 있습니다.
