# 울산 공공기관 강사 구인공고 알리미

울산 지역 공공기관(교육청·학교, 시설공단 체육시설, 국가·공공기관 등)의 강사 구인공고를 매일 아침 모아
**엑셀 파일로 정리해 이메일로 보내주는** 프로젝트입니다. GitHub Actions 에서 무료로 돌아갑니다.

- 설계 문서: [docs/DESIGN.md](docs/DESIGN.md)
- 수집 대상 목록: [config/sources.yaml](config/sources.yaml)
- 강사 공고 판별·분류 규칙: [config/keywords.yaml](config/keywords.yaml)

## 받게 되는 것

매일 07시(KST) 무렵 이런 메일이 옵니다.

- 제목: `[울산 강사구인] 9/27(일) 신규 12건 · 마감임박 5건`
- 본문: 신규 공고 표(마감 D-day, 분야, 기관, 제목 링크), 수집에 실패한 사이트 경고
- 첨부: `울산_강사구인_2026-09-27.xlsx`
  - **신규**: 지난 메일 이후 새로 올라온 공고
  - **마감임박**: 마감 3일 이내
  - **진행중 전체**: 마감 전인 공고 전부
  - **수집현황**: 사이트별 수집 성공/실패

## 현재 수집하는 곳 (1단계)

| 기관 | 게시판 |
|---|---|
| 울산교육청 통합인력풀 | 일반채용공고(학교 방과후 개인위탁 강사 등), 온라인채용공고(직종: 강사) |
| 울산교육청 방과후·늘봄지원센터 | 채용공고(업체위탁) |
| 울산방과후학교 온라인지원시스템 | 개인위탁 강사모집 |
| 울산시설공단 | 강습위탁, 기간제(단시간) 근로자, 정규직 공고 |
| 고용24(워크넷) | 울산 지역 '강사' 공고 중 공공기관 것만 (API 인증키 등록 시) |

구·군청, 구·군 시설관리공단, 여성회관, 청소년·도서관 기관 등은 2단계에서 추가합니다.

## 설정 방법 (처음 한 번)

### 1. Gmail 앱 비밀번호 만들기

1. 보내는 데 쓸 Google 계정에 [2단계 인증](https://myaccount.google.com/signinoptions/two-step-verification)을 켭니다.
2. [앱 비밀번호](https://myaccount.google.com/apppasswords)에서 새 앱 비밀번호(16자리)를 만듭니다.

### 2. GitHub Secrets 등록

저장소 **Settings → Secrets and variables → Actions → New repository secret** 에서 등록합니다.

| 이름 | 값 | 필수 |
|---|---|---|
| `SMTP_USER` | 보내는 Gmail 주소 | ✅ |
| `SMTP_APP_PASSWORD` | 위에서 만든 앱 비밀번호 16자리 | ✅ |
| `MAIL_TO` | 받는 주소 (여러 명이면 쉼표로 구분). 비우면 `SMTP_USER` 로 보냄 | |
| `WORK24_API_KEY` | [고용24 Open API](https://www.work24.go.kr/cm/e/a/0110/selectOpenApiIntro.do) 채용정보 인증키 | |

`WORK24_API_KEY` 가 없으면 고용24만 건너뛰고 나머지는 정상 수집합니다 (수집현황에 '설정필요'로 표시).

### 3. 시험 실행

**Actions → 매일 강사 공고 수집·메일 발송 → Run workflow** 를 누르면 바로 한 번 실행됩니다.
메일이 오면 설정 완료이고, 이후 매일 자동으로 실행됩니다.

- 첫 메일에는 최근 45일 안에 올라온, 마감 전 공고가 모두 신규로 들어갑니다.
- 실행이 실패하면 GitHub 가 저장소 주인에게 실패 알림 메일을 보냅니다.

## 동작 방식

```
sources.yaml 의 게시판·API ──▶ 수집 ──▶ 강사 공고 판별·분야 분류 ──▶ DB(신규 판정)
                                                                    │
                        메일(엑셀 첨부) ◀── 엑셀 4개 시트 ◀──────────┘
```

- 수집 기록(SQLite)은 저장소의 `state` 브랜치에 저장됩니다. 지우면 다음 실행 때 모든 공고가 다시 신규로 옵니다.
- 메일 발송에 실패한 날의 공고는 다음 날 다시 신규로 보내집니다.
- 한 사이트가 고장 나도 나머지는 계속 수집하고, 메일 상단에 경고가 표시됩니다.

## 규칙 바꾸기

- **강사 공고로 볼 단어·뺄 단어**: `config/keywords.yaml` 의 `include` / `exclude` / `result_notice`
- **합격자 발표 같은 결과공고도 받기**: `keep_result_notices: true`
- **게시판 추가**: `config/sources.yaml` 에 항목 추가. 구조를 모르면
  **Actions → 사이트 진단 (probe) → Run workflow** 에서 `--no-reach --url <목록 주소>` 로 먼저 확인합니다.

## 직접 실행 (개발용)

```bash
pip install -r requirements-dev.txt
python -m pytest                                            # 테스트
PYTHONPATH=src python -m ulsan_jobs check-source uic_lesson  # 게시판 하나 시험 수집
PYTHONPATH=src python -m ulsan_jobs run --no-mail           # 전체 수집, 엑셀만 out/ 에 생성
PYTHONPATH=src python -m ulsan_jobs send-test-mail          # 메일 설정 확인 (SMTP_* 환경변수 필요)
```
