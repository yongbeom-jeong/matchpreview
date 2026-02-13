# app.py — MatchPreview (유저별 /browse + /s, 파일+DB 동시 저장) — whitelist 버전
from __future__ import annotations
import os, re, json, sqlite3, datetime, html
from pathlib import Path
from typing import Dict, Any, List, Optional, Tuple
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, HTMLResponse, RedirectResponse, PlainTextResponse
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

# ──────────────────────────────────────────────────────
# ENV / CONSTANTS
# ──────────────────────────────────────────────────────
ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / ".env", override=True)

SITE_API_KEY = os.getenv("SITE_API_KEY", "")
VIEW_BASE = (os.getenv("VIEW_BASE") or "").rstrip("/")
LOG_LEVEL = (os.getenv("LOG_LEVEL") or "INFO").upper()

KST = datetime.timezone(datetime.timedelta(hours=9))
UTC = datetime.timezone.utc

# 등록된 유저 화이트리스트
ACTIVE_USERS = [u.strip() for u in os.getenv("ACTIVE_USERS", "").split(",") if u.strip()]

def _check_user(user: str):
    if user not in ACTIVE_USERS:
        raise HTTPException(status_code=404, detail="User not found")

# ──────────────────────────────────────────────────────
# APP INIT
# ──────────────────────────────────────────────────────
app = FastAPI(title="MatchPreview (user-separated, browse+s)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], allow_credentials=True,
    allow_methods=["*"], allow_headers=["*"],
)

# ──────────────────────────────────────────────────────
# UTIL
# ──────────────────────────────────────────────────────
def _safe(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]+', "-", (s or "")).strip()

def _slug(s: str) -> str:
    s = (s or "").strip()
    s = s.replace(" ", "-")
    s = re.sub(r"[^\w\-가-힣·]", "-", s, flags=re.UNICODE)
    s = re.sub(r"-{2,}", "-", s).strip("-").lower()
    return s or "untitled"

def _now_kst() -> datetime.datetime:
    return datetime.datetime.now(KST)

def _now_kst_str() -> str:
    return _now_kst().strftime("%Y-%m-%d %H:%M:%S")

def _to_kst(dt_like: Any) -> datetime.datetime:
    if isinstance(dt_like, datetime.datetime):
        dt = dt_like
    else:
        s = str(dt_like or "").strip()
        if not s:
            return _now_kst()
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        try:
            dt = datetime.datetime.fromisoformat(s)
        except Exception:
            return _now_kst()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(KST)

