# r8kx2v/basketball/schedule.py
# Flashscore 농구 일정 스크래퍼 (Fixture 클래스 반환)
from __future__ import annotations

import os
import re
import time
import logging
import datetime
from typing import Iterable, List
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

from .models import Fixture


# =========================================================
# .env 로드 (이 파일(basketball) 폴더의 .env를 확실히 읽도록 고정)
# =========================================================
_THIS_DIR = os.path.dirname(os.path.abspath(__file__))
_ENV_PATH = os.path.join(_THIS_DIR, ".env")
load_dotenv(dotenv_path=_ENV_PATH, override=True)

# === 환경 변수 ===
FS_LEAGUE_URLS = [s.strip() for s in os.getenv("FS_LEAGUE_URLS", "").split(",") if s.strip()]
FS_FIXTURES_LIMIT = int(os.getenv("FS_FIXTURES_LIMIT", "20"))
FS_FAST = os.getenv("FS_FAST", "0").strip()  # 안 써도 됨 (호환용)

FLASH_SCORE_HEADLESS = (os.getenv("FLASH_SCORE_HEADLESS", "true").lower() != "false")
FLASH_SCORE_TIMEOUT = int(os.getenv("FLASH_SCORE_TIMEOUT", "15"))  # seconds
FLASH_SCORE_RPS = float(os.getenv("FLASH_SCORE_RPS", "1"))
HOURS_BEFORE_JSON = int(os.getenv("HOURS_BEFORE_JSON", "24"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)

KST = ZoneInfo("Asia/Seoul")
UTC = ZoneInfo("UTC")


def _safe_close(x) -> None:
    try:
        x.close()
    except Exception:
        pass


def _parse_time_kst(raw: str) -> datetime.datetime | None:
    """
    Flashscore 표기 예: '08.11. 09:00'
    => KST datetime으로 해석 후 UTC로 변환하여 반환
    """
    try:
        raw = (raw or "").strip()
        m = re.search(r"(\d{2})\.(\d{2})\.\s*(\d{2}):(\d{2})", raw)
        if not m:
            return None

        day, month, hour, minute = map(int, m.groups())
        now = datetime.datetime.now(KST)
        year = now.year

        # 연말(12월)인데 1월 일정이 뜨는 케이스 보정
        if month == 1 and now.month == 12:
            year += 1

        dt_kst = datetime.datetime(year, month, day, hour, minute, tzinfo=KST)
        return dt_kst.astimezone(UTC)

    except Exception:
        return None


def _within_window(dt_utc: datetime.datetime) -> bool:
    """
    현재~HOURS_BEFORE_JSON 시간 사이 경기만 통과.
    """
    delta_h = (dt_utc - datetime.datetime.now(UTC)).total_seconds() / 3600
    return 0 <= delta_h <= HOURS_BEFORE_JSON


def _scrape_league(page, url: str) -> Iterable[Fixture]:
    """
    리그 페이지 1개에서 경기 목록을 긁어서 Fixture로 yield
    """
    logging.info(f"[scrape] {url}")
    page.goto(url, timeout=FLASH_SCORE_TIMEOUT * 1000)
    page.wait_for_selector("div.event__match", timeout=FLASH_SCORE_TIMEOUT * 1000)

    league_el = page.query_selector(".heading__name")
    league_name = league_el.inner_text().strip() if league_el else "unknown"

    matches = page.query_selector_all("div.event__match")
    for i, m in enumerate(matches):
        if i >= FS_FIXTURES_LIMIT:
            break

        try:
            mid_raw = m.get_attribute("id") or ""
            mid = mid_raw.split("_")[-1] if "_" in mid_raw else mid_raw

            home_el = m.query_selector(".event__participant--home")
            away_el = m.query_selector(".event__participant--away")
            time_el = m.query_selector(".event__time")

            home = home_el.inner_text().strip() if home_el else ""
            away = away_el.inner_text().strip() if away_el else ""
            raw_time = time_el.inner_text().strip() if time_el else ""

            dt_utc = _parse_time_kst(raw_time)
            if dt_utc is None:
                continue
            if not _within_window(dt_utc):
                continue

            yield Fixture(
                flashscore_mid=mid,
                datetime_utc=dt_utc,
                home=home,
                away=away,
                league_name=league_name,
                sport="basketball",
            )

        except Exception as e:
            logging.warning(f"[scrape] item parse fail: {e}")
            continue


def get_upcoming_matches_fs() -> List[Fixture]:
    """
    여러 리그 URL을 돌면서 가까운 경기들을 모아 반환.
    (VSCode에서 F5로 실행해도 동작하도록 단독 실행 안정성 확보)
    """
    if not FS_LEAGUE_URLS:
        logging.warning(f"[schedule] FS_LEAGUE_URLS empty (env: {_ENV_PATH})")
        return []

    results: List[Fixture] = []
    browser = None
    context = None
    page = None
    playwright = None

    try:
        playwright = sync_playwright().start()
        browser = playwright.chromium.launch(headless=FLASH_SCORE_HEADLESS)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            timezone_id="Asia/Seoul",
            java_script_enabled=True,
        )
        page = context.new_page()

        for url in FS_LEAGUE_URLS:
            try:
                results.extend(list(_scrape_league(page, url)))
                time.sleep(1 / max(FLASH_SCORE_RPS, 0.01))
            except Exception as e:
                logging.warning(f"[schedule] league fail: {url} | {e}")

    finally:
        _safe_close(page)
        _safe_close(context)
        _safe_close(browser)
        try:
            if playwright:
                playwright.stop()
        except Exception:
            pass

    return results


# =========================================================
# 단독 실행 (VSCode Run / F5 / python -m 실행 대비)
# =========================================================
if __name__ == "__main__":
    print(f"[env] loaded: {_ENV_PATH}")
    print(f"[env] FS_LEAGUE_URLS={FS_LEAGUE_URLS}")
    print(f"[env] HOURS_BEFORE_JSON={HOURS_BEFORE_JSON}, LIMIT={FS_FIXTURES_LIMIT}")
    items = get_upcoming_matches_fs()
    print(f"[result] {len(items)} matches")
    for fx in items:
        print(fx)
