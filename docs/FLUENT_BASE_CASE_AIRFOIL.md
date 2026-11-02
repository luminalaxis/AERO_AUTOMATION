# Fluent 베이스 케이스 만들기 (단일 익형 데이터셋, Fluent 2026 R1)

`examples/airfoil_dataset`의 `aero-auto run`은 케이스마다 **베이스 케이스를 열고 → 형상 메쉬로 교체하고 → 유속·받음각·기준값을 바꿔 → 계산**합니다.
물리 모델, 경계조건, 리포트를 담은 `base_airfoil.cas.h5`를 처음 한 번만 만들면 됩니다.
팀원 4명이 **같은 베이스 케이스 파일**을 써야 결과가 일치합니다. 한 명이 만들어 공유하세요.

> 메뉴 이름은 Fluent 버전에 따라 조금씩 다를 수 있습니다. 화면이 다르면 같은 이름의 항목을 Outline View(왼쪽 트리)에서 찾으세요.

## 0. 준비

```bash
cd examples/airfoil_dataset
```

```bash
aero-auto mesh --only "naca0012*"
```

`runs/_meshes/0012_c0.3/mesh.msh`가 생깁니다 (NACA 0012, 코드 0.3 m, 약 5만 셀, 첫 셀 높이는 Re 5×10⁵에서 y+ ≈ 1).
`geometry.png`, `mesh.png`, `mesh_le.png`로 형상과 경계층을 먼저 확인하세요.

Fluent Launcher: Dimension **2D**, **Double Precision**, Solver Processes 4, Working Directory = `examples\airfoil_dataset`

## 1. 메쉬 읽기와 확인

1. `File > Read > Mesh...` → `runs\_meshes\0012_c0.3\mesh.msh`
2. `Domain > Mesh > Check`: 끝에 `Done.`, ERROR 없음
   - Domain Extents가 대략 x −14.9 ~ 15.1 m, y −15 ~ 15 m (반지름 50c = 15 m 원)
3. `Domain > Mesh > Quality`: Minimum Orthogonal Quality 0.1 이상이면 양호
4. 경계 이름과 타입

| 경계 | 타입 | 위치 |
|---|---|---|
| `inlet` | velocity-inlet | 원거리 경계 앞쪽 반원 |
| `outlet` | pressure-outlet | 원거리 경계 뒤쪽 반원 |
| `main` | wall | 익형 |
| `interior-fluid` | interior | |

## 2. General

Pressure-Based, **Steady**, 2D Space **Planar**

## 3. Models → Viscous

- **k-omega → SST** (기획서 기준: k-ω SST 완전 난류)
- Transition SST 비교용 베이스 케이스는 맨 아래 "Transition SST 비교"를 보세요.

## 4. Materials → air

Density **constant 1.225**, Viscosity **constant 1.7894e-05** (마하수 0.03~0.07이라 비압축으로 충분)

## 5. Named Expression `speed`, `alpha`

콘솔에 입력합니다.

```
/define/named-expressions/add "speed" definition "9.738 [m/s]" q
```

```
/define/named-expressions/add "alpha" definition "4 [deg]" q
```

확인: `/define/named-expressions/list`에 두 개가 보이면 됩니다.
저널이 케이스마다 `speed`(Re 2×10⁵ → 9.738 m/s, 5×10⁵ → 24.35 m/s)와 `alpha`(받음각)를 바꿉니다.

## 6. Boundary Conditions

### `inlet` (velocity-inlet)
- Velocity Specification Method: **Components**
- X-Velocity: 입력란 오른쪽 드롭다운에서 expression → `speed * cos(alpha)`
- Y-Velocity: expression → `speed * sin(alpha)`
- Turbulence: **Intensity and Viscosity Ratio**, Intensity **0.1 %**, Viscosity Ratio **10**

### `outlet` (pressure-outlet)
- Gauge Pressure 0, Backflow 난류 값은 inlet과 같게

### `main` (wall)
- Stationary Wall, No Slip (기본값)

## 7. Reference Values

- Compute from: **inlet**
- Area **0.3**, Length **0.3** (코드 0.3 m × 폭 1 m), Density 1.225, Velocity 9.738

케이스마다 저널이 Velocity·Length·Area를 다시 넣습니다.

## 8. Solution Methods / Controls / 잔차 기준

