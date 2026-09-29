"""S2B(학교장터) 견적요청·소액수의 공고 수집 (캠프·교육 수주 메일에 함께 넣는다).

학교가 2천만원 이하로 사는 캠프·교육 용역은 나라장터가 아니라 대부분 S2B 에 올라온다.
교육청 홈페이지가 끌어다 보여 주는 '견적요청/소액수의공고 현황' 공개 목록을 읽는다 (로그인 없음, EUC-KR).

목록  POST https://www.s2b.kr/S2BNCustomer/tcmo001.do
        forwardName=openApiTcmo1 search_yn=Y areaKind=부산 tender_item=3(용역)
        tender_sep1=1 tender_name= tender_sep2=1(공고일 기준) tender_date_start=YYYYMMDD tender_date_end=YYYYMMDD
        pageNo=N  → 한 쪽 10건, 최근 공고가 위. 마지막 쪽 번호는 goList(N) 링크에서 읽는다.
상세  GET  tcmo001.do?forwardName=openViewTcmo11(1인)|openViewTcmo12(2인)
             &estimateCode=...&tender_step_code=A&page_flag=2   (브라우저에서도 그대로 열린다)
      견적제출 공급업체(tcmu100VO.estimateCompany)
        1 전체공개     → 누구나 견적 제출 가능          → 참가지역 제한 없음 []
        3 지역 선택    → limit_region_l1.. 에서 고른 시도 → 참가지역 ['부산'] 등
        2 공급업체 선택 → 학교가 업체를 미리 정해 둠      → ['지정 업체'] (참고: 영업 대상 학교)
      2인 수의 안내공고는 기초금액(VAT 포함, basic_cost)이 있다.

9/29 확인 (GitHub 수집 서버에서 접속됨): 부산·울산·경남 진로캠프 1인 수의 견적 11건은 모두 '공급업체 선택',
2인 수의 안내공고 1건은 '지역 선택'(부산).
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from html import unescape
from urllib.parse import urlencode

from .camp import SOURCE_S2B, TIER_SKIP, TOPIC_CORE, Bid, CampConfig, CampOutcome, CampStore, tier_of, topic_of
from .http import Http

log = logging.getLogger(__name__)

URL = "https://www.s2b.kr/S2BNCustomer/tcmo001.do"
ENCODING = "euc-kr"
DESIGNATED = "지정 업체"
KIND_NAMES = {"1": "1인", "2": "2인"}

_ROW = re.compile(
    r"f_detail\('(\d+)'\s*,\s*'(\d)'\)\s*;?\s*\"\s*>(.*?)</a>"  # 공고번호, 1인/2인, 공고명
    r".*?<tr[^>]*>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>\s*<td[^>]*>(.*?)</td>",
    re.S,  # 다음 줄: 거래구분, 기관명, 공고일, 견적서제출마감일
)
_LAST_PAGE = re.compile(r"goList\((\d+)\)")
_SCOPE_INPUT = re.compile(r"<input[^>]*name=\"tcmu100VO\.estimateCompany\"[^>]*>", re.I)
_REGION_SELECT = re.compile(r"<select[^>]*name=\"limit_region_([ls])(\d+)\"[^>]*>(.*?)</select>", re.S | re.I)
_SELECTED = re.compile(r"<option[^>]*value=\"([^\"]*)\"[^>]*\bselected\b[^>]*>([^<]*)", re.I)
_BASIC_COST = re.compile(r"name=\"basic_cost\"[^>]*value=\"([\d,]+)\"", re.I)


def _text(fragment: str) -> str:
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", fragment))).strip()


@dataclass
class S2BRow:
    code: str
    kind: str  # '1' 1인 수의 / '2' 2인 수의
    title: str
    category: str  # 물품/공사/용역
    school: str
    posted: date | None
    close: datetime | None


def _date(text: str) -> date | None:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", text or "")
    return date(int(m[1]), int(m[2]), int(m[3])) if m else None


def _datetime(text: str) -> datetime | None:
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})(?:\s+(\d{1,2}):(\d{2}))?", text or "")
    if not m:
        return None
    return datetime(int(m[1]), int(m[2]), int(m[3]), int(m[4] or 0), int(m[5] or 0))


def parse_list(html: str) -> tuple[list[S2BRow], int]:
    """목록 한 쪽 → (행들, 마지막 쪽 번호)."""
    rows = [
        S2BRow(code=code, kind=kind, title=_text(title), category=_text(cat), school=_text(school),
               posted=_date(_text(posted)), close=_datetime(_text(close)))
        for code, kind, title, cat, school, posted, close in _ROW.findall(html)
    ]
    pages = [int(n) for n in _LAST_PAGE.findall(html)]
    return rows, max(pages) if pages else 1


@dataclass
class S2BDetail:
    regions: list[str] | None  # [] 전체공개, ['부산'] 지역 선택, ['지정 업체'], None 모름
    price: int | None = None  # 기초금액(VAT 포함)


def parse_detail(html: str) -> S2BDetail:
    scope = None
    for tag in _SCOPE_INPUT.findall(html):
        if re.search(r"\bchecked\b", tag, re.I):
            m = re.search(r"value=\"(\d)\"", tag)
            scope = m[1] if m else None
    regions: list[str] | None
    if scope == "1":
        regions = []
    elif scope == "2":
        regions = [DESIGNATED]
    elif scope == "3":
        sido: dict[str, str] = {}
        sigun: dict[str, str] = {}
        for kind, slot, body in _REGION_SELECT.findall(html):
            picked = [_text(name) for value, name in _SELECTED.findall(body) if value.strip()]
            if picked:
                (sido if kind.lower() == "l" else sigun)[slot] = picked[0]
        names = [f"{sido[s]} {sigun.get(s, '')}".strip() for s in sorted(sido, key=int)]
        regions = list(dict.fromkeys(names)) or None
    else:
        regions = None
    m = _BASIC_COST.search(html)
    price = int(m[1].replace(",", "")) if m and m[1].replace(",", "") else None
    return S2BDetail(regions=regions, price=price or None)


def detail_url(code: str, kind: str) -> str:
    forward = "openViewTcmo11" if kind == "1" else "openViewTcmo12"
    return URL + "?" + urlencode(
        {"forwardName": forward, "estimateCode": code, "tender_step_code": "A", "page_flag": "2"}
    )


class S2BClient:
    def __init__(self, http: Http | None = None):
        self.http = http or Http(min_interval=0.5, max_seconds=60)
        self.calls = 0

    def list_page(self, region: str, start: date, end: date, page: int) -> tuple[list[S2BRow], int]:
        form = {
            "forwardName": "openApiTcmo1", "search_yn": "Y", "areaKind": region, "tender_item": "3",
            "tender_sep1": "1", "tender_name": "", "company_name_s": "", "tender_sep2": "1",
            "tender_date_start": f"{start:%Y%m%d}", "tender_date_end": f"{end:%Y%m%d}", "estimate_kind": "",
            "pageNo": str(page), "estimateCode": "", "tender_step_code": "", "page_flag": "",
        }
        self.calls += 1
        resp = self.http.post(URL, data={k: v.encode(ENCODING) for k, v in form.items()})
        html = resp.content.decode(ENCODING, errors="replace")
        if "견적요청" not in html:  # 점검 화면·오류 화면이면 행이 0건으로 보이므로 실패로 알린다
            raise RuntimeError(f"S2B 목록 화면이 아님 ({region} {page}쪽, {len(resp.content)}B)")
        return parse_list(html)

    def detail(self, code: str, kind: str) -> S2BDetail:
        self.calls += 1
        resp = self.http.get(detail_url(code, kind))
        return parse_detail(resp.content.decode(ENCODING, errors="replace"))


def collect_s2b(store: CampStore, cfg: CampConfig, now: datetime, client: S2BClient | None,
                out: CampOutcome, diag: dict, lookback_days: int | None = None) -> None:
    """S2B 목록(용역, 공고일 기준 최근 며칠)을 지역마다 읽어 키워드에 맞는 견적을 camp_bids 에 저장한다."""
    opts = cfg.s2b
    now_naive = now.replace(tzinfo=None)
    empty = store.is_empty(SOURCE_S2B)
    days = lookback_days or int(opts.get("first_lookback_days" if empty else "lookback_days", 10 if empty else 2))
    start = now_naive.date() - timedelta(days=days)
    max_pages = int(opts.get("max_pages", 120))
    client = client or S2BClient()
    info: dict = {"begin": start.isoformat(), "pages": {}, "scopes": {}, "detail_errors": 0}
    try:
        for region in opts.get("regions", ["부산"]):
            page, last = 1, 1
            while page <= min(last, max_pages):
                rows, last = client.list_page(region, start, now_naive.date(), page)
                info["pages"][region] = page
                if not rows:
                    break
                for r in rows:
                    _handle_row(r, region, store, cfg, now, out, info, client)
                page += 1
            if last > max_pages:
                log.warning("S2B %s: %d쪽 중 %d쪽까지만 읽음 (max_pages)", region, last, max_pages)
                info.setdefault("truncated", {})[region] = last
    finally:
        out.s2b_calls = client.calls
        diag["s2b"] = info


def _handle_row(r: S2BRow, region: str, store: CampStore, cfg: CampConfig, now: datetime, out: CampOutcome,
                info: dict, client: S2BClient) -> None:
    now_naive = now.replace(tzinfo=None)
    if r.category and r.category != "용역":
        return
    out.s2b_scanned += 1
    topic = topic_of(r.title, cfg)
    if topic is None:
        return
    out.s2b_matched += 1
    b = Bid(
        bid_no=f"S2B-{r.code}", bid_ord=r.kind, title=r.title, org=f"S2B {region}", demand_org=r.school,
        posted_at=datetime.combine(r.posted, datetime.min.time()) if r.posted else None, close_at=r.close,
        method=f"{KIND_NAMES.get(r.kind, r.kind)} 수의 견적", url=detail_url(r.code, r.kind),
        source=SOURCE_S2B, topic=topic,
    )
    if b.close_at is not None and b.close_at < now_naive:
        return
    row = store.known(b.bid_no)
    if row is not None and row["regions"] is not None:
        b.regions = json.loads(row["regions"])
        b.price, b.price_label = row["price"], row["price_label"] or ""
    else:
        try:
            d = client.detail(r.code, r.kind)
            b.regions = d.regions
            if d.price:
                b.price, b.price_label = d.price, "기초금액"
        except Exception as exc:  # noqa: BLE001 - 상세를 못 읽어도 공고는 '검토'로 알린다
            log.warning("S2B 상세 조회 실패 %s: %s", r.code, exc)
            info["detail_errors"] += 1
            out.region_errors += 1
    scope = "모름" if b.regions is None else "전체공개" if not b.regions else (
        "지정" if b.regions == [DESIGNATED] else "지역")
    info["scopes"][scope] = info["scopes"].get(scope, 0) + 1
    # 학교가 업체를 정해 둔 교육 일반 견적은 영업에도 쓸모가 적어 알리지 않는다 (진로·취업·창업만 참고로)
    if b.regions == [DESIGNATED] and topic != TOPIC_CORE:
        b.tier = TIER_SKIP
    else:
        b.tier = tier_of(b, cfg)
    store.save(b, now)
