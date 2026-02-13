# -*- coding: utf-8 -*-
# hockey/main.py — 하키 메인(스케줄 → 중복검사 → 라이터 → 퍼블리셔)
# 실행: 루트에서  python -m hockey.main
#
# 동작:
#  1) schedule.get_upcoming_matches_fs()로 24시간 이내 경기만 순회
#  2) posts.db에서 (date_key, sport, league_slug, title_slug) 정확 일치 중복검사
#  3) writer.write_article(match_meta) 호출(내부에서 h2h/stats/lineups 사용)
#  4) publisher.publish 로 게시
#
# 환경변수:
#   LOG_LEVEL=INFO|DEBUG
#   HOCKEY_MAX_TO_WRITE=8
#   HOCKEY_DRY_RUN=false
#   HOCKEY_SLEEP_SECONDS=0.5
#   HOCKEY_HOURS_AHEAD=24         # 시간 윈도우(기본 24h)
#   DB_PATH=./posts.db            # (옵션) 사용자 루트 기준 경로 또는 절대경로
#   PUBLISH_URL, SITE_API_KEY, PUBLISH_BACKUP_DIR  # publisher와 동일

from __future__ import annotations
import os, re, json, time, logging, sqlite3, itertools, traceback
from typing import Any, Dict, Iterable, Tuple
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo
from dotenv import load_dotenv

# ── env/load ───────────────────────────────────────────────────────────
load_dotenv(override=True)

LOG_LEVEL         = os.getenv("LOG_LEVEL", "INFO").upper()
MAX_TO_WRITE      = int(os.getenv("HOCKEY_MAX_TO_WRITE", "8"))
DRY_RUN           = os.getenv("HOCKEY_DRY_RUN", "false").lower() == "true"
SLEEP_SECONDS     = float(os.getenv("HOCKEY_SLEEP_SECONDS", "0.5"))
HOURS_AHEAD       = int(os.getenv("HOCKEY_HOURS_AHEAD", "24"))
SPORT             = "hockey"

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] [hockey.main] %(message)s",
)

KST = ZoneInfo("Asia/Seoul")
UTC = ZoneInfo("UTC")

# ── modules ────────────────────────────────────────────────────────────
try:
    from . import schedule, writer, publisher
except ImportError:
    import schedule, writer, publisher  # type: ignore

# ── utils: slug, time, path ───────────────────────────────────────────
_slug_re1 = re.compile(r"[^\w\-가-힣·]", re.UNICODE)
_slug_re2 = re.compile(r"-{2,}")

def _slug(s: str) -> str:
    s = (s or "").strip().replace(" ", "-")
    s = _slug_re1.sub("-", s)
    s = _slug_re2.sub("-", s).strip("-").lower()
    return s or "untitled"

def _to_kst_like_app(dt_like: Any) -> datetime:
    if isinstance(dt_like, datetime):
        dt = dt_like if dt_like.tzinfo else dt_like.replace(tzinfo=UTC)
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

def _resolve_posts_db_path() -> Path:
    sport_dir = Path(__file__).resolve().parent      # .../<user>/hockey
    user_root = sport_dir.parent                     # .../<user>
    env_db = os.getenv("DB_PATH", "").strip()
    if env_db:
        prj_root = user_root.parent
        p = Path(env_db)
        return (p if p.is_absolute() else (prj_root / p)).resolve()
    return (user_root / "posts.db").resolve()

def _expected_db_keys(match: Dict[str, Any]) -> Dict[str, str]:
    league_raw = match.get("league_name") or match.get("league") or "unknown"
    home = match.get("home") or match.get("team_home") or "Home"
    away = match.get("away") or match.get("team_away") or "Away"
    kickoff = match.get("kickoff") or match.get("datetime_utc") or \
              match.get("utc") or match.get("kickoff_utc") or match.get("start")

    date_key   = _to_kst_like_app(kickoff).strftime("%Y-%m-%d")
    league_slug = _slug(league_raw)
    title_slug  = _slug(f"{home} vs {away}")
    return {
        "date_key": date_key,
        "sport": SPORT,
        "league_slug": league_slug,
        "title_slug": title_slug,
    }

# ── duplicate check ───────────────────────────────────────────────────
def _already_published(match: Dict[str, Any]) -> Tuple[bool, str]:
    """
    posts.db 에서 (date_key, sport, league_slug, title_slug) 완전 일치 시 True.
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

# ── window filter (기본 24h) ──────────────────────────────────────────
def _within_window(match: Dict[str, Any]) -> bool:
    kickoff_iso = match.get("kickoff") or match.get("datetime_utc") or \
                  match.get("utc") or match.get("kickoff_utc") or match.get("start")
    if not kickoff_iso:
        return False
    dt_kst = _to_kst_like_app(kickoff_iso)
    delta_h = (dt_kst - datetime.now(KST)).total_seconds() / 3600
    return 0 <= delta_h <= HOURS_AHEAD

# ── iteration ─────────────────────────────────────────────────────────
def _iter_matches() -> Iterable[Dict[str, Any]]:
    yield from schedule.get_upcoming_matches_fs()

# ── processing ────────────────────────────────────────────────────────
def process_one(m: Dict[str, Any]) -> None:
    if not _within_window(m):
        return

    is_dup, reason = _already_published(m)
    if is_dup:
        return

    # writer 가 내부에서 h2h/stats/lineups 호출 → 기사 생성
    home = m.get("home") or m.get("team_home") or ""
    away = m.get("away") or m.get("team_away") or ""
    title_preview = f"{home} vs {away}"

    try:
        logging.info(f"[write] {title_preview} | {m.get('league_name','')}")
        article = writer.write_article(m)
        if not article or not article.get("body"):
            logging.warning("[write] empty article, skip")
            return

        title = article.get("title") or title_preview
        body  = str(article.get("body") or "")
        meta  = article.get("meta") or {}

        # 최소 메타 보정
        meta.setdefault("sport", SPORT)
        meta.setdefault("league_name", m.get("league_name"))
        meta.setdefault("home", home)
        meta.setdefault("away", away)
        if "kickoff_utc" not in meta and ("datetime_utc" in m or "kickoff" in m):
            meta["kickoff_utc"] = m.get("datetime_utc") or m.get("kickoff")

        if DRY_RUN:
            logging.info(f"[DRY-RUN] {title} | len(body)={len(body)}")
            return

        res = publisher.publish(title=title, body=body, meta=meta)
        if res.get("ok"):
            logging.info(f"[publish] ok: {title}")
        else:
            logging.error(f"[publish] fail: {title} | {res}")

        if SLEEP_SECONDS > 0:
            time.sleep(SLEEP_SECONDS)

    except Exception as e:
        logging.error(f"[process] {title_preview} error: {e}\n{traceback.format_exc()}")

# ── main (single run) ─────────────────────────────────────────────────
def main() -> None:
    logging.info("hockey main start (hours_ahead=%d, max=%d, dry_run=%s)", HOURS_AHEAD, MAX_TO_WRITE, DRY_RUN)
    for m in _iter_matches():
        process_one(m)
    logging.info("hockey main done")

if __name__ == "__main__":
    main()
