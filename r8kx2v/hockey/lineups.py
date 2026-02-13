# r8kx2v/hockey/lineups.py — Flashscore 하키 라인업 파서 (KO, 안정판 최종)
from __future__ import annotations
import os, logging, json, time, re, contextlib
from typing import Any, Dict, List, Tuple
from playwright.sync_api import sync_playwright, TimeoutError as PWTimeout
from dotenv import load_dotenv

load_dotenv(override=True)

HEADLESS = (os.getenv("FLASH_SCORE_HEADLESS", "true").lower() != "false")
TIMEOUT  = int(os.getenv("FLASH_SCORE_TIMEOUT", "30"))  # seconds
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] [lineups] %(message)s",
)

POS_MAP = {0: ["G"], 1: ["RD", "LD"], 2: ["RW", "C", "LW"]}

def _dump(page, mid: str, tag: str):
    try:
        with open(f"lineups_{tag}_{mid}.html", "w", encoding="utf-8") as f:
            f.write(page.content())
        page.screenshot(path=f"lineups_{tag}_{mid}.png", full_page=True)
        logging.warning(f"dump: lineups_{tag}_{mid}.html, lineups_{tag}_{mid}.png")
    except Exception as e:
        logging.error(f"dump failed: {e}")

def _inject_cookie_bypass(page):
    page.add_init_script("""
    (() => {
      try {
        localStorage.setItem('onetrust_placeholder','1');
        localStorage.setItem('onetrust_active_groups','C0001:1,C0002:1,C0003:1,C0004:1');
      } catch(e) {}
      const year = 365*24*3600;
      document.cookie = 'OptanonConsent=isIABGlobal=false; path=/; domain=.flashscore.co.kr; max-age=' + year;
      document.cookie = 'OptanonAlertBoxClosed=1; path=/; domain=.flashscore.co.kr; max-age=' + year;
    })();
    """)

def _accept_cookie_banner(page):
    with contextlib.suppress(Exception):
        btn = page.locator("#onetrust-accept-btn-handler, button#onetrust-accept-btn-handler, button:has-text('동의')")
        if btn.count() > 0:
            btn.first.click(timeout=3000)
            time.sleep(0.5)

def _wait_players_render(page) -> bool:
    with contextlib.suppress(PWTimeout):
        page.wait_for_load_state("networkidle", timeout=TIMEOUT * 1000)
    for _ in range(TIMEOUT):
        if page.locator("div.lf__player").count() > 0:
            return True
        time.sleep(1)
    return False

def _pick_block(page, side: str):
    # lf__formation (언더스코어 2개)
    if side == "away":
        sel = ".lf__formation.lf__formationAway"
    else:
        sel = ".lf__formation:not(.lf__formationAway), .lf__formation"
    loc = page.locator(sel)
    return loc.first if loc.count() > 0 else None

def _text(loc) -> str:
    with contextlib.suppress(Exception):
        return loc.inner_text().strip()
    return ""

def _parse_rating(s: str) -> str:
    m = re.search(r"[0-9]+(?:\.[0-9]+)?", s or "")
    return m.group(0) if m else (s or "N/A")

def _extract_player(player_loc) -> Tuple[str, str]:
    name_loc = player_loc.locator(
        "[data-testid='wcl-lineupsParticipantName'] a, "
        "a[class*='lineupsParticipantName'], "
        "a[class^='wcl-lineupsParticipantName_']"
    )
    rating_loc = player_loc.locator("[data-testid='wcl-badgeRating'] span, .wcl-badgeRating")
    name = _text(name_loc.first) if name_loc.count() > 0 else "unknown"
    rating_raw = _text(rating_loc.first) if rating_loc.count() > 0 else ""
    rating = _parse_rating(rating_raw) if rating_raw else "N/A"
    return name, rating

