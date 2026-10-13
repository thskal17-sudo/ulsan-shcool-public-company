"""잡알리오 공공기관 채용정보 Open API (재정경제부).

공공데이터포털 15125273 '공공기관 채용정보 조회서비스'.
요청: https://apis.data.go.kr/1051000/recruitment/list
      ?serviceKey=...&resultType=json&ongoingYn=Y&workRgnLst=R3016&pageNo=1&numOfRows=100
응답: {"resultCode":200,"resultMsg":"성공했습니다.","totalCount":20,"result":[{"recrutPblntSn":305916,
      "instNm":"…","recrutPbancTtl":"…","pbancBgngYmd":"20261012","pbancEndYmd":"20261019",
      "workRgnNmLst":"서울,울산,인천","hireTypeNmLst":"비정규직","srcUrl":"…",…}]}

job.alio.go.kr 은 해외 접속이 자주 막히지만 공공데이터포털은 막히지 않는다 (2026-10-12 확인).
- workRgnLst 근무지역 코드: R3016 울산 (검색 폼의 근무지 값과 같다. R3014 부산 · R3022 경남). ongoingYn=Y 는 접수 중인 공고만.
- recrutPblntSn 은 잡알리오 상세 주소(recruitview.do?idx=)의 idx 와 같아서, 게시판을 읽던 때
  저장한 공고와 이어진다.
"""
from __future__ import annotations

import json

from ..classify import infer_district, infer_org_type
from ..models import Posting
from . import datagokr
from .base import Collector

ENDPOINT = "https://apis.data.go.kr/1051000/recruitment/list"
VIEW_URL = "https://job.alio.go.kr/recruitview.do?idx={sn}"


class AlioJobCollector(Collector):
    def collect(self) -> list[Posting]:
        key = datagokr.service_key(self.source)
        region = str(self.source.params.get("workRgnLst", "R3016"))
        max_pages = int(self.source.options.get("max_pages", 5))
        rows = 100
        postings: list[Posting] = []
        for page in range(1, max_pages + 1):
            query = {"resultType": "json", "ongoingYn": "Y", "workRgnLst": region, "pageNo": page, "numOfRows": rows}
            resp = datagokr.get(self.http, ENDPOINT, query, key)
            items, total = parse_alio_json(resp.content)
            postings.extend(self._to_posting(i) for i in items if i.get("recrutPbancTtl"))
            if not items or page * rows >= total:
                break
        return postings

    def _to_posting(self, item: dict) -> Posting:
        org = str(item.get("instNm") or "")
        sn = str(item.get("recrutPblntSn") or "")
        url = VIEW_URL.format(sn=sn)
        return Posting(
            source_id=self.source.id,
            title=str(item.get("recrutPbancTtl") or "").strip(),
            url=url,
            post_key=sn,
            org_name=org,
            org_type=infer_org_type(org, self.source.org_type),
            district=infer_district(org, self.source.district),
            posted_date=datagokr.ymd(str(item.get("pbancBgngYmd") or "")),
            deadline=datagokr.ymd(str(item.get("pbancEndYmd") or "")),
            detail_url=url,
        )


def parse_alio_json(content: bytes) -> tuple[list[dict], int]:
    """(공고 목록, 전체 건수). 오류 응답은 예외 (포털 게이트웨이 오류는 XML 로 온다)."""
    try:
        data = json.loads(content)
    except ValueError:
        text = content.decode("utf-8", "replace")
        raise RuntimeError(f"잡알리오 API 오류: {' '.join(text.split())[:200]}") from None
    if str(data.get("resultCode")) != "200":
        raise RuntimeError(f"잡알리오 API 오류: {data.get('resultCode')} {data.get('resultMsg', '')}")
    return list(data.get("result") or []), int(data.get("totalCount") or 0)
