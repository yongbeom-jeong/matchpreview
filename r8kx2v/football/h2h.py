# h2h.py — Flashscore df_hh feed → 최근 5경기 요약 + 각 팀 최신 경기 메타(+확정 스코어)
# 출력 스키마:
# {
#   "_module": "h2h",
#   "_schema": "mpwin/h2h@1",
#   "recent_5_summary": {
#     "home": {
#       "team": "...",
#       "wins": 0, "draws": 0, "losses": 0,
#       "goals_for": 0, "goals_against": 0,
#       "avg_goals_for": 0.0, "avg_goals_against": 0.0,
#       "last_match": {
#         "mid": "...", "home": "...", "away": "...",
#         "score": {"home": 0, "away": 0},
#         "result": "home_win|draw|away_win"
#       }
#     },
#     "away": { ... 동일 ... }
#   }
# }

from __future__ import annotations
from typing import Dict, Any, List, Tuple, Optional
import re, json, requests, os
from dotenv import load_dotenv

load_dotenv(override=True)

FS_BASE = os.getenv("FS_NINJA_BASE", "https://global.flashscore.ninja")
FS_VER  = os.getenv("FS_FEED_VER", "34")
FS_SEG  = os.getenv("FS_LOCALE_SEG", "x")
TIMEOUT = float(os.getenv("HTTP_TIMEOUT", "12"))

FEED_URL = f"{FS_BASE}/{FS_VER}/{FS_SEG}/feed/df_hh_1" + "_{mid}"

BASE_HEADERS = {
    "User-Agent": os.getenv("FS_UA", "Mozilla/5.0"),
    "Accept": "*/*",
    "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    "Accept-Encoding": "gzip, deflate",
    "Origin": "https://www.flashscore.co.kr",
    "Referer": "https://www.flashscore.co.kr/",
    "x-fsign": "SW9D1eZo",
}

TOKEN_PAIR = "÷"
FIELD_SEP = "¬"
ENTRY_ANCHOR = "KC"
ENTRY_PREFIX = f"{ENTRY_ANCHOR}{TOKEN_PAIR}"
_int_re = re.compile(r"^\d+$")

def _normalize_team(s: str) -> str:
    return (s or "").replace("*", "").strip()

def _split_entries(stream: str) -> List[str]:
    if not stream: return []
    parts = stream.split(FIELD_SEP + "~" + ENTRY_PREFIX)
    entries: List[str] = []
    for i, p in enumerate(parts):
        if not p: continue
        if i == 0:
            if ENTRY_PREFIX in p:
                idx = p.find(ENTRY_PREFIX)
                if idx != -1:
                    entries.append(p[idx:])
        else:
            entries.append(ENTRY_PREFIX + p)
    return entries

def _kv_map(entry: str) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for tok in entry.split(FIELD_SEP):
        if TOKEN_PAIR in tok:
            k, v = tok.split(TOKEN_PAIR, 1)
            k = k.strip(); v = v.strip()
            if k and v:
                out[k] = v
    return out

def _to_int(s: Optional[str]) -> Optional[int]:
    if s and _int_re.match(s):
        try: return int(s)
        except: return None
    return None

def _parse_kl(kl: Optional[str]) -> Optional[Tuple[int,int]]:
    if not kl: return None
    m = re.match(r"^\s*(\d+)\s*:\s*(\d+)\s*$", kl)
    return (int(m.group(1)), int(m.group(2))) if m else None

def _row_involves_team(kv: Dict[str, Any], team: str) -> bool:
    return _normalize_team(kv.get("KJ","")) == _normalize_team(team) or \
           _normalize_team(kv.get("KK","")) == _normalize_team(team)

def _opponent_name(kv: Dict[str, Any], team: str) -> str:
    kj = _normalize_team(kv.get("KJ", ""))
    kk = _normalize_team(kv.get("KK", ""))
    return kk if kj == _normalize_team(team) else kj

def _compute_home_away_score(kv: Dict[str, Any]) -> Optional[Tuple[int,int]]:
    # df_hh 한 줄에서 홈/원정 득점 추출
    ku = _to_int(kv.get("KU"))  # 홈
    kt = _to_int(kv.get("KT"))  # 원정
    if ku is not None and kt is not None:
        return ku, kt
    pair = _parse_kl(kv.get("KL"))
    return pair

