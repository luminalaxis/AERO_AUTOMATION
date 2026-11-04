# AERO_AUTOMATION

아 귀찮은 케이스 분류 해석 자동화 할겁니다

2D **다요소 날개(메인 + 슬롯 플랩 1~2개, 최대 3개)** 형상 최적화를 위한 ANSYS Fluent 배치 해석 자동화 도구입니다. 예제는 **지면 효과를 받는 다운포스용 뒤집힌 날개**(움직이는 바닥, 지상고 30~60 mm)이고, 형상 순위 기준은 **최대 다운포스**(`objective = "max_cl"`)입니다.
2단(메인 + 플랩 1개)·3단(메인 + 플랩 2개) 구성의 형상(익형, 요소별 코드 길이, 플랩 각도, 갭, 오버랩) × 지상고 × 받음각 × 속도 케이스를 만들고, **저충실도 해석으로 가망 없는 케이스를 미리 걸러낸 뒤**, 형상마다 메쉬를 자동 생성해 Fluent로 돌리고 Cl/Cd 정리, 형상 순위, 이미지 저장까지 한 번에 처리합니다.

두 번째 예제(`examples/airfoil_dataset`)는 **단일 익형 120개 × 받음각 14개 × Re 2개 = 3,360 케이스**로 양항계수 데이터셋을 만들고, CST 형상 계수를 입력으로 하는 **CL·CD 회귀 모델(얇은 익형 이론 기준 / LightGBM / MLP 앙상블)을 학습·평가**합니다. 아래 ["예제 2: 단일 익형 데이터셋과 회귀 모델"](#예제-2-단일-익형-데이터셋과-회귀-모델)을 보세요.

```
project.toml (2단/3단 세트, 형상·지상고·받음각·속도 스윕)
   │ screen 패널법(+지면 거울상) + 경계층 근사로 전 케이스 추정 (병렬)
   │        → 민감도 그래프, 하위 형상 계열·실속 이후 받음각 제외 (세트·속도별, selection.json)
   │ mesh   남은 형상만 gmsh 자동 메쉬 → Fluent .msh  (속도만 다른 케이스는 메쉬 공유, CPU 코어 수만큼 동시에)
   │        직사각형 영역: inlet / outlet / top / ground(moving wall)
   │        + geometry.png, mesh.png, mesh_slot1/2.png, mesh_le.png, mesh_domain.png, 직교 품질
   │ check  Fluent 없이 실행 준비 점검: 베이스 케이스, 메쉬, 경계 이름, 저널, 경로, 디스크·시간 추정
   │ run    케이스별 저널 생성 → fluent 2ddp -t4 -hidden -wait -i run.jou
   │        (transcript 시작 → base_2el/3el.cas.h5 읽기 → 메쉬 교체 → 속도/기준값 변경 → 계산 → 컨투어 이미지)
   │        --priority: 스팟 체크 → 유망한 형상 순서
   │ collect
   ▼
runs/screening/      저충실도 추정, 민감도 그래프, 선별 결과
runs/summary.csv     케이스별 조건 + Cl, Cd, L/D (요소별 포함), 단위 폭당 양력·항력 [N/m]
runs/ranking.csv     형상별 최대 Cl, 최대 L/D 순위 (속도별로 따로)
runs/plots/          속도별 폴더에 Cl-α, Cd-α, L/D-α, 극곡선 + 속도에 따른 형상별 최대 L/D
runs/<그룹>/<케이스>/ convergence.png, img_*.png (Fluent 컨투어), transcript.txt (Fluent 출력)
runs_mesh/mesh_study/ 메쉬 독립성 확인 (mesh_study.toml → gci: Richardson 외삽, GCI)
```

## 설치

파이썬 3.11 이상.

```bash
pip install -e .
```

numpy, matplotlib, gmsh가 함께 설치됩니다. gmsh는 pip 패키지에 실행 파일이 포함돼 있어 따로 설치할 필요 없습니다.

회귀 모델 학습(`aero-auto train`)까지 쓰려면 LightGBM과 PyTorch도 설치합니다.

```bash
pip install -e ".[ml]"
```

터미널에서 `aero-auto`를 실행하면 맨 위에 큰 글씨 로고 배너가 나오고, 색 조합은 실행할 때마다 무작위로 바뀝니다(명령 없이 `aero-auto`만 치면 배너 + 도움말). 출력을 파일로 넘기거나 파이프로 연결하면 배너는 자동으로 빠집니다. 끄려면 `--no-banner` 또는 환경 변수 `AERO_AUTO_NO_BANNER=1`, 색만 끄려면 `NO_COLOR=1`을 씁니다.
터미널에서는 실행할 때 화면을 먼저 지우고(끄려면 `--no-clear` 또는 `AERO_AUTO_NO_CLEAR=1`), 도움말(`-h`)은 `usage:`·제목·명령 이름·옵션을 굵은 밝은 색으로 구분해 보여줍니다. 파일·파이프로 넘기면 지우지도, 색을 넣지도 않습니다.

## 빠른 시작 (예제)

```bash
cd examples/flap_optimization
```

```bash
aero-auto plan --dry-run   # 케이스 목록 확인 (25344개 = 형상 264개 × 지상고 4 × 받음각 6 × 속도 4)
```

```bash
aero-auto screen           # 세트·속도별 저충실도 선별 (예제: 25344개 → 3484개, 4코어 5~10분)
```

```bash
aero-auto mesh             # 남은 형상만 메쉬 생성 (형상 872개, 4코어 동시 생성 약 50분) + 이미지
```

```bash
aero-auto check            # Fluent 실행 전 점검 (아래 "Fluent 실행 순서" 참고)
```

```bash
aero-auto run --priority   # 남은 케이스만 Fluent 배치 실행 (스팟 체크 → 유망한 형상 순서) → 결과 정리
```

```bash
aero-auto status           # 진행 상태
```

```bash
aero-auto collect          # 결과 다시 정리 (summary, ranking, 그래프)
```

- `--only "2el_4412c375*"`: 일부 케이스만 실행 (와일드카드, 그룹 이름도 가능)
- `-j 2`: 동시 실행 수 (라이선스와 코어 수 고려). `mesh -j 4`는 동시에 만들 메쉬 수(기본: CPU 코어 수, 최대 8. 메쉬당 메모리 약 300 MB)
- `--limit 100`: 남은 케이스 중 앞의 100개만 실행 (나눠서 돌릴 때, `--priority`와 함께)
- 중단돼도 다시 `run` 하면 완료된 케이스와 이미 만든 메쉬는 건너뜁니다. 전부 다시 하려면 `run --force`, `mesh --force`.
- `screen`을 한 번 돌리면 이후 `mesh`, `run`은 제외된 케이스를 자동으로 건너뜁니다(상태 `screened`). 스크리닝을 무시하려면 `--all`.
- 실패 판정: 종료 코드, 로그의 `Error:` / `Divergence detected`, 시간 초과(`timeout_minutes`), 메쉬 생성 실패.
  로그는 `solver.log`(표준 출력)와 `transcript.txt`(저널의 `/file/start-transcript`)를 함께 봅니다. Windows에서 `-hidden`으로 실행하면
  Fluent 출력이 표준 출력으로 나오지 않아서 transcript가 필요합니다. transcript에 다시 찍힌 저널 주석 줄은 검사에서 뺍니다.
- 다시 실행할 때는 그 케이스 폴더의 이전 결과(리포트, 이미지, transcript, `result.cas.h5`/`.dat.h5`)를 먼저 지웁니다.
  Fluent가 덮어쓰기 확인을 물으면 그다음 저널 줄이 답으로 읽혀 배치가 꼬이기 때문입니다.
- `status`는 끝난 케이스의 실행 시간(중앙값)으로 남은 시간을 추정합니다 (`-j`로 동시 실행 수 지정).

## Fluent 실행 순서 (예제)

Fluent를 돌리기 전에 할 수 있는 것은 `screen` → `mesh` → `check`까지입니다. 그다음부터는 Fluent가 필요합니다.

| 순서 | 명령 | 내용 |
|---|---|---|
| 1 | `aero-auto screen` | 25,344 → 3,484 케이스 (스팟 체크 형상 8개 × 대표 받음각 = 28 케이스 포함). 4코어 5~10분 |
| 2 | `aero-auto mesh` | 남은 케이스의 메쉬 872개 (4코어 동시 생성 약 50분, 실패 0개) |
| 3 | `aero-auto check` | 아래 항목 점검. `[오류]`, `[할 일]`이 없을 때까지 |
| 4 | Fluent GUI | 베이스 케이스 `base_2el.cas.h5`, `base_3el.cas.h5` ([docs/FLUENT_BASE_CASE.md](docs/FLUENT_BASE_CASE.md), 잔차 기준 1e-6 포함) |
| 5 | `run --only <파일럿>` | 2단·3단 파일럿 1개씩([아래](#파일럿-확인-순서)). `transcript.txt`, `convergence.png`, 이미지 확인 후 `check`로 실제 시간·디스크 재추정 |
| 6 | `-c mesh_study.toml mesh/run/gci` | 메쉬 독립성 12 케이스 → 본 배치 메쉬의 GCI ([아래](#메쉬-독립성-확인-mesh_level-refine-gci)) |
| 7 | `run --stage1 --priority` → `collect` | 1단계 172 케이스: 스팟 체크 + 계열·조건별 대표 형상의 전체 받음각. 스크리닝이 뺀 쪽이 CFD에서 더 좋다는 경고가 없는지 확인 |
| 8 | `narrow-aoa` | 나머지 형상은 대표 형상의 CFD 최대 다운포스 각도 주변 3개만 남김 (3,484 → 1,828 케이스, [아래](#단계적-실행-run---stage1-narrow-aoa)) |
| 9 | `run --priority` | 나머지. `status`로 남은 시간 확인 |

### 단계적 실행 (`run --stage1`, `narrow-aoa`)

받음각 6개를 모든 형상에 다 푸는 대신, 계열마다 대표 형상 하나로 다운포스가 최대인 받음각을 CFD로 찾고 나머지 형상은 그 주변만 풉니다.

```bash
aero-auto run --stage1 --priority
```

```bash
aero-auto narrow-aoa --dry-run
```

```bash
aero-auto narrow-aoa --keep 3
```

```bash
aero-auto run --priority
```

- **1단계 (`run --stage1`):** 스팟 체크 + 그룹(남은 계열 × 조건(세트·속도))마다 스크리닝 점수 1위 형상(대표 형상)의 모든 받음각. `--per ground_clearance`를 주면 지상고 값마다 대표 형상을 따로 뽑습니다(지상고에 따라 최대 각도가 다를 때 더 안전).
- **2단계 (`narrow-aoa`):** 그룹마다 대표 형상의 CFD 결과에서 `[results] objective`(예제: 최대 Cl = 최대 다운포스)가 최대인 받음각을 찾고, 같은 그룹의 나머지 형상은 그 받음각을 가운데로 한 `--keep`개(기본 3: 앞뒤 한 칸)만 남깁니다. 범위 끝이면 안쪽으로 밀어서 3개를 채우고, 실제 최대가 범위 밖일 수 있다고 경고합니다. 제외한 케이스는 `selection.json`의 제외 목록에 이유와 함께 들어가 이후 `run`이 건너뜁니다. 이미 끝난 케이스와 스팟 체크는 그대로 두고, 발산한 대표 케이스는 쓰지 않습니다. 대표 형상 결과가 3개 미만인 그룹은 줄이지 않습니다. `--dry-run`은 바꾸지 않고 결과만 보여줍니다. 그룹 변수는 기본으로 `run --stage1`에서 쓴 `--per`를 따릅니다.
- `screen`을 다시 하면 `selection.json`이 새로 만들어지므로 `narrow-aoa`도 다시 실행합니다(끝난 1단계 결과는 그대로 씀).

| 방식 | 1단계 | 총 케이스 (예제) |
|---|---|---|
| 줄이지 않음 | - | 3,484 |
| `--keep 3` (기본) | 172 | 1,828 (-48%) |
| `--keep 3`, `--per ground_clearance` | 604 | 2,044 (-41%) |
| `--keep 2` | 172 | 1,276 (-63%, 최대점이 두 점 사이 어디인지 몰라 순위가 흔들릴 수 있음) |

전제: 같은 계열 안에서는 메인 코드·갭·지상고가 달라도 최대 각도가 비슷하다는 것입니다. 지상고에 따라 다를 것 같으면 `--per ground_clearance`를 쓰세요.

**`check` 점검 항목** (아무것도 만들거나 바꾸지 않음, 문제가 있으면 종료 코드 1)

| 항목 | 내용 |
|---|---|
| 케이스 | 스크리닝 반영 후 실행할 케이스 수, 스크리닝 뒤에 추가된 케이스, 이미 끝난 케이스 |
| 저널 | 모든 케이스의 변수 치환, `start-transcript` 유무, `${mesh_file}` 유무, 이미지 저장과 `graphics` 설정, 리포트 파일 이름과 `[results] report_files` 패턴, 평균 구간 대 반복 수 |
| 베이스 케이스 | 저널의 `read-case` 경로마다 파일이 있는지 (세트별 케이스 수와 함께) |
| Fluent | 실행 파일(`[solver] executable` 또는 `AWP_ROOTxxx`), 동시 실행 × `processes`와 코어 수 |
| 메쉬 | 있음/없음/실패, **베이스 케이스(세트)마다 경계 이름이 같은지**(다르면 `replace-mesh` 실패), 셀 수, 첫 셀 높이, Fluent 정의 직교 품질 |
| 경로 | 한글 등 영문 외 문자(Fluent가 못 읽는 경우가 많음), OneDrive, 가장 긴 파일 경로(Windows 260자) |
| 디스크·시간 | 남은 케이스 × 케이스당 크기·시간. 끝난 케이스가 있으면 그 실제 값(중앙값)으로, 없으면 셀 수와 몇 가지 가정으로 |

예제에서 Fluent 전 `check` 결과(메쉬 872개를 모두 만든 뒤, 베이스 케이스를 만들기 전): 베이스 케이스 2개 `[할 일]`,
메쉬 경계 이름은 세트마다 일치(2단 436개, 3단 436개), 셀 6.7만~13.3만 개(중앙값 9.6만), 첫 셀 높이 약 12 µm,
직교 품질 최소 0.059(0.1 미만 셀은 메쉬 4개에 1개씩, 모두 뒷전 0.01c 안: 플랩 뒷전 3개, 메인 뒷전 1개), 가장 긴 파일 경로는 프로젝트 폴더 경로 + 87자,
`result.cas.h5`까지 저장하면 디스크 약 68 GB(result 파일을 셀당 200 B로 잡은 추정), 케이스당 3~10분이면 동시 1개 기준 7~24일입니다. 그래서 위 5번의 파일럿 시간으로 일정을 다시 잡고, 필요하면 `-j`(라이선스·코어),
힘계수 수렴 조건(베이스 케이스 문서 8번), `--limit`로 나눠 실행을 조합하세요.

## 예제 2: 단일 익형 데이터셋과 회귀 모델

인공지능개론 텀프로젝트 기획서("CFD 없이 에어포일 양항계수를 예측하는 회귀 모델")의 데이터 생성부터 모델 평가까지를 담은 예제입니다.

| 항목 | 내용 |
|---|---|
| 형상 | NACA 4자리 60개(공식 생성) + UIUC 60개(`airfoils.csv`). UIUC 중 **캠버 6.5% 이상 10개**(`family = high_camber`: s1223, s1210, e423, ch10sm, fx74cl5140, mh114, dae11, dae21, goe226, e591)는 학습에서 빼고 "처음 보는 형상" 평가에만 씀 |
| 받음각 | −4, −2, 0, 2, 4, 6, 8, 10, 11~16° (14개, 실속 부근 1° 간격) |
| Re | 2×10⁵, 5×10⁵ (코드 0.3 m 기준 유속 9.7, 24.3 m/s) |
| 해석 | Fluent 2D 정상 상태 k-ω SST, 직사각형 영역(앞전 기준 앞 20c, 뒤 40c, 위·아래 20c, 앞·위·아래 = inlet, 뒤 = outlet), 형상당 메쉬 1개(약 5.2만 셀) |
| 입력 → 목표 | CST 계수 16개(면당 8개) + 받음각 + log10(Re) → CL, log10(CD) |
| 라벨 규칙 | 케이스마다 수렴 상태(`conv_state`)와 y+(`yplus_max`, `yplus_avg`) 기록. 수렴 케이스는 마지막 200회, 진동 케이스는 마지막 500회 반복 평균. 발산 케이스는 학습·평가에서 빼고 건수만 보고 |
| 평가 | 조건별 MAE와 표본 수, 형상 단위 부트스트랩 95% 신뢰구간, 90% 예측 구간의 실제 포함률. 실행 간 비교는 공통 케이스만으로(`compare`), 경계 사례 10개(`edge-cases`) |

### 순서

```bash
cd examples/airfoil_dataset
```

```bash
aero-auto fetch-airfoils      # UIUC 좌표 60개를 airfoils/ 에 내려받음 (없는 것만)
```

```bash
aero-auto plan --dry-run      # 3,360 케이스, 형상(메쉬) 120개
```

```bash
aero-auto mesh                # 형상별 메쉬 (각 5~8초)
```

Fluent 베이스 케이스 `base_airfoil.cas.h5`를 만든 뒤([docs/FLUENT_BASE_CASE_AIRFOIL.md](docs/FLUENT_BASE_CASE_AIRFOIL.md)) 팀원 4명이 나눠 실행합니다.

```bash
aero-auto run --part 1/4      # 팀원별로 1/4 ~ 4/4. 형상 30개(840 케이스)씩, 같은 형상은 한 사람에게
```

각자의 `runs/<형상>/` 폴더를 한 `runs/`에 모은 뒤 정리합니다.

```bash
aero-auto collect --no-plots  # summary.csv (cl, cd, 수렴 상태)
```

```bash
aero-auto dataset             # CST 계수 + 형상 단위 분할 → runs/dataset/
```

```bash
aero-auto train               # 기준 방법 / LightGBM / MLP → runs/ml/cst16_h64x64/
```

```bash
aero-auto edge-cases          # 경계 사례 10개 + 사례별 그래프 → runs/ml/cst16_h64x64/edge_cases_mlp/
```

실험 1·2와 14주차 개선 실험은 아래 "학습과 평가"의 표대로 학습한 뒤 `aero-auto compare`로 비교합니다.

- UIUC 사이트에 접속이 안 되면 `coord_seligFmt.zip`(UIUC 전체 묶음)을 받아 `aero-auto fetch-airfoils --zip coord_seligFmt.zip`. 내려받은 `airfoils/*.dat`은 팀원이 같은 좌표를 쓰도록 커밋해 두는 것을 권장합니다.
- `--part K/N`은 `plan`, `mesh`, `run`, `status`, `collect` 등 모든 명령에서 쓸 수 있습니다.

### 수렴 상태 (`conv_state`)

`collect`는 케이스마다 리포트 파일의 반복 이력(마지막 `[convergence] window`개)과 솔버 로그로 수렴 상태를 판정해 `summary.csv`에 넣습니다.

| 상태 | 기준 |
|---|---|
| `converged` | CL 진폭(최대 − 최소) ≤ max(`cl_tol`, `cl_rel_tol`·\|CL\|) 그리고 CD 진폭 ≤ max(`cd_tol`, `cd_rel_tol`·CD), 또는 Fluent가 잔차 기준 만족으로 멈춤 |
| `oscillating` | 그 밖의 경우. `conv_reason`이 `amplitude`(주기적으로 흔들림) 또는 `drift`(아직 한쪽으로 변하는 중) |
| `diverged` | 로그에 `Divergence detected` / `floating point exception`, 값이 NaN·inf, 또는 \|계수\| > `max_abs_coef` |
| `no_data` | 결과 파일 없음 (실행 실패 등) |

함께 기록하는 열: `conv_cl_amp`, `conv_cd_amp`, `conv_cl_std`, `conv_cd_std`(진동 케이스의 정답 불확실성), `conv_cl_drift`, `conv_iters`, `res_continuity` 등 마지막 잔차. `convergence.png` 제목에도 상태가 붙습니다.
`[convergence]` 설정은 모든 프로젝트에서 쓸 수 있습니다(기본: `window = 200`, `cl_tol = 0.005`, `cl_rel_tol = 0.01`, `cd_tol = 1e-4`, `cd_rel_tol = 0.02`, `max_abs_coef = 10`).

- **결과값(정답)**: 마지막 `[results] average_last`(예제: 200)개 반복의 평균입니다. 진동 케이스는 `average_last_oscillating`(예제: 500)개로 더 길게 평균합니다(기획서 라벨 규칙). 실제로 평균에 쓴 반복 수는 `avg_iters` 열에 남습니다.
- **y+**: 베이스 케이스의 리포트 `yplus-max`(벽면 y+ 최대), `yplus-avg`(면적 가중 평균)를 같은 리포트 파일에 넣으면 `summary.csv`의 `yplus-max`, `yplus-avg`와 `dataset.csv`의 `yplus_max`, `yplus_avg`로 기록됩니다([docs/FLUENT_BASE_CASE_AIRFOIL.md](docs/FLUENT_BASE_CASE_AIRFOIL.md) 9단계). `dataset`이 중앙값과 최대를 출력하고, 리포트가 없으면 경고합니다.
- **발산**: `dataset`이 상태별 건수를 출력하고, `train`은 발산 케이스를 학습·평가에서 빼고 분할별 건수만 `metrics.csv`(`state=diverged`)에 남깁니다.

### 데이터셋 (`aero-auto dataset` → `runs/dataset/`)

| 파일 | 내용 |
|---|---|
| `dataset.csv` | 케이스 1건 = 1행. `cst16_u0..u7, cst16_l0..l7`(면당 8개), `cst8_u0..u3, cst8_l0..l3`(면당 4개, 실험 1용), `aoa`, `log10_re`, `cl`, `cd`, `log10_cd`, `conv_state`, `avg_iters`, `yplus_max`, `yplus_avg`, `near_stall`, `aoa_clmax`, `split`, 두께·캠버, 얇은 익형 이론 `alpha0_thin_deg`. Transition SST 결과가 있으면 `cl_tsst`, `log10_cd_tsst`, `d_cl_tsst`, `d_log10_cd_tsst`(T-SST − SST) |
| `airfoils.csv` | 형상별 CST 계수, 피팅 오차(`cst16_rms`, `cst16_max_err`, 코드 기준), 두께·캠버와 위치, 뒷전 두께, 분할 |
| `split.json` | 형상 → `train` / `val` / `test` / `test_ood`. 한 번 만들면 고정(새 형상만 추가 배정). 다시 섞으려면 `dataset --resplit` |
| `cst_fits.png` | 피팅 오차가 가장 큰 형상 6개의 원래 좌표와 CST 재구성 비교 |

- **CST**: 윗면·아랫면을 각각 `y = √x(1−x)·Σ Aᵢ·Bᵢ(x) + x·y_te`(번스타인 다항식)로 최소제곱 피팅합니다. 예제 120개 기준 최대 오차는 NACA 4자리 중앙값 0.0005c(최대 0.002c), UIUC 중앙값 0.001c이고, 좌표 점이 성긴 파일(naca23012, e603)은 앞전 부근에서 0.006c 정도입니다. 최대 오차가 0.005c를 넘으면 경고합니다.
- **분할**: 같은 형상의 모든 받음각·Re 케이스는 같은 분할에 들어갑니다. `holdout_families` 계열은 `test_ood`로만, 나머지는 family(naca4 / uiuc)별로 `split` 비율(70/15/15)에 맞춰 나눕니다. 예제는 학습 77 / 검증 16~17 / 평가 16~17 / 처음 보는 형상 10개입니다.
- **실속 부근**: 형상·Re별로 CL이 최대인 받음각(`aoa_clmax`, 학습에 쓰는 수렴 상태 케이스 기준) ± `near_stall_window`(2°)이면 `near_stall = 1`. 평가에서는 이보다 작은 받음각을 `attached`, 큰 받음각을 `post_stall`로 나눕니다.

### 학습과 평가 (`aero-auto train` → `runs/ml/<이름>/`)

| 모델 | 내용 |
|---|---|
| `baseline` | 얇은 익형 이론 CL = 2π(α − α0), α0는 형상의 캠버선에서 계산. log10 CD = 학습 데이터 평균 |
| `lgbm` | LightGBM. 목표별 평균 모델 + 분위 회귀(5%, 95%) → 90% 예측 구간. 검증 손실로 조기 종료 |
| `mlp` | PyTorch MLP(SiLU), 시드만 다른 5개 앙상블. 평균 = 예측, 앙상블 표준편차 × 1.645 → 90% 예측 구간 |

`metrics.csv`에는 모델 × 분할(`val`, `test`, `test_ood`) × 조건마다 표본 수(`n`, `n_shapes`), `mae_cl`, `mae_log10_cd`, 90% 예측 구간 폭(`width90_*`)과 실제 포함률(`cover90_*`)이 들어가고, MAE와 포함률마다 **형상 단위 부트스트랩 95% 신뢰구간**(`*_lo95`, `*_hi95`)이 붙습니다. 같은 형상의 받음각·Re 케이스는 서로 독립이 아니므로 케이스가 아니라 형상을 복원 추출(2,000회)해 구간을 구합니다.
조건은 `all`(학습에 쓰는 수렴 상태 전체), 유동 구간 `attached`(CL 최대 받음각 − 창보다 작은 받음각, 부착 유동)·`near_stall`(CL 최대 받음각 ± 창)·`post_stall`(+ 창보다 큰 받음각, 실속 후 박리 유동), `state=converged/oscillating`(수렴 상태별)이고, `state=diverged`는 건수(`n`)만 있습니다. 유동 구간은 CFD 정답(CL 최대 받음각)을 알아야 정해지므로 평가 조건으로만 쓰고 모델 입력에는 넣지 않습니다.
`predictions.csv`는 케이스별 정답·예측·구간과 진동 폭, y+, Transition SST 정답(있으면), `run.json`은 설정과 MLP 앙상블 멤버별 조기 종료 지점입니다.
`val`은 조기 종료에 쓰이므로 최종 보고에는 `test`와 `test_ood`를 쓰세요.

기획서의 실험은 옵션으로 학습하고(결과 폴더 이름은 `--name`), `compare`로 같은 케이스끼리 비교합니다.

| 실험 | 학습 | 비교 |
|---|---|---|
| 기본 비교 | `aero-auto train` | `aero-auto compare cst16_h64x64` (기준 방법 대비) |
| 실험 1: 형상 계수 16개 vs 8개 | `aero-auto train --features cst8 --name exp1_cst8` | `aero-auto compare cst16_h64x64 exp1_cst8 --model lgbm mlp` |
| 실험 2: MLP 은닉층 크기 3단계 | `aero-auto train --model mlp --hidden 32,32 --name exp2_small` / `--hidden 128,128,128 --name exp2_large` | `aero-auto compare cst16_h64x64 exp2_small exp2_large --model mlp` |
| 개선 1: 실속 부근 학습 비중 | `aero-auto train --stall-weight 3 --name imp_stall3` | `aero-auto compare cst16_h64x64 imp_stall3 --model lgbm mlp` |
| 개선 2: 처음 보는 계열 일부 학습 | `aero-auto train --ood-train-fraction 0.3 --name imp_ood30` | `aero-auto compare cst16_h64x64 imp_ood30 --model lgbm mlp` |

`[ml]` 설정: `holdout_families`, `split`(3개 비율), `seed`, `near_stall_window`, `train_states`(학습·`all` 조건에 쓰는 수렴 상태, 기본 converged + oscillating), `cst_orders`(면당 계수 수 목록, 기본 `[8, 4]`), `shape_key`(형상 이름 변수, 기본 `airfoil`), `family_key`, `re_key`, `tsst_summary`(Transition SST 결과, 아래 참고).

### 실행 간 비교 (`aero-auto compare` → `runs/ml/_compare/<이름>.csv`)

기획서의 "모든 비교는 동일 분할·동일 지표로"를 지키도록, 여러 학습 결과를 **모든 실행에 공통인 평가 케이스(test, test_ood)만으로** 다시 계산합니다. 개선 2처럼 처음 보는 형상 일부를 학습에 넣은 실행과 비교해도 그 형상은 양쪽에서 모두 빠집니다.

- 기준은 첫 번째 실행의 같은 모델입니다. 실행을 하나만 주면 그 실행의 첫 모델(기본: 기준 방법)이 기준입니다.
- `d_mae_*` = MAE − 기준 MAE(음수 = 개선)와 **짝지은 형상 단위 부트스트랩 95% 신뢰구간**. 모든 모델에 같은 형상 재표집을 써서 차이의 구간을 구하고, 구간이 0을 포함하지 않으면 화면에 `*`를 붙입니다.
- 실행 사이에 `dataset`을 다시 만들어 정답이나 분할이 달라진 케이스는 빼고 경고합니다.

### 경계 사례 (`aero-auto edge-cases` → `runs/ml/<실행>/edge_cases_<모델>/`)

```bash
aero-auto edge-cases --run cst16_h64x64 --model mlp
```

평가 세트(test, test_ood)에서 경계 사례 10개를 골라 `edge_cases.csv`와 사례별 그래프(같은 형상·Re의 CL-α, log10 CD-α: CFD 정답, 모델 예측과 90% 예측 구간, Transition SST 정답)를 만듭니다.

| 범주 | 고르는 케이스 | 개수 |
|---|---|---|
| `stall_pre` | 실속 직전: 받음각 범위 안에서 실속하는 형상·Re의 CL 최대 받음각 이하 (`near_stall`) | 대표 1 + 최악 1 |
| `stall_post` | 실속 직후: CL 최대 받음각 초과 (`near_stall`) | 대표 1 + 최악 1 |
| `turbulence` | SST와 Transition SST의 정답 차이가 큰 케이스 (`tsst_summary` 필요) | 2 |
| `attached_test` | 부착 유동(`attached`), 평가 세트 | 대표 1 + 최악 1 |
| `attached_ood` | 부착 유동, 처음 보는 형상 | 대표 1 + 최악 1 |

- 오차 점수 = \|CL 오차\| / CL MAE + \|log10 CD 오차\| / log10 CD MAE (그 모델의 평가 세트 평균 오차의 몇 배인지). **최악**(실패 사례) = 점수 최대, **대표**(정상 사례) = 점수가 범주 중앙값에 가장 가까운 케이스. 같은 형상은 되도록 한 번만 고릅니다.
- 난류 모델 차이도 같은 척도(모델 평균 오차 대비 배수, `tsst_diff_score`)로 순위를 매깁니다. 평가 분할(val, test, test_ood) 형상만 해당합니다.
- `in_pi90_cl`, `in_pi90_log10_cd`: 정답이 90% 예측 구간 안인지(1/0). 오차가 큰데 구간도 넓으면 모델이 불확실성을 알린 실패, 구간이 좁으면 알리지 못한 실패입니다.

### Transition SST 비교 (신뢰성 검증, 경계 사례)

Re 2×10⁵ 케이스 일부를 Transition SST로 다시 풀고([docs/FLUENT_BASE_CASE_AIRFOIL.md](docs/FLUENT_BASE_CASE_AIRFOIL.md) 맨 아래), `project.toml`에 결과 위치를 적습니다.

```toml
[ml]
tsst_summary = "runs_tsst/summary.csv"
```

`aero-auto dataset`이 이름이 같은 케이스끼리 짝지어 `cl_tsst`, `log10_cd_tsst`, `d_cl_tsst`, `d_log10_cd_tsst`(T-SST − SST)를 붙이고 Re별 차이(중앙값, 최대와 그 케이스)를 출력합니다. 이어서 `train` → `edge-cases`를 다시 실행하면 난류 모델 민감 사례가 채워집니다. Transition SST 정답은 학습에 쓰지 않습니다.

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
| `ground_clearance` | 지상고 [mm]. 있으면 지면 효과 해석 (아래 참고) |
| `ground_ref` | 지상고 기준. `"lowest"`(기본) = 날개 최하단~지면, `"main_le"` = 메인 앞전~지면 |

### 다운포스 날개 (`downforce = true`)

- 전체 형상(메인 + 플랩)을 위아래로 뒤집습니다. 플랩은 앞 요소 뒷전의 **위쪽**에 놓이고, 갭·오버랩·플랩 각도 정의는 거울상 그대로입니다. 메쉬 이름 끝에 `_inv`가 붙습니다.
- **받음각 `aoa`는 다운포스가 커지는 방향이 +**입니다. Fluent에 넣는 실제 유입 각도는 `flow_angle = -aoa`로 자동 계산되고, 저널은 `${flow_angle}`을 씁니다.
- 결과의 `cl`은 **다운포스 계수(아래 방향 +)**, `l/d`는 다운포스/항력, 힘은 `downforce_N_per_m`입니다. 요소별(`cl-main`, `cl-flap1` 등)도 같은 부호입니다.
- 지면이 없을 때(자유류)는 뒤집힌 날개가 똑바른 날개의 정확한 거울상이라, 스크리닝은 똑바른 형상의 양력을 다운포스로 씁니다.

### 지면 효과 (`ground_clearance`)

- **해석 영역:** 직사각형으로 바뀝니다. 앞 = `inlet`(velocity-inlet), 뒤 = `outlet`(pressure-outlet), 위 = `top`(symmetry), 아래 = `ground`(wall → Fluent에서 **moving wall**, 유입 속도와 같은 속도로 +x 방향). 크기는 `[mesh]`의 `ground_upstream`/`ground_downstream`/`ground_height`로 조절합니다.
- **받음각:** **메인 앞전을 중심으로 날개 전체를 실제로 회전**합니다(뒤집힌 날개는 앞전이 내려가는 쪽이 +). 유동은 항상 지면과 평행해서 `flow_angle = 0`입니다. 그래서 받음각마다 메쉬가 따로 생기고, 속도만 다른 케이스는 메쉬를 공유합니다. 메쉬 이름 끝에 `_h<지상고>_a<받음각>`이 붙습니다.
- **지상고 기준:** 기본(`ground_ref = "lowest"`)은 **회전한 날개의 최하단~지면 거리**입니다. `"main_le"`로 바꾸면 메인 앞전~지면 거리가 됩니다. 이 경우 뒤집힌 날개는 아랫면이 앞전보다 시위의 약 10% 아래로 내려와서, 낮은 지상고에서는 날개가 지면을 뚫는 케이스가 생기고 그 케이스는 형상 불가로 처리됩니다. `geometry.png`에 최하단 높이와 앞전 높이가 함께 표시됩니다.
- **메쉬:** 날개와 지면 사이 통로를 지상고의 1/8 크기로 세분화합니다. 지면은 움직이는 바닥이라 지면 경계층은 따로 두지 않습니다.
- **스크리닝:** 패널법에 지면 거울상(image method)을 넣어 지면 효과를 근사합니다. 지상고가 낮을수록 다운포스가 커지는 경향은 잡지만, 아주 낮은 지상고에서 점성 때문에 다운포스가 꺾이는 현상은 과소평가하므로 `ground_clearance`는 기본 보호 변수입니다(지상고 값만으로는 제외하지 않음).
- **지면 모드 스크리닝의 한계:** 지면 근처에서는 비점성 해석이 날개 아래 흡입을 크게 과대평가합니다(예제에서 추정 Cl max 2단 약 6~33(지상고별 중앙값 10~17), 3단 약 14~110(중앙값 29~55), 실제는 대략 2~4). 그 결과 박리가 일찍 판정돼 추정 실속각이 0~6°(중앙값 1~2°)로 너무 낮게 나옵니다. 그래서 예제는 **받음각 선별을 끄고(`prune_aoa = false`)** 형상 계열 선별만 씁니다(25,344 → 3,484케이스). 받음각 범위는 첫 CFD 결과를 보고 줄이세요.

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

예제 규모: 2단 72개 + 3단 192개 = 형상 264개, × 지상고 4 × 받음각 6 × 속도 4 = 25344 케이스.

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
6. **값 커버리지 스팟 체크 (`spot_check_values`, 기본 켬):** 남은 계열에 한 번도 나오지 않는 변수 값(예: 플랩 각도 20°, 플랩 코드 80 mm)마다 그 값을 가진 최고 계열의 최고 형상도 받음각 1~2개만 CFD로 돌립니다. 저충실도 점수가 한쪽 끝 값에만 몰려도 반대쪽을 CFD로 한 번은 확인하게 해서, 5번 경고가 그 방향도 잡도록 합니다.
7. **플랩 박리 경고 (`flap_sep_warn`):** 남은 형상의 추정 플랩 흡입면 박리 비율 중앙값이 30%를 넘으면 `screen`이 경고합니다. 선별 점수(비점성 Cl)는 박리로 인한 양력 손실을 반영하지 않아서 플랩 각도가 큰 형상이 실제보다 유리하게 나오기 때문입니다.

**예제 스크리닝 결과 (확인할 점):** 남은 계열이 플랩 코드·각도의 최댓값 쪽에 몰립니다. 2단은 플랩 각도 30°만(코드 115·150 mm), 3단은 두 플랩 모두 150 mm에 두 번째 플랩 45°만 남습니다. 같은 저충실도 해석의 플랩 박리 추정은 남은 계열이 2단 약 40%, 3단 30~40%로, 제외된 각도(2단 20°: 약 20%, 3단 35°: 20~30%)보다 훨씬 큽니다. 실제로는 각도가 작은 계열이 다운포스가 더 클 수도 있으므로, 본 배치 전에 `run --priority --limit 100`으로 스팟 체크(20°, 80 mm, 35° 계열 포함)를 먼저 돌리고 `collect` 경고를 확인하세요. 경고가 나오면 `[screening] protect`에 `"*deflection"`을 넣어(각도별로 제외하지 않음) 다시 `screen` 합니다. 이 경우 CFD 케이스가 대략 두 배가 됩니다.

`selection.json`에는 실행 우선순위(`priority`: 스팟 체크 → 계열 순위 → 형상 점수 → 받음각)도 저장되어 `run --priority`가 이 순서를 씁니다.

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
| `spot_check_values` | true | 남은 계열에 없는 변수 값마다 그 값의 최고 계열도 대표 케이스만 CFD로 확인 |
| `flap_sep_warn` | 0.3 | 남은 형상의 추정 플랩 박리 비율 중앙값이 이보다 크면 경고 |
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
| `farfield_shape` | `"circle"` | (지면 없을 때) 영역 모양. `"circle"` = 원형 원거리 경계, `"box"` = 직사각형 인클로저 |
| `farfield_radius`, `farfield_size` | 50, 5.0 | (circle) 원거리 경계 반지름과 셀 크기 [메인 코드] |
| `farfield_split` | false | (circle) true면 원거리 경계를 앞 반원 `inlet`(velocity-inlet) + 뒤 반원 `outlet`(pressure-outlet)으로 나눔. 밀도 일정(비압축) 해석용 |
| `box_upstream`, `box_downstream`, `box_height` | 20, 40, 20 | (box) 메인 앞전 ~ 앞 경계, 메인 앞전 ~ 뒤 경계, 시위선 ~ 위·아래 경계 거리 [메인 코드]. 앞·위·아래 = `inlet`(velocity-inlet), 뒤 = `outlet`(pressure-outlet) |
| `box_far_size` | 2.0 | (box) 바깥 경계 셀 크기 [메인 코드] |
| `ground_upstream`, `ground_downstream`, `ground_height` | 5, 12, 6 | (지면 있을 때) 메인 앞전~inlet, 메인 앞전~outlet, 지면~위쪽 경계 [메인 코드] |
| `ground_far_size` | 0.5 | (지면 있을 때) inlet/outlet/top 근처 셀 크기 [메인 코드] |
| `min_quality` | 0.05 | 최소 셀 품질 (삼각형 SICN, 사각형 scaled Jacobian). 미달 시 경계층을 얇게 해서 재시도 |
| `timeout` | 300 | 형상 하나의 메쉬 생성 제한 시간 [s]. 메쉬는 별도 프로세스에서 만들고, 넘으면 그 형상만 실패 처리하고 다음 형상으로 넘어감 (0 = 제한 없음) |
| `refine` | 1.0 | 메쉬 전체 세분화 비율 (메쉬 독립성 확인용). 1.5면 모든 셀 크기·첫 셀 높이 ÷1.5, 표면·뒷전 셀 수 ×1.5, 경계층 성장률^(1/1.5). 슬롯·지면 통로·뒷전 세분화도 같은 비율 |

경계 이름은 `main`, `flap1`, `flap2`(wall)와, 지면이 있으면 `ground`(wall), `inlet`(velocity-inlet), `outlet`(pressure-outlet), `top`(symmetry), 지면이 없으면 `farfield`(pressure-far-field, `farfield_split = true`면 `inlet` + `outlet`, `farfield_shape = "box"`면 앞·위·아래 `inlet` + 뒤 `outlet`)입니다. 메쉬 폴더의 `mesh_domain.png`로 전체 영역 모양을 확인할 수 있습니다. 셀 영역은 `fluid`입니다. 같은 구성(2단 또는 3단) 안에서는 이름이 같아서 Fluent에서 메쉬만 교체해도 설정이 유지됩니다. 슬롯마다(메인↔플랩1, 플랩1↔플랩2) 그 슬롯의 최소 간격 기준으로 세분화합니다.
`[mesh]` 항목과 같은 이름의 케이스 변수가 있으면 케이스 값이 우선합니다. 단 `reynolds`는 형상 안에서 가장 큰 값을 써서 속도가 달라도 메쉬를 공유합니다.

**좌표 파일 정리 (자동):** UIUC 같은 좌표 파일은 그대로 쓰면 메쉬가 실패하는 경우가 있어 읽을 때 두 가지를 고칩니다. (1) 뒷전 두 끝점 간격이 0.0005c 미만이면(반올림으로 생긴 틈, 예: S1221) 뾰족한 뒷전으로 닫습니다. (2) 뾰족한 뒷전 바로 앞 윗면·아랫면 점의 쐐기각이 1° 미만이면(칼날형 뒷전, 예: S4180) 그 점 쌍을 지웁니다. 이 처리가 없으면 (1)은 셀 품질 미달, (2)는 gmsh가 멈추는 원인이 됩니다. NACA 4자리 공식 형상은 바뀌지 않습니다.

**동시 생성과 재현성:** `aero-auto mesh`는 CPU 코어 수만큼(기본 최대 8개, 작업 프로세스당 메모리 약 300 MB) 메쉬를 동시에 만듭니다(`-j`로 지정). 예제(4코어)에서 순차 대비 약 3.4배 빠릅니다. 형상마다 새 작업 프로세스에서 시간 제한을 두고 만들기 때문에 한 형상이 멈추거나 실패해도 나머지는 계속 만듭니다. 새 프로세스를 쓰는 이유는 재현성입니다. gmsh는 한 프로세스 안에서 앞서 만든 메쉬에 따라 같은 설정이어도 셀이 조금씩 달라지지만(예제 형상에서 73,452 → 73,454 → 73,406), 새 프로세스의 첫 메쉬는 항상 같습니다. 그래서 만드는 순서, 동시 개수, 팀원 분담(`--part`)과 관계없이 같은 메쉬가 나옵니다.

**품질:** `mesh_info.json`에 gmsh 품질(`min_quality`)과 함께 **Fluent와 같은 정의의 직교 품질**(`min_orthogonal_quality`, `Mesh > Quality`의 Minimum Orthogonal Quality)과 0.1 미만 셀 수를 기록합니다. `check`가 전체 메쉬의 최솟값을 보여주고 0.1 미만이면 경고, 0.01 미만이면 오류로 표시합니다.

### 메쉬 독립성 확인 (`mesh_level`, `refine`, `gci`)

같은 케이스를 메쉬 3단계로 풀어 Cl, Cd가 메쉬에 따라 얼마나 바뀌는지 보고, 본 배치 메쉬의 이산화 불확실성(GCI)을 구합니다. 예제: `examples/flap_optimization/mesh_study.toml` (2단·3단 각 1개 형상 × 받음각 4°·12° × 3단계 = 12 케이스, 결과 `runs_mesh/`).

```bash
aero-auto -c mesh_study.toml mesh
```

```bash
aero-auto -c mesh_study.toml run
```

```bash
aero-auto -c mesh_study.toml gci
```

- **단계 지정:** `[cases.sweep] mesh_level = [1, 2, 3]`과 `[derived] refine = "1.5 ** (mesh_level - 2)"`. `[mesh]` 항목과 같은 이름의 케이스 변수는 케이스 값이 우선하므로 단계마다 `refine`이 0.67 / 1 / 1.5가 되고, 2단계는 본 배치와 같은 메쉬입니다. 케이스 변수 `mesh_level`이 있으면 메쉬 이름 끝에 `_m<단계>`가 붙어 단계별로 따로 만들어집니다.
- **이름 충돌 방지:** `mesh_level` 없이 케이스마다 `[mesh]` 항목(`refine`, `surface_cells` 등)을 바꾸면, 이름이 같은 메쉬를 공유해 엉뚱한 메쉬로 계산되므로 케이스를 만들 때 오류로 멈춥니다.
- **`gci` 계산 (Celik et al. 2008):** 메쉬 단계만 다르고 나머지 변수가 같은 케이스끼리 묶고, 셀 수로 대표 셀 크기 h = N^(-1/2)를 정한 뒤 Cl, Cd(요소별 `cl-main`, `cl-flap1` 등 포함)마다 관측 차수 p, 셀 크기 0으로 외삽한 값, GCI(조밀·보통 메쉬), 수렴 형태(`monotonic` / `oscillatory` / `divergent`)를 `runs_mesh/mesh_study/gci.csv`와 그룹별 `gci_*.png`에 남깁니다.
- **판단 예:** 보통(본 배치) 메쉬의 GCI가 Cl 2%, Cd 5% 이내면 설정 그대로 진행. 넘으면 `project.toml`의 `[mesh] refine = 1.3`처럼 전체를 조밀하게 하고 다시 확인합니다. 단조 수렴이 아니면 먼저 반복 수렴(`conv_state`)을 확인하세요.

최적화 동안에는 메쉬 설정을 고정하세요. 설정이 같아야 형상 간 비교에서 메쉬 차이로 생기는 노이즈가 줄어듭니다.

## Fluent 베이스 케이스 만들기 (최초 1회, 2단·3단 각각)

> 화면 단위의 자세한 순서는 [docs/FLUENT_BASE_CASE.md](docs/FLUENT_BASE_CASE.md)를 보세요.

해석 설정은 베이스 케이스에 해 두고, 케이스마다 메쉬만 바꿔 끼웁니다. 2단과 3단은 경계 구성이 달라서 **`base_2el.cas.h5`와 `base_3el.cas.h5`를 따로** 만듭니다(세트별 `base_case` 변수로 저널이 골라 읽음).

아래는 예제(지면 효과) 기준입니다. 지면이 없는 프로젝트는 맨 아래 "지면이 없을 때"를 보세요.

1. 해당 구성의 메쉬 하나를 Fluent 2D 배정밀도(2ddp)에서 엽니다: `File > Read > Mesh` → `runs/_meshes/<형상>/mesh.msh`
2. `Mesh > Check`가 통과하는지 확인합니다. 단위는 m입니다.
3. **General**: Pressure-Based, Steady
4. **Models**: Viscous → Transition SST (Re 수십만 대역 권장. 층류 박리 기포와 천이를 잡음) 또는 k-omega SST(완전 난류)
5. **Materials**: air, Density = **constant 1.225 kg/m³**, Viscosity = 1.7894e-05 (마하수 0.1 미만이라 비압축으로 충분)
6. **Named Expressions**: `speed` = `27.78 [m/s]` (값은 저널이 케이스마다 바꿈)
7. **Boundary Conditions**
   - `inlet` (velocity-inlet): Velocity Magnitude = `speed`, 방향 Normal to Boundary, 난류 강도 0.5%, 점성비 10 정도
   - `outlet` (pressure-outlet): Gauge Pressure = 0
   - `top` (symmetry)
   - `ground` (wall): **Moving Wall**, Absolute, Translational, Speed = `speed`, Direction = (1, 0). 바닥이 공기와 같은 속도로 +x 방향으로 움직이는 실제 주행 조건
   - `main`, `flap1`(, `flap2`): wall (정지)
8. **Reference Values**: Compute from `inlet`, Density = 1.225, Length = 메인 코드(m), Area = 메인 코드 × 1 m (2D). 속도·길이·면적은 저널이 케이스마다 다시 설정합니다
9. **Report Definitions**: Force 리포트를 Coefficient로 만듭니다. **Zones에 `ground`는 넣지 마세요.**
   - `cx`: Zones = 날개 wall 전부(main, flap1, flap2) / Force Vector = (1, 0)
   - `cy`: Zones = 날개 wall 전부 / Force Vector = (0, 1)
   - `cx-main`, `cy-main`, `cx-flap1`, `cy-flap1`(, `cx-flap2`, `cy-flap2`): 각 요소만
   - Report File `coef-rfile`에 위 리포트를 모두 넣습니다.

   cl/cd 대신 물체축 cx/cy를 쓰는 이유: 도구가 유입 각도 θ(`flow_angle`, 지면 모드는 0)로 `cl = cy·cosθ − cx·sinθ`, `cd = cx·cosθ + cy·sinθ`를 계산합니다. 다운포스 날개는 `cl` 부호를 바꿔 아래 방향을 +로 출력합니다 (`cl-flap1`, `cd-flap2` 등 요소별도 자동).
10. **Solution**: Coupled, Pseudo Transient 권장 (지면 근처 수렴 안정). 초기화는 저널이 Hybrid로 합니다
11. **이미지용 그래픽 객체와 뷰**
   - Contour `contour-pressure` (Pressure Coefficient), `contour-velocity` (Velocity Magnitude)
   - 날개 전체가 보이게 확대 → `View > Save View` 이름 `near`
   - 슬롯 부분 확대 → 이름 `slot`
12. `File > Write > Case`로 프로젝트 폴더(`examples/flap_optimization/`)에 `base_2el.cas.h5` / `base_3el.cas.h5`로 저장합니다.

**지면이 없을 때(자유류):** 경계가 `farfield` 하나입니다. Density = ideal-gas, Named Expression `flow_angle`, `mach`를 만들고, `farfield`(pressure-far-field)에 Mach = `mach`, 방향 = (`cos(flow_angle)`, `sin(flow_angle)`)를 넣습니다. 저널은 `speed` 대신 `flow_angle`과 `mach`를 바꾸도록 씁니다(git 기록의 이전 `journal.jou` 참고).

### 파일럿 확인 순서

1. `sample_meshes.zip`(예제 폴더)의 메쉬 2개를 Fluent에서 열어 Mesh Check를 확인합니다.
   - 2단: `4412c375_4412c115d20g6o5_inv_h30_a4` (메인 375 mm + 플랩 115 mm, 뒤집힌 날개, 지상고 30 mm, 받음각 4°)
   - 3단: `4412c375_4412c80d25g6o5_4412c80d35g8o3_inv_h30_a4` (메인 375 mm + 플랩 80 mm × 2, 지상고 30 mm, 받음각 4°)
2. 위 순서로 베이스 케이스를 만든 뒤 `File > Replace Mesh`로 같은 구성의 다른 메쉬로 바꿔도 경계조건, 리포트, 뷰가 유지되는지 확인합니다.
3. `aero-auto check`로 베이스 케이스·메쉬·저널을 점검한 뒤, 케이스 하나씩 배치로 돌려봅니다. 스크리닝에서 남은 형상(메쉬 독립성 확인과 같은 형상)이라 결과가 본 배치에 그대로 쓰입니다:

   ```bash
   aero-auto run --only "2el_4412c375_f150d30g6_h30_v100_a4"
   ```

   ```bash
   aero-auto run --only "3el_4412c375_f150d25_f150d45g5_h30_v100_a4"
   ```

4. `runs/2el_4412c375_h30/2el_4412c375_f150d30g6_h30_v100_a4/transcript.txt`에서 저널 명령이 오류 없이 실행됐는지, `img_*.png`가 저장됐는지 확인합니다. Windows에서는 Fluent 출력이 `solver.log`가 아니라 이 transcript에 남습니다.

저널(`journal.jou`)의 TUI 명령은 Fluent 버전에 따라 프롬프트가 다를 수 있습니다. 오류가 나면 GUI에서 `File > Write > Start Journal`로 같은 작업을 녹화해 해당 줄을 교체하세요. 특히 `/file/replace-mesh`, `/define/named-expressions/edit`, `/report/reference-values/...`, `/display/save-picture` 부분을 확인하면 됩니다.

## 결과물

| 파일 | 내용 |
|---|---|
| `runs/summary.csv` | 케이스별 형상·받음각·속도, 상태, cx/cy, **cl/cd, l/d** (요소별 포함, 다운포스 날개는 cl = 다운포스 계수), `downforce_N_per_m`(또는 `lift_N_per_m`)/`drag_N_per_m`, 반복 수, **수렴 상태 `conv_state`**와 근거(`conv_*`, 마지막 잔차 `res_*`) |
| `runs/ranking.csv` | 형상별 최대 Cl과 그때 받음각, 최대 L/D와 그때 받음각·Cl·Cd. `objective` 기준 정렬 (`split_by` 조건별 순위, 2단·3단 함께 비교) |
| `runs/plots/*.png` | 형상별 Cl-α, Cd-α, L/D-α, 극곡선 (`split_by`가 있으면 조건별 폴더), `by_<조건>.png` |
| `runs/_meshes/<형상>/` | `geometry.png`, `mesh.png`, `mesh_slot1.png`(, `mesh_slot2.png`), `mesh_le.png`, `mesh_domain.png`(전체 영역), `mesh_info.json` |
| `runs/<그룹>/<케이스>/` | `convergence.png`(리포트 수렴 이력), `img_*.png`(Fluent 컨투어), `transcript.txt`(Fluent 출력, Windows `-hidden` 실행의 오류·잔차는 여기), `solver.log`(표준 출력), `result.cas.h5` |
| `runs_mesh/mesh_study/` | `gci.csv`, `gci_*.png` (메쉬 독립성 확인, `mesh_study.toml`) |

결과값은 마지막 `average_last`개 iteration의 평균입니다(`average_last_oscillating`을 주면 진동 판정 케이스만 그 개수로 평균, 실제 개수는 `avg_iters`). 진동하며 수렴하는 경우를 대비한 것으로, `summary.csv`의 `conv_state`와 `convergence.png`로 수렴 여부를 함께 확인하세요.

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
| `[results]` | `report_files`, `average_last`, `average_last_oscillating`(진동 케이스 평균 개수), `x_param`, `wind_axes`, `objective`(`max_ld`/`max_cl`), `split_by`(조건별 분석 변수) |
| `[screening]` | 스크리닝 설정 (위 표 참고) |
| `[convergence]` | 수렴 상태 판정 기준 (예제 2 참고) |
| `[ml]` | 데이터셋 분할·학습 설정 (예제 2 참고) |

저널 템플릿에서 쓸 수 있는 변수는 모든 케이스 변수(`case_set`, `base_case` 등 포함)에 더해 `${case_name}`, `${group}`, `${case_dir}`, `${project_dir}`, `${nprocs}`, `${mesh_file}`, `${mesh_name}`입니다. 포맷 지정도 됩니다: `${aoa:.2f}`.

## 테스트

```bash
pip install -e ".[dev]"
```

```bash
pytest
```

실제 Fluent 대신 가짜 솔버(`tests/fake_solver.py`, `tests/fake_airfoil_solver.py`, 테스트 안의 transcript·메쉬 단계용 가짜 솔버)로 실행 전 점검(`check`: 베이스 케이스·메쉬·경계 이름·저널·한글 경로·시간 추정), transcript 오류 검출과 이전 결과 정리, 메쉬 동시 생성, Fluent 정의 직교 품질, `refine`·`mesh_level` 메쉬 단계와 GCI(해석해로 검증한 관측 차수·외삽값), 값 커버리지 스팟 체크와 실행 우선순위, 4단(플랩 3개) 형상·메쉬, CST 피팅·얇은 익형 이론 α0, 수렴 상태 판정(진동 500회 평균, y+ 기록, 발산 건수), 형상 단위 분할, 단일 익형 데이터셋 생성부터 세 모델 학습·조건별 평가(부트스트랩 신뢰구간, 90% 구간 포함률)와 실행 간 비교, Transition SST 비교, 경계 사례 선정까지의 흐름과 2단·3단 형상 배치 정확도(mm 단위 갭), Fluent 메쉬 면 방향 규칙, 패널법 이론값, 케이스 세트, 세트·속도별 스크리닝 선별과 실행 반영, 메쉬 생성부터 해석, cl/cd 변환, 순위, 그래프까지의 전체 흐름을 검증합니다.
