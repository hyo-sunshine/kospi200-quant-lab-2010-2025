# 원본 데이터 — 출처와 수집 방법

전 데이터 공통 기간 **2010-01-04 ~ 2025-12-30** (공매도만 2016~).

## 1. krx/ — 한국거래소 시장데이터 (pykrx)

| 파일 | 내용 | 수집 방법 (pykrx) |
|---|---|---|
| `ohlcv_2010_2025.csv` | 일별 시/고/저/종가·거래량 | `stock.get_market_ohlcv(start, end, ticker)` 를 유니버스 전 종목 반복 |
| `ohlcv_*_corrected.csv` | 결측 보정 + 2014 가격제한폭(±15%) 변경 보정 + ticker 6자리 zfill | 패널 빌드에 실제 사용된 보정본 |
| `market_cap_2010_2025.csv` | 시가총액·상장주식수·거래대금 | `stock.get_market_cap(start, end, ticker)` |
| `investor_flow_2010_2025.csv` | 투자자별 순매수 (외국인/기관/개인/법인, 원) | `stock.get_market_trading_value_by_date(start, end, ticker, detail=True)` |
| `kospi_index_2010_2025.csv` | KOSPI200 지수 OHLCV | `stock.get_index_ohlcv(start, end, "1028")` |
| `short_volume_2016_2025.csv` | 공매도 거래량·비중 (2016-01 공시 시작) | `stock.get_shorting_volume_by_date(start, end, ticker)` |

- `_zfill` 접미사 = ticker 를 6자리 문자열로 보정한 판본 (원본은 정수라 앞자리 0 유실).
- 수집 시 호출 간 0.3~0.5초 대기 (KRX 서버 부하 방지), 체크포인트 저장 후 재개 방식.

## 2. macro/ — 한국은행 ECOS API

`ecos_<통계코드>.csv` (컬럼: 날짜, 값). `_summary.csv` 가 코드→지표 매핑.
수집: ECOS OpenAPI `StatisticSearch` (https://ecos.bok.or.kr, API key 필요).

| 코드 | 지표 | 주기 |
|---|---|---|
| 722Y001 | 한국은행 기준금리 | 일 |
| 731Y001 | 원/달러 환율 | 일 |
| 817Y002 | 국고채 3년 금리 | 일 |
| 901Y009 | 소비자물가지수 CPI | 월 |
| 901Y033 | 전산업생산지수 | 월 |
| 161Y006 | M2 평잔 | 월 |
| 901Y118 | 수출금액(통관) | 월 |
| 200Y104 | 실질 GDP(계절조정) | 분기 |

## 3. dart/ — 전자공시 분기 재무 (DART OpenAPI)

- `corp_code.csv`: DART 기업코드 ↔ 종목코드 매핑 (`corpCode.xml` API).
- 분기 재무 원본: `fnlttSinglAcntAll` API (종목×연도×보고서코드×연결/별도).
  응답 JSON 캐시 약 35,000개는 용량 문제로 이 저장소에 미포함 —
  원 저장소 `sangjunInBus/data/dart/` 에 보존. 가공 결과물은
  `../processed/dart_quarterly_2010_2025.parquet` 로 포함됨.
- **PIT 안전 결합**: 재무값은 분기말이 아니라 공시일(분기말+45일, 사업보고서 +90일)
  이후부터만 사용 — 미래정보 누출 방지.

## 4. meta/ — 유니버스·보정 메타

| 파일 | 내용 |
|---|---|
| `kospi200_universe_by_year.csv` | **연도별 PIT KOSPI200 구성종목** — `stock.get_index_portfolio_deposit_file("1028", 그해 첫 거래일)`. 생존 편향 제거의 핵심 |
| `ticker_sector_map.csv` | 종목→섹터 16분류 |
| `boundary_*.csv` | 2014 가격제한폭 제도 변경(±15%) 경계 보정 계수 |
