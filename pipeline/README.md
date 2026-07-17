# pipeline — 일별 데이터 수집 · 패널 증분 갱신

매일 08:00 배치(또는 수동)로 `master_panel.parquet`을 최신 거래일까지 갱신한다.

```bash
../.venv/bin/python -m pipeline.update_panel              # 오늘까지
curl -X POST http://127.0.0.1:8500/api/update-data        # 서버에서 실행
```

## 수집 소스 (자동 선택)

| 모드 | 조건 | 갱신되는 것 | 갱신 안 되는 것 |
|---|---|---|---|
| **KRX** (pykrx) | 환경변수 `KRX_ID`/`KRX_PW` (data.krx.co.kr 무료 회원) | 시세·시총·거래대금·**수급**·**공매도**·지수 | 매크로(ECOS 키)·재무(DART 키) |
| **네이버** (폴백) | 키 불필요 | 시세(OHLCV)·지수·시총(근사) | 수급·공매도 → **NaN**, 매크로·재무 → 마지막 값 유지 |

> KRX 정보데이터시스템은 2025-12-27부터 로그인 필수. 계정만 만들면
> 수급 데이터가 살아나므로 **rank_ensemble 모델의 핵심 피처를 위해 계정 확보 권장**.

## 파생 피처 재계산 원칙

- 기존 이력 행은 **절대 수정하지 않고** 신규 거래일 행만 append (임시파일 → 원자적 교체)
- 모든 수식은 기존 패널 저장값과 **역검증(일치율 100%)** 후 사용 — [features.py](features.py) 참고
- EWM 계열(RSI/EMA/MACD/ATR)은 종목별 전체 이력으로 계산해 연속성 보장
- 라벨(label_*)은 NaN — 미래 정보이므로 예측에 불필요

## 알려진 근사 (원식 미복원)

| 항목 | 처리 | 영향 |
|---|---|---|
| `macro_usdkrw_chg_5d/20d` | 거래일 기준 pct_change (원식과 중앙값 3e-3 오차) | 미미 |
| `is_market_stress` | vola>1.5% & 20일수익<-5%, 또는 20일수익<-10% | 플래그 1개 |
| `trading_value` (네이버) | close×volume 근사 | LSTM log 피처라 미미 |
| `kospi_close` | **KOSPI 종합지수** (컬럼 사전의 "KOSPI200" 표기는 오기 — 실측 확인) | — |

## 남은 확장 (키 확보 시)

1. `KRX_ID`/`KRX_PW` → 수급·공매도 갱신 + 유니버스(구성종목) 자동 반영
2. ECOS 키 → `macro_*` 실시간 갱신 (현재 ffill)
3. DART 키 → 분기 재무 z 갱신 (현재 ffill, 분기 1회면 충분)
