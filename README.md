# KOSPI200 Quant Lab (2010–2025)

KOSPI200 유니버스 기반 퀀트 전략 연구·운용 저장소.
전략 모델 2개 + QuantDesk 웹 콘솔(예측 실행·DB 조회·자동매매 준비)로 구성된다.

## 구조

```
├── data/
│   ├── raw/                       ← KRX·DART·ECOS 원천 데이터
│   ├── processed/                 ← master_panel.parquet (일별 패널, 114컬럼)
│   └── db/quant.db                ← 예측 결과 DB (자동 생성, git 제외)
├── model/
│   ├── rank_ensemble_strategy/    ← 모델 1: LightGBM 랭크앙상블 (상위 8종목)
│   └── lstm_sequence_strategy/    ← 모델 2: LSTM 시퀀스 (1/7/30일 초과수익률)
├── app/                           ← QuantDesk 콘솔 (FastAPI + 스케줄러 + UI)
└── requirements.txt
```

## 데이터 준비 (저장소에 데이터 미포함)

`data/` 는 용량 문제(master_panel.parquet 368MB > GitHub 100MB 제한)로 **git에 없다**.
클론 후 아래 중 하나로 준비:

1. **기존 로컬/원 저장소에서 복사** — `data/processed/master_panel.parquet` 파일
   **1개만** `data/processed/` 에 배치하면 된다 (종목명·유니버스 메타 CSV는
   저장소에 포함되어 있음). 원본 수집·빌드 스크립트는 `sangjunInBus` 저장소의
   `scripts/` 참고: collect_* → build_master_panel_v9.py
2. 패널이 있으면 이후 갱신은 `pipeline/` 이 자동 수행 (매일 08:00 배치)

## 환경변수 설정 (.env)

```bash
cp .env.example .env   # 템플릿 복사 후 실제 값 입력
```

