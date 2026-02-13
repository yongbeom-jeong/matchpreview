# lastmatch.py — df_sui 경기 이벤트 로그 + Top 기록(full)
# 변경: h2h에서 전달 가능한 "확정 스코어"를 그대로 수용하여 반환에 포함.
# 로그로 스코어를 재계산하지 않음(optional fallback만 유지 가능).

from __future__ import annotations
import os, re, json, logging, requests
from typing import Dict, Any, List, Tuple, Optional
from dotenv import load_dotenv

load_dotenv(override=True)

FS_BASE = os.getenv("FS_NINJA_BASE", "https://global.flashscore.ninja")
FS_VER  = os.getenv("FS_FEED_VER", "34")
FS_SEG  = os.getenv("FS_LOCALE_SEG", "x")
UA      = os.getenv("HTTP_UA", "Mozilla/5.0")
TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "12"))

PAIR_SEP, FIELD_SEP, CHUNK_SEP = "÷", "¬", "~"

logging.basicConfig(
    level=getattr(logging, (os.getenv("LOG_LEVEL") or "INFO").upper(), logging.INFO),
    format="%(asctime)s [%(levelname)s] %(message)s"
)
log = logging.getLogger(__name__)

def _headers(mid: str) -> Dict[str, str]:
    return {
        "User-Agent": UA,
        "Accept": "*/*",
        "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
        "Origin": "https://www.flashscore.co.kr",
        "Referer": f"https://www.flashscore.co.kr/match/{mid}/#match-summary",
        "x-fsign": "SW9D1eZo",
    }

# ---------------- df_sui (logs) ----------------
def _url_df_sui(mid: str) -> str:
    return f"{FS_BASE}/{FS_VER}/{FS_SEG}/feed/df_sui_1_{mid}"

