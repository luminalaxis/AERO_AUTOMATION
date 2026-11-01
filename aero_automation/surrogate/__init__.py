"""CFD 결과(summary.csv)로 에어포일 Cl, log10(Cd) 회귀 대리 모델을 만들고 검증한다.

    aero-auto dataset   # summary.csv → 학습 데이터(CST 계수, 받음각, log10Re → Cl, log10Cd) + 분할
    aero-auto train     # 기준 방법 / LightGBM / MLP 앙상블 학습·평가 (실험 설정별 폴더)
    aero-auto compare   # 실험 결과 비교표

모듈:
    cst       CST(Class-Shape Transformation) 형상 계수 피팅, 캠버선, 얇은 익형 이론 α0
    dataset   학습 데이터 구성, 수렴 상태 필터, 형상 단위 분할, 분포 밖(OOD) 세트
    models    기준 방법, LightGBM(분위 회귀 예측 구간), MLP 5시드 앙상블(PyTorch)
    evaluate  MAE, 형상 단위 부트스트랩 95% CI, 예측 구간 포함률, 조건별 성능, 경계 사례 후보
"""
