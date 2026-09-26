"""울산방과후학교 온라인지원시스템 - 개인위탁 강사모집 (https://afschool.use.go.kr/usPrivateApply).

목록이 표가 아니라 <ul class="ul_table"><li><a href=".../usPrivateApply/467">…</a></li> 형태이고,
글 한 건의 텍스트가 다음처럼 이어져 있다.
    8 (6차 공고)2026학년도 초등방과후 … 외부강사 모집공고 (접수종료) 위탁방법 : 개인
    학교명 : 덕신초등학교 등록일 : 2026-09-21 접수기간 : 2026-09-21 09시 00분 ~ 2026-09-24 13시 00분
"""
from __future__ import annotations

import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..classify import infer_district
from ..dates import extract_deadline, parse_date
from ..models import Posting
from .base import Collector

_ITEM = re.compile(
    r"^\s*(?:\d+\s+)?(?P<title>.+?)\s*(?:\((?P<state>접수[^)]*)\))?\s*위탁방법\s*:",
)
_FIELD = r"{name}\s*:\s*(?P<v>.+?)(?=\s+(?:위탁방법|학교명|등록일|접수기간|첨부파일)\s*:?|\s*$)"


def _field(text: str, name: str) -> str:
    m = re.search(_FIELD.format(name=name), text)
    return m.group("v").strip() if m else ""


def parse_afschool(html: bytes | str, base_url: str, today) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    items = []
    for li in soup.select("ul.ul_table > li"):
        a = li.find("a", href=True)
        if a is None:
            continue
        text = re.sub(r"\s+", " ", li.get_text(" ")).strip()
        m = _ITEM.match(text)
        title = m.group("title").strip() if m else re.sub(r"^\d+\s+", "", text)[:120]
        period = _field(text, "접수기간")
        items.append(
            {
                "title": title,
                "state": (m.group("state") or "") if m else "",
                "url": urljoin(base_url, a["href"]),
                "school": _field(text, "학교명"),
                "posted": parse_date(_field(text, "등록일"), today),
                "deadline": extract_deadline(period, today),
            }
        )
    return items


class AfschoolCollector(Collector):
    def collect(self) -> list[Posting]:
        resp = self.http.get(self.source.url)
        postings = []
        for item in parse_afschool(resp.content, resp.url, self.today):
            key = item["url"].rstrip("/").rsplit("/", 1)[-1]
            postings.append(
                Posting(
                    source_id=self.source.id,
                    title=item["title"],
                    url=item["url"],
                    post_key=key,
                    org_name=item["school"],
                    org_type=self.source.org_type,
                    district=infer_district(item["school"], self.source.district),
                    posted_date=item["posted"],
                    deadline=item["deadline"],
                    label=item["state"],
                    detail_url=item["url"],
                )
            )
        return postings
