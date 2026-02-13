from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
from typing import Tuple

# =========================
# Schedule output
# =========================
@dataclass(frozen=True, slots=True)
class Fixture:
    flashscore_mid: str
    datetime_utc: datetime

    home: str
    away: str

    # 팀 식별자(있으면 채우고, 없으면 None)
    home_id: Optional[str] = None
    away_id: Optional[str] = None

    league_name: str = ""
    sport: str = "basketball"


# =========================
# Standings output
# =========================
@dataclass(frozen=True, slots=True)
class TeamStanding:
    team: str
    wins: int
    losses: int


# =========================
# H2H (last match) output
# =========================
@dataclass(frozen=True, slots=True)
class LastMatchSummary:
    team: str
    opp: str

    score_for: int
    score_against: int
    result: str

    last_mid: str
    played_at_utc: Optional[datetime] = None

    # ✅ 추가: 직전 경기에서 team이 홈이었는지
    is_home: bool = True

@dataclass(frozen=True, slots=True)
class H2H:
    home: LastMatchSummary
    away: LastMatchSummary

@dataclass(frozen=True, slots=True)
class QuarterSummary:
    team: str

    q1_for: int
    q1_against: int

    q2_for: int
    q2_against: int

    q3_for: int
    q3_against: int

    q4_for: int
    q4_against: int

@dataclass(frozen=True, slots=True)
class PlayerTop:
    name: str
    points: int
    minutes: str  # "mm:ss"

@dataclass(frozen=True, slots=True)
class TeamTopPlayers:
    team: str
    top3: Tuple[PlayerTop, ...]  # 0~3

@dataclass(frozen=True)
class TeamLineup:
    team: str
    starters: Tuple[str, ...]
    starter_ratings: Tuple[Optional[float], ...] = ()