| 키 | 용도 | 발급 (전부 무료) | 없으면 |
|---|---|---|---|
| `ECOS_API_KEY` | 환율·기준금리·국고3년 일별 갱신 | [ecos.bok.or.kr](https://ecos.bok.or.kr) 회원가입 → 마이페이지 → 인증키 신청 | 매크로가 마지막 값으로 고정(ffill) — 환율 피처 신호 소실 |
| `DART_API_KEY` | 분기 재무제표 (추후 사용) | [opendart.fss.or.kr](https://opendart.fss.or.kr) 인증키 신청 | 현재는 영향 없음 (재무 z는 ffill) |
| `KRX_ID` / `KRX_PW` | 수급·공매도·정확한 시총 일별 수집 | [data.krx.co.kr](https://data.krx.co.kr) 회원가입 — **SNS 가입 시 마이페이지→정보수정에서 비밀번호 신규 설정 필요** | 네이버 폴백(시세만 갱신) — rank_ensemble 수급 피처 정지 |

`.env` 는 gitignore 대상이라 저장소에 올라가지 않는다. 파이프라인이 시작 시
`.env` 를 자동으로 읽으므로 별도 export 는 불필요하다.

**모델 가중치는 저장소에 포함** — LightGBM 18개(`model/rank_ensemble_strategy/models/`)
+ LSTM 체크포인트(`model/lstm_sequence_strategy/models/final_lstm_model.pt`).
패널만 준비되면 클론 직후 바로 예측 가능하다.

## 빠른 시작

```bash
# 1) 환경 (최초 1회)
/opt/homebrew/bin/python3.12 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 2) 서버 실행 → http://127.0.0.1:8500
.venv/bin/python -m uvicorn main:app --app-dir app --port 8500
```

서버가 뜨면:
- **매일 08:00 (Asia/Seoul)** 등록된 모델 전체 예측 → `data/db/quant.db`의
  `daily_predictions` 테이블에 적재 (APScheduler, 서버 프로세스가 켜져 있어야 함)
- UI에서 [모델 예측] 화면으로 수동 실행도 가능

## 모델

| | 모델 1 rank_ensemble | 모델 2 lstm_sequence |
|---|---|---|
| 알고리즘 | LightGBM 18개 (2지평×9시드) | LSTM (20일 시퀀스) |
| 출력 | 횡단면 랭크 신호 → 상위 8종목 | 1/7/30일 후 시장 대비 초과수익률 Top5 |
| 가중치 | `models/prod_*.txt` (저장소 포함) | `models/final_lstm_model.pt` (**별도 준비**) |
| 검증 | walk-forward 2013–2025, CAGR 16.8% | 노트북 walk-forward 참고 |

**LSTM 가중치 준비**: `model/lstm_sequence_strategy/train_production.py` 실행(느림),
또는 Colab 노트북 학습 산출물 `final_lstm_model.pt`를 `models/`에 복사.

## API 요약

| 경로 | 설명 |
|---|---|
| `GET /api/health` | 서버·스케줄러·브로커 상태 |
| `GET /api/models` | 모델 메타데이터 + 로드 가능 여부 |
| `POST /api/predict` | 모델 예측 실행 (`{"model_id": ...}`, 비동기 run_id 반환) |
| `GET /api/runs/{id}` | 실행 상태 폴링 |
| `GET /api/predictions` | DB 예측 조회 (`model_id`, `run_date` 필터) |
| `GET /api/stocks`, `GET /api/prices/{ticker}` | UI 차트용 시세 |
| `GET/PUT /api/settings` | 자동매매 전략 설정 |
| `GET /api/broker/status·balance·trades` | 한국투자증권(KIS) 연동 상태·잔고·체결 내역 |
| `GET /api/broker/plan` | 모델 신호 기반 매매 플랜 (리밸런스·익절·손절) |
| `POST /api/broker/order` | 수동 주문 (UI 확인 후) |
| `POST /api/broker/execute-plan` | 플랜 일괄 실행 — **모의투자 전용** |

### KIS 연동 (.env)

`.env`에 `KIS_APP_KEY` / `KIS_APP_SECRET` / `KIS_ACCOUNT_NO` / `KIS_ENV`를
설정하면 자동 인식된다 ([apiportal.koreainvestment.com](https://apiportal.koreainvestment.com)
발급, 모의투자 무료). 미설정 시 브로커 기능만 '미연동'으로 표시되고 나머지는 동일.

- `KIS_ENV=paper`(모의투자, 기본): [매매 · 주문] 화면에서 플랜 일괄 실행 +
  `auto_trade` ON 시 **평일 09:05** 자동 리밸런스 (08:00 예측 → 개장 후 5분 회피)
- `KIS_ENV=real`(실전): 안전장치로 **수동 주문만** 허용 — 자동/일괄 실행은 서버에서 차단

## 로드맵

- [x] 모델 2개 구조화 + 공통 어댑터 (로드/예측 인터페이스)
- [x] 예측 API·시세 API·설정 API
- [x] 매일 08:00 데이터 갱신 → 예측 → DB 적재 스케줄러
- [x] QuantDesk UI (대시보드/차트/모델/결과/설정)
- [x] 일별 데이터 자동 갱신 파이프라인 (`pipeline/` — KRX 계정 또는 네이버 폴백)
- [ ] KRX 계정(`KRX_ID`/`KRX_PW`) → 수급·공매도 일별 갱신 활성화
- [ ] ECOS·DART API 키 → 매크로·재무 갱신 활성화
- [x] 한국투자증권 KIS OpenAPI 연동 (잔고·주문·체결 + 매매 플랜·09:05 자동매매)
- [ ] 예측 적중률 평가 (실현 수익률 대비)

## 주의

- 네이버 폴백 모드에서는 수급·공매도 피처가 갱신되지 않아 (NaN)
  rank_ensemble 모델의 신호 품질이 저하된다 — KRX 계정 확보 권장 (상세: `pipeline/README.md`).
- 백테스트 성과는 실거래 성과를 보장하지 않는다 (상세: 각 전략 README).
