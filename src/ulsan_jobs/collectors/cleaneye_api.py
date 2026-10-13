"""클린아이(지방공공기관 통합공시) 채용정보 Open API.

공공데이터포털 15159757 '행정안전부 한국지역정보개발원_채용정보 조회 서비스'.
요청: https://apis.data.go.kr/B551982/openApiEmployInfo/openXmlEmployInfo
      ?serviceKey=...&type=xml&sidoCd=007007&pageNo=1&numOfRows=100
응답: <response><openXmlEmployInfo-header><resultCode/><resultMsg/></openXmlEmployInfo-header>
      <openXmlEmployInfo-body><items><item><ENT_NAME/><ENT_TITLE/><PUB_DATE/><PUB_END_DATE/>
      <URL/><DUTY_DETAIL/>...</item></items></openXmlEmployInfo-body></response>

지방공기업·출자출연기관(시설공단, 도시공사, 문화재단, 복지재단 등)의 모집중 공고만 온다.
type=xml 이 없으면 resultCode 11(NOTEXISTPARAMETER_ERROR(type)), sidoCd 가 없거나 해당 지역에
공고가 없으면 resultCode 3(NONOTEXISTDATA_ERROR).
sidoCd: 007002 부산 · 007003 대구 · 007007 울산 · 007014 경북 · 007015 경남 (2026-10-12 확인).
인증키는 공공데이터포털 개인 인증키(일반 인증키) 그대로 쓴다.
"""
from __future__ import annotations

import os
import xml.etree.ElementTree as ET
from urllib.parse import unquote

import requests

from ..classify import infer_district, infer_org_type
from ..dates import parse_date
from ..models import Posting
from .base import Collector, NotConfigured
from .datagokr import scrub_secret

ENDPOINT = "https://apis.data.go.kr/B551982/openApiEmployInfo/openXmlEmployInfo"
NO_DATA = "3"


class CleaneyeCollector(Collector):
    def collect(self) -> list[Posting]:
        key_env = self.source.auth_env or "DATA_GO_KR_KEY"
        key = os.environ.get(key_env, "").strip()
        if not key:
            raise NotConfigured(f"{key_env} 미설정 (공공데이터포털 인증키 필요)")
        if "%" in key:  # 포털의 Encoding 키를 넣은 경우
            key = unquote(key)

        sido = str(self.source.params.get("sidoCd", "")).strip()
        if not sido:
            raise NotConfigured("params.sidoCd 미설정 (예: 울산 007007)")
        max_pages = int(self.source.options.get("max_pages", 3))
        rows = 100

        postings: list[Posting] = []
        for page in range(1, max_pages + 1):
            query = {"serviceKey": key, "type": "xml", "sidoCd": sido, "pageNo": page, "numOfRows": rows}
            try:
                resp = self.http.get(ENDPOINT, params=query)
            except requests.RequestException as exc:
                # requests 의 오류 문구에는 요청 주소(serviceKey 포함)가 들어간다. 공개 저장소의
                # Actions 로그에 '수집 실패 …: <오류>'로 찍히므로 키를 가린 뒤 다시 던진다
                scrub_secret(exc, key)
                raise
            items = parse_cleaneye_xml(resp.content)
            postings.extend(self._to_posting(item) for item in items if item.get("ENT_TITLE"))
            if len(items) < rows:
                break
        return postings

    def _to_posting(self, item: dict[str, str]) -> Posting:
        org = item.get("ENT_NAME", "")
        url = item.get("URL", "")
        detail = item.get("DUTY_DETAIL", "")
        return Posting(
            source_id=self.source.id,
            title=item.get("ENT_TITLE", "").strip(),
            url=url or "https://job.cleaneye.go.kr",
            post_key=url.rsplit("idx=", 1)[-1] if "idx=" in url else "",
            org_name=org,
            org_type=infer_org_type(org, self.source.org_type),
            district=infer_district(f"{org} {item.get('WORK_PLACE', '')}", self.source.district),
            posted_date=parse_date(item.get("PUB_DATE"), self.today),
            deadline=parse_date(item.get("PUB_END_DATE"), self.today),
            label=detail if detail not in ("", "-", "null") else "",
        )


def parse_cleaneye_xml(content: bytes) -> list[dict[str, str]]:
    """item 목록. 공고가 없다는 응답(resultCode 3)은 빈 목록, 다른 오류 코드는 예외."""
    root = ET.fromstring(content)
    code = (root.findtext(".//resultCode") or "").strip()
    if code == NO_DATA:
        return []
    if code not in ("", "0", "00"):
        msg = (root.findtext(".//resultMsg") or "").strip()
        raise RuntimeError(f"클린아이 API 오류: {code} {msg}")
    return [{child.tag: (child.text or "").strip() for child in item} for item in root.iter("item")]
