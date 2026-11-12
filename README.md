# MatchPreview

**FastAPI · SQLite · Python | 사용자별 경기 프리뷰 저장·조회 서비스**

외부에서 전달받은 스포츠 경기 프리뷰를 HTTP API로 등록하고, 사용자별 파일 시스템과 SQLite DB에 저장한 뒤 웹에서 날짜·종목·리그별로 탐색하도록 구현한 개인 프로젝트입니다.

> **담당 범위:** API, 데이터 저장 구조, 조회 화면, 환경변수 설정
>
> **중점적으로 고민한 문제:** 사용자별 데이터 분리, 조회 경로 설계, 발행 요청 검증

## 문제와 해결 방식

| 문제 | 구현 방식 | 고려할 점 |
| --- | --- | --- |
| 여러 사용자의 프리뷰가 혼재할 수 있음 | 사용자별 디렉터리와 `posts.db`를 분리 | 사용자 수 증가 시 DB 파일·스키마 관리 비용이 늘어남 |
| 날짜·리그·경기별 탐색 필요 | `date_key` 단일 인덱스, 월별 표현식 인덱스, `(date_key, sport, league_slug, title_slug)` 복합 인덱스 구성 | 성능 향상 수치는 별도로 측정하지 않음 |
| 임의의 발행 요청 방지 필요 | `/internal/publish/{user}`에서 `X-API-KEY` 헤더 확인 | 발행 API에 적용되는 검사이며 사용자 로그인 인증과는 다름 |
| 등록되지 않은 사용자 경로 차단 | `ACTIVE_USERS` 화이트리스트 검사 | **사용자 본인 여부를 확인하는 인증·인가 기능은 아님** |
| 웹 조회와 파일 보관 동시 지원 | 본문·메타데이터를 SQLite에 저장하고 HTML/TXT/JSON 파일도 생성 | 파일과 DB의 저장을 하나의 트랜잭션으로 묶지는 않음 |

## 주요 기능

- `POST /internal/publish/{user}`: 프리뷰 등록, 요청 데이터 검사, API Key 검증
- `/{user}/browse`: 날짜 → 종목 → 리그 → 경기 제목 순으로 탐색
- `/{user}/s`: 작성일 기준 월별 글 수 집계
- `GET /health`: 서버 상태 확인
- 파일 저장: `index.html`, `article.txt`, `meta.json` / SQLite: `posts` 테이블

## 기술 스택

- **Backend:** Python, FastAPI, Uvicorn
- **Database:** SQLite (표준 `sqlite3` 모듈), SQL 인덱스
- **기타:** HTML, 환경변수(`python-dotenv`)

## 핵심 처리 흐름

```text
내부 발행 요청
  → 사용자 화이트리스트 검사
  → X-API-KEY 검증
  → 제목·본문 및 메타데이터 확인
  → 사용자별 파일 작성
  → 사용자별 SQLite DB 기록
  → 브라우징 URL 반환
```

파일 저장과 DB 저장이 순차적으로 이루어지므로 중간 장애 시 데이터가 불일치할 수 있습니다. 개선 과제로 남겨 두었습니다.

## 로컬 실행

```bash
python -m venv .venv
# Windows PowerShell: .\.venv\Scripts\Activate.ps1
# macOS/Linux: source .venv/bin/activate
pip install fastapi uvicorn python-dotenv
```

루트에 `.env` 파일을 작성합니다. **실제 API 키와 사용자 데이터는 Git에 커밋하지 않습니다.**

```dotenv
SITE_API_KEY=replace-with-a-secret
ACTIVE_USERS=user1,user2
VIEW_BASE=http://127.0.0.1:8000
LOG_LEVEL=INFO
POLL_SECONDS=3600
```

```bash
uvicorn app:app --reload
```

- 상태 확인: `http://127.0.0.1:8000/health`
- 조회: `http://127.0.0.1:8000/user1/browse`

### 프리뷰 등록 예시

```http
POST /internal/publish/user1
X-API-KEY: replace-with-a-secret
Content-Type: application/json

{
  "title": "경기 프리뷰",
  "body": "프리뷰 내용",
  "meta": {
    "sport": "football",
    "league_name": "Premier League",
    "kickoff": "2026-06-29T12:00:00Z"
  }
}
```

## 코드 위치

| 파일 | 설명 |
| --- | --- |
| [`app.py`](app.py) | FastAPI 라우트, 사용자별 SQLite 및 파일 저장, 조회 처리 |
| [`main.py`](main.py) | 별도 사용자별 실행 작업을 위한 진입점 |

## 현재 한계와 개선 방향

- 사용자 화이트리스트는 인증 수단이 아니므로, 다중 사용자 서비스로 확장하려면 인증·인가가 필요합니다.
- 파일 시스템과 DB를 함께 쓰는 구조에는 부분 실패 시 복구 방안이 필요합니다.
- 사용자별 별도 SQLite를 하나의 DB에서 사용자 ID로 구분하는 구조와 비교해 운영 복잡도를 검토할 계획입니다.
- 조회 성능은 실제 데이터량을 늘려 `EXPLAIN QUERY PLAN` 등으로 검증할 수 있습니다.

**이 저장소는 학습 및 개인 개발 프로젝트이며, 운영 환경에서의 보안·성능 보장을 의미하지 않습니다.**
