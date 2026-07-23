"""Kostolany 6-regime taxonomy and action recommendations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Mapping


class Regime(IntEnum):
    """Six Kostolany egg phases."""

    A1 = 0  # accumulation / correction up
    A2 = 1  # participation / accompanying up
    A3 = 2  # exaggeration / euphoria
    B1 = 3  # distribution / correction down
    B2 = 4  # accompanying down
    B3 = 5  # panic / capitulation


@dataclass(frozen=True)
class RegimeMeta:
    code: str
    name_ko: str
    name_en: str
    side: str  # "up" | "down"
    intensity: int  # 1=correction, 2=accompanying, 3=exaggeration
    action_ko: str
    color: str
    # Egg coordinates in normalized space: x in [-1,1] (left=fear, right=greed),
    # y in [-1,1] (bottom=low price, top=high price) along an oval path.
    egg_x: float
    egg_y: float


REGIME_META: Mapping[Regime, RegimeMeta] = {
    Regime.A1: RegimeMeta(
        code="A1",
        name_ko="수정(상승)",
        name_en="Accumulation",
        side="up",
        intensity=1,
        action_ko="매수·축적",
        color="#2F6FED",
        egg_x=-0.55,
        egg_y=-0.75,
    ),
    Regime.A2: RegimeMeta(
        code="A2",
        name_ko="동행(상승)",
        name_en="Participation",
        side="up",
        intensity=2,
        action_ko="보유·관망 (추격 자제)",
        color="#3D9B6E",
        egg_x=0.15,
        egg_y=0.15,
    ),
    Regime.A3: RegimeMeta(
        code="A3",
        name_ko="과장(상승)",
        name_en="Euphoria",
        side="up",
        intensity=3,
        action_ko="매도·차익",
        color="#D64545",
        egg_x=0.75,
        egg_y=0.85,
    ),
    Regime.B1: RegimeMeta(
        code="B1",
        name_ko="수정(하락)",
        name_en="Distribution",
        side="down",
        intensity=1,
        action_ko="매도·정리",
        color="#C47A2C",
        egg_x=0.55,
        egg_y=0.55,
    ),
    Regime.B2: RegimeMeta(
        code="B2",
        name_ko="동행(하락)",
        name_en="Decline",
        side="down",
        intensity=2,
        action_ko="관망·현금 확보",
        color="#8B6BB5",
        egg_x=-0.15,
        egg_y=-0.15,
    ),
    Regime.B3: RegimeMeta(
        code="B3",
        name_ko="과장(하락)",
        name_en="Capitulation",
        side="down",
        intensity=3,
        action_ko="매수 (바닥에서 줍기)",
        color="#1F4E8C",
        egg_x=-0.75,
        egg_y=-0.90,
    ),
}

REGIME_ORDER: tuple[Regime, ...] = (
    Regime.A1,
    Regime.A2,
    Regime.A3,
    Regime.B1,
    Regime.B2,
    Regime.B3,
)

# Typical cycle transitions (soft prior for HMM mapping / transition alerts)
CYCLE_EDGES: tuple[tuple[Regime, Regime], ...] = (
    (Regime.A1, Regime.A2),
    (Regime.A2, Regime.A3),
    (Regime.A3, Regime.B1),
    (Regime.B1, Regime.B2),
    (Regime.B2, Regime.B3),
    (Regime.B3, Regime.A1),
)

DISCLAIMER_KO = (
    "본 정보는 교육·연구 목적의 국면 인식 보조 자료이며 투자 권유·자문이 아닙니다. "
    "투자 판단과 손실에 대한 책임은 이용자 본인에게 있습니다."
)
