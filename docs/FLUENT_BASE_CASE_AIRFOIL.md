# Fluent 베이스 케이스 만들기 (단일 에어포일, 자유류, Fluent 2026 R1)

`examples/airfoil_surrogate` 용입니다. 진행 순서는 지면 효과 예제의 [FLUENT_BASE_CASE.md](FLUENT_BASE_CASE.md)와 같고, 아래는 **다른 부분만** 적었습니다.
필요한 베이스 케이스는 다음 두 개입니다.

| 파일 | 난류 모델 | 쓰는 곳 |
|---|---|---|
| `base_1el_sst.cas.h5` | k-omega SST | `project.toml` (학습 데이터 전체), `project_mesh.toml` (메시 독립성) |
| `base_1el_tsst.cas.h5` | Transition SST | `project_tsst.toml` (Re 2×10⁵ 난류 모델 비교) |

## 0. 샘플 메쉬 만들기

```bash
cd examples/airfoil_surrogate
```

```bash
aero-auto mesh --only "NACA2412_*"
```

`runs/_meshes/NACA2412_c1/mesh.msh`가 생깁니다. 코드 1 m, 반지름 50 m의 원형 영역입니다.

## 1. 메쉬 확인

- Domain Extents가 x −49.5 ~ 50.5 m, y −50 ~ 50 m 정도면 정상입니다.
- 경계 이름과 타입이 아래와 같은지 확인합니다.

| 경계 | 타입 |
|---|---|
| `inlet` | velocity-inlet (앞쪽 반원) |
| `outlet` | pressure-outlet (뒤쪽 반원) |
| `main` | wall |
| `interior-fluid` | interior |

## 2. General / Models / Materials

- 솔버는 Pressure-Based, Steady, 2D Planar입니다.
- Viscous는 **k-omega SST**로 둡니다. Transition SST 베이스 케이스는 이것만 바꿔 따로 저장합니다(아래 9번).
- 공기는 **constant** 밀도 1.225, 점성계수 1.7894e-05입니다. Re 2×10⁵는 코드 1 m에서 약 2.9 m/s라서 비압축으로 충분합니다.

## 3. Named Expression 2개

콘솔에 입력합니다.

```
/define/named-expressions/add "speed" definition "2.92 [m/s]" q
/define/named-expressions/add "flow_angle" definition "4 [deg]" q
```

케이스마다 저널이 `speed`(Re에서 계산한 유속)와 `flow_angle`(받음각)을 바꿉니다.

## 4. Boundary Conditions

### `inlet` (velocity-inlet)

- Velocity Specification Method: **Components**
  - X-Velocity: expression `speed * cos(flow_angle)`
  - Y-Velocity: expression `speed * sin(flow_angle)`
- Turbulence: **Intensity and Viscosity Ratio**, 0.1 %, 10 (난류 강도가 낮은 풍동 조건)
  - Transition SST는 난류 강도가 천이 위치를 좌우합니다. 원거리(50c)에서 익형까지 오는 동안 강도가 줄어드므로, 익형 앞 강도를 확인하고 두 모델에 같은 값을 쓰세요.

### `outlet` (pressure-outlet)

- Gauge Pressure는 0입니다.
- Backflow Direction Specification Method를 **From Neighboring Cell**로 둡니다. 큰 받음각에서 바깥 반원 일부로 유동이 들어와도 안정적입니다.
- Backflow 난류 값은 inlet과 같게 둡니다.

### `main` (wall)

Stationary Wall, No Slip입니다.

## 5. Reference Values

- Compute from: inlet
- Area = 1, Length = 1, Density = 1.225, Velocity = 2.92

저널이 매 케이스 다시 넣습니다.

## 6. Solution Methods

- Coupled, **Pseudo Time Method**를 씁니다.
- Gradient는 Least Squares Cell Based, 나머지는 Second Order (Upwind)입니다.
- **Residual 수렴 판정을 끄거나**(Convergence Criterion: none) 1e-8처럼 아주 작게 둡니다.
  - 모든 케이스가 `iterations`(3000)회를 다 돌아야 마지막 500회 평균과 진동 판정이 같은 기준이 됩니다.

## 7. Report Definitions

### Force 리포트 (Force Coefficient)

| Name | Force Vector | Zones |
|---|---|---|
| `cx` | 1, 0 | main |
| `cy` | 0, 1 | main |

받음각은 유입 방향으로 주므로, 도구가 `flow_angle`로 회전해 cl, cd를 계산합니다.

### y+ 리포트 (Surface Report, 데이터 품질 기록용)

| Name | Report Type | Field | Surfaces |
|---|---|---|---|
| `yplus-avg` | **Area-Weighted Average** | Turbulence → Wall Yplus | main |
| `yplus-max` | **Facet Maximum** | Turbulence → Wall Yplus | main |

이름에 `yplus`가 들어가면 `aero-auto dataset`이 자동으로 집계해 `data_report.md`에 보고합니다.

## 8. Report File

- Name을 `coef-rfile`, File Name을 `coef-rfile.out`으로 합니다.
- `cx`, `cy`, `yplus-avg`, `yplus-max`를 넣고 Get Data Every는 1 Iteration입니다.

## 9. 저장

- `File > Write > Case...`로 `base_1el_sst.cas.h5`를 `examples/airfoil_surrogate`에 저장합니다.
- Transition SST 베이스 케이스는 다음 순서로 만듭니다.
  1. Viscous만 **Transition SST**로 바꿉니다.
  2. inlet Intermittency는 1로 둡니다.
  3. `base_1el_tsst.cas.h5`로 저장합니다.

## 10. 파일럿

```bash
aero-auto run --only "NACA2412_Re200k_a4" "NACA2412_Re200k_a15"
```

```bash
aero-auto collect
```

확인할 것:

- `runs/NACA2412/NACA2412_Re200k_a4/convergence.png`에서 cx, cy가 평평해졌는지 봅니다.
- `runs/summary.csv`에서 다음 열을 확인합니다.
  - `cl` ≈ 0.6~0.7 (Re 2×10⁵, α 4°)
  - `convergence`
  - `yplus-max` ≤ 1 정도
- 실속 부근(α 15°)은 `oscillating`이 나올 수 있습니다. 값은 마지막 500회 평균입니다.
