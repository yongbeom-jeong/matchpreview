# r8kx2v/football2/writer.py
# OpenAI(gpt-5) 호출 및 기사 생성 (지시문 + 데이터 + 샘플 구조)
#
# football2 요구사항:
# - ▶양팀 스탯에서 "홈팀 : 팀명" / "원정팀 : 팀명"만 출력 (순위/승점/경기수 등 standings 상세 제거)
# - "최근 5경기" 라인은 그대로 출력 (h2h recent_5_summary 기반)
# - absences(결장 유력/의심)는 Flashscore "기본 match URL"로 수집해서 프롬프트 상단에 주입

from __future__ import annotations

import os, re, json, glob
from typing import Dict, Any
from datetime import datetime
import openai

from .h2h import get_h2h_snapshot
from .lastmatch import get_lastmatch
from .absences import get_absences

MODEL = os.getenv("OPENAI_MODEL", "gpt-5")
_client = openai.OpenAI()  # OPENAI_API_KEY 필요


# ── 유틸 ─────────────────────────────────────────────────────────────────
def _clean(s: str) -> str:
    return re.sub(r"[ \t]+\n", "\n", (s or "")).strip()


def _to_iso(x):
    return x.isoformat() if isinstance(x, datetime) else x


def _fmt_float(x) -> str:
    try:
        v = float(x)
        s = f"{v:.2f}".rstrip("0").rstrip(".")
        return s if s else "0"
    except Exception:
        return str(x)


# ── ▶양팀 스탯 문자열 생성 (football2: 팀명만 + 최근5) ───────────────────
def _build_team_line_simple(team_name: str) -> str:
    return (team_name or "").strip()


def _build_recent5_line(r5: Dict[str, Any]) -> str:
    w  = r5.get("wins", 0)
    d  = r5.get("draws", 0)
    l  = r5.get("losses", 0)
    gf = r5.get("goals_for", 0)
    ga = r5.get("goals_against", 0)
    avgf = _fmt_float(r5.get("avg_goals_for", 0.0))
    avga = _fmt_float(r5.get("avg_goals_against", 0.0))
    return (f"최근 5경기 : {w}승 {d}무 {l}패, {gf}득점 {ga}실점, "
            f"평균득점({avgf}), 평균실점({avga})")


def _build_yangtim_stats_block(standings: Dict[str, Any], h2h_data: Dict[str, Any],
                               home_name: str, away_name: str) -> str:
    # football2에서는 standings 상세를 출력하지 않지만,
    # standings 인자 시그니처는 기존 호환을 위해 유지.
    r5 = (h2h_data or {}).get("recent_5_summary", {})
    rh = r5.get("home", {}) if isinstance(r5, dict) else {}
    ra = r5.get("away", {}) if isinstance(r5, dict) else {}

    home_line = _build_team_line_simple(home_name)
    away_line = _build_team_line_simple(away_name)
    home_r5   = _build_recent5_line(rh)
    away_r5   = _build_recent5_line(ra)

    block = f"""▶양팀 스탯
홈팀 : {home_line}
{home_r5}

원정팀 : {away_line}
{away_r5}"""
    return _clean(block)


# ── 샘플 로더: ./sample/*.txt (최대 2개) ────────────────────────────────
def _read_samples_bundle() -> str:
    samples_dir = os.getenv("SAMPLES_DIR")
    if not samples_dir:
        here = os.path.dirname(os.path.abspath(__file__))
        samples_dir = os.path.join(here, "sample")
    max_files = int(os.getenv("SAMPLES_MAX", "2"))

    paths = sorted(glob.glob(os.path.join(samples_dir, "*.txt")))[:max_files]
    parts = []
    for path in paths:
        try:
            with open(path, "r", encoding="utf-8") as f:
                parts.append(f"\n---- {os.path.basename(path)} ----\n{f.read().strip()}\n")
        except Exception as e:
            parts.append(f"\n---- {os.path.basename(path)} ----\n[파일 읽기 오류: {e}]\n")
    return "\n".join(parts).strip()


# ── 프롬프트 빌더 ────────────────────────────────────────────────────────
def _build_prompt(match_meta: Dict[str, Any],
                  standings: Dict[str, Any],
                  h2h_data: Dict[str, Any],
                  last_home: Dict[str, Any],
                  last_away: Dict[str, Any],
                  absences: Dict[str, Any]) -> str:
    instruction = _clean("""
아래 제공된 데이터만 사용하여 경기 프리뷰를 작성하라.
반드시 뒤에 제공된 샘플(txt)들의 문체와 구성, 섹션 흐름을 그대로 따르되
샘플의 문장을 베끼지 말고, 현재 데이터에 맞게 새로 작성하라.

추가 지시:
- 모든 선수 이름은 한국어 표기로 작성하라. (영문 표기는 금지)
- 선수 이름을 쓸 때 알파벳은 제외하고 이름만 써라. (L. 메시라면 L.은 쓰지 말고 단순히 메시라고 쓸 것)
- 직전 경기 리뷰의 첫 문장은 샘플과 같이 직전 경기의 결과를 서술하라.
- absences(결장)가 제공되면 글 상단(▶양팀 스탯 위)에 샘플처럼 '결장' 섹션을 추가해라.
  홈/원정 각각 '결장 유력'과 '결장 의심'을 간단히 정리하고, 없으면 '없음'이라고 써라.
- football2의 ▶양팀 스탯은 반드시 아래 포맷을 따른다:
  홈팀 : 팀명
  최근 5경기 : ...
  원정팀 : 팀명
  최근 5경기 : ...
  (순위/승점/경기수/득실/평균실점 등 standings 상세는 쓰지 말 것)
""")

    safe_meta = {k: _to_iso(v) for k, v in match_meta.items()}
    yangtim_stats = _build_yangtim_stats_block(
        standings, h2h_data, safe_meta.get("home", ""), safe_meta.get("away", "")
    )

    data_block = f"""
=== MATCH META ===
{json.dumps(safe_meta, ensure_ascii=False, default=str)}

=== MODULE: absences (flashscore/match) ===
{json.dumps(absences, ensure_ascii=False, default=str)}

=== ▶양팀 스탯 (h2h recent5) ===
{yangtim_stats}

=== MODULE: lastmatch_home (mpwin/lastmatch@2) ===
{json.dumps(last_home, ensure_ascii=False, default=str)}

=== MODULE: lastmatch_away (mpwin/lastmatch@2) ===
{json.dumps(last_away, ensure_ascii=False, default=str)}
""".strip()

    samples = _read_samples_bundle()
    samples_block = f"=== SAMPLES (sample/*.txt, up to 2) ===\n{samples}" if samples else "=== SAMPLES ===\n[샘플 없음]"

    return _clean(f"{instruction}\n\n{data_block}\n\n{samples_block}")


