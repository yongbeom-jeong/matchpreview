# -*- coding: utf-8 -*-
# basketball/main.py — 농구 메인
#
# 실행:
#   python -m r8kx2v.basketball.main
#
# 특징:
# - 스케줄에서 넘어온 경기 전부 처리
# - 최대 작성 제한 없음
# - 중복 / 시간 윈도우 / 빈 글만 필터링

from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Iterable
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

load_dotenv(override=True)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
DRY_RUN = os.getenv("BASKETBALL_DRY_RUN", "false").lower() == "true"
SLEEP_SECONDS = float(os.getenv("BASKETBALL_SLEEP_SECONDS", "0.5"))
HOURS_AHEAD = int(os.getenv("BASKETBALL_HOURS_AHEAD", "24"))

SPORT = "basketball"

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] [basketball.main] %(message)s",
)

KST = ZoneInfo("Asia/Seoul")
UTC = ZoneInfo("UTC")

try:
    from . import schedule, writer, publisher
except ImportError:
    import schedule, writer, publisher  # type: ignore


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
    sport_dir = Path(__file__).resolve().parent
    user_root = sport_dir.parent
    env_db = os.getenv("DB_PATH", "").strip()
    if env_db:
        prj_root = user_root.parent
        p = Path(env_db)
        return (p if p.is_absolute() else (prj_root / p)).resolve()
    return (user_root / "posts.db").resolve()


def _expected_db_keys(match: Dict[str, Any]) -> Dict[str, str]:
    league_raw = match.get("league_name") or "unknown"
    home = match.get("home") or "Home"
    away = match.get("away") or "Away"
    kickoff = match.get("kickoff") or match.get("datetime_utc")

    date_key = _to_kst_like_app(kickoff).strftime("%Y-%m-%d")
    league_slug = _slug(league_raw)
    title_slug = _slug(f"{home} vs {away}")

    return {
        "date_key": date_key,
        "sport": SPORT,
        "league_slug": league_slug,
        "title_slug": title_slug,
    }


def _already_published(match: Dict[str, Any]) -> bool:
    try:
        keys = _expected_db_keys(match)
        db_path = _resolve_posts_db_path()
        if not db_path.exists():
            return False

        conn = sqlite3.connect(str(db_path))
        cur = conn.cursor()
        cur.execute(
            "SELECT 1 FROM posts WHERE date_key=? AND sport=? AND league_slug=? AND title_slug=? LIMIT 1",
            (keys["date_key"], keys["sport"], keys["league_slug"], keys["title_slug"]),
        )
        row = cur.fetchone()
        conn.close()

        if row:
            logging.info(f"[dup] skip (already published): {keys}")
            return True
        return False

    except Exception as e:
        logging.warning(f"[dup-check-fail] {e}")
        return False


def _within_window(match: Dict[str, Any]) -> bool:
    kickoff = match.get("kickoff") or match.get("datetime_utc")
    if not kickoff:
        return False

    dt_kst = _to_kst_like_app(kickoff)
    delta_h = (dt_kst - datetime.now(KST)).total_seconds() / 3600
    return 0 <= delta_h <= HOURS_AHEAD


def _iter_matches() -> Iterable[Dict[str, Any]]:
    for fx in schedule.get_upcoming_matches_fs():
        mid = str(getattr(fx, "flashscore_mid", "") or "").strip()
        yield {
            "flashscore_mid": fx.flashscore_mid,
            "datetime_utc": fx.datetime_utc,
            "home": fx.home,
            "away": fx.away,
            "league_name": fx.league_name,
            "sport": SPORT,
            # ✅ 결장자(Absences) 모듈이 사용할 match URL을 메타에 포함
            "url": f"https://www.flashscore.co.kr/match/{mid}/" if mid else "",
        }


def process_one(m: Dict[str, Any]) -> None:
    if not _within_window(m):
        return

    if _already_published(m):
        return

    home = m.get("home") or ""
    away = m.get("away") or ""
    title_preview = f"{home} vs {away}"

    try:
        logging.info(f"[write] {title_preview} | {m.get('league_name','')}")

        article = writer.write_article(m)
        if not article or not article.get("body"):
            logging.warning("[write] empty article, skip")
            return

        title = article.get("title") or title_preview
        body = str(article.get("body") or "")
        meta = article.get("meta") or {}

        meta.setdefault("sport", SPORT)
        meta.setdefault("league_name", m.get("league_name"))
        meta.setdefault("home", home)
        meta.setdefault("away", away)
        meta.setdefault("kickoff_utc", m.get("datetime_utc"))

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


def main() -> None:
    logging.info(
        "basketball main start (hours_ahead=%d, dry_run=%s)",
        HOURS_AHEAD,
        DRY_RUN,
    )

    for m in _iter_matches():
        process_one(m)

    logging.info("basketball main done")


if __name__ == "__main__":
    main()
