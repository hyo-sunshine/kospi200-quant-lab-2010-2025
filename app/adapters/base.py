# -*- coding: utf-8 -*-
"""모델 어댑터 공통 — 파일 경로 기반 모듈 로더와 어댑터 인터페이스.

model/ 아래 전략 패키지들은 스크립트 스타일(`from common import ...`)이라
그대로 import하면 서로 이름이 충돌한다. importlib으로 고유 이름을 붙여 로드한다.
"""
import importlib.util
import sys
from abc import ABC, abstractmethod
from pathlib import Path


def load_module_from_path(unique_name: str, path: Path):
    """파일 경로에서 모듈을 고유 이름으로 로드한다 (common.py 이름 충돌 방지)."""
    if unique_name in sys.modules:
        return sys.modules[unique_name]
    if not path.exists():
        raise FileNotFoundError(f"모듈 파일이 없습니다: {path}")
    spec = importlib.util.spec_from_file_location(unique_name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[unique_name] = module
    spec.loader.exec_module(module)
    return module


class ModelAdapter(ABC):
    """두 전략 모델을 동일한 방식으로 로드/예측하기 위한 인터페이스."""

    model_id: str = ""

    @abstractmethod
    def is_ready(self) -> dict:
        """가중치 파일 존재 여부 등 로드 가능 상태. {"ready": bool, "detail": str}"""

    @abstractmethod
    def predict(self) -> dict:
        """최신일 예측 수행.

        Returns:
            {"base_date": "YYYY-MM-DD", "rows": [DB insert용 dict, ...]}
        """
