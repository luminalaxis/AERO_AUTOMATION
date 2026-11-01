# 에어포일 Cl/Cd 대리 모델 (CFD 없이 양항계수 예측)

인공지능개론 텀프로젝트 기획서 "CFD 없이 에어포일 양항계수를 예측하는 회귀 모델"을 이 도구로 수행하는 방법입니다.
CFD 배치(데이터 생성)는 기존 기능(`plan`/`mesh`/`run`/`collect`)을 그대로 쓰고, 그 뒤 단계는 `dataset`/`train`/`compare`가 맡습니다.

```
examples/airfoil_surrogate/project.toml
   │ fetch_uiuc.py   UIUC 좌표 내려받기 → airfoils.csv (NACA 60 + UIUC 약 60)
   │ run --part k/4  팀원 4명이 형상 단위로 나눠 Fluent 배치 (형상 120 × α 14 × Re 2)
   │ collect         summary.csv: Cl, Cd + 수렴 상태(converged/oscillating/diverged) + y+
   │ dataset         CST 16·8계수 피팅, 라벨 필터, 조건 ①(regime_truth), 형상 단위 분할 + 분포 밖 세트
   │ train --suite   기준 방법 / LightGBM 분위 회귀 / MLP 5시드 앙상블, 실험 1·2, 14주차 실험
   │ compare
   ▼
runs/surrogate/dataset.csv, shapes.csv, split.json, data_report.md, turbulence_comparison.csv
runs/surrogate/experiments/<실험>/predictions.csv, metrics.json, conditions.csv, boundary_candidates.csv, *.png
runs/surrogate/comparison.md, comparison.csv
```

## 기획서 항목과 기능 대응

| 기획서 | 이 도구 |
|---|---|
| 입력: CST 16개, α, log₁₀Re → 출력: C_L, log₁₀C_D | `dataset.csv`의 `cst16_u0..u7, cst16_l0..l7`, `aoa`, `log10re` → `cl`, `log10cd` |
| 형상: NACA 4자리 + UIUC | `airfoils.csv` (NACA 60개 포함). `fetch_uiuc.py`로 UIUC를 받아 추가 |
| 받음각 14개 (−4°~16°, 11°~16°는 1° 간격) × Re 2개 | `project.toml` `[cases.sweep]` `aoa`, `re_k` |
| 정답: Fluent 2D k-ω SST | `journal.jou` + `base_1el_sst.cas.h5` ([베이스 케이스 만들기](FLUENT_BASE_CASE_AIRFOIL.md)) |
| 팀원 4인이 동일 스크립트로 분담 | `aero-auto run --part 1/4` … `4/4` (같은 형상은 한 사람에게) |
| 수렴 상태와 y⁺ 기록 | `summary.csv`의 `convergence`, `cl_std`, `cd_std`, `conv_metric`, `yplus-avg`, `yplus-max` |
| 진동: 마지막 500회 평균 | `[results] average_last = 500`, 진동 판정 `converge_tol` |
| 발산: 제외하고 건수만 보고 | `dataset`이 `diverged`/`timeout`/`failed`를 빼고 `data_report.md`에 건수 표시 |
| 형상별/고캠버 계열을 분포 밖 세트로 | `[surrogate] ood_camber` (최대 캠버 기준), `ood_airfoils` (직접 지정) |
| 나머지 70/15/15 | `[surrogate] split`. 형상 단위, 출처(NACA/UIUC)별로 나눔 |
| NACA 0012 메시 독립성 | `project_mesh.toml` (거친/기준/조밀 3단계) |
| 풍동 실측 비교 | `runs_mesh/summary.csv`를 실험값과 비교 (실험값은 직접 준비) |
| Re 2×10⁵에서 SST vs Transition SST | `project_tsst.toml` → `turbulence_comparison.csv` |
| 기준 방법: C_L = 2π(α − α₀), α₀는 캠버선, C_D는 학습 평균 | `train --model baseline` (α₀: CST 캠버선으로 얇은 익형 이론 적분) |
| 비교 모델 1: LightGBM 분위 회귀 | `train --model lgbm` (점 예측 + 5%/95% 분위 → 90% 구간) |
| 비교 모델 2: MLP 5시드 앙상블 | `train --model mlp` (평균·분산 출력, deep ensemble) |
| 실험 1: CST 16 vs 8(재피팅) | `train --cst 8` (면당 4계수로 다시 피팅한 `cst8_*` 사용) |
| 실험 2: MLP 은닉층 3단계 | `train --hidden 32,32` / `128,128` / `512,512` |
| 동일 분할·동일 지표 | 모든 실험이 같은 `split.json`, Cl MAE와 log₁₀Cd MAE |
| 조건별 성능과 표본 수 | `conditions.csv` (regime_truth, aoa_band, reynolds, source, pi_width) |
| 형상 단위 부트스트랩 95% CI | `metrics.json`·`conditions.csv`의 `*_mae_ci` (형상 복원 추출) |
| 90% 예측 구간 포함률 | `*_coverage`. 보정이 필요하면 `--calibrate` (등각 보정) |
| 조건 ①은 평가 목적으로만 | `regime_truth`: 정답 극곡선의 α_clmax 기준. 입력에 넣지 않고 평가·학습 가중치에만 씀 |
| 경계 사례 10개 | `boundary_candidates.csv` (실속 부근 최악/최선, 전체 최악/최선, 난류 모델 민감 케이스) |
| 14주차: 실속 가중치, 분포 밖 일부 추가 | `train --suite week14` (`--stall-weight 3`, `--add-ood`) |
| 오픈소스(scikit-learn, LightGBM, PyTorch)만 | `pip install -e ".[ml]"` |

