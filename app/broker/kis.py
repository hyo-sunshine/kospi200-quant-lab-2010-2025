# -*- coding: utf-8 -*-
"""한국투자증권(KIS) OpenAPI 연동 — 스텁.

자동 매매 기능은 아직 구현하지 않는다 (모델·UI 완성이 우선).
UI가 필요로 하는 형태의 응답 구조만 정의해 두고, 실제 연동 시
이 파일의 함수 내부만 KIS REST 호출로 교체하면 된다.

KIS OpenAPI 참고: https://apiportal.koreainvestment.com
  - 국내주식 주문:   /uapi/domestic-stock/v1/trading/order-cash
  - 잔고 조회:       /uapi/domestic-stock/v1/trading/inquire-balance
  - 접근토큰 발급:   /oauth2/tokenP  (APP_KEY/APP_SECRET 환경변수로 관리할 것)
"""

PROVIDER = "한국투자증권 (KIS OpenAPI)"


def get_status() -> dict:
    """연동 상태. 실제 구현 시 토큰 발급 성공 여부로 대체."""
    return {
        "connected": False,
        "provider": PROVIDER,
        "account_no": None,
        "message": "미연동 — 자동 매매는 모델/UI 완성 후 구현 예정",
    }


def get_balance() -> dict:
    """계좌 잔고. 실제 구현 시 inquire-balance 응답을 이 구조로 매핑."""
    return {
        "connected": False,
        "total_asset": None,
        "cash": None,
        "today_pnl": None,
        "today_pnl_pct": None,
        "holdings": [],       # [{ticker, name, qty, avg_price, cur_price, pnl, pnl_pct}]
    }


def get_trade_log(limit: int = 20) -> list[dict]:
    """체결 로그. 실제 구현 시 주문/체결 내역 조회로 대체."""
    return []


def place_order(ticker: str, side: str, qty: int, price: int | None = None) -> dict:
    """주문 — 미구현. 자동 매매 단계에서 order-cash 호출로 구현."""
    raise NotImplementedError("KIS 주문 연동은 아직 구현되지 않았습니다.")
