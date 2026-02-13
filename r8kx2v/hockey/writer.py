# -*- coding: utf-8 -*-
# r8kx2v/hockey/writer.py
#
# 목표:
# - 결장자 이름/포맷을 코드에서 만지지 않는다.
# - absences 원본 데이터를 LLM에게 넘기고, 풋볼처럼 LLM이 '깔끔한 이름'으로 출력하도록 유도한다.
# - 결장자 블록은 본문 최상단에 '🚨홈팀 결장 유력🚨' 형식(공백 없음)으로 출력한다.
#
# 구성:
# - 샘플: ./sample/*.txt 로드
# - ▶양팀 스탯: h2h
# - ▶직전 경기 리뷰: stats
# - ▶실전 분석: lineups
# - ✅ 결장자(absences): get_absences(match_url) 결과를 LLM에 제공

from __future__ import annotations

import os
import json
import glob
from datetime import datetime
from typing import Any, Dict, List, cast

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

try:
    from .h2h import analyze_h2h, get_team_lastmatch
    from .stats import get_stats as stats_get
    from .lineups import get_lineups
    from .absences import get_absences
except ImportError:
    from h2h import analyze_h2h, get_team_lastmatch  # type: ignore
    from stats import get_stats as stats_get         # type: ignore
    from lineups import get_lineups                  # type: ignore
    from absences import get_absences                # type: ignore


MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
_client = OpenAI()  # OPENAI_API_KEY 필요


# ---------------- utils ----------------
def _to_iso(v: Any) -> Any:
    if isinstance(v, datetime):
        return v.isoformat()
    return v


def _load_all_samples() -> str:
    """writer.py와 같은 위치의 sample/*.txt 전부 읽어서 합친다."""
    base_dir = os.path.dirname(__file__) if "__file__" in globals() else os.getcwd()
    sample_dir = os.path.join(base_dir, "sample")
    parts: List[str] = []
    for fp in sorted(glob.glob(os.path.join(sample_dir, "*.txt"))):
        try:
            with open(fp, "r", encoding="utf-8") as f:
                t = f.read().strip()
                if t:
                    parts.append(t)
        except Exception:
            continue
    return ("\n\n---\n\n".join(parts)).strip()


def _stats_for(side_block: Dict[str, Any]) -> Dict[str, Any]:
    """stats.get_stats 호출 래퍼"""
    if not side_block:
        return {}
    res = stats_get(
        match_id=str(side_block.get("last_match_id", "")),
        side=str(side_block.get("side", "")),
        team=str(side_block.get("team", "")),
        opponent=str(side_block.get("opponent", "")),
        outcome=str(side_block.get("result", "")),
        score_for=int(side_block.get("score_for", 0) or 0),
        score_against=int(side_block.get("score_against", 0) or 0),
    )
    return cast(Dict[str, Any], res or {})


def _get_match_url(match_meta: Dict[str, Any]) -> str:
    """schedule이 채워준 url/flashscore_url을 그대로 사용. 없으면 ''."""
    return str(match_meta.get("url") or match_meta.get("flashscore_url") or "").strip()


def _make_messages(data: Dict[str, Any]) -> List[ChatCompletionMessageParam]:
    """지침 + 샘플 + DATA"""
    samples = _load_all_samples()

    # 중요: 여기서 결장자 출력 형식을 “풋볼 형식”으로 강제하고,
    #      알파벳 이니셜(P., J. 등)을 제거하여 한국어 이름만 남기도록 LLM에 지시한다.
    user_prompt = f"""지침:
샘플의 어조와 문체를 그대로 따르되, 내용은 <DATA>의 사실에 근거해 자연스럽게 서술하라.
각 단락(▶양팀 스탯 / ▶직전 경기 리뷰 / ▶실전 분석)은 <DATA>의 값을 기반으로 하되,
수치가 비어 있거나 'N/A'인 경우에는 '없다/확인 불가' 같은 문구를 쓰지 말고 자연스럽게 생략하라.

[결장자 출력 규칙 - 매우 중요]
- <DATA.absences> 구조는 다음과 같다:
  - absences.home.out / absences.home.doubt
  - absences.away.out / absences.away.doubt
  각 리스트 원소는 {{name, reason}} 형태다.
- 결장자 정보가 하나라도 있으면, 본문 최상단(▶양팀 스탯 위)에 아래 4블록을 "그대로" 작성하라.
  (줄바꿈/구분은 아래 예시와 동일)
    🚨홈팀 결장 유력🚨
    이름(사유), 이름(사유)

    🏥홈팀 결장 의심🏥
    이름(사유), 이름(사유)

    🚨원정팀 결장 유력🚨
    이름(사유), 이름(사유)

    🏥원정팀 결장 의심🏥
    이름(사유), 이름(사유)
- 위 4개 헤더는 공백 없이 정확히:
  - "🚨홈팀 결장 유력🚨"
  - "🏥홈팀 결장 의심🏥"
  - "🚨원정팀 결장 유력🚨"
  - "🏥원정팀 결장 의심🏥"
- 각 줄의 이름 나열은 "쉼표+공백"(', ')으로 구분해 한 줄로 쓰되, 사람이 읽기 자연스럽게 2~8명 정도만 나열하라(그 이상이면 핵심만).
- absences의 name 값에 'P. 뒤부아'처럼 알파벳 이니셜이 포함되어도,
  최종 출력에서는 이니셜(예: 'P.', 'J.', 'A.' 등 알파벳+점)과 그 뒤 공백을 제거하고 한국어 이름만 남겨라.
  예: 'P. 뒤부아' -> '뒤부아'
  예: 'J. 드루인' -> '드루인'
- absences의 reason은 괄호로 붙인다: 이름(복부 염좌)
- 해당 블록에서 실제로 나열할 선수가 없으면 '없음'이라고 한 단어만 쓴다.
- 결장자 정보가 전부 비어 있으면, 결장자 블록 자체를 쓰지 마라.

[구성 규칙]
▶양팀 스탯 단락에서는 각 문장이 '홈팀 : ', '최근 5경기 : ', '원정팀 : ', '최근 5경기 : '로 시작할 것.
▶직전 경기 리뷰 단락에서는 직전 경기 스코어, 골리 선방율, 유효슈팅 개수를 기본으로 하고 이외에 1~2문장 더 추가하라.
단, '▶실전 분석' 단락에서는 각 팀의 골리(Goalie)를 반드시 문장 안에서 언급하고,
하키 포메이션/공격 용어(예: forecheck, power play, penalty kill, slot, blue line 등)를 자연스럽게 1~2개 포함하라.

<DATA> 외의 추측은 하지 않는다.
샘플의 문체를 유지하되 자연스러운 해설체로 작성하라.

[샘플]
{samples}

[DATA]
{json.dumps(data, ensure_ascii=False, indent=2)}
"""

    messages: List[ChatCompletionMessageParam] = cast(
        List[ChatCompletionMessageParam],
        [
            {
                "role": "system",
                "content": "너는 하키 경기 프리뷰를 작성하는 한국어 스포츠 기자다. 데이터 기반으로 사실적으로 쓴다.",
            },
            {"role": "user", "content": user_prompt},
        ],
    )
    return messages


