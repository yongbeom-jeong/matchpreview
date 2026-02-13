from __future__ import annotations

import contextlib
import logging
import os
import re
import time
from typing import List, Optional, Tuple

from dotenv import load_dotenv
from playwright.sync_api import TimeoutError as PWTimeout
from playwright.sync_api import sync_playwright

from .models import TeamLineup

load_dotenv(override=True)

HEADLESS = (os.getenv("FLASH_SCORE_HEADLESS", "true").lower() != "false")
TIMEOUT = int(os.getenv("FLASH_SCORE_TIMEOUT", "30"))  # seconds
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] [basketball.main] %(message)s",
)

BASE_HOST = "https://www.flashscore.co.kr"

# 평점: 보통 6.8 / 7.1 / 10.0 같은 형태
_RATING_RE = re.compile(r"\b(10\.0|[0-9]\.\d)\b")


def _inject_cookie_bypass(page):
    page.add_init_script(
        """
    (() => {
      try {
        localStorage.setItem('onetrust_placeholder','1');
        localStorage.setItem('onetrust_active_groups','C0001:1,C0002:1,C0003:1,C0004:1');
      } catch(e) {}
      const year = 365*24*3600;
      document.cookie = 'OptanonConsent=isIABGlobal=false; path=/; domain=.flashscore.co.kr; max-age=' + year;
      document.cookie = 'OptanonAlertBoxClosed=1; path=/; domain=.flashscore.co.kr; max-age=' + year;
    })();
    """
    )


def _accept_cookie_banner(page):
    with contextlib.suppress(Exception):
        btn = page.locator(
            "#onetrust-accept-btn-handler, button#onetrust-accept-btn-handler, button:has-text('동의')"
        )
        if btn.count() > 0:
            btn.first.click(timeout=3000)
            time.sleep(0.5)


def _text(loc) -> str:
    with contextlib.suppress(Exception):
        return (loc.inner_text() or "").strip()
    return ""


def _strip_star(s: str) -> str:
    s = (s or "").strip()
    if s.startswith("*"):
        s = s[1:].strip()
    return s


def _parse_rating(text: str) -> Optional[float]:
    s = (text or "").strip()
    if not s:
        return None
    m = _RATING_RE.search(s)
    if not m:
        return None
    try:
        return float(m.group(1))
    except Exception:
        return None


def _wait_lineups_render(page) -> bool:
    """
    농구 라인업 렌더 기준:
      - .lf__sidesBox 존재
      - .lf__participantNew 최소 5개 이상
    """
    with contextlib.suppress(PWTimeout):
        page.wait_for_load_state("networkidle", timeout=TIMEOUT * 1000)

    for _ in range(TIMEOUT):
        with contextlib.suppress(Exception):
            if page.locator(".lf__sidesBox").count() > 0 and page.locator(".lf__participantNew").count() >= 5:
                return True
        time.sleep(1)

    return False


def _pick_side_block(page, side: str):
    """
    농구 라인업: 좌/우 컬럼이 .lf__side 로 나뉨.
    보통:
      - 왼쪽(.lf__side nth=0) = home
      - 오른쪽(.lf__side nth=1) = away
    """
    side = (side or "").lower().strip()
    sides = page.locator(".lf__side")
    if sides.count() < 2:
        return None

    if side == "away":
        return sides.nth(1)
    return sides.nth(0)


def _extract_starters_with_ratings(side_block) -> Tuple[Tuple[str, ...], Tuple[Optional[float], ...]]:
    """
    side_block(.lf__side) 안에 선발 row가 .lf__participantNew 로 있음.
    이름은 /player/ 링크 텍스트에서 안정적으로 추출.
    평점은 (가능하면) data-testid='wcl-badgeRating'에서 추출, 없으면 row text에서 fallback.
    """
    names: List[str] = []
    ratings: List[Optional[float]] = []

    rows = side_block.locator(".lf__participantNew")
    if rows.count() == 0:
        return tuple(), tuple()

    # starters는 상위 5명만
    for i in range(min(rows.count(), 30)):
        r = rows.nth(i)

        name = ""

        # 1) /player/ 링크 내부 span(일반적으로 선수명)
        name_loc = r.locator("a[href^='/player/'] span")
        if name_loc.count() > 0:
            name = _text(name_loc.first)

        # 2) fallback: /player/ 링크 자체 텍스트
        if not name:
            a_loc = r.locator("a[href^='/player/']")
            if a_loc.count() > 0:
                name = _text(a_loc.first)

        name = _strip_star(name)
        if not name:
            continue
        if len(name) > 30:
            continue

        # 중복 방지
        if name in names:
            continue

        # rating: badgeRating 우선
        rating_val: Optional[float] = None
        rt = r.locator("[data-testid='wcl-badgeRating']")
        if rt.count() > 0:
            rating_val = _parse_rating(_text(rt.first))
        else:
            # fallback: row text에서 숫자 패턴
            rating_val = _parse_rating(_text(r))

        names.append(name)
        ratings.append(rating_val)

        if len(names) >= 5:
            break

    return tuple(names), tuple(ratings)


