# standings.py — Flashscore standings parser (df_to 기반, schema-aligned)

import os, re, json, logging, requests
from typing import Dict, Any
from dotenv import load_dotenv

load_dotenv(override=True)

LOG_LEVEL = (os.getenv("LOG_LEVEL", "INFO") or "INFO").upper()
logging.basicConfig(level=getattr(logging, LOG_LEVEL, logging.INFO),
                    format="%(asctime)s [%(levelname)s] %(message)s")

FS_NINJA_BASE = os.getenv("FS_NINJA_BASE", "https://global.flashscore.ninja")
FS_FEED_VER   = os.getenv("FS_FEED_VER", "34")
FS_LOCALE_SEG = os.getenv("FS_LOCALE_SEG", "x")
FS_TIMEOUT    = float(os.getenv("FS_TIMEOUT", "10"))

def _build_url(mid: str) -> str:
    # df_to 엔드포인트 (원래 버전)
    return f"{FS_NINJA_BASE}/{FS_FEED_VER}/{FS_LOCALE_SEG}/feed/df_to_1_{mid}_1"

def _headers(mid: str) -> Dict[str, str]:
    return {
        "User-Agent": os.getenv("FS_UA", "Mozilla/5.0"),
        "Accept": "*/*",
        "Accept-Language": "ko,en;q=0.9",
        "Origin": "https://www.flashscore.co.kr",
        "Referer": f"https://www.flashscore.co.kr/match/{mid}/#standings",
        "x-fsign": "SW9D1eZo",
    }

def fetch_standings(mid: str) -> str:
    url = _build_url(mid)
    logging.debug(f"[standings] GET {url}")
    r = requests.get(url, headers=_headers(mid), timeout=FS_TIMEOUT)
    r.raise_for_status()
    return r.text

def _extract_int(pattern: str, block: str) -> int | None:
    m = re.search(pattern, block)
    return int(m.group(1)) if m else None

def _parse_team_block(block: str) -> Dict[str, Any]:
    rank   = _extract_int(r"~TR÷(\d+)", block)
    team_m = re.search(r"TN÷([^¬]+)", block)
    team   = team_m.group(1) if team_m else None
    played = _extract_int(r"TM÷(\d+)", block)
    wins   = _extract_int(r"TW÷(\d+)", block)
    draws  = _extract_int(r"TDR÷(\d+)", block)
    losses = _extract_int(r"TL÷(\d+)", block)

    tg = re.search(r"TG÷(\d+):(\d+)", block)
    gf, ga = (int(tg.group(1)), int(tg.group(2))) if tg else (None, None)

    pts = _extract_int(r"TP÷(\d+)", block)

    avg_gf = round(gf / played, 2) if gf is not None and played else None
    avg_ga = round(ga / played, 2) if ga is not None and played else None

    return {
        "team": team,
        "rank": rank,
        "points": pts,
        "played": played,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "gf": gf,
        "ga": ga,
        "avg_gf": avg_gf,
        "avg_ga": avg_ga,
    }

def parse_standings(text: str, home_name: str, away_name: str) -> Dict[str, Any]:
    blocks = text.split("~TR÷")[1:]
    results: Dict[str, Any] = {}
    for b in blocks:
        block = "~TR÷" + b
        team_data = _parse_team_block(block)
        if not team_data["team"]:
            continue
        if home_name and home_name in team_data["team"]:
            results["home"] = team_data
        if away_name and away_name in team_data["team"]:
            results["away"] = team_data
    return results

def get_standings(mid: str, home_name: str, away_name: str) -> Dict[str, Any]:
    raw = fetch_standings(mid)
    picked = parse_standings(raw, home_name, away_name)
    return {
        "_module": "standings",
        "_schema": "mpwin/standings@1",
        **picked
    }

# CLI
if __name__ == "__main__":
    mid = input("mid= ").strip()
    home = input("홈팀= ").strip()
    away = input("원정팀= ").strip()
    data = get_standings(mid, home, away)
    print(json.dumps(data, ensure_ascii=False, indent=2))
