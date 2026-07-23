# -*- coding: utf-8 -*-
"""한국투자증권(KIS) OpenAPI 연동 클라이언트.

.env (프로젝트 루트)에 아래 키가 있으면 자동으로 연동된다. 없으면 모든
함수가 종전 스텁과 동일한 '미연동' 응답을 반환하므로 다른 기능에 영향이 없다.

    KIS_APP_KEY=...              # apiportal.koreainvestment.com 발급
    KIS_APP_SECRET=...
    KIS_ACCOUNT_NO=12345678-01   # 종합계좌 8자리 - 상품코드 2자리
    KIS_ENV=paper                # paper(모의투자, 기본) | real(실전)

설계
- 접근토큰은 24시간 유효·발급 1분 제한 → data/db/kis_token.json 에 캐시
- 모의(paper)/실전(real)은 도메인·TR ID만 다르고 파라미터는 동일
- 유량 제한(실전 20건/s, 모의 2건/s)에 맞춰 호출 간격을 강제
- 조회 실패는 예외를 올리지 않고 '미연동/오류' 구조 응답 (UI 안전).
  단, place_order 실패는 예외 — 주문은 실패를 조용히 삼키면 안 된다.
"""
import json
import logging
import threading
import time
from datetime import datetime, timedelta

import requests

from config import ROOT

logger = logging.getLogger("quantdesk.broker")

PROVIDER = "한국투자증권 (KIS OpenAPI)"
ENV_PATH = ROOT / ".env"
TOKEN_CACHE = ROOT / "data/db/kis_token.json"

DOMAIN = {
    "real": "https://openapi.koreainvestment.com:9443",
    "paper": "https://openapivts.koreainvestment.com:29443",
}
# TR ID — (실전, 모의)
TR = {
    "balance":    ("TTTC8434R", "VTTC8434R"),      # 주식 잔고 조회
    "buy":        ("TTTC0012U", "VTTC0012U"),      # 주식 주문(현금) 매수
    "sell":       ("TTTC0011U", "VTTC0011U"),      # 주식 주문(현금) 매도
    "daily_ccld": ("TTTC0081R", "VTTC0081R"),      # 일별 주문 체결 조회
    "price":      ("FHKST01010100", "FHKST01010100"),  # 현재가 (실전 도메인 전용)
}
MIN_INTERVAL = {"real": 0.06, "paper": 0.55}       # 유량 제한 대응 (초)

_cfg_lock = threading.Lock()
_cfg_cache: dict = {"mtime": None, "cfg": None}
_token_lock = threading.Lock()
_call_lock = threading.Lock()
_last_call_at = 0.0


# ---------- 설정 ----------
def get_config() -> dict | None:
    """`.env`의 KIS 설정. 4개 키 미충족 시 None (= 미연동 모드)."""
    try:
        mtime = ENV_PATH.stat().st_mtime
    except FileNotFoundError:
        return None
    with _cfg_lock:
        if _cfg_cache["mtime"] != mtime:
            vals: dict[str, str] = {}
            for line in ENV_PATH.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                v = v.strip()
                if " #" in v:                     # 인라인 주석 허용 (값에 공백+#는 없다고 가정)
                    v = v.split(" #", 1)[0].strip()
                vals[k.strip()] = v
            cfg = None
            acct = vals.get("KIS_ACCOUNT_NO", "").replace("-", "").strip()
            if vals.get("KIS_APP_KEY") and vals.get("KIS_APP_SECRET") and len(acct) >= 8:
                cfg = {
                    "app_key": vals["KIS_APP_KEY"],
                    "app_secret": vals["KIS_APP_SECRET"],
                    "cano": acct[:8],
                    "acnt_prdt": acct[8:10] or "01",
                    "env": "real" if vals.get("KIS_ENV", "paper").lower() == "real" else "paper",
                }
            _cfg_cache.update(mtime=mtime, cfg=cfg)
        return _cfg_cache["cfg"]


# ---------- 토큰 ----------
def _read_cached_token(cfg: dict) -> str | None:
    try:
        c = json.loads(TOKEN_CACHE.read_text())
        if (c.get("env") == cfg["env"] and c.get("app_key") == cfg["app_key"][:10]
                and c.get("expires_at", 0) - time.time() > 3600):
            return c["token"]
    except Exception:
        pass
    return None


def _get_token(cfg: dict) -> str | None:
    token = _read_cached_token(cfg)
    if token:
        return token
    with _token_lock:
        token = _read_cached_token(cfg)      # 락 대기 중 다른 스레드가 발급했을 수 있음
        if token:
            return token
        try:
            res = requests.post(
                DOMAIN[cfg["env"]] + "/oauth2/tokenP",
                json={"grant_type": "client_credentials",
                      "appkey": cfg["app_key"], "appsecret": cfg["app_secret"]},
                timeout=10)
            data = res.json()
        except Exception as e:
            logger.warning("KIS 토큰 발급 요청 실패: %s", e)
            return None
        token = data.get("access_token")
        if not token:
            logger.warning("KIS 토큰 발급 거절: %s", data)
            return None
        TOKEN_CACHE.parent.mkdir(parents=True, exist_ok=True)
        TOKEN_CACHE.write_text(json.dumps({
            "token": token, "env": cfg["env"], "app_key": cfg["app_key"][:10],
            "expires_at": time.time() + int(data.get("expires_in", 86400))}))
        logger.info("KIS 접근토큰 발급 (%s)", cfg["env"])
        return token


