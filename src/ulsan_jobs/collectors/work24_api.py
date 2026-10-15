"""고용24(구 워크넷) 채용정보 Open API.

요청: https://www.work24.go.kr/cm/openApi/call/wk/callOpenApiSvcInfo210L01.do
      ?authKey=...&callTp=L&returnType=XML&startPage=1&display=100&region=31000&keyword=강사
응답: <wantedRoot><total/><wanted><wantedAuthNo/><company/><title/><region/><regDt/><closeDt/>
      <wantedInfoUrl/>...</wanted>...</wantedRoot>

고용24에는 민간 학원 강사 공고가 매우 많으므로, 기관명이 공공기관 패턴에 맞는 공고만 남긴다.

목록의 제목은 길면 앞부분이 '...'로 잘려 온다('...강사 모집 (간호사면허)【경총 …'). 잘린 제목만 같은 인증키로
상세 API(callOpenApiSvcInfo210D01.do?callTp=D&wantedAuthNo=…&infoSvc=VALIDATION)를 불러 전체 제목
(wantedTitle)으로 바꾼다. 상세를 못 읽으면 목록 제목을 그대로 쓴다.
"""
from __future__ import annotations

import os
import re
import xml.etree.ElementTree as ET

import requests

from ..classify import infer_district, infer_org_type
from ..dates import find_all_dates, parse_date
from ..models import Posting
from .base import Collector, NotConfigured

ENDPOINT = "https://www.work24.go.kr/cm/openApi/call/wk/callOpenApiSvcInfo210L01.do"
DETAIL_ENDPOINT = "https://www.work24.go.kr/cm/openApi/call/wk/callOpenApiSvcInfo210D01.do"
_CUT = re.compile(r"\.\.\.|…")

DEFAULT_ORG_INCLUDE = (
    r"학교|교육청|교육지원청|교육원|공단|공사|재단|구청|군청|시청|행정복지센터|주민센터|복지관|도서관|수련관|"
    r"문화의집|여성회관|새일센터|여성인력개발센터|평생학습|평생교육|진흥원|개발원|연구원|대학교|폴리텍|청소년|"
    r"체육회|문화원|보건소|국립|시립|구립|군립|울산광역시"
)
DEFAULT_ORG_EXCLUDE = r"학원|교습소|공부방|과외|\(주\)|㈜|주식회사|유한회사"


class Work24Collector(Collector):
    def collect(self) -> list[Posting]:
        key_env = self.source.auth_env or "WORK24_API_KEY"
        auth_key = os.environ.get(key_env, "").strip()
        if not auth_key:
            raise NotConfigured(f"{key_env} 미설정 (고용24 Open API 인증키 필요)")

        params = self.source.params
        include = re.compile(self.source.options.get("org_include", DEFAULT_ORG_INCLUDE))
        exclude = re.compile(self.source.options.get("org_exclude", DEFAULT_ORG_EXCLUDE))
        max_pages = int(self.source.options.get("max_pages", 5))
        display = 100

        postings: list[Posting] = []
        for page in range(1, max_pages + 1):
            query = {
                "authKey": auth_key,
                "callTp": "L",
                "returnType": "XML",
                "startPage": page,
                "display": display,
                "region": params.get("region", "31000"),
                "keyword": params.get("keyword", "강사"),
            }
            resp = self.http.get(ENDPOINT, params=query)
            items, total = parse_work24_xml(resp.content)
            for item in items:
                company = item.get("company", "")
                if not include.search(company) or exclude.search(company):
                    continue
                if _CUT.search(item.get("title", "")):
                    item["title"] = self._full_title(auth_key, item.get("wantedAuthNo", "")) or item["title"]
                postings.append(self._to_posting(item))
            if page * display >= total or not items:
                break
        return postings

    def _full_title(self, auth_key: str, wanted_no: str) -> str:
        """상세 API 의 전체 제목. 못 읽으면 빈 문자열 (목록 제목을 그대로 쓴다)."""
        if not wanted_no:
            return ""
        query = {"authKey": auth_key, "callTp": "D", "returnType": "XML", "wantedAuthNo": wanted_no,
                 "infoSvc": "VALIDATION"}
        try:
            return parse_work24_detail_title(self.http.get(DETAIL_ENDPOINT, params=query).content)
        except (requests.RequestException, ET.ParseError):
            return ""

    def _to_posting(self, item: dict[str, str]) -> Posting:
        company = item.get("company", "")
        close_dates = find_all_dates(item.get("closeDt", ""), self.today)
        return Posting(
            source_id=self.source.id,
            title=item.get("title", "").strip(),
            url=item.get("wantedInfoUrl") or item.get("wantedMobileInfoUrl") or ENDPOINT,
            post_key=item.get("wantedAuthNo", ""),
            org_name=company,
            org_type=infer_org_type(company, "국가·공공기관"),
            district=infer_district(item.get("region", ""), "울산전체"),
            posted_date=parse_date(item.get("regDt"), self.today),
            deadline=close_dates[-1][1] if close_dates else None,  # '채용시까지 26-10-30' 같은 표기 대비
        )


def parse_work24_xml(content: bytes) -> tuple[list[dict[str, str]], int]:
    root = ET.fromstring(content)
    error = root.findtext(".//error") or root.findtext(".//message")
    if root.find(".//wanted") is None and error:
        raise RuntimeError(f"고용24 API 오류: {error.strip()}")
    total_text = root.findtext("total") or root.findtext(".//total") or "0"
    try:
        total = int(total_text.strip() or 0)
    except ValueError:
        total = 0
    items = []
    for wanted in root.iter("wanted"):
        items.append({child.tag: (child.text or "").strip() for child in wanted})
    return items, total


def parse_work24_detail_title(content: bytes) -> str:
    """상세 API 응답의 구인 제목 (wantedTitle). 없거나 이것도 잘려 있으면 빈 문자열."""
    root = ET.fromstring(content)
    title = re.sub(r"\s+", " ", root.findtext(".//wantedTitle") or "").strip()
    return "" if not title or _CUT.search(title) else title