def _fetch_df_sui(mid: str) -> str:
    r = requests.get(_url_df_sui(mid), headers=_headers(mid), timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

def _iter_entries_sui(stream: str) -> List[Dict[str,str]]:
    out: List[Dict[str,str]] = []
    for raw in stream.split(CHUNK_SEP):
        if "III" not in raw:
            continue
        # IA(사이드), IB(분), INX/IOX(스코어 스냅숏), IF(선수), IK(이벤트)
        ctx: Dict[str, str] = {"IA": "", "IB": "", "INX": "", "IOX": ""}
        cur_if: Optional[str] = None
        for tok in (t for t in raw.split(FIELD_SEP) if PAIR_SEP in t):
            try:
                k, v = tok.split(PAIR_SEP, 1)
            except ValueError:
                continue
            k, v = k.strip(), v.strip()
            if k in ("IA", "IB", "INX", "IOX"):
                ctx[k] = v
                continue
            if k == "IF":
                cur_if = v
                continue
            if k == "IK":
                out.append({
                    "IA": ctx["IA"],
                    "IB": ctx["IB"],
                    "INX": ctx["INX"],
                    "IOX": ctx["IOX"],
                    "IF": cur_if or "",
                    "IK": v,
                })
    return out

_time_re = re.compile(r"^(?P<m>\d+)(?:\+(?P<a>\d+))?['’]?$")
def _time_key(t: str) -> Tuple[int,int]:
    m = _time_re.match((t or "").strip())
    return (int(m.group("m") or 0), int(m.group("a") or 0)) if m else (9999, 0)

def _side_label(ia: str, home: str, away: str) -> Tuple[str, str]:
    if ia == "1": return "홈", home
    if ia == "2": return "원정", away
    return "?", "?"

_GOAL_KEYWORDS = ("골", "득점", "PK 성공", "페널티킥 성공", "페널티킥 득점")

def _build_logs(entries: List[Dict[str,str]], home: str, away: str) -> Dict[str, List[str]]:
    entries_sorted = sorted(entries, key=lambda e: _time_key(e.get("IB","")))
    logs_home: List[str] = []
    logs_away: List[str] = []

    # 교체 페어링용 버퍼
    subs_buf: Dict[Tuple[str,str], List[Dict[str, Optional[str]]]] = {}

    def add(ia: str, text: str):
        if ia == "1":
            logs_home.append(text)
        elif ia == "2":
            logs_away.append(text)

    for ev in entries_sorted:
        ia, t, kind, player = ev.get("IA",""), ev.get("IB",""), ev.get("IK",""), ev.get("IF","")
        k_norm = (kind or "").strip()

        # 득점/자책(표시용)
        if "자책골" in k_norm or any(kw in k_norm for kw in _GOAL_KEYWORDS):
            side, team = _side_label(ia, home, away)
            add(ia, f"{t} {side} {team} {player} " + ("자책골" if "자책골" in k_norm else "골"))

        # 카드
        if "경고" in k_norm:
            side, team = _side_label(ia, home, away)
            add(ia, f"{t} {side} {team} {player} 경고")
        if "퇴장" in k_norm or "레드" in k_norm:
            side, team = _side_label(ia, home, away)
            add(ia, f"{t} {side} {team} {player} 퇴장")

        # 교체 페어링(간단 표기)
        if "교체 - 아웃" in k_norm or "교체 - 인" in k_norm:
            key = (ia, t)
            lst = subs_buf.setdefault(key, [])
            if "교체 - 아웃" in k_norm:
                for rec in lst:
                    if rec.get("out") is None:
                        rec["out"] = player
                        break
                else:
                    lst.append({"out": player, "in": None})
            else:
                for rec in lst:
                    if rec.get("in") is None:
                        rec["in"] = player
                        break
                else:
                    lst.append({"out": None, "in": player})

    # 교체 문장 생성
    for (ia, t), pairs in subs_buf.items():
        for rec in pairs:
            out_p = rec.get("out") or "교체 아웃"
            in_p  = rec.get("in") or "교체 인"
            side, team = _side_label(ia, home, away)
            add(ia, f"{t} {side} {team} {out_p} ↔ {in_p} 교체")

    return {"home": logs_home, "away": logs_away}

# ---------------- df_st (Top 기록) ----------------
def _url_df_st(mid: str) -> str:
    return f"{FS_BASE}/{FS_VER}/{FS_SEG}/feed/df_st_1_{mid}"

def _fetch_df_st(mid: str) -> str:
    r = requests.get(_url_df_st(mid), headers=_headers(mid), timeout=TIMEOUT)
    r.raise_for_status()
    return r.text

def _parse_top_records(stream: str) -> List[Dict[str,str]]:
    out: List[Dict[str,str]] = []
    if not stream:
        return out
    for chunk in stream.split("~"):
        if f"SD{PAIR_SEP}" in chunk and f"SG{PAIR_SEP}" in chunk:
            label: Optional[str] = None
            home_v: Optional[str] = None
            away_v: Optional[str] = None
            for tok in chunk.split(FIELD_SEP):
                if PAIR_SEP not in tok:
                    continue
                k, v = tok.split(PAIR_SEP, 1)
                if k == "SG": label = v.strip()
                elif k == "SH": home_v = v.strip()
                elif k == "SI": away_v = v.strip()
            if label is not None and home_v is not None and away_v is not None:
                out.append({"label": label, "home": home_v, "away": away_v})
    return out

# ---------------- Public API ----------------
def get_lastmatch(
    mid: str,
    home: str,
    away: str,
    known_score: Optional[Dict[str, Optional[int]]] = None
) -> Dict[str, Any]:
    """
    known_score: {"home": int|None, "away": int|None}  ← h2h.last_match.score 그대로 전달 가능
    """
    # 이벤트 로그
    try:
        sui = _fetch_df_sui(mid)
        entries = _iter_entries_sui(sui)
        logs = _build_logs(entries, home, away)
    except Exception as e:
        log.warning("df_sui fetch/parse 실패: %s", e)
        logs = {"home": [], "away": []}

    # Top 기록(full)
    try:
        st = _fetch_df_st(mid)
        top_full = _parse_top_records(st)
    except Exception as e:
        log.warning("df_st fetch/parse 실패: %s", e)
        top_full = []

    # 점수: h2h에서 받은 확정 스코어가 있으면 그대로 사용
    score: Dict[str, Optional[int]] = {"home": None, "away": None}
    if isinstance(known_score, dict):
        h = known_score.get("home")
        a = known_score.get("away")
        if isinstance(h, int) or h is None:
            score["home"] = h
        if isinstance(a, int) or a is None:
            score["away"] = a

    # 결과 라벨
    result: Optional[str] = None
    h = score.get("home")
    a = score.get("away")
    if isinstance(h, int) and isinstance(a, int):
        if h > a:
            result = "home_win"
        elif h < a:
            result = "away_win"
        else:
            result = "draw"

    return {
        "_module": "lastmatch",
        "_schema": "mpwin/lastmatch@2",
        "match": {"home": home, "away": away},
        "logs": logs,
        "top_records_full": top_full,
        "score": score,
        "result": result,
    }

# ---------------- CLI ----------------
if __name__ == "__main__":
    mid = input("mid= ").strip()
    home = input("홈팀= ").strip()
    away = input("원정팀= ").strip()
    # CLI 테스트에선 known_score 없이
    data = get_lastmatch(mid, home, away, known_score=None)
    print(json.dumps(data, ensure_ascii=False, indent=2))
