# r8kx2v/hockey/stats.py — IDE 타입경고 제거 완료 버전
from __future__ import annotations
import re
import requests
from typing import Optional, TypedDict, Dict, Any


class StatResult(TypedDict, total=False):
    team: str
    opponent: str
    side: str
    result: str
    score: str
    shots_on_goal: Optional[int]
    save_pct: Optional[float]


HEADERS: Dict[str, str] = {
    "User-Agent": "Mozilla/5.0",
    "Referer": "https://www.flashscore.co.kr/",
    "Origin": "https://www.flashscore.co.kr",
    "Accept": "*/*",
    "X-Fsign": "SW9D1eZo",
}


def fetch_stats(match_id: str) -> str:
    url = f"https://global.flashscore.ninja/34/x/feed/df_st_1_{match_id}"
    r = requests.get(url, headers=HEADERS, timeout=10)
    r.raise_for_status()
    return r.text


def _get_game_section(raw: str) -> str:
    m = re.search(r"SE÷경기(.*?)SE÷1 P", raw, flags=re.S)
    return m.group(1) if m else raw


def _blocks_from_game(game: str) -> list[str]:
    parts = re.split(r"(?:¬~SG÷|~SG÷)", game)
    return [p for p in parts if p]


def _parse_save_pct_backup(text: str, side: str) -> Optional[float]:
    cleaned = re.sub(r"[^A-Za-z0-9가-힣\s]", " ", text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    m = re.search(r"세이브\s*확률(.*)", cleaned)
    if not m:
        return None
    tail = m.group(1)
    m2 = re.search(r"SH(.*?)SI(.*)", tail)
    if not m2:
        return None
    home_part, away_part = m2.group(1), m2.group(2)

    def _pick(part: str) -> Optional[float]:
        nums = list(map(int, re.findall(r"\d+", part)))
        if len(nums) >= 4:
            return float(f"{nums[0]}.{nums[1]}")
        if len(nums) >= 3:
            return float(nums[0])
        return None

    return _pick(home_part) if side == "home" else _pick(away_part)


def parse_stats(raw: str, side: str) -> StatResult:
    """홈/어웨이 기준으로 유효슈팅·세이브 확률 추출"""
    result: StatResult = {"shots_on_goal": None, "save_pct": None}

    if side not in ("home", "away"):
        return result

    game = _get_game_section(raw)
    blocks = _blocks_from_game(game)

    for block in blocks:
        if block.startswith("유효슈팅"):
            m = re.search(r"SH÷(\d+).*?SI÷(\d+)", block, flags=re.S)
            if m:
                home_val, away_val = int(m.group(1)), int(m.group(2))
                if side == "home":
                    result["shots_on_goal"] = home_val
                else:
                    result["shots_on_goal"] = away_val

        elif block.startswith("세이브 확률"):
            mh = re.search(r"SH÷(\d+(?:\.\d+)?)%", block)
            ma = re.search(r"SI÷(\d+(?:\.\d+)?)%", block)
            if mh and ma:
                home_pct = float(mh.group(1))
                away_pct = float(ma.group(1))
                if side == "home":
                    result["save_pct"] = home_pct
                else:
                    result["save_pct"] = away_pct
            else:
                backup = _parse_save_pct_backup(block, side)
                if backup is not None:
                    result["save_pct"] = backup
    return result


def get_stats(
    match_id: str,
    side: str,
    team: str,
    opponent: str,
    outcome: str,
    score_for: int,
    score_against: int,
) -> StatResult:
    """팀 기준으로 경기 스탯과 기본 정보 통합 반환"""
    raw = fetch_stats(match_id)
    parsed: StatResult = parse_stats(raw, side)

    result: StatResult = {
        "team": team,
        "opponent": opponent,
        "side": side,
        "result": outcome,
        "score": f"{score_for}-{score_against}",
        "shots_on_goal": parsed.get("shots_on_goal"),
        "save_pct": parsed.get("save_pct"),
    }
    return result


if __name__ == "__main__":
    mid = input("Enter match_id: ").strip()
    side = input("Enter side (home/away): ").strip().lower()
    team = input("Enter team: ").strip()
    opponent = input("Enter opponent: ").strip()
    outcome = input("Enter result (win/lose): ").strip()
    score_for = int(input("Score for: "))
    score_against = int(input("Score against: "))
    data = get_stats(mid, side, team, opponent, outcome, score_for, score_against)
    print(data)