## 진행 순서

### 0. 설치

```bash
pip install -e ".[ml]"
```

### 1. 형상 목록

```bash
cd examples/airfoil_surrogate
```

```bash
python fetch_uiuc.py
```

`airfoils/`에 UIUC `.dat`가 저장되고 `airfoils.csv`가 NACA + UIUC로 갱신됩니다. 받지 못한 이름은 끝에 표시되니 다른 익형으로 바꾸면 됩니다.
좌표 읽기가 실패하거나 CST 피팅 오차가 큰 파일(좌표 이상)은 자동으로 빠집니다.

`airfoils.csv` 열:
- `main_airfoil`: `NACA2412` 또는 `airfoils/e387.dat`. NACA는 반드시 `NACA`를 붙입니다. 숫자만 쓰면 `0012`가 12로 읽힙니다.
- `foil`: 케이스·폴더 이름
- `source`: `naca` 또는 `uiuc`. 분할과 조건별 성능에 쓰입니다.

### 2. 베이스 케이스와 파일럿

[FLUENT_BASE_CASE_AIRFOIL.md](FLUENT_BASE_CASE_AIRFOIL.md)를 따라 `base_1el_sst.cas.h5`(필요하면 `base_1el_tsst.cas.h5`도)를 만듭니다. 그다음 케이스 몇 개만 돌려 봅니다.

```bash
aero-auto run --only "NACA2412_Re200k_a4" "NACA2412_Re200k_a15"
```

### 3. 신뢰성 검증 (본 계산 전에 권장)

메시 독립성: NACA 0012를 거친/기준/조밀 메쉬로 돌립니다.

```bash
aero-auto -c project_mesh.toml run
```

```bash
aero-auto -c project_mesh.toml collect
```

난류 모델 비교: Re 2×10⁵, 형상 5개를 Transition SST로 돌립니다.

```bash
aero-auto -c project_tsst.toml run
```

```bash
aero-auto -c project_tsst.toml collect
```

- 메시 비교는 `runs_mesh/summary.csv`(세트 `coarse`/`medium`/`fine`)에서 Cl, Cd 변화를 봅니다. 기준(medium)이 `project.toml`의 `[mesh]`와 같습니다.
- 난류 모델 비교는 `dataset` 단계에서 `turbulence_comparison.csv`로 자동 정리됩니다.

### 4. 본 계산 (4명 분담)

각자 같은 저장소와 베이스 케이스로 자기 몫만 실행합니다.

```bash
aero-auto run --part 1/4
```

(2번 팀원은 `--part 2/4`, …) `--part`는 형상 이름을 정렬해 번갈아 나누므로, 설정 파일이 같으면 누가 실행해도 몫이 같습니다. 중단돼도 다시 실행하면 완료된 케이스는 건너뜁니다.

각자 결과를 정리합니다.

```bash
aero-auto collect --part 1/4
```

정리한 `runs/summary.csv`를 한 사람에게 모읍니다(예: `member2_summary.csv`).

### 5. 학습 데이터

```bash
aero-auto dataset --summary member2_summary.csv member3_summary.csv member4_summary.csv
```

