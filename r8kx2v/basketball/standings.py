from __future__ import annotations

import re
import requests
from datetime import datetime

from .models import Fixture, TeamStanding


# =========================
# Config
# =========================
FS_NINJA_BASE = "https://global.flashscore.ninja"
FS_FEED_VER = "34"
FS_LOCALE_SEG = "x"
TIMEOUT = 10

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
    "Accept-Language": "ko,en;q=0.9",
    "Origin": "https://www.flashscore.co.kr",
    "Referer": "https://www.flashscore.co.kr/",
    "x-fsign": "SW9D1eZo",
}


# =========================
# Helpers
# =========================
def _build_url(mid: str) -> str:
    return f"{FS_NINJA_BASE}/{FS_FEED_VER}/{FS_LOCALE_SEG}/feed/df_to_1_{mid}_1"


def _normalize(name: str) -> str:
    """
    공백 / NBSP / zero-width space 제거
    """
    return "".join(name.split()) if name else ""


def _extract_int(pattern: str, text: str) -> int | None:
    m = re.search(pattern, text)
    return int(m.group(1)) if m else None


def _parse_team_block(block: str) -> TeamStanding | None:
    """
    팀 하나의 ~TR 블록에서
    팀명 / 승 / 패만 파싱
    """
    team_m = re.search(r"TN÷([^¬]+)", block)
    if not team_m:
        return None

    team = team_m.group(1)
    wins = _extract_int(r"TW÷(\d+)", block)
    losses = _extract_int(r"TL÷(\d+)", block)

    if wins is None or losses is None:
        return None

    return TeamStanding(
        team=team,
        wins=wins,
        losses=losses,
    )


# =========================
# Public API
# =========================
def fetch_standings(
    fixture: Fixture,
) -> tuple[TeamStanding | None, TeamStanding | None]:
    """
    Fixture(mid, home, away)를 받아
    홈 / 원정 팀의 시즌 승-패를 반환
    """

    url = _build_url(fixture.flashscore_mid)

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception:
        return None, None

    text = resp.text

    # 팀 단위 블록 분리 (축구 standings와 동일한 방식)
    blocks = text.split("~TR÷")[1:]

    home_standing = None
    away_standing = None

    for b in blocks:
        block = "~TR÷" + b
        standing = _parse_team_block(block)
        if not standing:
            continue

        team_norm = _normalize(standing.team)

        if _normalize(fixture.home) in team_norm:
            home_standing = standing

        if _normalize(fixture.away) in team_norm:
            away_standing = standing

    return home_standing, away_standing


# =========================
# Standalone execution
# =========================
if __name__ == "__main__":
    fixture = Fixture(
        flashscore_mid="t2WIx1L5",
        datetime_utc=datetime.utcnow(),
        home="안양 정관장",
        away="울산 현대모비스",
        league_name="KBL",
    )

    home_st, away_st = fetch_standings(fixture)

    print("HOME STANDING:", home_st)
    print("AWAY STANDING:", away_st)