# ---------- 공통 요청 ----------
def _throttle(env: str):
    global _last_call_at
    with _call_lock:
        wait = MIN_INTERVAL[env] - (time.monotonic() - _last_call_at)
        if wait > 0:
            time.sleep(wait)
        _last_call_at = time.monotonic()


def _request(cfg: dict, method: str, path: str, tr_key: str,
             params: dict | None = None, body: dict | None = None,
             domain_env: str | None = None) -> dict | None:
    """KIS REST 호출. rt_cd != '0' 이거나 통신 실패면 None."""
    token = _get_token(cfg)
    if not token:
        return None
    env = domain_env or cfg["env"]
    tr_id = TR[tr_key][0 if env == "real" else 1]
    headers = {
        "content-type": "application/json; charset=utf-8",
        "authorization": f"Bearer {token}",
        "appkey": cfg["app_key"], "appsecret": cfg["app_secret"],
        "tr_id": tr_id, "custtype": "P",
    }
    _throttle(cfg["env"])
    try:
        res = requests.request(method, DOMAIN[env] + path, headers=headers,
                               params=params, json=body, timeout=10)
        data = res.json()
    except Exception as e:
        logger.warning("KIS 호출 실패 %s: %s", path, e)
        return None
    if data.get("rt_cd") != "0":
        logger.warning("KIS 오류 %s [%s] %s", path, data.get("msg_cd"), data.get("msg1"))
        data["_error"] = True
    return data


def _f(v, default=0.0) -> float:
    try:
        return float(str(v).replace(",", ""))
    except (TypeError, ValueError):
        return default


# ---------- 공개 API (api.py 가 사용하는 인터페이스) ----------
def get_status() -> dict:
    """연동 상태 — 토큰 발급 성공 여부로 판단."""
    cfg = get_config()
    if not cfg:
        return {"connected": False, "provider": PROVIDER, "account_no": None,
                "env": None,
                "message": "미연동 — .env에 KIS_APP_KEY / KIS_APP_SECRET / KIS_ACCOUNT_NO 설정"}
    if not _get_token(cfg):
        return {"connected": False, "provider": PROVIDER, "account_no": None,
                "env": cfg["env"],
                "message": "인증 실패 — APP KEY/SECRET 또는 KIS_ENV(모의/실전 앱 구분)를 확인하세요"}
    masked = cfg["cano"][:4] + "****-" + cfg["acnt_prdt"]
    label = "모의투자" if cfg["env"] == "paper" else "실전투자"
    return {"connected": True, "provider": PROVIDER, "account_no": masked,
            "env": cfg["env"],
            "message": f"{label} 연동됨 — 계좌 {masked}"}