- Pressure-Velocity Coupling **Coupled**, **Pseudo Time Method** 사용
- Gradient Least Squares Cell Based, Pressure **Second Order**, 나머지 **Second Order Upwind**
- `Solution > Monitors > Residual`: **Absolute Criteria를 모두 1e-6**으로 낮춥니다.
  - 기본값(1e-3)이면 힘 계수가 수렴하기 전에 계산이 멈춥니다.
  - 1e-6에 도달하면 Fluent가 "solution is converged"를 출력하고 일찍 끝나며, 도구는 이 경우를 `converged`로 기록합니다.

## 9. Report Definitions와 Report File

`Solution > Report Definitions > New > Force Report > Force...`

| Name | Report Output Type | Force Vector (X, Y) | Zones |
|---|---|---|---|
| `cx` | Force Coefficient | 1, 0 | main |
| `cy` | Force Coefficient | 0, 1 | main |

이름은 정확히 `cx`, `cy`여야 합니다. 도구가 받음각 α로 `cl = cy·cosα − cx·sinα`, `cd = cx·cosα + cy·sinα`를 계산하므로 받음각마다 힘 방향을 바꿀 필요가 없습니다.

`Solution > Monitors > Report Files > New...`
- Name **`coef-rfile`**, 위 2개 리포트 추가, File Name `coef-rfile.out`, Get Data Every 1 Iteration

이 파일의 반복별 이력으로 수렴 상태(converged / oscillating / diverged)를 판정합니다.

## 10. 동작 확인 (권장)

1. `Solution > Initialization`: **Hybrid** → Initialize
2. `Run Calculation`: 20 iterations → Calculate
3. 오류 없이 돌고 `coef-rfile.out`이 생기는지 확인

## 11. 저장

`File > Write > Case...` → `examples\airfoil_dataset\base_airfoil.cas.h5` (Case만 저장, 데이터 불필요)

## 12. 파일럿 실행

```bash
aero-auto run --all --only "naca0012_Re200000_a4" "naca0012_Re500000_a12"
```

```bash
aero-auto collect --only "naca0012*"
```

확인할 것
- `runs\naca0012\naca0012_Re200000_a4\solver.log`에 Error가 없는지
- `runs\summary.csv`의 해당 행: `cl` ≈ 0.42~0.46, `cd` ≈ 0.011~0.015 (완전 난류 SST, Re 2×10⁵), `conv_state`
- `convergence.png` 제목에 수렴 상태가 표시됨
- 케이스당 소요 시간 (`aero-auto status`) → 3,360 케이스 전체 일정 계산

저널 명령이 버전에 맞지 않으면 GUI에서 `File > Write > Start Journal`로 같은 작업(Replace Mesh, Named Expression 수정, Reference Values 수정)을 녹화해 `journal.jou`의 해당 줄을 교체하세요.

## 메시 독립성과 풍동 실측 비교 (본 생성 전)

기획서의 "해석 신뢰성 확인" 단계입니다. 본 생성과 같은 베이스 케이스로 NACA 0012만 돌립니다.

1. `[mesh]`의 `surface_cells`(100 / 200 / 400)와 `y_plus`(2 / 1 / 0.5)를 바꾼 프로젝트 사본(`project_mesh.toml`, `output_dir = "runs_mesh"`)을 만들어 `--only "naca0012*"`로 실행
2. CL, CD 변화가 1~2% 이내로 들어오는 단계를 본 생성 설정으로 고정
3. 공개 풍동 실측(예: Re 2×10⁵ 부근 NACA 0012 극선도)과 같은 받음각에서 비교

## Transition SST 비교 (경계 사례용)

1. `base_airfoil.cas.h5`를 열고 Viscous → **Transition SST**, inlet Intermittency 1 → `base_airfoil_tsst.cas.h5`로 저장
2. `project.toml`을 `project_tsst.toml`로 복사해 아래만 바꿉니다.
   ```toml
   [project]
   output_dir = "runs_tsst"
   [constants]
   base_case = "base_airfoil_tsst.cas.h5"
   turbulence_model = "transition-sst"
   ```
3. 비교할 경계 사례만 실행
   ```bash
   aero-auto -c project_tsst.toml run --all --only "naca2412_Re200000_a1[0-6]"
   ```
4. 같은 이름의 케이스를 `runs/summary.csv`와 `runs_tsst/summary.csv`에서 비교합니다. Re 2×10⁵에서는 층류 박리 기포 때문에 두 모델의 CD 차이가 크게 날 수 있습니다.