# ---------------- main entry ----------------
def write_article(match_meta: Dict[str, Any]) -> Dict[str, Any]:
    """
    match_meta 예:
      {
        "flashscore_mid": "...",
        "home": "...",
        "away": "...",
        "datetime_utc": datetime|iso,
        "league_name": "...",
        "url": "https://www.flashscore.co.kr/match/hockey/.../?mid=...."
      }
    """
    if not isinstance(match_meta, dict):
        raise TypeError("match_meta must be dict")

    mid = str(match_meta.get("flashscore_mid") or "").strip()
    home = str(match_meta.get("home") or "").strip()
    away = str(match_meta.get("away") or "").strip()

    # ▶양팀 스탯: h2h
    h2h = analyze_h2h(mid) or {}

    # 직전 경기 식별
    last_meta = get_team_lastmatch(mid) or {}
    home_lm = last_meta.get("home") or {}
    away_lm = last_meta.get("away") or {}

    # ▶직전 경기 리뷰: stats
    home_stats = _stats_for(cast(Dict[str, Any], home_lm or {}))
    away_stats = _stats_for(cast(Dict[str, Any], away_lm or {}))

    # ▶실전 분석: lineups (직전 경기 기준)
    lu_home: Dict[str, Any] = {}
    lu_away: Dict[str, Any] = {}
    try:
        if home_lm.get("last_match_id") and home_lm.get("side"):
            lu_home = get_lineups(str(home_lm["last_match_id"]), str(home_lm["side"])) or {}
    except Exception:
        lu_home = {}
    try:
        if away_lm.get("last_match_id") and away_lm.get("side"):
            lu_away = get_lineups(str(away_lm["last_match_id"]), str(away_lm["side"])) or {}
    except Exception:
        lu_away = {}

    # ✅ 결장자(absences): "가공하지 않고" 원본 dict 그대로 LLM에 넘김
    match_url = _get_match_url(match_meta)
    absences: Dict[str, Any] = {"home": {"out": [], "doubt": []}, "away": {"out": [], "doubt": []}}
    try:
        if match_url:
            absences = cast(Dict[str, Any], get_absences(match_url, debug=False) or absences)
    except Exception:
        # 실패 시 그냥 빈 구조 유지 (결장자 블록 자체를 쓰지 않게 유도됨)
        pass

    data: Dict[str, Any] = {
        "match": {"id": mid, "home": home, "away": away},
        "match_url": match_url,
        "absences": absences,  # ✅ 여기만 넘기고, 블록 문자열은 만들지 않음
        "h2h": h2h,
        "last_match": {"home": home_stats, "away": away_stats},
        "lineups": {"home": lu_home, "away": lu_away},
    }

    messages = _make_messages(data)
    resp = _client.chat.completions.create(model=MODEL, messages=messages)
    body = (resp.choices[0].message.content or "").strip()
    title = f"{home} vs {away}"

    meta_out: Dict[str, Any] = {
        **{k: _to_iso(v) for k, v in match_meta.items()},
        "flashscore_mid": mid,
        "sport": "hockey",
        "home": home,
        "away": away,
        "league_name": match_meta.get("league_name"),
    }
    if isinstance(match_meta.get("datetime_utc"), datetime):
        meta_out.setdefault("kickoff_utc", match_meta["datetime_utc"].isoformat())

    return {"title": title, "body": body, "meta": meta_out}


if __name__ == "__main__":
    import sys

    if sys.stdin.isatty():
        print("provide match_meta JSON on stdin")
        raise SystemExit(0)

    mm = json.load(sys.stdin)
    out = write_article(mm)
    print(json.dumps(out, ensure_ascii=False, indent=2))
