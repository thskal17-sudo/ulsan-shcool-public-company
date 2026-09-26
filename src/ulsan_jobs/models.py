from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> date:
    return now_kst().date()


@dataclass
class Posting:
    """수집기가 돌려주는 공고 한 건 (모든 소스 공통 형식)."""

    source_id: str
    title: str
    url: str
    post_key: str = ""  # 원문 게시글 번호 등 소스 안에서 공고를 식별하는 값 (없으면 url)
    org_name: str = ""
    org_type: str = ""
    district: str = ""
    posted_date: date | None = None
    deadline: date | None = None
    category: str = ""
    status: str = "모집중"
    detail_url: str | None = None  # 마감일을 찾으러 들어갈 상세 페이지 (GET 가능할 때만)

    @property
    def uid(self) -> str:
        key = f"{self.source_id}|{self.post_key or self.url}"
        return hashlib.sha1(key.encode("utf-8")).hexdigest()[:20]


@dataclass
class SourceResult:
    """소스 하나를 수집한 결과 (수집현황 시트·경고용)."""

    source_id: str
    name: str
    org_type: str = ""
    url: str = ""
    state: str = "정상"  # 정상 / 오류 / 0건 / 설정필요 / 미구현
    fetched: int = 0  # 목록에서 읽은 글 수
    matched: int = 0  # 강사 공고로 판별된 수
    new: int = 0
    error: str = ""
    samples: list[str] = field(default_factory=list)

    @property
    def is_problem(self) -> bool:
        return self.state in ("오류", "0건")
