from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Dict, List, Optional

import requests

from .models import Fixture, H2H, LastMatchSummary


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
# Helpers
# =========================
def _build_url(mid: str) -> str:
    return f"{FS_NINJA_BASE}/{FS_FEED_VER}/{FS_LOCALE_SEG}/feed/df_hh_1_{mid}"


def _clean_text(text: str) -> str:
    return re.sub(r"[\x00-\x1f\x7f]+", "", text or "")


def _strip_star(name: str) -> str:
    s = (name or "").strip()
    if s.startswith("*"):
        s = s[1:].strip()
    return s


def _norm(name: str) -> str:
    # 비교용 정규화: 별표 제거 + 모든 공백 제거
    return "".join(_strip_star(name).split())


def _to_int(x: Optional[str]) -> int:
    try:
        return int(x) if x is not None else 0
    except Exception:
        return 0


def _epoch_to_dt_utc(epoch_sec: int) -> Optional[datetime]:
    if epoch_sec <= 0:
        return None
    return datetime.fromtimestamp(epoch_sec, tz=timezone.utc).replace(tzinfo=None)


def _kv_map(entry: str) -> Dict[str, str]:
    kv: Dict[str, str] = {}
    for tok in entry.split(FIELD_SEP):
        if PAIR_SEP in tok:
            k, v = tok.split(PAIR_SEP, 1)
            kv[k] = v
    return kv


def _iter_match_entries(stream: str) -> List[Dict[str, str]]:
    """
    응답 전체에서 모든 경기 엔트리를 뽑는다.
    엔트리 시작은 "~KC÷{epoch}" 형태.
    """
    parts = stream.split(FIELD_SEP + "~KC÷")
    entries: List[Dict[str, str]] = []

    for i, p in enumerate(parts):
        if not p:
            continue
        # split 이후 조각에 KC가 빠져있으니 복원
        if i == 0:
            # 첫 조각은 헤더/잡정보 포함 가능: KC÷가 있으면 그 지점부터만
            idx = p.find("KC÷")
            if idx == -1:
                continue
            raw_entry = p[idx:]
        else:
            raw_entry = "KC÷" + p

        kv = _kv_map(raw_entry)

        # 최소 요건: mid + 팀명 2개
        if kv.get("KP") and kv.get("KJ") and kv.get("KK"):
            entries.append(kv)

    return entries


def _extract_scores(kv: Dict[str, str]) -> Optional[tuple[int, int]]:
    """
    home_score, away_score 반환.
    """
    home_score = _to_int(kv.get("KU"))
    away_score = _to_int(kv.get("KT"))

    if home_score == 0 and away_score == 0:
        kl = kv.get("KL")  # "76:68"
        if kl and ":" in kl:
            a, b = kl.split(":", 1)
            home_score = _to_int(a.strip())
            away_score = _to_int(b.strip())

    if home_score == 0 and away_score == 0:
        return None
    return home_score, away_score


def _pick_last_match_for_team(entries: List[Dict[str, str]], team_name: str) -> Optional[LastMatchSummary]:
    """
    entries에서 'team이 포함된 가장 최근 1경기'를 반환.
    - 응답 섹션 구조에 의존하지 않음(안전)
    - 최신순 보장 안 되더라도 KC로 정렬해서 최신부터 탐색
    - 반환에 is_home 포함
    """
    team_key = _norm(team_name)

    def kc_of(kv: Dict[str, str]) -> int:
        return _to_int(kv.get("KC"))

    for kv in sorted(entries, key=kc_of, reverse=True):
        home_name_raw = kv.get("KJ", "")
        away_name_raw = kv.get("KK", "")

        home_name = _strip_star(home_name_raw)
        away_name = _strip_star(away_name_raw)

        home_key = _norm(home_name)
        away_key = _norm(away_name)

        # 이 경기에 team이 없으면 스킵
        if team_key != home_key and team_key != away_key:
            continue

        scores = _extract_scores(kv)
        if not scores:
            continue
        home_score, away_score = scores

        is_home = (team_key == home_key)

        if is_home:
            team = home_name
            opp = away_name
            score_for, score_against = home_score, away_score
        else:
            team = away_name
            opp = home_name
            score_for, score_against = away_score, home_score

        if score_for > score_against:
            result = "W"
        elif score_for < score_against:
            result = "L"
        else:
            result = "D"

        last_mid = kv.get("KP", "")
        played_at_utc = _epoch_to_dt_utc(_to_int(kv.get("KC")))

        return LastMatchSummary(
            team=team,
            opp=opp,
            score_for=score_for,
            score_against=score_against,
            result=result,
            last_mid=last_mid,
            played_at_utc=played_at_utc,
            is_home=is_home,   # ✅ 추가
        )

    return None


# =========================
# Public API
# =========================
def fetch_h2h(fixture: Fixture) -> Optional[H2H]:
    """
    df_hh_1_{fixture_mid} 호출 → 홈/원정 각각 직전 경기 요약 반환
    (섹션에 의존하지 않고 응답 전체에서 팀별 최신 경기 1개를 찾음)
    """
    url = _build_url(fixture.flashscore_mid)

    try:
        resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
        resp.raise_for_status()
    except Exception:
        return None

    stream = _clean_text(resp.text)
    entries = _iter_match_entries(stream)
    if not entries:
        return None

    home_sum = _pick_last_match_for_team(entries, fixture.home)
    away_sum = _pick_last_match_for_team(entries, fixture.away)

    if not home_sum or not away_sum:
        return None

    return H2H(home=home_sum, away=away_sum)


def fetch_last_match(fixture: Fixture, side: str) -> Optional[LastMatchSummary]:
    h2h = fetch_h2h(fixture)
    if not h2h:
        return None
    if side == "home":
        return h2h.home
    if side == "away":
        return h2h.away
    raise ValueError(f"side must be 'home' or 'away'. got={side!r}")


# =========================
# Standalone test
# =========================
if __name__ == "__main__":
    now_utc_naive = datetime.now(timezone.utc).replace(tzinfo=None)

    fixture = Fixture(
        flashscore_mid="t2WIx1L5",
        datetime_utc=now_utc_naive,
        home="안양 정관장",
        away="울산 현대모비스",
        league_name="KBL",
    )

    out = fetch_h2h(fixture)
    print(out)
