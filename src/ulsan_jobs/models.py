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
    label: str = ""  # 게시판의 구분/분류 값 (예: '방과후강사(관련)') - 강사 공고 판별에 함께 사용
    detail_url: str | None = None  # 마감일을 찾으러 들어갈 상세 페이지 (GET 가능할 때만)
    info: dict | None = None  # 공고문에서 찾은 수업 일정·대상 등 (jobinfo.extract_info). None = 아직 안 봄

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
    state: str = "정상"  # 정상 / 오류 / 0건 / 접속불가 / 시간초과 / 설정필요 / 미구현
    fetched: int = 0  # 목록에서 읽은 글 수
    matched: int = 0  # 강사 공고로 판별된 수
    new: int = 0
    error: str = ""
    samples: list[str] = field(default_factory=list)

    @property
    def is_problem(self) -> bool:
        """확인이 필요한 실패 (사이트 개편·서버 오류 등으로 공고를 놓쳤을 수 있음)."""
        return self.state in ("오류", "0건", "시간초과")

    @property
    def is_unreachable(self) -> bool:
        """수집 서버에서 접속 자체가 안 됨 (해외 접속 차단·일시 장애). 다른 서버·다음 실행에서 다시 수집."""
        return self.state == "접속불가"

    @property
    def needs_other_server(self) -> bool:
        """다른 수집 서버에서 다시 수집해 볼 만한가 (접속불가, 또는 시간이 모자라 건너뜀)."""
        return self.state in ("접속불가", "시간초과")
