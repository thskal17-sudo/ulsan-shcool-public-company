from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from ..config import Source
from ..http import Http
from ..models import Posting


class NotConfigured(Exception):
    """인증키 등 설정이 없어 수집을 건너뛴다 (오류가 아니라 '설정필요'로 표시)."""


class Collector(ABC):
    def __init__(self, source: Source, http: Http, today: date):
        self.source = source
        self.http = http
        self.today = today

    @abstractmethod
    def collect(self) -> list[Posting]:
        """목록에서 읽은 모든 글 (강사 공고 판별 전)."""

    def fetch_detail_text(self, posting: Posting) -> str | None:
        """마감일 추출용 상세 본문. 상세를 GET 으로 열 수 없는 소스는 None."""
        if not posting.detail_url:
            return None
        from bs4 import BeautifulSoup

        resp = self.http.get(posting.detail_url)
        soup = BeautifulSoup(resp.content, "lxml")
        for tag in soup(["script", "style", "nav", "header", "footer"]):
            tag.decompose()
        return soup.get_text(" ")