def _to_gpt(result: Dict[str, Any]) -> str:
    lines = []
    order = ["G","RD","LD","RW","C","LW"]
    label = {
        "G":"골리","RD":"오른쪽 수비수","LD":"왼쪽 수비수",
        "RW":"오른쪽 공격수","C":"가운데 공격수","LW":"왼쪽 공격수"
    }
    for pos in order:
        if pos in result:
            lines.append(f"{label[pos]}: {result[pos]['name']} (평점 {result[pos]['rating']})")
    return "라인업 정보:\n" + "\n".join(lines)

def get_lineups(last_match_id: str, side: str) -> Dict[str, Any]:
    base = f"https://www.flashscore.co.kr/match/hockey/{last_match_id}/"
    logging.info(f"Base page: {base}")

    with sync_playwright() as p:
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
            page.goto(base, timeout=TIMEOUT * 1000)
        except PWTimeout:
            logging.error("goto timeout at base")
            _dump(page, last_match_id, "goto_timeout_base")
            ctx.close(); browser.close()
            return {}

        _accept_cookie_banner(page)
        with contextlib.suppress(PWTimeout):
            page.wait_for_load_state("networkidle", timeout=TIMEOUT * 1000)
        time.sleep(1.0)

        tab_a = page.locator('a[data-analytics-alias="lineups"]')
        if tab_a.count() == 0:
            logging.error("라인업 탭 링크 없음")
            _dump(page, last_match_id, "no_tab")
            ctx.close(); browser.close()
            return {}

        with contextlib.suppress(Exception):
            tab_a.first.scroll_into_view_if_needed(timeout=3000)
        try:
            tab_a.first.click(timeout=5000, force=True)
        except Exception:
            href = tab_a.first.get_attribute("href") or ""
            if not href.startswith("/"):
                logging.error("href 추출 실패")
                _dump(page, last_match_id, "no_href")
                ctx.close(); browser.close()
                return {}
            page.goto("https://www.flashscore.co.kr" + href, timeout=TIMEOUT * 1000)

        with contextlib.suppress(PWTimeout):
            page.wait_for_load_state("networkidle", timeout=TIMEOUT * 1000)
        time.sleep(1.0)
        with contextlib.suppress(Exception):
            page.locator("section#lineups").scroll_into_view_if_needed(timeout=3000)

        if not _wait_players_render(page):
            logging.error("lf__player 렌더 대기 실패")
            _dump(page, last_match_id, "not_rendered")
            ctx.close(); browser.close()
            return {}

        block = _pick_block(page, side.lower())
        if not block:
            logging.error("formation block not found")
            _dump(page, last_match_id, "no_formation")
            ctx.close(); browser.close()
            return {}

        lines: List[List[Tuple[str, str]]] = []
        line_nodes = block.locator(".lf__line")
        for i in range(min(3, line_nodes.count())):  # 0: G, 1: D(2), 2: F(3)
            line = line_nodes.nth(i)
            players = line.locator(".lf__player")
            tuples: List[Tuple[str, str]] = []
            for j in range(players.count()):
                tuples.append(_extract_player(players.nth(j)))
            if tuples:
                lines.append(tuples)

        result: Dict[str, Any] = {}
        # 골리
        if len(lines) >= 1 and len(lines[0]) == 1:
            n, r = lines[0][0]; result["G"] = {"name": n, "rating": r}
        # 수비
        if len(lines) >= 2 and len(lines[1]) == 2:
            n, r = lines[1][0]; result["RD"] = {"name": n, "rating": r}
            n, r = lines[1][1]; result["LD"] = {"name": n, "rating": r}
        # 공격
        if len(lines) >= 3 and len(lines[2]) == 3:
            n, r = lines[2][0]; result["RW"] = {"name": n, "rating": r}
            n, r = lines[2][1]; result["C"]  = {"name": n, "rating": r}
            n, r = lines[2][2]; result["LW"] = {"name": n, "rating": r}

        ctx.close(); browser.close()

    result["formatted_prompt"] = _to_gpt(result)
    return result

if __name__ == "__main__":
    mid = input("Enter last_match_id: ").strip()
    side = input("Enter side (home/away): ").strip().lower()
    data = get_lineups(mid, side)
    print(json.dumps(data, ensure_ascii=False, indent=2))
