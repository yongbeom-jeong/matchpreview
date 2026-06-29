# MatchPreview

스포츠 경기 프리뷰 글을 사용자별로 저장하고 조회할 수 있는 FastAPI 기반 자동화 프로젝트입니다.
생성된 경기 프리뷰 데이터를 내부 API로 받아 파일과 SQLite DB에 동시에 저장하고, 날짜/종목/리그/경기별로 탐색할 수 있는 웹 페이지를 제공합니다.

## 주요 기능

* FastAPI 기반 웹 서버
* 사용자별 데이터 분리 저장
* 내부 발행 API를 통한 경기 프리뷰 등록
* SQLite를 이용한 게시글 메타데이터 및 본문 저장
* HTML 파일과 텍스트 파일 동시 생성
* 날짜, 종목, 리그, 경기 제목 기준 게시글 탐색
* 월별 작성 글 수 집계 페이지 제공
* API Key 기반 내부 발행 요청 보호

## 기술 스택

* Python
* FastAPI
* SQLite
* HTML
* dotenv
* Uvicorn

## 프로젝트 구조

```text
matchpreview/
├── app.py          # FastAPI 서버 및 게시글 조회/저장 API
├── main.py         # 사용자별 작업 실행 런처
├── __init__.py
└── .gitignore
```

## 실행 방법

### 1. 패키지 설치

```bash
pip install fastapi uvicorn python-dotenv
```

### 2. 환경 변수 설정

프로젝트 루트에 `.env` 파일을 생성하고 다음 값을 설정합니다.

```env
SITE_API_KEY=your_api_key
ACTIVE_USERS=user1,user2
VIEW_BASE=http://localhost:8000
LOG_LEVEL=INFO
POLL_SECONDS=3600
```

### 3. 서버 실행

```bash
uvicorn app:app --reload
```

서버 실행 후 다음 주소에서 상태를 확인할 수 있습니다.

```text
GET /health
```

## API 예시

### 게시글 발행

```http
POST /internal/publish/{user}
X-API-KEY: your_api_key
Content-Type: application/json
```

요청 예시:

```json
{
  "title": "토트넘 vs 아스널 경기 프리뷰",
  "body": "경기 프리뷰 본문입니다.",
  "meta": {
    "sport": "football",
    "league_name": "Premier League",
    "kickoff": "2026-06-29T12:00:00Z"
  }
}
```

## 조회 경로

```text
/{user}/browse
/{user}/browse/{date_key}
/{user}/browse/{date_key}/{sport}
/{user}/browse/{date_key}/{sport}/{league_slug}
/{user}/browse/{date_key}/{sport}/{league_slug}/{title_slug}
```

## 개발 목적

스포츠 경기 데이터를 기반으로 프리뷰 글을 자동 생성하고, 이를 사용자별로 저장·관리하는 자동화 시스템을 구현하기 위해 만든 프로젝트입니다.
단순히 글을 생성하는 것에서 끝나지 않고, 생성된 결과물을 API, 파일 시스템, 데이터베이스, 웹 페이지 조회 흐름까지 연결해 실제 서비스 형태로 운영할 수 있도록 구성하는 데 초점을 두었습니다.

## 향후 개선 방향

* 경기 데이터 수집 모듈 정리
* AI 기반 프리뷰 생성 로직 모듈화
* 관리자 페이지 추가
* 배포 환경 구성
* Docker 기반 실행 환경 추가
* AWS 배포 및 운영 자동화