def get_balance() -> dict:
    """계좌 잔고 + 보유 종목. 미연동/오류 시 connected=False 구조 유지."""
    empty = {"connected": False, "total_asset": None, "cash": None,
             "today_pnl": None, "today_pnl_pct": None, "holdings": []}
    cfg = get_config()
    if not cfg:
        return empty
    data = _request(cfg, "GET", "/uapi/domestic-stock/v1/trading/inquire-balance",
                    "balance", params={
                        "CANO": cfg["cano"], "ACNT_PRDT_CD": cfg["acnt_prdt"],
                        "AFHR_FLPR_YN": "N", "OFL_YN": "", "INQR_DVSN": "02",
                        "UNPR_DVSN": "01", "FUND_STTL_ICLD_YN": "N",
                        "FNCG_AMT_AUTO_RDPT_YN": "N", "PRCS_DVSN": "00",
                        "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""})
    if not data or data.get("_error"):
        return empty
    holdings = []
    for r in data.get("output1", []):
        qty = int(_f(r.get("hldg_qty")))
        if qty <= 0:
            continue
        holdings.append({
            "ticker": str(r.get("pdno", "")).zfill(6),
            "name": r.get("prdt_name", ""),
            "qty": qty,
            "avg_price": _f(r.get("pchs_avg_pric")),
            "cur_price": _f(r.get("prpr")),
            "pnl": _f(r.get("evlu_pfls_amt")),
            "pnl_pct": _f(r.get("evlu_pfls_rt")),
        })
    out2 = (data.get("output2") or [{}])[0]
    total_eval = _f(out2.get("tot_evlu_amt"))          # 총평가금액 (예수금 포함)
    pnl_sum = _f(out2.get("evlu_pfls_smtl_amt"))       # 평가손익 합계
    invested = sum(h["avg_price"] * h["qty"] for h in holdings)
    return {
        "connected": True,
        "env": cfg["env"],
        "total_asset": total_eval,
        "cash": _f(out2.get("dnca_tot_amt")),          # 예수금
        "today_pnl": pnl_sum,
        "today_pnl_pct": round(pnl_sum / invested * 100, 2) if invested else 0.0,
        "holdings": holdings,
    }


def get_current_price(ticker: str) -> float | None:
    """현재가. 앱키가 발급된 환경(모의/실전)과 같은 도메인으로 호출해야 한다."""
    cfg = get_config()
    if not cfg:
        return None
    data = _request(cfg, "GET", "/uapi/domestic-stock/v1/quotations/inquire-price",
                    "price", params={"FID_COND_MRKT_DIV_CODE": "J",
                                     "FID_INPUT_ISCD": str(ticker).zfill(6)})
    if not data or data.get("_error"):
        return None
    price = _f((data.get("output") or {}).get("stck_prpr"), default=0.0)
    return price or None


def get_trade_log(limit: int = 20) -> list[dict]:
    """최근 1주일 주문/체결 내역 (KIS 계좌 기준)."""
    cfg = get_config()
    if not cfg:
        return []
    end = datetime.now()
    start = end - timedelta(days=7)
    data = _request(cfg, "GET", "/uapi/domestic-stock/v1/trading/inquire-daily-ccld",
                    "daily_ccld", params={
                        "CANO": cfg["cano"], "ACNT_PRDT_CD": cfg["acnt_prdt"],
                        "INQR_STRT_DT": start.strftime("%Y%m%d"),
                        "INQR_END_DT": end.strftime("%Y%m%d"),
                        "SLL_BUY_DVSN_CD": "00", "INQR_DVSN": "00", "PDNO": "",
                        "CCLD_DVSN": "00", "ORD_GNO_BRNO": "", "ODNO": "",
                        "INQR_DVSN_3": "00", "INQR_DVSN_1": "",
                        "CTX_AREA_FK100": "", "CTX_AREA_NK100": ""})
    if not data or data.get("_error"):
        return []
    trades = []
    for r in data.get("output1", [])[:limit]:
        side_name = r.get("sll_buy_dvsn_cd_name", "")
        trades.append({
            "ts": f"{r.get('ord_dt', '')} {r.get('ord_tmd', '')}".strip(),
            "side": "BUY" if "매수" in side_name else "SELL",
            "ticker": str(r.get("pdno", "")).zfill(6),
            "name": r.get("prdt_name", ""),
            "qty": int(_f(r.get("ord_qty"))),
            "filled_qty": int(_f(r.get("tot_ccld_qty"))),
            "price": _f(r.get("avg_prvs")) or _f(r.get("ord_unpr")),
            "status": "filled" if _f(r.get("tot_ccld_qty")) > 0 else "submitted",
            "order_no": r.get("odno", ""),
        })
    return trades


def place_order(ticker: str, side: str, qty: int, price: int | None = None) -> dict:
    """현금 주문. price가 None이면 시장가(01), 있으면 지정가(00).

    실패 시 RuntimeError — 호출부(trading.py / api.py)가 기록·표시한다.
    """
    cfg = get_config()
    if not cfg:
        raise RuntimeError("KIS 미연동 — .env 설정 후 이용하세요.")
    side = side.upper()
    if side not in ("BUY", "SELL"):
        raise ValueError(f"side는 BUY/SELL: {side}")
    qty = int(qty)
    if qty <= 0:
        raise ValueError("수량은 1 이상이어야 합니다.")
    ord_dvsn = "01" if not price else "00"
    body = {
        "CANO": cfg["cano"], "ACNT_PRDT_CD": cfg["acnt_prdt"],
        "PDNO": str(ticker).zfill(6), "ORD_DVSN": ord_dvsn,
        "ORD_QTY": str(qty), "ORD_UNPR": str(int(price)) if price else "0",
    }
    data = _request(cfg, "POST", "/uapi/domestic-stock/v1/trading/order-cash",
                    "buy" if side == "BUY" else "sell", body=body)
    if not data:
        raise RuntimeError("KIS 주문 요청 실패 (통신 오류)")
    if data.get("_error"):
        raise RuntimeError(f"KIS 주문 거절: {data.get('msg1', '알 수 없는 오류')}")
    out = data.get("output", {})
    logger.info("KIS 주문 접수 %s %s x%d (%s) → 주문번호 %s",
                side, ticker, qty, "시장가" if ord_dvsn == "01" else f"지정가 {price}",
                out.get("ODNO"))
    return {
        "status": "submitted", "side": side, "ticker": str(ticker).zfill(6),
        "qty": qty, "price": price, "ord_dvsn": ord_dvsn,
        "order_no": out.get("ODNO", ""), "env": cfg["env"],
        "message": data.get("msg1", "주문 접수"),
    }