def _unique_latest_rows_for_team(stream: str, team: str, exclude_vs: Optional[str]) -> List[Dict[str,Any]]:
    rows = []; kp_seen=set()
    for ent in _split_entries(stream):
        kv = _kv_map(ent)
        if not _row_involves_team(kv, team): continue
        opp = _opponent_name(kv, team)
        if exclude_vs and _normalize_team(opp)==_normalize_team(exclude_vs):
            continue
        kp = kv.get("KP")
        kc = _to_int(kv.get("KC")) or 0
        if not kp:
            kp = f"KC:{kc}:{_normalize_team(kv.get('KJ',''))}:{_normalize_team(kv.get('KK',''))}:{kv.get('KL','')}"
        if kp in kp_seen: continue
        kp_seen.add(kp)
        kv["_KC_INT"]=kc
        rows.append(kv)
    rows.sort(key=lambda r:r.get("_KC_INT",0), reverse=True)
    return rows

def _result_label(home_g: int, away_g: int) -> str:
    if home_g > away_g: return "home_win"
    if home_g < away_g: return "away_win"
    return "draw"

def _last_match_meta(latest_row: Optional[Dict[str,Any]], team: str) -> Optional[Dict[str,Any]]:
    if not latest_row: return None
    team_norm=_normalize_team(team)
    opp=_opponent_name(latest_row, team_norm)
    ks=(latest_row.get("KS") or "").lower()
    mid=latest_row.get("KP","").strip()

    # 홈/원정 팀명 확정
    if ks=="home": home_name,away_name=team_norm,opp
    elif ks=="away": home_name,away_name=opp,team_norm
    else:
        kj=_normalize_team(latest_row.get("KJ",""))
        home_name,away_name=(team_norm,opp) if kj==team_norm else (opp,team_norm)

    # 확정 스코어 추출
    pair=_compute_home_away_score(latest_row)
    score_home, score_away = (pair if pair else (None, None))
    result = None
    if pair:
        result = _result_label(pair[0], pair[1])

    return {
        "mid": mid,
        "home": home_name,
        "away": away_name,
        "score": {"home": score_home, "away": score_away},
        "result": result
    }

def _compute_gf_ga_for_team(kv: Dict[str, Any], team: str) -> Optional[Tuple[int,int]]:
    pair = _compute_home_away_score(kv)
    if not pair: return None
    home_g, away_g = pair
    ks = (kv.get("KS") or "").lower()
    if ks == "home":
        return (home_g, away_g) if _normalize_team(kv.get("KJ","")) == _normalize_team(team) else (away_g, home_g)
    if ks == "away":
        return (away_g, home_g) if _normalize_team(kv.get("KK","")) == _normalize_team(team) else (home_g, away_g)
    kj = _normalize_team(kv.get("KJ",""))
    return (home_g, away_g) if kj == _normalize_team(team) else (away_g, home_g)

def _summarize_last_n(rows: List[Dict[str,Any]], team: str, n:int=5)->Dict[str,Any]:
    take=rows[:n]; w=d=l=0; gf_sum=ga_sum=0
    for kv in take:
        res=_compute_gf_ga_for_team(kv,team)
        if not res: continue
        gf,ga=res; gf_sum+=gf; ga_sum+=ga
        if gf>ga: w+=1
        elif gf==ga: d+=1
        else: l+=1
    games=max(1,len(take))
    return {
        "wins":w,"draws":d,"losses":l,
        "goals_for":gf_sum,"goals_against":ga_sum,
        "avg_goals_for":round(gf_sum/games,2),
        "avg_goals_against":round(ga_sum/games,2),
    }

def fetch_stream(mid:str, headers:Optional[Dict[str,str]]=None, timeout:float=12.0)->str:
    url=FEED_URL.format(mid=mid)
    req_headers=dict(BASE_HEADERS)
    if headers: req_headers.update(headers)
    r=requests.get(url,headers=req_headers,timeout=timeout)
    r.raise_for_status()
    return r.text

def get_h2h_snapshot(mid:str, home:str, away:str)->Dict[str,Any]:
    stream=fetch_stream(mid, timeout=TIMEOUT)
    home_rows=_unique_latest_rows_for_team(stream,team=home,exclude_vs=away)
    away_rows=_unique_latest_rows_for_team(stream,team=away,exclude_vs=home)

    home_sum=_summarize_last_n(home_rows,team=home,n=5)
    away_sum=_summarize_last_n(away_rows,team=away,n=5)

    home_last=_last_match_meta(home_rows[0] if home_rows else None,team=home)
    away_last=_last_match_meta(away_rows[0] if away_rows else None,team=away)
    if home_last: home_sum["last_match"]=home_last
    if away_last: away_sum["last_match"]=away_last

    return {
        "_module":"h2h",
        "_schema":"mpwin/h2h@1",
        "recent_5_summary":{
            "home":{"team":home,**home_sum},
            "away":{"team":away,**away_sum},
        }
    }

# CLI
if __name__=="__main__":
    mid=input("mid= ").strip()
    home=input("홈팀= ").strip()
    away=input("원정팀= ").strip()
    data=get_h2h_snapshot(mid,home,away)
    print(json.dumps(data,ensure_ascii=False,indent=2))
