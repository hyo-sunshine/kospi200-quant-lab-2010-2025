# -*- coding: utf-8 -*-
"""모델 1 어댑터 — rank_ensemble_strategy (LightGBM 18개 앙상블).

model/rank_ensemble_strategy/predict.py 의 인퍼런스 로직을
DB 적재 가능한 함수 형태로 감싼 것. 신호 정의는 원본과 동일하다.
"""
import json

from config import RANK_ENSEMBLE_DIR
from adapters.base import ModelAdapter, load_module_from_path

TOP_N_STORE = 12          # DB에는 상위 12종목까지 저장 (매수 8 + 관찰 4)


class RankEnsembleAdapter(ModelAdapter):
    model_id = "rank_ensemble"

    def __init__(self, name_lookup=None):
        self._common = None
        self._name_lookup = name_lookup or {}

    @property
    def common(self):
        if self._common is None:
            self._common = load_module_from_path(
                "rank_ensemble_common", RANK_ENSEMBLE_DIR / "common.py")
        return self._common

    def is_ready(self) -> dict:
        features_path = RANK_ENSEMBLE_DIR / "models/features.json"
        if not features_path.exists():
            return {"ready": False, "detail": "models/features.json 없음"}
        boosters = list((RANK_ENSEMBLE_DIR / "models").glob("prod_*.txt"))
        if len(boosters) < 18:
            return {"ready": False,
                    "detail": f"부스터 파일 {len(boosters)}/18개 — 재학습 필요"}
        return {"ready": True, "detail": f"LightGBM booster {len(boosters)}개"}

    def predict(self) -> dict:
        import lightgbm as lgb

        c = self.common
        feats = json.loads((c.MODELS_DIR / "features.json").read_text())
        df = c.load_feature_panel(with_labels=False)

        # 평활 20일 + 여유를 위해 최근 300거래일만 스코어링 (predict.py와 동일)
        recent = sorted(df["date"].unique())[-300:]
        pool = df.loc[df["date"].isin(recent)
                      & (df["is_pit_universe_kospi200"] == 1)
                      & (df["is_volume_zero"] == 0)
                      & df["mom_12_1"].notna()].copy()
        if pool.empty:
            raise RuntimeError("스코어링 가능한 유니버스가 비어 있습니다.")

        sig = {}
        for tag in ["flow60", "flow20"]:
            ranks = []
            for seed in c.SEEDS:
                booster = lgb.Booster(
                    model_file=str(c.MODELS_DIR / f"prod_{tag}_seed{seed}.txt"))
                pool["score"] = booster.predict(pool[feats[tag]])
                m = (pool.pivot_table(index="date", columns="ticker", values="score")
                     .rolling(c.SMOOTH, min_periods=5).mean())
                ranks.append(m.rank(axis=1, pct=True))
            sig[tag] = sum(ranks) / len(ranks)

        signal = c.W60 * sig["flow60"] + (1 - c.W60) * sig["flow20"]
        last = signal.index[-1]
        s = signal.loc[last].dropna().sort_values(ascending=False)

        latest_close = (pool[pool["date"] == last]
                        .set_index("ticker")["close"].to_dict())
        base_date = str(last.date())

        rows = []
        for rank, (ticker, score) in enumerate(s.head(TOP_N_STORE).items(), 1):
            rows.append({
                "base_date": base_date,
                "model_id": self.model_id,
                "ticker": ticker,
                "name": self._name_lookup.get(ticker),
                "rank": rank,
                "score": round(float(score), 6),
                # horizon 0 = 랭크신호(지평 없음). NULL이면 SQLite UNIQUE가
                # 중복 적재를 허용하므로 반드시 0을 쓴다.
                "horizon": 0,
                "close": float(latest_close.get(ticker) or 0) or None,
                "signal": "BUY" if rank <= c.N_HOLD else "WATCH",
            })
        return {"base_date": base_date, "rows": rows}
