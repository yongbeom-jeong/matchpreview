# -*- coding: utf-8 -*-
# basketball/writer.py
#
# 변경점(이번):
# - ✅ 결장자(absences) 추가: Flashscore match_url로 absences 모듈 호출
# - ✅ LLM이 샘플처럼 결장자 4블록을 "본문 최상단"에 출력하도록 프롬프트 지시
# - ✅ match_meta에서 url/flashscore_url을 받아 사용 (없으면 결장자 생략)

from __future__ import annotations

import glob
import json
import os
import re
from dataclasses import fields, is_dataclass
from datetime import datetime
from typing import Any, Dict, List, cast

from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam

from .models import Fixture
from .standings import fetch_standings
from .h2h import fetch_h2h
from .quarter import fetch_quarter_summary
from .player import fetch_team_top_players
from .lineup import fetch_team_lineup

# ✅ absences import (패키지/단독 실행 모두 대비)
try:
    from .absences import get_absences
except Exception:  # pragma: no cover
    from absences import get_absences  # type: ignore

MODEL = os.getenv("OPENAI_MODEL", "gpt-5-mini")
_client = OpenAI()


def _to_jsonable(x: Any) -> Any:
    """
    dataclass(slots/frozen 포함)도 안전하게 JSON-serializable로 변환.
    - asdict()를 사용하지 않아서 IDE 타입 경고(빨간줄) 없음
    """
    if x is None:
        return None
    if isinstance(x, (str, int, float, bool)):
        return x
    if isinstance(x, datetime):
        return x.isoformat()

    # ✅ dataclass 인스턴스 처리 (class 자체가 들어오는 경우는 제외)
    if is_dataclass(x) and not isinstance(x, type):
        return {f.name: _to_jsonable(getattr(x, f.name)) for f in fields(x)}

    if isinstance(x, dict):
        return {str(k): _to_jsonable(v) for k, v in x.items()}

    if isinstance(x, (list, tuple)):
        return [_to_jsonable(v) for v in x]

    return str(x)


def _parse_dt(v: Any) -> datetime:
    if isinstance(v, datetime):
        return v
    s = str(v or "").strip()
    if not s:
        return datetime.utcnow()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s)
    except Exception:
        return datetime.utcnow()


def _load_samples(max_files: int = 3) -> str:
    base_dir = os.path.dirname(__file__) if "__file__" in globals() else os.getcwd()
    sample_dir = os.path.join(base_dir, "sample")

    env_max = os.getenv("SAMPLES_MAX")
    if env_max:
        try:
            max_files = max(0, int(env_max))
        except Exception:
            pass

    parts: List[str] = []
    for fp in sorted(glob.glob(os.path.join(sample_dir, "*.txt")))[:max_files]:
        try:
            with open(fp, "r", encoding="utf-8") as f:
                t = f.read().strip()
                if t:
                    parts.append(t)
        except Exception:
            continue

    return ("\n\n---\n\n".join(parts)).strip()


def _get_match_url(match_meta: Dict[str, Any]) -> str:
    """schedule/publisher가 채워준 url/flashscore_url을 그대로 사용. 없으면 ''."""
    return str(match_meta.get("url") or match_meta.get("flashscore_url") or "").strip()