또는 `[surrogate] summaries`에 경로를 모두 적습니다. 같은 케이스가 여러 파일에 있으면 완료된 쪽을 씁니다.

결과는 `runs/surrogate/`에 저장됩니다.

| 파일 | 내용 |
|---|---|
| `dataset.csv` | 케이스별 입력(CST 16·8, aoa, log10re), 정답(cl, log10cd), 수렴 상태, y+, `regime_truth`, `split` |
| `shapes.csv` | 형상별 CST 계수, 피팅 오차, 최대 캠버·두께, α₀, 세트 |
| `split.json` | 형상 → train/val/test/ood, `ood_addable`(14주차 추가 학습 후보) |
| `data_report.md` | 상태·수렴별 건수(발산 건수 포함), 세트별 형상·건수, 조건별 건수, CST 오차, y+ 통계, 난류 모델 비교 요약 |
| `turbulence_comparison.csv` | 같은 (익형, Re, α)의 SST vs 비교 모델 Cl, log10Cd 차이 |

**조건 ① `regime_truth`**: 같은 익형·Re의 정답 극곡선에서 Cl이 최대인 받음각 α_clmax를 찾아 나눕니다.
- `pre_stall`: α < α_clmax − 2°
- `near_stall`: |α − α_clmax| ≤ 2°
- `post_stall`: α > α_clmax + 2°

정답을 알아야 판별할 수 있으므로 모델 입력에는 넣지 않습니다. 평가(조건별 성능)와 14주차 학습 가중치에만 씁니다.

**분할**:
- 최대 캠버 ≥ `ood_camber`(예제 4.5%)인 형상은 분포 밖 세트(`ood`)로 보냅니다. NACA 5·6계열과 고캠버 UIUC가 여기에 해당합니다.
- 나머지는 출처별로 섞어 70/15/15로 나눕니다.
- `ood` 중 `ood_add_fraction`(30%)은 14주차 추가 학습 후보입니다. 나머지 `ood_holdout`은 **모든 실험에서 학습에 쓰지 않으므로**, 실험 간 분포 밖 성능 비교는 `ood_holdout`으로 합니다.

### 6. 학습과 평가

기획서 3장의 실험 묶음은 `--suite`로 한 번에 돌립니다.

기준 방법, 비교 모델 1·2, 실험 1(CST 8), 실험 2(은닉층 32/128/512):

```bash
aero-auto train --suite plan
```

14주차(실속 가중치 3배, 분포 밖 30% 추가):

```bash
aero-auto train --suite week14
```

실험 하나만 돌릴 때는 직접 지정합니다.

```bash
aero-auto train --model mlp --cst 8 --hidden 64,64
```

```bash
aero-auto train --model lgbm --calibrate
```

비교표만 다시 만들려면:

```bash
aero-auto compare
```

실험마다 `runs/surrogate/experiments/<실험 이름>/`에 결과가 저장됩니다.

| 파일 | 내용 |
|---|---|
| `metrics.json` | 세트(val, test, ood, ood_holdout)별 n, 형상 수, MAE(+형상 부트스트랩 95% CI), RMSE, 예측 구간 포함률(+CI), 평균 구간 폭, 모델 설정 |
| `conditions.csv` | 세트 × 조건 × 값별 같은 지표와 표본 수 |
| `predictions.csv` | val/test/ood 케이스별 정답, 예측, 예측 구간 |
| `boundary_candidates.csv` | 경계 사례 후보 (`kind`: stall_worst, stall_best, overall_worst, overall_best, turbulence_sensitive) |
| `parity.png` | 정답 vs 예측 (test, ood_holdout) |
| `polars_test.png`, `polars_ood.png` | 형상 몇 개의 Cl-α, log10Cd-α (정답 점, 예측 선, 예측 구간 띠) |

`comparison.md`는 test와 ood_holdout에서 실험별 MAE[95% CI]와 포함률을 한 표로 보여줍니다.

### 조건별 성능 (`conditions.csv`)

| condition | 판별 시점 | 의미 |
|---|---|---|
| `regime_truth` | 정답 필요 (평가 전용, 조건 ①) | 실속 전 / 실속 부근 / 실속 후 |
| `aoa_band` | CFD 전 | `a<0`, `0<=a<=10`, `a>10` (기획서의 1° 간격 구간) |
| `reynolds` | CFD 전 | 2e+05, 5e+05 |
| `source` | CFD 전 | naca, uiuc |
| `pi_width` | CFD 전 (모델 출력) | Cl 예측 구간 폭의 사분위 Q1(좁음)~Q4(넓음). 모델이 스스로 불확실하다고 표시한 케이스가 실제로 틀리는지 확인 |

