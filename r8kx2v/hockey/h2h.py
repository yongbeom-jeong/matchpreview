# r8kx2v/hockey/h2h.py — Flashscore 하키 H2H 파서 (최종 안정 버전)
from __future__ import annotations
from typing import Any, Dict, List, Tuple
import re, os, json, requests
from dotenv import load_dotenv

load_dotenv(override=True)

FS_BASE = os.getenv("FS_NINJA_BASE", "https://global.flashscore.ninja")
FS_VER  = os.getenv("FS_FEED_VER", "34")
FS_SEG  = os.getenv("FS_LOCALE_SEG", "x")
TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "12"))

FEED_URL = f"{FS_BASE}/{FS_VER}/{FS_SEG}/feed/df_hh_1" + "_{mid}"
HEADERS = {
    "User-Agent": os.getenv("FS_UA", "Mozilla/5.0"),
    "Accept": "*/*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Origin": "https://www.flashscore.co.kr",
    "Referer": "https://www.flashscore.co.kr/",
    "x-fsign": "SW9D1eZo",
}

FIELD_SEP = "¬"
PAIR = "÷"


def _fetch(mid: str) -> str:
    url = FEED_URL.format(mid=mid)
    r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
    r.raise_for_status()
    text = re.sub(r"[\x00-\x1f\x7f]+", "", r.text)
    return text


def _split_entries(block: str) -> List[str]:
    parts = block.split(FIELD_SEP + "~KC÷")
    out = []
    for i, p in enumerate(parts):
        if not p:
            continue
        if i == 0:
            idx = p.find("KC÷")
            if idx != -1:
                out.append(p[idx:])
        else:
            out.append("KC÷" + p)
    return out


def _kv_map(entry: str) -> Dict[str, str]:
    kv = {}
    for tok in entry.split(FIELD_SEP):
        if PAIR in tok:
            k, v = tok.split(PAIR, 1)
            kv[k] = v
    return kv


def _to_int(s: str) -> int:
    try:
        return int(s)
    except:
        return 0


def _find_team_sections(stream: str) -> List[Tuple[str, str]]:
    anchors = list(re.finditer(r"KB÷마지막 경기:\s*([^¬~]+)", stream))
    sections: List[Tuple[str, str]] = []
    for i, m in enumerate(anchors):
        team = m.group(1).strip()
        start = m.start()
        end = anchors[i + 1].start() if i + 1 < len(anchors) else len(stream)
        sections.append((team, stream[start:end]))
    return sections


def _summarize_section(section_text: str) -> Dict[str, Any]:
    """최근 5경기 요약 (글쓰기용 데이터만 계산)"""
    entries = _split_entries(section_text)
    rows = []
    for ent in entries:
        kv = _kv_map(ent)
        kn = (kv.get("KN", "") or "").lower()
        ks = (kv.get("KS", "") or "").lower()
        kc = _to_int(kv.get("KC", "0"))
        ku = _to_int(kv.get("KU", "0"))
        kt = _to_int(kv.get("KT", "0"))

        if kn in ("w", "ot", "so", "wo"):
            res = "win"
        elif kn in ("l", "lo", "sol"):
            res = "lose"
        else:
            continue

        if ks == "home":
            gf, ga = ku, kt
        elif ks == "away":
            gf, ga = kt, ku
        else:
            gf, ga = 0, 0

        rows.append({"kc": kc, "res": res, "gf": gf, "ga": ga})

    rows.sort(key=lambda r: r["kc"], reverse=True)
    last5 = rows[:5]
    if not last5:
        return {"wins": 0, "losses": 0, "gf": 0, "ga": 0, "avg_gf": 0.0, "avg_ga": 0.0}

    wins = sum(1 for r in last5 if r["res"] == "win")
    losses = len(last5) - wins
    gf_total = sum(r["gf"] for r in last5)
    ga_total = sum(r["ga"] for r in last5)
    avg_gf = round(gf_total / len(last5), 2)
    avg_ga = round(ga_total / len(last5), 2)

    return {
        "wins": wins,
        "losses": losses,
        "gf": gf_total,
        "ga": ga_total,
        "avg_gf": avg_gf,
        "avg_ga": avg_ga,
    }


def analyze_h2h(mid: str) -> Dict[str, Any]:
    """글쓰기용 요약 데이터 (스탯용 데이터 제외)"""
    stream = _fetch(mid)
    ts = _find_team_sections(stream)
    if not ts:
        return {"home": {}, "away": {}}
    if len(ts) == 1:
        team, block = ts[0]
        return {"home": {"team": team, **_summarize_section(block)}, "away": {}}

    (home_name, home_block), (away_name, away_block) = ts[0], ts[1]
    home_sum = _summarize_section(home_block)
    away_sum = _summarize_section(away_block)
    return {
        "home": {"team": home_name, **home_sum},
        "away": {"team": away_name, **away_sum},
    }


def get_team_lastmatch(mid: str) -> Dict[str, Any]:
    """스탯 모듈용 — 각 팀의 직전 경기 match_id, side, opponent, score, result 포함"""
    stream = _fetch(mid)
    ts = _find_team_sections(stream)
    if not ts or len(ts) < 2:
        return {"home": {}, "away": {}}

    (home_name, home_block), (away_name, away_block) = ts[0], ts[1]

    def _find_last_match(section_text: str) -> Dict[str, Any]:
        entries = _split_entries(section_text)
        if not entries:
            return {}
        kv = _kv_map(entries[0])
        match_id = kv.get("KP")
        side = kv.get("KS")
        result = kv.get("KN", "").lower()
        ku = _to_int(kv.get("KU", "0"))
        kt = _to_int(kv.get("KT", "0"))
        score_for = ku if side == "home" else kt
        score_against = kt if side == "home" else ku

        opponent = kv.get("KK")
        if not opponent:
            m = re.search(r"KK÷([^¬~]+)", section_text)
            if m:
                opponent = m.group(1).strip()
        if not opponent:
            opponent = "unknown"

        return {
            "last_match_id": match_id,
            "side": side,
            "result": "win" if result.startswith("w") else "lose",
            "score_for": score_for,
            "score_against": score_against,
            "opponent": opponent,
        }

    home_data = _find_last_match(home_block)
    away_data = _find_last_match(away_block)

    home_data["team"] = home_name
    away_data["team"] = away_name

    return {"home": home_data, "away": away_data}


if __name__ == "__main__":
    mid = input("match_id(mid)= ").strip()
    print(json.dumps(analyze_h2h(mid), ensure_ascii=False, indent=2))
    print(json.dumps(get_team_lastmatch(mid), ensure_ascii=False, indent=2))