def _make_prompt(data: Dict[str, Any]) -> str:
    samples = _load_samples()

    home = str(data.get("match", {}).get("home", "") or "")
    away = str(data.get("match", {}).get("away", "") or "")

    hs = data.get("standings", {}).get("home", {})
    as_ = data.get("standings", {}).get("away", {})

    def _w(obj):
        if isinstance(obj, dict):
            return obj.get("wins", None)
        return getattr(obj, "wins", None)

    def _l(obj):
        if isinstance(obj, dict):
            return obj.get("losses", None)
        return getattr(obj, "losses", None)

    home_w, home_l = _w(hs), _l(hs)
    away_w, away_l = _w(as_), _l(as_)

    home_record = f"{home_w}승 {home_l}패" if home_w is not None and home_l is not None else ""
    away_record = f"{away_w}승 {away_l}패" if away_w is not None and away_l is not None else ""

    return f"""너는 한국어 농구 경기 프리뷰를 쓰는 스포츠 기자다.

원칙:
- 아래 [샘플]은 '어조/문장 길이/리듬' 참고용이다. 샘플의 문장 구조/표현/마무리 문구를 그대로 재사용하지 마라.
- 내용은 반드시 [DATA]에 있는 사실만 사용하라. 불확실한 내용은 단정하지 말고 자연스럽게 생략하라.
- 표/불릿/번호목록 없이 문단으로만 작성하라.
- 섹션은 2개만 사용하라: ▶직전 경기 리뷰, ▶경기 총평
- 각 문단의 첫 문장은 서로 다른 방식으로 시작하라(동일한 도입 패턴 반복 금지).

형식:
- 글 맨 위에 "{home} vs {away}" 같은 제목 줄을 쓰지 마라. (제목은 시스템에서 처리된다)
- ▶직전 경기 리뷰에서는 팀명을 다음 형식으로 ‘단독 줄’로 적어라:
  {home}{f" ({home_record})" if home_record else ""}
  {away}{f" ({away_record})" if away_record else ""}
  (승패 정보가 DATA에 없으면 괄호 자체를 생략해도 된다)

[결장자 출력 규칙 - 매우 중요]
- <DATA.absences> 구조는 다음과 같다:
  - absences.home.out / absences.home.doubt
  - absences.away.out / absences.away.doubt
  각 리스트 원소는 {{name, reason}} 형태다.
- 결장자 정보가 "하나라도" 있으면, 본문 최상단(▶직전 경기 리뷰 위)에 아래 4블록을 "그대로" 작성하라.
  (줄바꿈/구분은 아래 예시와 동일)
    🚨홈팀 결장 유력🚨
    이름(사유), 이름(사유)

    🏥홈팀 결장 의심🏥
    없음

    🚨원정팀 결장 유력🚨
    이름(사유)

    🏥원정팀 결장 의심🏥
    이름(사유)
- 해당 카테고리에 선수가 없으면 그 줄에는 반드시 "없음"이라고만 쓰고 끝내라.
- 결장자가 4카테고리 모두 비어 있으면, 결장자 4블록을 아예 쓰지 마라(완전 생략).

선수 이름 표기(매우 중요):
- 선수 이름에 이니셜/약어가 붙어 있으면 반드시 제거하고 이름만 표기하라.
  예) "J. 허프" -> "허프", "P. 시아캄" -> "시아캄", "La. 볼" -> "볼", "C. 섹스턴" -> "섹스턴"
- 문장 어디에서든 동일하게 적용하라(득점자/라인업/평점/총평/결장자 전부).
- 결장자 사유는 괄호로 붙여라: 이름(사유)
- 사유가 비어 있으면 괄호를 생략해도 된다.

섹션 가이드:
▶직전 경기 리뷰:
- 각 팀의 직전 경기 내용을 구체적으로 쓴다(스코어/쿼터 흐름/주요 선수 기록).
- 해석은 과하지 않게 1~2문장만 붙여라.

▶경기 총평:
- 리뷰에서 쓴 쿼터 흐름/점수 전개를 그대로 다시 설명하지 마라.
- 대신 매치업/변수/관전 포인트 중심으로 쓴다(예: 전술적 상성, 라인업 매치, 리바운드·턴오버 리스크, 벤치 뎁스, 페이스 조절).
- 마지막 문장은 뻔한 클리셰로 끝내지 말고 새로운 한 문장으로 마무리하라.
  (예: "4쿼터 운영이 승부를 가를 관건이다", "초반 주도권을 잡는 쪽이 우위를 점할 가능성이 있다", "종합하면" 같은 표현 지양)

[샘플]
{samples}

[DATA]
{json.dumps(_to_jsonable(data), ensure_ascii=False, indent=2)}
"""


def _make_messages(data: Dict[str, Any]) -> List[ChatCompletionMessageParam]:
    prompt = _make_prompt(data)
    return cast(
        List[ChatCompletionMessageParam],
        [
            {
                "role": "system",
                "content": "너는 데이터 기반으로만 글을 쓰는 한국어 스포츠 기자다. 샘플의 톤을 따르되 자연스러운 문단형으로 작성한다.",
            },
            {"role": "user", "content": prompt},
        ],
    )


def _strip_weird_lists(text: str) -> str:
    """
    프롬프트를 느슨하게 했기 때문에,
    혹시 불릿이 섞이면 '마커'만 최소 제거하는 안전장치.
    (결장자 블록은 불릿이 아니라 콤마/줄바꿈이므로 영향 없음)
    """
    if not text:
        return text
    text = re.sub(r"(?m)^\s*[-•]\s+", "", text)
    text = re.sub(r"(?m)^\s*\d+\.\s+", "", text)
    return text.strip()