def fetch_team_lineup(match_mid: str, team: str, side: str) -> Optional[TeamLineup]:
    """
    입력:
      - match_mid: h2h에서 얻은 last_mid
      - team: 팀명(저장용)
      - side: "home" | "away"  (h2h is_home에서 결정)

    출력:
      - TeamLineup(team=team, starters=(...5명...), starter_ratings=(...))
      - 실패 시 starters=() 로 반환(파이프라인이 죽지 않게)
    """
    url = f"{BASE_HOST}/match/basketball/{match_mid}/"
    logging.info(f"open: {url} side={side} team={team}")

    with sync_playwright() as p:
        browser = None
        ctx = None
        page = None

        try:
            browser = p.chromium.launch(headless=HEADLESS)
            ctx = browser.new_context(
                viewport={"width": 1366, "height": 900},
                timezone_id="Asia/Seoul",
                java_script_enabled=True,
                locale="ko-KR",
            )
            page = ctx.new_page()
            _inject_cookie_bypass(page)

            try:
                page.goto(url, timeout=TIMEOUT * 1000)
            except PWTimeout:
                logging.error("goto timeout")
                return TeamLineup(team=team, starters=(), starter_ratings=())

            _accept_cookie_banner(page)

            with contextlib.suppress(PWTimeout):
                page.wait_for_load_state("networkidle", timeout=TIMEOUT * 1000)
            time.sleep(1.0)

            # 라인업 탭 클릭
            tab_a = page.locator('a[data-analytics-alias="lineups"]')
            if tab_a.count() == 0:
                logging.error("lineups tab not found")
                return TeamLineup(team=team, starters=(), starter_ratings=())

            with contextlib.suppress(Exception):
                tab_a.first.scroll_into_view_if_needed(timeout=3000)

            try:
                tab_a.first.click(timeout=5000, force=True)
            except Exception:
                # 클릭 막히면 href로 직접 이동
                href = tab_a.first.get_attribute("href") or ""
                if not href.startswith("/"):
                    logging.error("lineups href not found")
                    return TeamLineup(team=team, starters=(), starter_ratings=())
                try:
                    page.goto(BASE_HOST + href, timeout=TIMEOUT * 1000)
                except PWTimeout:
                    logging.error("goto lineups href timeout")
                    return TeamLineup(team=team, starters=(), starter_ratings=())

            with contextlib.suppress(PWTimeout):
                page.wait_for_load_state("networkidle", timeout=TIMEOUT * 1000)
            time.sleep(1.0)

            if not _wait_lineups_render(page):
                logging.error("lineups render wait failed")
                return TeamLineup(team=team, starters=(), starter_ratings=())

            side_block = _pick_side_block(page, side)
            if not side_block:
                logging.error("side block not found (.lf__side)")
                return TeamLineup(team=team, starters=(), starter_ratings=())

            starters, starter_ratings = _extract_starters_with_ratings(side_block)
            return TeamLineup(team=team, starters=starters, starter_ratings=starter_ratings)

        finally:
            # 어떤 예외가 터져도 무조건 정리 (브라우저 누수 방지)
            with contextlib.suppress(Exception):
                if page is not None:
                    page.close()
            with contextlib.suppress(Exception):
                if ctx is not None:
                    ctx.close()
            with contextlib.suppress(Exception):
                if browser is not None:
                    browser.close()


if __name__ == "__main__":
    mid = "fo8yQNy0"
    print(fetch_team_lineup(mid, "안양 정관장", "home"))
    print(fetch_team_lineup(mid, "부산 KCC", "away"))
