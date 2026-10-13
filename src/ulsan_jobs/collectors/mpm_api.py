"""나라일터 공공취업정보 Open API (인사혁신처).

공공데이터포털 15156780 '인사혁신처_공공취업정보 조회 서비스'.
요청: https://apis.data.go.kr/1760000/PblJobService/getList
      ?serviceKey=...&pageNo=1&numOfRows=100&Pblanc_ty=&Instt_se=&Sort_order=desc
      &Begin_de=2026-09-29&End_de=2026-10-13
응답: <response><header><resultCode>00</resultCode>...</header><body><items><item>
      <areacode/><begindate/><enddate/><idx/><insttname/><regdate/><title/><type01/><type02/>
      </item></items><totalCount/></body></response>

나라일터(gojobs.go.kr)에 국가기관·지자체·교육청·공공기관이 올린 공고가 그대로 온다. 사이트는
해외 접속이 자주 막히지만 공공데이터포털은 막히지 않는다 (2026-10-12 수집 서버에서 확인).
- 키워드(Kwrd)·정렬(Sort_order)은 무시되고 오래된 글부터 오므로, 등록일(Begin_de~End_de)로
  최근 며칠만 받아 지역코드로 울산 공고를 고른다. 날짜 조건은 제대로 걸린다.
- areacode 는 시군구 코드(울산 31110 중구 … 31710 울주군)이고 여러 곳이면 쉼표로 이어진다.
  00000(미지정)이면 기관명에 '울산'이 있을 때만 울산 공고로 본다.
- idx 는 나라일터 상세 주소의 empmnsn 과 같아서, 게시판을 읽던 때 저장한 공고와 이어진다.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from datetime import timedelta

from ..classify import infer_district, infer_org_type
from ..models import Posting
from . import datagokr
from .base import Collector

ENDPOINT = "https://apis.data.go.kr/1760000/PblJobService/getList"
VIEW_URL = "https://www.gojobs.go.kr/apmView.do?empmnsn={idx}&searchJobsecode=020&menuNo=401&selMenuNo=400"

# 울산 구·군 시군구 코드 (행정표준코드)
ULSAN_GU = {"31110": "중구", "31140": "남구", "31170": "동구", "31200": "북구", "31710": "울주군"}


class MpmJobCollector(Collector):
    def collect(self) -> list[Posting]:
        key = datagokr.service_key(self.source)
        prefix = str(self.source.params.get("areacode_prefix", "31"))
        region = str(self.source.params.get("region_name", "울산"))
        days = int(self.source.options.get("days", 14))
        max_pages = int(self.source.options.get("max_pages", 20))
        rows = 100
        query = {
            "numOfRows": rows,
            "Pblanc_ty": "",  # 전체 공고유형
            "Instt_se": "",  # 전체 기관구분
            "Sort_order": "desc",
            "Begin_de": (self.today - timedelta(days=days)).isoformat(),
            "End_de": self.today.isoformat(),
        }
        postings: list[Posting] = []
        for page in range(1, max_pages + 1):
            resp = datagokr.get(self.http, ENDPOINT, {**query, "pageNo": page}, key)
            items, total = parse_mpm_xml(resp.content)
            postings.extend(
                self._to_posting(i) for i in items if i.get("title") and in_area(i, prefix, region)
            )
            if not items or page * rows >= total:
                break
        return postings

    def _to_posting(self, item: dict[str, str]) -> Posting:
        org = item.get("insttname", "")
        idx = item.get("idx", "")
        codes = [c.strip() for c in item.get("areacode", "").split(",") if c.strip()]
        gu = {ULSAN_GU[c] for c in codes if c in ULSAN_GU}
        url = VIEW_URL.format(idx=idx)
        return Posting(
            source_id=self.source.id,
            title=item.get("title", "").strip(),
            url=url,
            post_key=idx,
            org_name=org,
            org_type=infer_org_type(org, self.source.org_type),
            district=gu.pop() if len(gu) == 1 else infer_district(org, self.source.district),
            posted_date=datagokr.ymd(item.get("regdate")),
            deadline=datagokr.ymd(item.get("enddate")),
            detail_url=url,
        )


def in_area(item: dict[str, str], prefix: str = "31", region: str = "울산") -> bool:
    codes = [c.strip() for c in item.get("areacode", "").split(",") if c.strip()]
    if any(c.startswith(prefix) for c in codes):
        return True
    unknown = all(c == "00000" for c in codes)
    return unknown and region in item.get("insttname", "")


def parse_mpm_xml(content: bytes) -> tuple[list[dict[str, str]], int]:
    """(item 목록, 전체 건수). 오류 응답(resultCode 가 00 이 아님)은 예외."""
    root = ET.fromstring(content)
    code = (root.findtext(".//resultCode") or root.findtext(".//returnReasonCode") or "").strip()
    if code not in ("00", "0"):
        msg = (root.findtext(".//resultMsg") or root.findtext(".//returnAuthMsg") or root.findtext(".//errMsg") or "").strip()
        raise RuntimeError(f"나라일터 API 오류: {code} {msg}")
    items = [{child.tag: (child.text or "").strip() for child in item} for item in root.iter("item")]
    return items, int(root.findtext(".//totalCount") or 0)
