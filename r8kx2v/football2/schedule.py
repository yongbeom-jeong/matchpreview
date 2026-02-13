# schedule.py — 리그 fixtures 크롤러 (안전 패치 적용)
import os, re, time, datetime as dt
from typing import List, Dict, Optional, Tuple, Iterator
from contextlib import contextmanager
from playwright.sync_api import sync_playwright, Browser, Page, TimeoutError as PWTimeout
from pathlib import Path
from dotenv import load_dotenv


# === ENV ===
load_dotenv(Path(__file__).resolve().parents[2] / ".env", override=True)
load_dotenv(Path(__file__).resolve().parent / ".env", override=True)
FS_LEAGUE_URLS = [s.strip() for s in os.getenv("FS_LEAGUE_URLS", "").split(",") if s.strip()]
FS_FIXTURES_LIMIT = int(os.getenv("FS_FIXTURES_LIMIT", "15"))
FS_FAST = os.getenv("FS_FAST", "1") not in ("0", "false", "False")

HEADLESS = (os.getenv("FLASH_SCORE_HEADLESS", "true").lower() != "false")
TIMEOUT_MS = int(float(os.getenv("FLASH_SCORE_TIMEOUT", "15")) * 1000)
RPS = max(1, int(os.getenv("FLASH_SCORE_RPS", "1")))
_BASE_DELAY = 1.0 / RPS
_last_ts = 0.0


# === 내부 유틸 ===
def _throttle():
    global _last_ts
    now = time.time()
    wait = _BASE_DELAY - (now - _last_ts)
    if wait > 0:
        time.sleep(wait)
    _last_ts = time.time()


def _safe_close(x):
    try:
        x.close()
    except:
        pass


# === ★ 핵심 패치: browser_page 개선 (브라우저·컨텍스트·페이지가 무조건 닫히도록) ===
@contextmanager
def browser_page() -> Iterator[Page]:
    browser = None
    ctx = None
    page = None
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=HEADLESS,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            ctx = browser.new_context(
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0 Safari/537.36"
                ),
                viewport={"width": 1366, "height": 900},
                java_script_enabled=True,
                accept_downloads=False,
                locale="ko-KR",
                timezone_id=os.getenv("TZ", "Asia/Seoul"),
            )
            page = ctx.new_page()
            page.set_default_timeout(TIMEOUT_MS)
            yield page
    finally:
        _safe_close(page)
        _safe_close(ctx)
        _safe_close(browser)


# === 쿠키 수락 ===
def accept_cookies(page: Page, timeout_ms: int = 3000) -> None:
    BTN_TEXTS = [
        "동의", "동의하고 계속", "모두 동의", "확인", "수락", "허용",
        "Accept", "Agree", "I agree", "Allow all", "Accept all",
        "Akzeptieren", "Tout accepter", "Aceptar", "Aceptar todo", "承諾", "同意",
    ]
    try:
        for sel in ["button", "[role=button]", "div[role=button]"]:
            for txt in BTN_TEXTS:
                loc = (
                    page.get_by_role("button", name=txt)
                    if sel == "button"
                    else page.locator(f"{sel}:has-text('{txt}')")
                )
                if loc.first.count() > 0:
                    loc.first.click(timeout=timeout_ms)
                    page.wait_for_timeout(300)
                    return
        for txt in BTN_TEXTS:
            loc = page.get_by_text(txt, exact=False)
            if loc.first.count() > 0:
                loc.first.click(timeout=timeout_ms)
                page.wait_for_timeout(300)
                return
    except:
        pass


# === 이동 ===
def goto_wait(page: Page, url: str, wait_for: Optional[str] = None):
    _throttle()
    page.goto(url, wait_until="domcontentloaded")
    page.wait_for_timeout(600)
    accept_cookies(page)
    if wait_for:
        try:
            page.wait_for_selector(wait_for, state="visible", timeout=TIMEOUT_MS)
        except PWTimeout:
            pass
    return page


# === 셀렉터 ===
ROW_SEL = "div.event__match"
TIME_NODE = "div.event__time"
LINK_SEL = "a.eventRowLink"

PARTICIPANT_HOME = (
    ".event__homeParticipant .wcl-name_jjfMf, "
    ".event__participant--home, "
    ".event__participant--home .wcl-name_jjfMf"
)
PARTICIPANT_AWAY = (
    ".event__awayParticipant .wcl-name_jjfMf, "
    ".event__participant--away, "
    ".event__participant--away .wcl-name_jjfMf"
)

SEASON_HINT_SEL = '[class*="tournamentHeader__"], h1, title'
TIME_TXT_RE = re.compile(r"(?P<day>\d{1,2})\.(?P<mon>\d{1,2})\.\s+(?P<h>\d{1,2}):(?P<m>\d{2})")
SEASON_RE = re.compile(r"(?P<y1>20\d{2})\s*/\s*(?P<y2>20\d{2})")


# === 시즌 ===
def _extract_season_text(page: Page) -> str:
    try:
        locs = page.locator(SEASON_HINT_SEL)
        n = min(locs.count(), 12)
        for i in range(n):
            txt = (locs.nth(i).inner_text() or "").strip()
            m = SEASON_RE.search(txt)
            if m:
                return f"{m.group('y1')}/{m.group('y2')}"
    except:
        pass
    try:
        t = page.title() or ""
        m = SEASON_RE.search(t)
        if m:
            return f"{m.group('y1')}/{m.group('y2')}"
    except:
        pass
    now = dt.datetime.now(dt.timezone.utc)
    y1 = now.year if now.month >= 7 else now.year - 1
    return f"{y1}/{y1+1}"


