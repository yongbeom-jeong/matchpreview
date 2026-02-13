# r8kx2v/hockey/schedule.py — Flashscore 하키 일정 스크래퍼 (url 포함)
from __future__ import annotations

import os, re, logging, datetime, time
from typing import Dict, Any, Iterable
from zoneinfo import ZoneInfo

from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

# === 환경 변수 ===
load_dotenv(override=True)

FS_LEAGUE_URLS = [s.strip() for s in os.getenv("FS_LEAGUE_URLS", "").split(",") if s.strip()]
FS_FIXTURES_LIMIT = int(os.getenv("FS_FIXTURES_LIMIT", "10"))
FLASH_SCORE_HEADLESS = (os.getenv("FLASH_SCORE_HEADLESS", "true").lower() != "false")
FLASH_SCORE_TIMEOUT = int(os.getenv("FLASH_SCORE_TIMEOUT", "15"))
FLASH_SCORE_RPS = float(os.getenv("FLASH_SCORE_RPS", "1"))
HOURS_BEFORE_JSON = int(os.getenv("HOURS_BEFORE_JSON", "24"))

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s",
)

KST = ZoneInfo("Asia/Seoul")
UTC = ZoneInfo("UTC")

FS_BASE = "https://www.flashscore.co.kr"


# === 내부 유틸 ===
def _safe_close(x):
    try:
        x.close()
    except Exception:
        pass


def _abs_url(href: str) -> str:
    """
    Flashscore href를 절대 URL로 변환.
    - '/match/..' → 'https://www.flashscore.co.kr/match/..'
    - 이미 http(s)면 그대로
    - 그 외 상대경로면 base 붙임
    """
    href = (href or "").strip()
    if not href:
        return ""
    if href.startswith("http://") or href.startswith("https://"):
        return href
    if href.startswith("/"):
        return FS_BASE + href
    return FS_BASE + "/" + href.lstrip("/")


# === 시간 파싱 ===
def _parse_time_kst(raw: str) -> datetime.datetime | None:
    """'08.11. 09:00' → (KST 가정) → UTC 변환"""
    try:
        raw = (raw or "").strip()
        m = re.search(r"(\d{2})\.(\d{2})\.\s*(\d{2}):(\d{2})", raw)
        if not m:
            return None
        day, month, hour, minute = map(int, m.groups())
        now = datetime.datetime.now(KST)
        year = now.year
        # 12월 말 → 1월 초 넘어가는 케이스만 보정
        if month == 1 and now.month == 12:
            year += 1
        dt_kst = datetime.datetime(year, month, day, hour, minute, tzinfo=KST)
        return dt_kst.astimezone(UTC)
    except Exception:
        return None


# === 시간 윈도우 ===
def _within_window(dt_utc: datetime.datetime) -> bool:
    if not dt_utc:
        return False
    delta_h = (dt_utc - datetime.datetime.now(UTC)).total_seconds() / 3600
    return 0 <= delta_h <= HOURS_BEFORE_JSON


# === 리그 스크래핑 ===
def _scrape_league(page, url: str) -> Iterable[Dict[str, Any]]:
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
            # flashscore_mid
            mid_raw = m.get_attribute("id") or ""
            mid = mid_raw.split("_")[-1] if "_" in mid_raw else mid_raw

            # 팀/시간
            home_el = m.query_selector(".event__participant--home")
            away_el = m.query_selector(".event__participant--away")
            time_el = m.query_selector(".event__time")

            home = home_el.inner_text().strip() if home_el else ""
            away = away_el.inner_text().strip() if away_el else ""
            raw_time = time_el.inner_text().strip() if time_el else ""
            dt_utc = _parse_time_kst(raw_time)

            if not (isinstance(dt_utc, datetime.datetime) and _within_window(dt_utc)):
                continue

            # ✅ NEW: match URL (a.eventRowLink href)
            a = m.query_selector("a.eventRowLink")
            href = (a.get_attribute("href") or "").strip() if a else ""
            match_url = _abs_url(href)

            yield {
                "flashscore_mid": mid,
                "url": match_url,              # ✅ writer/absences에서 바로 사용
                "flashscore_url": match_url,   # ✅ 풋볼 writer 호환용(둘 다 넣어둠)
                "datetime_utc": dt_utc,
                "home": home,
                "away": away,
                "league_name": league_name,
                "sport": "hockey",
            }

        except Exception as e:
            logging.warning(f"[scrape] item parse fail: {e}")
            continue


# === 전체 리그 종합 (안전 패치) ===
def get_upcoming_matches_fs() -> Iterable[Dict[str, Any]]:
    if not FS_LEAGUE_URLS:
        logging.warning("[schedule] FS_LEAGUE_URLS empty")
        return []

    results = []
    browser = None
    context = None
    page = None
    playwright = None

    # Playwright 전체를 try-finally로 감싸서 무조건 close되도록 한다.
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
                time.sleep(1 / FLASH_SCORE_RPS)
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


# === 실행 테스트 ===
if __name__ == "__main__":
    for m in get_upcoming_matches_fs():
        print(m)
