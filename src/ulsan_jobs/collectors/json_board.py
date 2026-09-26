"""목록을 스크립트(AJAX)로 불러오는 게시판용 수집기: JSON 응답을 직접 읽는다.

예) 울산광역시 여성회관 공지사항 - 페이지는 빈 표만 있고, 브라우저가
    POST /womenhall/v1/commons/bbs/selectBoardList.do (classId=NOTICE, pageIndex=1) 로
    {"resultList": [{"bbsKey": "...", "title": "...", "regdate": "2026-09-01", ...}], ...} 를 받아 채운다.

options
    api_url        JSON 을 돌려주는 주소 (필수)
    method         post(기본) 또는 get
    data           함께 보낼 값 (예: {classId: NOTICE})
    page_param     쪽 번호 이름 (예: pageIndex)
    items_key      목록이 들어 있는 키 (기본 resultList)
    key_field      글 번호 키 (기본 bbsKey)
    title_fields   제목 후보 키 목록
    date_fields    작성일 후보 키 목록
    link_template  상세 주소 틀. {key} 와 항목의 다른 키를 쓸 수 있다
    org_name       기관명
세션 쿠키가 필요한 사이트가 있어 source.url(화면 주소)을 먼저 한 번 연다.
"""
from __future__ import annotations

import html
import re

from ..dates import parse_date
from ..models import Posting
from .base import Collector

DEFAULT_TITLE_FIELDS = ["title", "subject", "bbsTitle", "bbsSubject", "nttSj", "sj", "boardTitle"]
DEFAULT_DATE_FIELDS = ["regdate", "regDate", "reg_date", "frstRegistPnttm", "writeDate", "createDate", "date"]


def _first(item: dict, fields: list[str]) -> str:
    for f in fields:
        value = item.get(f)
        if value not in (None, ""):
            return str(value)
    return ""


def _clean_title(text: str) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def parse_json_items(payload: dict, opts: dict, today) -> list[dict]:
    items = payload
    for part in str(opts.get("items_key", "resultList")).split("."):
        items = items.get(part, []) if isinstance(items, dict) else []
    out = []
    key_field = opts.get("key_field", "bbsKey")
    for item in items if isinstance(items, list) else []:
        if not isinstance(item, dict):
            continue
        title = _clean_title(_first(item, opts.get("title_fields", DEFAULT_TITLE_FIELDS)))
        key = str(item.get(key_field, "")).strip()
        if len(title) < 2 or not key:
            continue
        template = opts.get("link_template", "")
        url = template.format_map({**{k: v for k, v in item.items() if isinstance(v, (str, int))}, "key": key})
        out.append(
            {
                "title": title,
                "key": key,
                "url": url,
                "posted": parse_date(_first(item, opts.get("date_fields", DEFAULT_DATE_FIELDS)), today),
            }
        )
    return out


class JsonBoardCollector(Collector):
    def collect(self) -> list[Posting]:
        opts = self.source.options
        if self.source.url:
            self.http.get(self.source.url)  # 세션 쿠키
        postings: list[Posting] = []
        seen: set[str] = set()
        for page in range(1, max(1, self.source.pages) + 1):
            data = dict(opts.get("data", {}))
            if opts.get("page_param"):
                data[opts["page_param"]] = page
            if str(opts.get("method", "post")).lower() == "get":
                resp = self.http.get(opts["api_url"], params=data)
            else:
                resp = self.http.post(opts["api_url"], data=data)
            items = parse_json_items(resp.json(), opts, self.today)
            fresh = [i for i in items if i["key"] not in seen]
            for i in fresh:
                seen.add(i["key"])
                postings.append(
                    Posting(
                        source_id=self.source.id,
                        title=i["title"],
                        url=i["url"] or (self.source.url or ""),
                        post_key=i["key"],
                        org_name=opts.get("org_name", ""),
                        org_type=self.source.org_type,
                        district=self.source.district,
                        posted_date=i["posted"],
                        detail_url=i["url"] or None,
                    )
                )
            if not fresh or not opts.get("page_param"):
                break
        return postings