def _html(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="ko"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{html.escape(title)}</title>
<style>
:root{{--fg:#222;--muted:#666;--line:#e8e8e8;--bg:#fff}}
body{{max-width:960px;margin:24px auto;padding:0 16px;font:16px/1.65 ui-sans-serif,system-ui,Apple SD Gothic Neo,Malgun Gothic,Arial;color:var(--fg);background:var(--bg)}}
h1{{font-size:28px;margin:6px 0}}h2{{font-size:22px;margin:18px 0 8px}}h3{{font-size:18px;margin:12px 0 6px}}
hr{{border:0;border-top:1px solid var(--line);margin:20px 0}}
pre{{white-space:pre-wrap;word-break:break-word}}
a{{color:#0b62d6;text-decoration:none}}a:hover{{text-decoration:underline}}
ul{{padding-left:20px}}li{{margin:4px 0}}
.badge{{display:inline-block;border:1px solid var(--line);border-radius:999px;padding:2px 10px;margin-right:6px;font-size:12px;color:var(--muted)}}
.small{{color:var(--muted);font-size:13px}}
table{{border-collapse:collapse}}td,th{{border:1px solid var(--line);padding:6px 8px}}th{{background:#fafafa}}
</style></head><body>{body}</body></html>"""

# ──────────────────────────────────────────────────────
# DB
# ──────────────────────────────────────────────────────
def _user_root(user: str) -> Path:
    return ROOT / user

def _db_path(user: str) -> Path:
    return _user_root(user) / "posts.db"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    date_key   TEXT NOT NULL,
    sport      TEXT NOT NULL,
    league     TEXT NOT NULL,
    league_slug TEXT NOT NULL,
    title      TEXT NOT NULL,
    title_slug TEXT NOT NULL,
    body       TEXT NOT NULL,
    meta       TEXT
);
"""
_INDEXES = [
    "CREATE INDEX IF NOT EXISTS idx_posts_date ON posts(date_key)",
    "CREATE INDEX IF NOT EXISTS idx_posts_month ON posts(substr(date_key,1,7))",
    "CREATE INDEX IF NOT EXISTS idx_posts_key ON posts(date_key, sport, league_slug, title_slug)"
]

def _ensure_db(user: str) -> sqlite3.Connection:
    p = _db_path(user)
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(p)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.executescript(_SCHEMA)
    for q in _INDEXES:
        conn.execute(q)
    conn.commit()
    return conn

def _insert_post(user: str, *, date_key: str, sport: str, league: str, league_slug: str,
                 title: str, title_slug: str, body: str, meta: Dict[str, Any]) -> None:
    conn = _ensure_db(user)
    conn.execute(
        "INSERT INTO posts(created_at,date_key,sport,league,league_slug,title,title_slug,body,meta) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (_now_kst_str(), date_key, sport, league, league_slug, title, title_slug, body, json.dumps(meta, ensure_ascii=False))
    )
    conn.commit()
    conn.close()

def _list_dates(user: str) -> List[str]:
    conn = _ensure_db(user)
    cur = conn.execute("SELECT DISTINCT date_key FROM posts ORDER BY date_key DESC")
    rows = [r[0] for r in cur.fetchall()]
    conn.close()
    return rows

def _list_posts_on(user: str, date_key: str) -> List[Dict[str, str]]:
    conn = _ensure_db(user)
    cur = conn.execute(
        "SELECT sport, league, league_slug, title, title_slug, created_at "
        "FROM posts WHERE date_key=? ORDER BY id DESC", (date_key,)
    )
    out = [{"sport": r[0], "league": r[1], "league_slug": r[2],
            "title": r[3], "title_slug": r[4], "created_at": r[5]} for r in cur.fetchall()]
    conn.close()
    return out

def _monthly_counts(user: str) -> List[Tuple[str, int]]:
    conn = _ensure_db(user)
    cur = conn.execute(
        "SELECT substr(created_at, 1, 7) AS ym, COUNT(*) "
        "FROM posts GROUP BY ym ORDER BY ym DESC"
    )
    rows = [(r[0], int(r[1])) for r in cur.fetchall()]
    conn.close()
    return rows


def _fetch_body_from_db(user: str, date_key: str, sport: str, league_slug: str, title_slug: str) -> Optional[Tuple[str, str, str]]:
    conn = _ensure_db(user)
    cur = conn.execute(
        "SELECT title, league, body FROM posts WHERE date_key=? AND sport=? AND league_slug=? AND title_slug=? "
        "ORDER BY id DESC LIMIT 1",
        (date_key, sport, league_slug, title_slug)
    )
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return row[0], row[1], row[2]

# ──────────────────────────────────────────────────────
# FILE I/O
# ──────────────────────────────────────────────────────
def _article_dir(user: str, date_key: str, sport: str, league_slug: str, title_slug: str) -> Path:
    return _user_root(user) / "browse" / date_key / sport / league_slug / title_slug

def _write_article_files(user: str, date_key: str, sport: str, league_slug: str, title_slug: str,
                         title: str, league: str, body: str, meta: Dict[str, Any]) -> Path:
    dst = _article_dir(user, date_key, sport, league_slug, title_slug)
    dst.mkdir(parents=True, exist_ok=True)

    head = f"""
<h1>{html.escape(title)}</h1>
<p class="small">
  <span class="badge">{html.escape(sport)}</span>
  <span class="badge">{html.escape(league)}</span>
  <span class="badge">{html.escape(date_key)}</span>
</p><hr>"""
    html_page = _html(title, head + f"<pre>{html.escape(body)}</pre>")
    (dst / "index.html").write_text(html_page, encoding="utf-8")
    (dst / "article.txt").write_text(body, encoding="utf-8")
    (dst / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return dst

def _read_index_html(user: str, date_key: str, sport: str, league_slug: str, title_slug: str) -> Optional[str]:
    p = _article_dir(user, date_key, sport, league_slug, title_slug) / "index.html"
    return p.read_text(encoding="utf-8") if p.exists() else None

# ──────────────────────────────────────────────────────
# ROUTES
# ──────────────────────────────────────────────────────
@app.get("/health", response_class=PlainTextResponse)
def health():
    return "ok"

@app.get("/")
def home():
    return {"ok": True, "msg": "MatchPreview running (user-separated /browse + /s)"}

@app.get("/{user}")
def user_root(user: str):
    _check_user(user)
    return RedirectResponse(url=f"/{user}/browse")

@app.post("/internal/publish/{user}")
async def internal_publish(user: str, request: Request):
    _check_user(user)
    api_key = request.headers.get("X-API-KEY", "")
    if not api_key or api_key != SITE_API_KEY:
        raise HTTPException(status_code=403, detail="Invalid API key")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    title = (payload.get("title") or "").strip()
    body = (payload.get("body") or "").strip()
    meta = payload.get("meta") or {}
    if not title or not body:
        raise HTTPException(status_code=400, detail="Missing title/body")

    sport = (meta.get("sport") or "football").strip()
    league_raw = (meta.get("league_name") or meta.get("league") or "unknown").strip()
    kickoff = meta.get("kickoff") or meta.get("datetime_utc") or meta.get("start")

    date_key = _to_kst(kickoff).strftime("%Y-%m-%d")
    league_slug = _slug(league_raw)
    title_slug = _slug(title)

    _write_article_files(user, date_key, sport, league_slug, title_slug, title, league_raw, body, meta)
    _insert_post(user, date_key=date_key, sport=sport, league=league_raw,
                 league_slug=league_slug, title=title, title_slug=title_slug,
                 body=body, meta=meta)

    view_path = f"/{user}/browse/{date_key}/{sport}/{league_slug}/{title_slug}"
    view_url = f"{VIEW_BASE}{view_path}" if VIEW_BASE else view_path
    return JSONResponse({"ok": True, "user": user, "date": date_key, "sport": sport,
                         "league": league_raw, "title": title, "view_url": view_url})

@app.get("/{user}/browse")
def browse_dates(user: str):
    _check_user(user)
    dates = _list_dates(user)
    if not dates:
        return HTMLResponse(_html("Match Days", "<h2>📅 Match Days</h2><p>아직 글이 없습니다.</p>"))
    items = "".join(f'<li><a href="/{user}/browse/{d}">{html.escape(d)}</a></li>' for d in dates)
    return HTMLResponse(_html("Match Days", f"<h2>📅 Match Days</h2><ul>{items}</ul>"))

@app.get("/{user}/browse/{date_key}")
def browse_day(user: str, date_key: str):
    _check_user(user)
    posts = _list_posts_on(user, date_key)
    if not posts:
        return HTMLResponse(_html(date_key, f"<h2>📅 {html.escape(date_key)}</h2><p>글이 없습니다.</p>"))
    sports = sorted(set(p["sport"] for p in posts))
    items = "".join(f'<li><a href="/{user}/browse/{date_key}/{html.escape(s)}">{html.escape(s)}</a></li>' for s in sports)
    return HTMLResponse(_html(f"{date_key}", f"<h2>📅 {html.escape(date_key)}</h2><ul>{items}</ul>"))

@app.get("/{user}/browse/{date_key}/{sport}")
def browse_sport(user: str, date_key: str, sport: str):
    _check_user(user)
    posts = [p for p in _list_posts_on(user, date_key) if p["sport"] == sport]
    if not posts:
        return HTMLResponse(_html(sport, f"<h2>{html.escape(sport)}</h2><p>리그가 없습니다.</p>"))

    leagues = {}
    for p in posts:
        name = p["league"]
        name = re.sub(r"\s*경기.*", "", name)
        name = re.sub(r"\s*-\s*.*", "", name)
        leagues[p["league_slug"]] = name.strip()

    items = "".join(
        f'<li><a href="/{user}/browse/{date_key}/{sport}/{slug}">{html.escape(name)}</a></li>'
        for slug, name in leagues.items()
    )
    return HTMLResponse(_html(sport, f"<h2>⚽ {html.escape(sport)}</h2><ul>{items}</ul>"))

@app.get("/{user}/browse/{date_key}/{sport}/{league_slug}")
def browse_league(user: str, date_key: str, sport: str, league_slug: str):
    _check_user(user)
    posts = [p for p in _list_posts_on(user, date_key)
             if p["sport"] == sport and p["league_slug"] == league_slug]
    if not posts:
        return HTMLResponse(_html(league_slug, f"<h2>{html.escape(league_slug)}</h2><p>글이 없습니다.</p>"))

    league_name = posts[0]["league"]
    league_name = re.sub(r"\s*경기.*", "", league_name)
    league_name = re.sub(r"\s*-\s*.*", "", league_name)
    league_name = league_name.strip()

    items = "".join(
        f'<li><a href="/{user}/browse/{date_key}/{sport}/{league_slug}/{p["title_slug"]}">'
        f'{html.escape(p["title"])}</a> <span class="small">{html.escape(p["created_at"])}</span></li>'
        for p in posts
    )
    return HTMLResponse(_html(league_name, f"<h2>{html.escape(league_name)}</h2><ul>{items}</ul>"))

@app.get("/{user}/browse/{date_key}/{sport}/{league_slug}/{title_slug}")
def view_article(user: str, date_key: str, sport: str, league_slug: str, title_slug: str):
    _check_user(user)
    doc = _read_index_html(user, date_key, sport, league_slug, title_slug)
    if doc:
        return HTMLResponse(doc)
    fetched = _fetch_body_from_db(user, date_key, sport, league_slug, title_slug)
    if not fetched:
        raise HTTPException(status_code=404, detail="Article not found")
    title, league, body = fetched
    head = f"""
<h1>{html.escape(title)}</h1>
<p class="small"><span class="badge">{html.escape(sport)}</span>
<span class="badge">{html.escape(league)}</span>
<span class="badge">{html.escape(date_key)}</span></p><hr>"""
    return HTMLResponse(_html(title, head + f"<pre>{html.escape(body)}</pre>"))

@app.get("/{user}/s")
def monthly_summary(user: str):
    _check_user(user)
    rows = _monthly_counts(user)
    if not rows:
        return HTMLResponse(_html(f"{user} - s", "<h2>월별 집계(작성일 기준)</h2><p>데이터가 없습니다.</p>"))
    lis = "".join(f"<li>{html.escape(ym)} : <b>{cnt}</b>개</li>" for ym, cnt in rows)
    body = f"""
<h2>월별 집계(작성일 기준)</h2>
<p class="small">작성일(KST) 기준 월별 합계 — 이 페이지는 링크로 노출하지 않습니다.</p>
<ul>{lis}</ul>
"""
    return HTMLResponse(_html(f"{user} - s", body))
