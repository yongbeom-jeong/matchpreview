from __future__ import annotations

import re
from typing import Dict, Optional, Tuple

import requests

from .models import LastMatchSummary, QuarterSummary


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


# =========================
# Internal helpers
# =========================
def _build_url(match_mid: str) -> str:
    # 네가 찍어준 패턴 그대로
    return f"{FS_NINJA_BASE}/{FS_FEED_VER}/{FS_LOCALE_SEG}/feed/df_sur_1_{match_mid}"


def _clean_text(text: str) -> str:
    # control chars 제거 (h2h랑 동일)
    return re.sub(r"[\x00-\x1f\x7f]+", "", text or "")

def _get(kv: Dict[str, str], key: str) -> Optional[str]:
    # "BC" 또는 "~BC" 형태 모두 대응
    return kv.get(key) or kv.get("~" + key)

def _kv_map(stream: str) -> Dict[str, str]:
    """
    df_sur_1 응답 전체를 kv 딕셔너리로 만든다.
    (쿼터 키 BA~BH만 쓰면 되므로 엔트리 선택 로직 불필요)
    """
    kv: Dict[str, str] = {}
    for tok in stream.split(FIELD_SEP):
        if PAIR_SEP in tok:
            k, v = tok.split(PAIR_SEP, 1)
            kv[k] = v
    return kv


def _to_int(x: Optional[str]) -> int:
    try:
        return int(x) if x is not None else 0
    except Exception:
        return 0


def _extract_home_away_quarters(kv: Dict[str, str]) -> Optional[Tuple[int, int, int, int, int, int, int, int]]:
    """
    return (home_q1, away_q1, home_q2, away_q2, home_q3, away_q3, home_q4, away_q4)

    키:
      1Q: BA/BB
      2Q: BC/BD (가끔 ~BC로 옴)
      3Q: BE/BF (가끔 ~BE로 옴)
      4Q: BG/BH (가끔 ~BG로 옴)
    """
    h1, a1 = _to_int(_get(kv, "BA")), _to_int(_get(kv, "BB"))
    h2, a2 = _to_int(_get(kv, "BC")), _to_int(_get(kv, "BD"))
    h3, a3 = _to_int(_get(kv, "BE")), _to_int(_get(kv, "BF"))
    h4, a4 = _to_int(_get(kv, "BG")), _to_int(_get(kv, "BH"))

    if (h1, a1, h2, a2, h3, a3, h4, a4) == (0, 0, 0, 0, 0, 0, 0, 0):
        return None

    return h1, a1, h2, a2, h3, a3, h4, a4



# =========================
# Public API
# =========================
def fetch_quarter_summary(last: LastMatchSummary) -> Optional[QuarterSummary]:
    """
    입력: LastMatchSummary (last_mid, is_home, team)
    출력: QuarterSummary (team 관점 q1~q4 for/against)
    """
    url = _build_url(last.last_mid)

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception:
        return None

    stream = _clean_text(resp.text)
    kv = _kv_map(stream)

    q = _extract_home_away_quarters(kv)
    if not q:
        return None

    home_q1, away_q1, home_q2, away_q2, home_q3, away_q3, home_q4, away_q4 = q

    # team 관점으로 뒤집기
    if last.is_home:
        q1_for, q1_against = home_q1, away_q1
        q2_for, q2_against = home_q2, away_q2
        q3_for, q3_against = home_q3, away_q3
        q4_for, q4_against = home_q4, away_q4
    else:
        q1_for, q1_against = away_q1, home_q1
        q2_for, q2_against = away_q2, home_q2
        q3_for, q3_against = away_q3, home_q3
        q4_for, q4_against = away_q4, home_q4

    return QuarterSummary(
        team=last.team,
        q1_for=q1_for,
        q1_against=q1_against,
        q2_for=q2_for,
        q2_against=q2_against,
        q3_for=q3_for,
        q3_against=q3_against,
        q4_for=q4_for,
        q4_against=q4_against,
    )


# =========================
# Standalone test
# =========================
if __name__ == "__main__":
    # h2h 출력에서 가져온 값으로 빠르게 테스트
    last = LastMatchSummary(
        team="안양 정관장",
        opp="부산 KCC",
        score_for=76,
        score_against=68,
        result="W",
        last_mid="fo8yQNy0",
        played_at_utc=None,
        is_home=True,
    )

    out = fetch_quarter_summary(last)
    print(out)
