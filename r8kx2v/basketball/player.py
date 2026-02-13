from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

import requests

from .models import PlayerTop, TeamTopPlayers


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

FIELD_SEP = "¬"
PAIR_SEP = "÷"

FEED_NAME = "df_psn_1"


# =========================
# Helpers
# =========================
def _build_url(match_mid: str) -> str:
    return f"{FS_NINJA_BASE}/{FS_FEED_VER}/{FS_LOCALE_SEG}/feed/{FEED_NAME}_{match_mid}"


def _clean_text(text: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", "", text or "")


def _to_int(x: Optional[str]) -> int:
    if x is None:
        return 0
    s = x.strip()
    if s == "-" or s == "":
        return 0
    try:
        return int(s)
    except Exception:
        return 0


def _time_to_seconds(mmss: str) -> int:
    s = (mmss or "").strip()
    if not s or s == "-":
        return 0
    if ":" not in s:
        return 0
    m, sec = s.split(":", 1)
    try:
        return int(m) * 60 + int(sec)
    except Exception:
        return 0


def _strip_star(name: str) -> str:
    s = (name or "").strip()
    if s.startswith("*"):
        s = s[1:].strip()
    return s


def _split_team_sections(stream: str) -> List[Tuple[str, str]]:
    """
    응답 전체에서 팀 섹션을 (~PA÷팀명) 기준으로 분리한다.
    - 앞쪽에 "PA÷전체" 같은 섹션이 있을 수 있음 → ~PA만 취급
    - 같은 팀이 중복으로 등장할 수 있음 → 이후 단계에서 가장 '선수 레코드가 많은 블록'을 채택
    """
    marker = FIELD_SEP + "~PA÷"
    parts = stream.split(marker)

    sections: List[Tuple[str, str]] = []
    for i, part in enumerate(parts):
        if i == 0:
            continue
        if not part:
            continue

        team_name = part.split(FIELD_SEP, 1)[0].strip()
        team_name = _strip_star(team_name)

        # block은 marker 이후 전체
        block = "~PA÷" + part
        sections.append((team_name, block))

    return sections


def _parse_players_from_team_block(block: str) -> List[PlayerTop]:
    """
    팀 블록에서 선수 레코드 추출.

    형태:
      ~PJ÷선수명
      ...
      PC÷<스탯열>

    PC는 |로 분리:
      idx0 = 득점(PTS)
      idx3 = 출전시간(MIN, mm:ss)

    필요한 것만:
      - name
      - points
      - minutes
    """
    tokens = block.split(FIELD_SEP)

    players: List[PlayerTop] = []
    current_name: Optional[str] = None

    for tok in tokens:
        if tok.startswith("~PJ" + PAIR_SEP):
            current_name = tok.split(PAIR_SEP, 1)[1].strip()
            current_name = _strip_star(current_name)

        elif tok.startswith("PC" + PAIR_SEP) and current_name:
            pc = tok.split(PAIR_SEP, 1)[1].strip()
            cols = pc.split("|")

            pts = _to_int(cols[0]) if len(cols) > 0 else 0
            mins = cols[3].strip() if len(cols) > 3 else "-"
            if mins == "":
                mins = "-"

            players.append(PlayerTop(name=current_name, points=pts, minutes=mins))
            current_name = None  # PC 처리했으면 다음 선수로 넘어감

    return players


def _top3_by_points(players: List[PlayerTop]) -> Tuple[PlayerTop, ...]:
    """
    득점 desc, 동점이면 출전시간 desc로 상위 3명
    """
    def key(p: PlayerTop):
        return (p.points, _time_to_seconds(p.minutes))

    ranked = sorted(players, key=key, reverse=True)
    return tuple(ranked[:3])


def _pick_best_block(blocks: List[str]) -> str:
    """
    같은 팀 블록이 중복될 수 있어서,
    '선수 레코드(~PJ)가 더 많은 블록'을 채택한다.
    """
    if not blocks:
        return ""
    best = blocks[0]
    best_count = best.count(FIELD_SEP + "~PJ÷")
    for b in blocks[1:]:
        c = b.count(FIELD_SEP + "~PJ÷")
        if c > best_count:
            best = b
            best_count = c
    return best


# =========================
# Public API
# =========================
def fetch_team_top_players(match_mid: str, team: str) -> Optional[TeamTopPlayers]:
    """
    입력:
      - match_mid: h2h의 last_mid
      - team: 관심 팀 이름(예: "안양 정관장", "울산 현대모비스")

    출력:
      - 해당 팀의 득점 TOP3만 (TeamTopPlayers)
      - 못 찾으면 None
    """
    url = _build_url(match_mid)

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception:
        return None

    stream = _clean_text(resp.text)
    sections = _split_team_sections(stream)
    if not sections:
        return None

    # 같은 팀이 중복으로 나올 수 있으므로 team->blocks로 모아서 best block 선택
    team_blocks: Dict[str, List[str]] = {}
    for team_name, block in sections:
        team_blocks.setdefault(team_name, []).append(block)

    # team 매칭은 공백 제거로 안정화
    target_key = team.replace(" ", "")

    for team_name, blocks in team_blocks.items():
        team_key = team_name.replace(" ", "")
        if team_key != target_key:
            continue

        block = _pick_best_block(blocks)
        if not block:
            return None

        players = _parse_players_from_team_block(block)
        top3 = _top3_by_points(players)

        return TeamTopPlayers(team=team_name, top3=top3)

    return None


# =========================
# Standalone test
# =========================
if __name__ == "__main__":
    mid = "fo8yQNy0"
    print(fetch_team_top_players(mid, "안양 정관장"))