def write_article(match_meta: Dict[str, Any]) -> Dict[str, Any]:
    mid = str(match_meta.get("flashscore_mid") or match_meta.get("mid") or "").strip()
    home = str(match_meta.get("home") or "").strip()
    away = str(match_meta.get("away") or "").strip()
    dt_utc = _parse_dt(match_meta.get("datetime_utc"))

    fixture = Fixture(
        flashscore_mid=mid,
        datetime_utc=dt_utc,
        home=home,
        away=away,
        league_name=str(match_meta.get("league_name") or ""),
        sport="basketball",
    )

    # standings
    home_st, away_st = fetch_standings(fixture)

    # h2h (각 팀 직전 경기 요약)
    h2h = fetch_h2h(fixture)

    # last match details (quarter / top players / lineup(+rating))
    home_pack: Dict[str, Any] = {}
    away_pack: Dict[str, Any] = {}

    if h2h:
        home_last = getattr(h2h, "home", None)
        away_last = getattr(h2h, "away", None)

        if home_last:
            home_is_home = bool(getattr(home_last, "is_home", True))
            last_mid = getattr(home_last, "last_mid", "") or ""
            home_pack = {
                "last": home_last,
                "quarter": fetch_quarter_summary(home_last),
                "top_players": fetch_team_top_players(last_mid, home),
                "lineup": fetch_team_lineup(last_mid, home, "home" if home_is_home else "away"),
            }

        if away_last:
            away_is_home = bool(getattr(away_last, "is_home", True))
            last_mid = getattr(away_last, "last_mid", "") or ""
            away_pack = {
                "last": away_last,
                "quarter": fetch_quarter_summary(away_last),
                "top_players": fetch_team_top_players(last_mid, away),
                "lineup": fetch_team_lineup(last_mid, away, "home" if away_is_home else "away"),
            }

    # ✅ 결장자(absences): match_url이 있을 때만 가져오고, 실패하면 빈 구조 유지
    match_url = _get_match_url(match_meta)
    absences: Dict[str, Any] = {"home": {"out": [], "doubt": []}, "away": {"out": [], "doubt": []}}
    try:
        if match_url:
            absences = cast(Dict[str, Any], get_absences(match_url, debug=False) or absences)
    except Exception:
        pass

    data: Dict[str, Any] = {
        "match": {
            "id": mid,
            "home": home,
            "away": away,
            "league_name": fixture.league_name,
            "datetime_utc": dt_utc,
        },
        "match_url": match_url,
        "absences": absences,
        "standings": {"home": home_st, "away": away_st},
        "h2h": h2h,
        "last_match_detail": {"home": home_pack, "away": away_pack},
    }

    messages = _make_messages(data)

    # temperature 미지원 모델 대비: 파라미터 넣지 않음
    resp = _client.chat.completions.create(
        model=MODEL,
        messages=messages,
    )

    body = (resp.choices[0].message.content or "").strip()
    body = _strip_weird_lists(body)

    title = f"{home} vs {away}"

    meta_out: Dict[str, Any] = {
        **{k: _to_jsonable(v) for k, v in match_meta.items()},
        "flashscore_mid": mid,
        "sport": "basketball",
        "home": home,
        "away": away,
        "league_name": fixture.league_name,
        "kickoff_utc": _to_jsonable(dt_utc),
        "url": match_url or match_meta.get("url") or match_meta.get("flashscore_url"),
    }

    return {"title": title, "body": body, "meta": meta_out}


if __name__ == "__main__":
    """
    테스트:
      echo '{"flashscore_mid":"CAGjsHPp","datetime_utc":"2026-01-09T00:00:00+00:00","home":"샬럿 호넷츠","away":"인디애나 페이셔스","league_name":"NBA","url":"https://www.flashscore.co.kr/match/basketball/.../?mid=XXXX"}' \
        | python -m r8kx2v.basketball.writer
    """
    import sys

    if sys.stdin.isatty():
        print("provide match_meta JSON on stdin")
        raise SystemExit(0)

    mm = json.load(sys.stdin)
    out = write_article(mm)
    print(json.dumps(_to_jsonable(out), ensure_ascii=False, indent=2))