def _year_for_month(month: int, season: str) -> int:
    m = SEASON_RE.match(season)
    if not m:
        now = dt.datetime.now(dt.timezone.utc)
        y1 = now.year if now.month >= 7 else now.year - 1
        y2 = y1 + 1
    else:
        y1, y2 = int(m.group("y1")), int(m.group("y2"))
    return y1 if month >= 7 else y2


def _parse_kickoff_utc(row, season: str) -> Optional[dt.datetime]:
    try:
        el = row.query_selector(TIME_NODE)
        txt = (el.inner_text() or "").strip()
    except:
        txt = ""
    m = TIME_TXT_RE.search(txt or "")
    if not m:
        return None
    day = int(m.group("day"))
    mon = int(m.group("mon"))
    hh = int(m.group("h"))
    mm = int(m.group("m"))
    year = _year_for_month(mon, season)
    kst = dt.datetime(year, mon, day, hh, mm, tzinfo=dt.timezone(dt.timedelta(hours=9)))
    return kst.astimezone(dt.timezone.utc)


def _read_teams(row) -> Optional[Tuple[str, str]]:
    try:
        home = row.query_selector(PARTICIPANT_HOME)
        away = row.query_selector(PARTICIPANT_AWAY)
        h = (home.inner_text() or "").strip() if home else ""
        a = (away.inner_text() or "").strip() if away else ""
        if h and a:
            return h, a
    except:
        pass
    return None


def _extract_mid(row) -> Optional[str]:
    try:
        rid = row.get_attribute("id") or ""
        if rid.startswith("g_1_") and len(rid) > 4:
            return rid[4:]
    except:
        pass
    try:
        a = row.query_selector(LINK_SEL)
        href = (a.get_attribute("href") or "").strip() if a else ""
        m = re.search(r"[?&]mid=([A-Za-z0-9]+)", href)
        if m:
            return m.group(1)
    except:
        pass
    return None


def _normalize_fixtures_url(url: str) -> str:
    if not url:
        return url
    u = url.strip().rstrip("/")
    if not u.endswith("/fixtures"):
        u += "/fixtures"
    return u + "/"


def _scroll_to_load(page: Page):
    steps = 5 if FS_FAST else 10
    for _ in range(steps):
        page.mouse.wheel(0, 2200)
        page.wait_for_timeout(250 if FS_FAST else 350)


def _league_name(page: Page) -> str:
    try:
        t = (page.title() or "").strip()
        if t:
            return t
    except:
        pass
    try:
        hdr = page.locator('[class*="tournamentHeader__"], h1')
        if hdr.count() > 0:
            return (hdr.first.inner_text() or "").strip().replace("\n", " ")
    except:
        pass
    return ""


# === 리그 단위 수집 ===
def _scrape_league(league_url: str, limit: int) -> List[Dict]:
    url = _normalize_fixtures_url(league_url)
    print(f"[sched] open league fixtures -> {url}")

    with browser_page() as page:
        goto_wait(page, url)
        accept_cookies(page)
        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except PWTimeout:
            pass
        try:
            page.wait_for_selector(TIME_NODE, timeout=12000)
        except PWTimeout:
            pass

        _scroll_to_load(page)

        season = _extract_season_text(page)
        lname = _league_name(page)
        rows = page.query_selector_all(ROW_SEL)
        print(f"[sched] rows={len(rows)} @ {lname or url}")

        out: List[Dict] = []
        for row in rows:
            try:
                mid = _extract_mid(row)
                if not mid:
                    continue
                teams = _read_teams(row)
                if not teams:
                    continue
                home, away = teams
                dt_utc = _parse_kickoff_utc(row, season)
                if not dt_utc:
                    continue

                a = row.query_selector(LINK_SEL)
                href = (a.get_attribute("href") or "").strip() if a else ""

                out.append({
                    "flashscore_mid": mid,
                    "url": href,
                    "home": home,
                    "away": away,
                    "datetime_utc": dt_utc,
                    "league_name": lname,
                })
            except:
                continue
            if len(out) >= limit:
                break
        return out


def _dedup_by_mid(items: List[Dict]) -> List[Dict]:
    seen, out = set(), []
    for m in items:
        mid = m.get("flashscore_mid")
        if not mid or mid in seen:
            continue
        seen.add(mid)
        out.append(m)
    return out


# === 외부 호출 ===
def get_upcoming_matches_fs() -> List[Dict]:
    if not FS_LEAGUE_URLS:
        print("[sched] FS_LEAGUE_URLS is empty; nothing to scrape.")
        return []

    limit = max(1, FS_FIXTURES_LIMIT)
    matches: List[Dict] = []

    for league_url in FS_LEAGUE_URLS:
        try:
            part = _scrape_league(league_url, limit)
            matches.extend(part)
            time.sleep(0.5)
        except Exception as e:
            print(f"[sched] skip league due to error: {league_url} -> {e}")

    matches = _dedup_by_mid(matches)
    matches.sort(key=lambda m: m["datetime_utc"])
    return matches


if __name__ == "__main__":
    from pprint import pprint
    res = get_upcoming_matches_fs()
    pprint(res)