# ── 외부 진입점 ─────────────────────────────────────────────────────────
def write_article_fs(match_meta: Dict[str, Any], standings: Dict[str, Any] = {}) -> Dict[str, Any]:
    mid = match_meta.get("flashscore_mid") or match_meta.get("mid") or ""
    home = match_meta.get("home", "")
    away = match_meta.get("away", "")

    # ✅ absences(결장) 수집: Flashscore "기본 match URL"로 실행 (실패해도 계속)
    absences: Dict[str, Any] = {"_module": "absences", "_schema": "flashscore/absences@1", "data": None}
    try:
        match_url = (match_meta.get("url") or match_meta.get("flashscore_url") or "").strip()
        absences["match_url"] = match_url
        if match_url:
            absences["data"] = get_absences(match_url, debug=False)
        else:
            absences["error"] = "no_match_url"
    except Exception as e:
        absences["error"] = str(e)

    # h2h 스냅샷
    try:
        h2h_data = get_h2h_snapshot(mid, home, away)
    except Exception:
        h2h_data = {
            "_module": "h2h",
            "_schema": "mpwin/h2h@1",
            "recent_5_summary": {
                "home": {
                    "team": home, "wins": 0, "draws": 0, "losses": 0,
                    "goals_for": 0, "goals_against": 0,
                    "avg_goals_for": 0.0, "avg_goals_against": 0.0,
                    "last_match": {"mid": "", "home": "", "away": "", "score": {"home": None, "away": None}, "result": None},
                },
                "away": {
                    "team": away, "wins": 0, "draws": 0, "losses": 0,
                    "goals_for": 0, "goals_against": 0,
                    "avg_goals_for": 0.0, "avg_goals_against": 0.0,
                    "last_match": {"mid": "", "home": "", "away": "", "score": {"home": None, "away": None}, "result": None},
                }
            }
        }

    # 각 팀 직전 경기 메타
    home_last = h2h_data.get("recent_5_summary", {}).get("home", {}).get("last_match", {}) or {}
    away_last = h2h_data.get("recent_5_summary", {}).get("away", {}).get("last_match", {}) or {}

    # lastmatch 호출 시 h2h의 확정 스코어를 그대로 전달
    try:
        last_home = get_lastmatch(
            home_last.get("mid", ""),
            home_last.get("home", ""),
            home_last.get("away", ""),
            known_score=home_last.get("score"),
        )
    except Exception:
        last_home = {
            "_module": "lastmatch", "_schema": "mpwin/lastmatch@2",
            "match": {"home": home, "away": away},
            "logs": {"home": [], "away": []},
            "top_records_full": [],
            "score": {"home": None, "away": None},
            "result": None,
        }

    try:
        last_away = get_lastmatch(
            away_last.get("mid", ""),
            away_last.get("home", ""),
            away_last.get("away", ""),
            known_score=away_last.get("score"),
        )
    except Exception:
        last_away = {
            "_module": "lastmatch", "_schema": "mpwin/lastmatch@2",
            "match": {"home": home, "away": away},
            "logs": {"home": [], "away": []},
            "top_records_full": [],
            "score": {"home": None, "away": None},
            "result": None,
        }

    # standings가 안 와도 기존 호환 유지(football2는 출력에 상세 사용 안 함)
    if not standings:
        standings = {"_module": "standings", "_schema": "mpwin/standings@1"}

    prompt = _build_prompt(match_meta, standings, h2h_data, last_home, last_away, absences)

    resp = _client.chat.completions.create(
        model=MODEL,
        messages=[
            {"role": "system", "content": "You are a precise Korean sports journalist. Use only provided data."},
            {"role": "user", "content": prompt},
        ],
        # max_tokens=800,
        # temperature=0.2,
    )

    body = (resp.choices[0].message.content or "").strip()
    title = f"{home} vs {away}"  # ← '프리뷰' 제거

    meta_out = {
        **{k: _to_iso(v) for k, v in match_meta.items()},
        "flashscore_mid": mid,
        "schemas": {
            "standings": standings.get("_schema"),
            "h2h": h2h_data.get("_schema"),
            "lastmatch_home": last_home.get("_schema"),
            "lastmatch_away": last_away.get("_schema"),
            "absences": absences.get("_schema"),
        }
    }
    if isinstance(match_meta.get("datetime_utc"), datetime):
        meta_out.setdefault("kickoff", match_meta["datetime_utc"].isoformat())

    return {"title": title, "body": body, "meta": meta_out}
