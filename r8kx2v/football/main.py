# r8kx2v/football/main.py — DB 기반 중복검출 (정확 매칭: 리그·경기 완전 일치)
from __future__ import annotations
import os, logging, traceback, re, requests, sqlite3
from typing import Dict, Any, Iterable, Optional
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# ── env ──────────────────────────────────────────────────────────────
load_dotenv(override=True)
HOURS_BEFORE_JSON = int(os.getenv("HOURS_BEFORE_JSON", "72"))
VIEW_BASE = os.getenv("VIEW_BASE", "http://localhost:8000").rstrip("/")
SPORT = os.getenv("SPORT", "football")
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
USER_ID = os.getenv("USER_ID", "r8kx2v")

# ── modules ──────────────────────────────────────────────────────────
from .schedule import get_upcoming_matches_fs
from .standings import get_standings
from .h2h import get_h2h_snapshot
from .lastmatch import get_lastmatch
from .writer import write_article_fs
from .publisher import publish

# ── logging/timezones ────────────────────────────────────────────────
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s"
)
KST = ZoneInfo("Asia/Seoul")
UTC = ZoneInfo("UTC")

# ── utils ────────────────────────────────────────────────────────────
def _safe(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "-", (s or "")).strip()

# app.py의 slug 규칙 복제 (완전 동일)
_slug_re1 = re.compile(r"[^\w\-가-힣·]", re.UNICODE)
_slug_re2 = re.compile(r"-{2,}")
def _slug(s: str) -> str:
    s = (s or "").strip().replace(" ", "-")
    s = _slug_re1.sub("-", s)
    s = _slug_re2.sub("-", s).strip("-").lower()
    return s or "untitled"

# app.py와 동일한 UTC→KST 변환
def _to_kst_like_app(dt_like: Any) -> datetime:
    if isinstance(dt_like, datetime):
        dt = dt_like
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt.astimezone(KST)
    s = str(dt_like or "").strip()
    if not s:
        return datetime.now(KST)
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    except Exception:
        return datetime.now(KST)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(KST)

# posts.db 정확 경로
def _resolve_posts_db_path() -> Path:
    sport_dir = Path(__file__).resolve().parent      # .../r8kx2v/football
    user_root = sport_dir.parent                     # .../r8kx2v
    env_db = os.getenv("DB_PATH", "").strip()
    if env_db:
        prj_root = user_root.parent
        p = Path(env_db)
        return (p if p.is_absolute() else (prj_root / p)).resolve()
    return (user_root / "posts.db").resolve()

# 예상 DB 키 계산 (app.py와 동일 규칙)
def _expected_db_keys(match: Dict[str, Any]) -> Dict[str, str]:
    league_raw = match.get("league_name") or match.get("league") or "unknown"
    home = match.get("home") or match.get("team_home") or "Home"
    away = match.get("away") or match.get("team_away") or "Away"
    sport = match.get("sport") or SPORT or "football"
    kickoff = match.get("kickoff") or match.get("datetime_utc") or \
              match.get("utc") or match.get("kickoff_utc") or match.get("start")

    date_key = _to_kst_like_app(kickoff).strftime("%Y-%m-%d")
    league_slug = _slug(league_raw)
    title_slug = _slug(f"{home} vs {away}")
    return {
        "date_key": date_key,
        "sport": sport,
        "league_slug": league_slug,
        "title_slug": title_slug,
    }

# ── DB 기반 중복 검출 (정확 매칭) ──────────────────────────────────
def _already_published(match: Dict[str, Any]) -> tuple[bool, str]:
    """
    posts.db에서 (date_key, sport, league_slug, title_slug) 완전 일치 시 True.
    """
    try:
        keys = _expected_db_keys(match)
        db_path = _resolve_posts_db_path()
        if not db_path.exists():
            return (False, "db_absent")

        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()

        cur.execute(
            "SELECT 1 FROM posts WHERE date_key=? AND sport=? AND league_slug=? AND title_slug=? LIMIT 1",
            (keys["date_key"], keys["sport"], keys["league_slug"], keys["title_slug"]),
        )
        row = cur.fetchone()
        conn.close()

        if row:
            logging.info(f"[dup] exact match in DB: {keys}")
            return (True, "exact")
        return (False, "none")

    except Exception as e:
        logging.warning(f"[dup-check-fail] {e}")
        return (False, "error")

# ── 시간 윈도우 필터 ────────────────────────────────────────────────
def _within_window(match: Dict[str, Any]) -> bool:
    kickoff_iso = match.get("kickoff") or match.get("datetime_utc") or \
                  match.get("utc") or match.get("kickoff_utc") or match.get("start")
    if not kickoff_iso:
        return False
    dt_kst = _to_kst_like_app(kickoff_iso)
    delta_h = (dt_kst - datetime.now(KST)).total_seconds() / 3600
    return 0 <= delta_h <= HOURS_BEFORE_JSON

# ── main flow ───────────────────────────────────────────────────────────
def _iter_matches() -> Iterable[Dict[str, Any]]:
    yield from get_upcoming_matches_fs()

def process_one(m: Dict[str, Any]) -> None:
    if not _within_window(m):
        return

    is_dup, reason = _already_published(m)
    if is_dup:
        return

    home = m.get("home") or m.get("team_home") or ""
    away = m.get("away") or m.get("team_away") or ""
    mid = m.get("flashscore_mid") or m.get("mid") or ""

    try:
        standings = get_standings(mid, home, away) if mid and home and away else {}
        logging.info(f"[write] {home} vs {away}")

        article = write_article_fs(m, standings=standings)
        if not article or not article.get("body"):
            logging.warning("[write] empty article, skip")
            return

        title = article.get("title") or f"{home} vs {away}"
        body = str(article.get("body") or "")
        meta = article.get("meta") or {}

        res = publish(title=title, body=body, meta=meta)
        if res.get("ok"):
            logging.info(f"[publish] ok: {title}")
        else:
            logging.error(f"[publish] fail ({res.get('status', '?')}): {title} | {res}")

    except Exception as e:
        logging.error(f"[process] {home} vs {away} error: {e}\n{traceback.format_exc()}")

def main():
    for m in _iter_matches():
        try:
            process_one(m)
        except Exception as e:
            logging.error(f"[process] {e}\n{traceback.format_exc()}")

if __name__ == "__main__":
    main()
