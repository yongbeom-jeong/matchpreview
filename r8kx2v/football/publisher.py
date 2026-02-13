# site.py — 게시 모듈(루트용)
import os, json, re, pathlib, requests
from typing import Dict, Any
from dotenv import load_dotenv
from pathlib import Path

# 매 호출 시 최신 .env 반영
ROOT = Path(__file__).resolve().parent
def _env(key: str, default: str = "") -> str:
    load_dotenv(ROOT / ".env", override=True)
    v = os.getenv(key)
    return v if (v is not None and v != "") else default

def _safe(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "-", (s or "")).strip()

def publish(title: str, body: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    FastAPI 내부 엔드포인트(/internal/publish)에 글을 올린다.
    성공: {"ok": True, ...서버 응답 JSON...}
    실패: {"ok": False, "error": "...", ["backup": 경로]}
    """
    PUBLISH_URL = _env("PUBLISH_URL", "http://localhost:8000/internal/publish")
    SITE_API_KEY = _env("SITE_API_KEY", "dev-key")
    BACKUP_DIR = pathlib.Path(_env("PUBLISH_BACKUP_DIR", "./publish_backup"))

    payload = {
        "title": (title or "").strip(),
        "body": (body or "").strip(),
        "meta": {
            # 서버(app.py)가 실제로 사용하는 핵심 키들
            "league_name": meta.get("league_name") or "",
            "kickoff": meta.get("kickoff"),
            "sport": meta.get("sport", "football"),
            # 참고용(서버는 무시해도 됨)
            "flashscore_mid": meta.get("flashscore_mid"),
            "home": meta.get("home"),
            "away": meta.get("away"),
        },
    }

    try:
        r = requests.post(
            PUBLISH_URL,
            headers={"X-API-KEY": SITE_API_KEY, "Content-Type": "application/json"},
            data=json.dumps(payload, ensure_ascii=False),
            timeout=15,
        )
        if r.status_code == 200:
            try:
                res = r.json()
            except Exception:
                res = {"raw": r.text[:400]}
            return {"ok": True, **res}
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:200]}"}
    except Exception as e:
        # 네트워크 실패 시 본문 백업
        try:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            path = BACKUP_DIR / f"{_safe(title)}.txt"
            path.write_text(body or "", encoding="utf-8")
            return {"ok": False, "error": f"publish failed: {e}", "backup": str(path)}
        except Exception as e2:
            return {"ok": False, "error": f"publish+backup failed: {e}; {e2}"}
