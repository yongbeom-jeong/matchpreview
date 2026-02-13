# r8kx2v/basketball/absences.py
# 실행:
#   python -m r8kx2v.basketball.absences "<flashscore match url>" --debug
#
# 예:
#   python -m r8kx2v.basketball.absences "https://www.flashscore.co.kr/match/basketball/.../?mid=XXXX" --debug

from __future__ import annotations

import argparse
import sys
from typing import Dict, List, Literal, Optional

from bs4 import BeautifulSoup

try:
    from playwright.sync_api import sync_playwright
except Exception:
    sync_playwright = None  # type: ignore


Side = Literal["home", "away"]
Status = Literal["out", "doubt"]


def get_absences(match_url: str, *, debug: bool = False) -> Dict[str, Dict[str, List[dict]]]:
    """Flashscore 경기 페이지 HTML을 가져와 결장자(결장 유력/의심)를 파싱한다."""
    html = fetch_match_html(match_url, debug=debug)
    return parse_absences(html)


def fetch_match_html(match_url: str, *, debug: bool = False) -> str:
    """Flashscore 경기 페이지에서 결장 섹션 DOM이 렌더링될 때까지 대기 후 HTML 반환."""
    if sync_playwright is None:
        raise RuntimeError("Playwright 필요: pip install playwright && playwright install")

    url = match_url.split("#", 1)[0].strip()

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
        )
        ctx = browser.new_context(
            locale="ko-KR",
            user_agent=_ua(),
            extra_http_headers={
                "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
                "Referer": "https://www.flashscore.co.kr/",
            },
            viewport={"width": 1280, "height": 900},
        )
        ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined});")
        page = ctx.new_page()

        page.goto(url, wait_until="domcontentloaded", timeout=60000)

        # 있으면 치우기(없으면 무시)
        _try_click(page, "[data-testid='wcl-tooltip-actionButton']", debug)  # 온보딩 '확인'
        _try_click(page, "button:has-text('동의')", debug)
        _try_click(page, "button:has-text('수락')", debug)
        _try_click(page, "button:has-text('확인')", debug)
        _try_click(page, "button:has-text('Accept')", debug)

        # ✅ 핵심: 결장(라인업/결장 섹션) 로드 대기
        if not _wait_absences_block(page, timeout_ms=25000):
            # lazy-load 가능성 → 스크롤 후 재시도
            page.mouse.wheel(0, 2400)
            page.wait_for_timeout(500)
            _wait_absences_block(page, timeout_ms=15000)

        html = page.content()

        if debug:
            print("DEBUG url:", url, file=sys.stderr)
            print("DEBUG html_len:", len(html), file=sys.stderr)
            print("DEBUG has loadable.complete:", "loadable complete" in html, file=sys.stderr)
            print("DEBUG has lf__sidesBox:", "lf__sidesBox" in html, file=sys.stderr)
            print("DEBUG has '결장 유력':", "결장 유력" in html, file=sys.stderr)
            print("DEBUG has '결장 의심':", "결장 의심" in html, file=sys.stderr)

        ctx.close()
        browser.close()

    return html


def parse_absences(html: str) -> Dict[str, Dict[str, List[dict]]]:
    """Flashscore DOM 기준 파싱.

    - div.loadable.complete > div.section
    - title: [data-testid="wcl-scores-overline-02"] => "결장 유력/결장 의심"
    - 참가자: [data-testid="wcl-lineupsParticipantGeneral-left/right"]
    """
    soup = BeautifulSoup(html, "lxml")

    out: Dict[str, Dict[str, List[dict]]] = {
        "home": {"out": [], "doubt": []},
        "away": {"out": [], "doubt": []},
    }

    def to_status(title: str) -> Optional[Status]:
        t = title.strip()
        # Flashscore가 한국어/영어 혼용될 수 있어서 둘 다 허용
        if t in ("결장 유력", "확인", "Confirmed"):
            return "out"
        if t in ("결장 의심", "의심", "Doubtful"):
            return "doubt"
        return None

    for section in soup.select("div.loadable.complete > div.section"):
        # 결장 섹션만
        if not section.select_one(".lf__sidesBox"):
            continue

        title_el = section.select_one('[data-testid="wcl-scores-overline-02"]')
        if not title_el:
            continue

        status = to_status(title_el.get_text(strip=True))
        if status is None:
            continue

        for p in section.select('[data-testid="wcl-lineupsParticipantGeneral-left"]'):
            pl = _parse_player(p)
            if pl["name"]:
                out["home"][status].append(pl)

        for p in section.select('[data-testid="wcl-lineupsParticipantGeneral-right"]'):
            pl = _parse_player(p)
            if pl["name"]:
                out["away"][status].append(pl)

    return out


def _parse_player(p) -> dict:
    name_el = p.select_one('[data-testid="wcl-scores-simple-text-01"]')
    if not name_el:
        return {"name": "", "reason": ""}

    reason_el = p.select_one('[data-testid="wcl-scores-caption-05"]')
    return {
        "name": name_el.get_text(strip=True),
        "reason": reason_el.get_text(strip=True) if reason_el else "",
    }


def _wait_absences_block(page, timeout_ms: int) -> bool:
    """결장 섹션이 떴는지 확인(최소 조건만)."""
    try:
        page.wait_for_selector("div.loadable.complete .lf__sidesBox", timeout=timeout_ms)
        return True
    except Exception:
        return False


def _ua() -> str:
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/121.0.0.0 Safari/537.36"
    )


def _try_click(page, selector: str, debug: bool) -> None:
    try:
        el = page.query_selector(selector)
        if el:
            el.click(timeout=1200)
            if debug:
                print(f"DEBUG clicked: {selector}", file=sys.stderr)
    except Exception:
        pass


def _print_block(data: Dict[str, Dict[str, List[dict]]]) -> None:
    def fmt(items: List[dict]) -> str:
        if not items:
            return "없음"
        return "\n".join([f"- {p['name']} ({p['reason']})".rstrip() for p in items])

    print("🚨 홈 결장 유력 🚨")
    print(fmt(data["home"]["out"]))
    print()
    print("🏥 홈 결장 의심 🏥")
    print(fmt(data["home"]["doubt"]))
    print()
    print("🚨 원정 결장 유력 🚨")
    print(fmt(data["away"]["out"]))
    print()
    print("🏥 원정 결장 의심 🏥")
    print(fmt(data["away"]["doubt"]))
    print()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("url", nargs="?", default="")
    ap.add_argument("--debug", action="store_true")
    args = ap.parse_args()

    if not args.url:
        print("URL을 넣어야 함. 예:")
        print('  python -m r8kx2v.basketball.absences "https://www.flashscore.co.kr/match/.../?mid=XXXX" --debug')
        raise SystemExit(2)

    data = get_absences(args.url, debug=args.debug)
    _print_block(data)


if __name__ == "__main__":
    main()
