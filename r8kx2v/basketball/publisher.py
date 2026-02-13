# basketball/publisher.py
# - hockey/football publisher랑 동일한 인터페이스: publish(title, body, meta)
# - 환경변수:
#     PUBLISH_URL          (기본: http://localhost:8000/internal/publish)
#     SITE_API_KEY         (기본: dev-key)
#     PUBLISH_BACKUP_DIR   (기본: ./publish_backup)

from __future__ import annotations

import json
import os
import pathlib
import re
from pathlib import Path
from typing import Any, Dict

import requests
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent


def _env(key: str, default: str = "") -> str:
    # basketball 폴더의 .env를 매 호출마다 override
    load_dotenv(ROOT / ".env", override=True)
    v = os.getenv(key)
    return v if (v is not None and v != "") else default


def _safe(s: str) -> str:
    return re.sub(r'[\\/:*?"<>|]', "-", (s or "")).strip()


def _pick_kickoff(meta: Dict[str, Any]):
    return (
        meta.get("kickoff")
        or meta.get("kickoff_utc")
        or meta.get("datetime_utc")
        or meta.get("datetime")
    )


def publish(title: str, body: str, meta: Dict[str, Any]) -> Dict[str, Any]:
    PUBLISH_URL = _env("PUBLISH_URL", "http://localhost:8000/internal/publish")
    SITE_API_KEY = _env("SITE_API_KEY", "dev-key")
    BACKUP_DIR = pathlib.Path(_env("PUBLISH_BACKUP_DIR", "./publish_backup"))

    payload = {
        "title": (title or "").strip(),
        "body": (body or "").strip(),
        "meta": {
            "league_name": meta.get("league_name") or "",
            "kickoff": _pick_kickoff(meta),
            "sport": meta.get("sport", "basketball"),
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
        # 실패하면 본문 백업
        try:
            BACKUP_DIR.mkdir(parents=True, exist_ok=True)
            fname = f"{_safe(payload['meta'].get('league_name') or 'basketball')}-{_safe(title)}.txt"
            path = BACKUP_DIR / fname
            path.write_text(body or "", encoding="utf-8")
            return {"ok": False, "error": f"publish failed: {e}", "backup": str(path)}
        except Exception as e2:
            return {"ok": False, "error": f"publish+backup failed: {e}; {e2}"}