"CFD를 대체해도 되는 조건"은 CFD 전에 판별 가능한 조건(`aoa_band`, `reynolds`, `source`, 분포 안/밖, `pi_width`)으로 정의해야 실제로 쓸 수 있습니다. `regime_truth`는 그 정의가 맞는지 사후에 검증하는 용도입니다.

### 예측 구간과 보정

- LightGBM은 5%·95% 분위 회귀를 따로 학습합니다. 데이터가 적으면 구간이 좁게 잡혀 포함률이 90%보다 낮게 나오기 쉽습니다.
- MLP 앙상블은 망마다 평균과 분산을 출력합니다. 예측 분산 = 망별 분산 평균 + 망 간 평균의 분산입니다.
- `--calibrate`(또는 `[surrogate] calibrate = "conformal"`)는 검증 세트로 등각(conformal) 보정량을 구해 구간을 넓히거나 좁힙니다.
  - 보정은 검증 세트와 같은 분포(test)에서의 포함률을 맞춰 줍니다.
  - 분포 밖(ood)에서 포함률이 떨어지는 것까지 고치지는 않습니다. 이 차이가 기획서의 핵심 분석 대상입니다.
  - 검증 세트는 조기 종료에도 쓰이므로 보정이 약간 낙관적일 수 있습니다. 보고서에 이 점을 함께 적어 두세요.

## `[surrogate]` 설정

| 항목 | 기본값 | 설명 |
|---|---|---|
| `summaries` | `["runs/summary.csv"]` | 합칠 결과 요약 파일들 |
| `compare_summary` | 없음 | 난류 모델 비교 결과 (없으면 비교 생략) |
| `output_dir` | `"runs/surrogate"` | 결과 폴더 |
| `airfoil_key`, `source_key`, `reynolds_key` | `main_airfoil`, `source`, `reynolds` | summary.csv 열 이름 |
| `use_convergence` | `["converged", "oscillating"]` | 학습에 쓸 수렴 상태 |
| `ood_camber` | 0.05 | 최대 캠버(코드 비) 이상이면 분포 밖 |
| `ood_airfoils` | `[]` | 추가로 분포 밖에 둘 익형 이름 (`e387`, `NACA2412` 형식) |
| `ood_add_fraction` | 0.3 | 분포 밖 중 14주차 추가 학습 후보 비율 |
| `split` | `[0.7, 0.15, 0.15]` | 학습/검증/평가 (형상 단위) |
| `seed` | 0 | 분할·모델 시드 |
| `stall_margin` | 2.0 | `regime_truth`의 near_stall 폭 [deg] |
| `interval` | 0.90 | 예측 구간 |
| `calibrate` | `"none"` | `"conformal"`이면 모든 실험에 등각 보정 |
| `bootstrap` | 1000 | 형상 단위 부트스트랩 반복 수 |
| `turb_threshold` | 0.1 | 난류 모델 민감 케이스 기준 \|ΔCl\| |

## 참고와 한계

- **CST 피팅**은 면당 Bernstein 계수 n개를 쓰고(16계수 = 7차, 8계수 = 3차), 뒷전 두께는 좌표로 고정합니다(특징량 제외). `shapes.csv`의 `cst16_rms`가 큰 형상은 좌표 파일을 확인하세요.
- **α₀**는 CST로 재구성한 캠버선에 얇은 익형 이론 적분을 적용해 구합니다. 정규화(앞전 = 앞전–뒷전 최장 거리 점) 때문에 NACA 정의 캠버선과 약간 다를 수 있고, 2412에서 −2.04°(이론값 −2.08°) 정도 차이가 납니다.
- **받음각은 유입 방향**으로 주므로 형상당 메쉬는 1개입니다(120개). 후류 세분화 각도(`wake_angles`)가 받음각 범위를 덮습니다.
- 3,400건 기준 MLP 앙상블(128×2, 5개)은 CPU 4코어에서 약 1분, LightGBM은 수 초가 걸립니다. 512×2는 몇 분 걸립니다.
- 여러 학습을 동시에 돌리면(두 터미널에서 `train`) CPU 스레드가 겹쳐 매우 느려집니다. 한 번에 하나씩 실행하세요.
