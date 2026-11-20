"""국가 산하기관 자체 입찰 게시판 수집 (캠프·교육 수주 메일에 함께 넣는다).

한국과학창의재단처럼 나라장터에 올리지 않고 자기 홈페이지에만 입찰공고를 올리는 기관이 있다.
config/camp.yaml 의 agencies.boards 에 적은 게시판 목록을 읽고, 나라장터·S2B 와 같은 키워드 규칙(topic_of)으로
골라 camp_bids 에 source='기관명' 으로 넣는다.

목록  표(table) 게시판은 강사 공고 수집기와 같은 공통 파서(collectors/board.parse_board)로 읽는다.
상세  키워드에 맞은 공고만 상세 화면을 한 번 열어 금액(추정가격·사업예산 등)·계약방법·마감 일시를 찾는다.

등급 (나라장터와 같은 규칙: 수의계약이거나 추정가격 small_max 이하 → 바로지원, 아니면 검토)
    금액이나 마감을 읽지 못하면 '검토' (원문 확인).
    기관 게시판에는 참가 가능 지역이 따로 없어서 지역 제한은 없는 것으로 보고 판정하되, 메일에는 '확인필요'로 보인다.

robots.txt 가 게시판 주소를 막으면 읽지 않고 메일 아래 '읽지 못한 곳'에 적는다 (사이트가 나중에 막아도 자동으로 멈춤).
"""
from __future__ import annotations

import hashlib
import logging
import re
import time as _time
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta
from urllib.parse import parse_qsl, urlsplit
from urllib.robotparser import RobotFileParser

import requests
from bs4 import BeautifulSoup

from .camp import TIER_REVIEW, Bid, CampConfig, CampOutcome, CampStore, tier_of, topic_of
from .collectors.board import BoardRow, parse_board, set_query
from .dates import find_all_dates
from .http import Http

log = logging.getLogger(__name__)

ROBOTS_AGENT = "UlsanInstructorJobs"


class RobotsBlocked(RuntimeError):
    pass


@dataclass
class Board:
    name: str  # camp_bids.source 로 쓰는 기관 이름 (예: 한국과학창의재단)
    id: str  # 공고번호 앞에 붙이는 짧은 이름 (예: KOFAC → 'KOFAC-1234')
    url: str
    pages: int = 1
    options: dict | None = None  # parse_board 옵션 (page_param, link_template, row_selector …)
    title_must: str = ""  # 이 정규식에 맞는 제목만 (공지·입찰이 섞인 게시판에서 입찰공고만 고를 때)
    detail: bool = True  # 키워드에 맞은 공고의 상세 화면을 열어 금액·마감을 찾는다
    encoding: str = ""  # 응답 글자 인코딩을 강제할 때 (예: euc-kr)
    skip_url: str = ""  # 상세 주소가 이 정규식에 맞는 글은 건너뜀 (예: 나라장터로 바로 가는 글 → 나라장터 수집에서 이미 받음)


def boards_of(cfg: CampConfig) -> list[Board]:
    out = []
    for b in (cfg.agencies.get("boards") or []):
        if not b or b.get("enabled", True) is False:
            continue
        out.append(Board(
            name=str(b["name"]), id=str(b.get("id") or b["name"]), url=str(b["url"]),
            pages=int(b.get("pages", 1)), options=dict(b.get("options") or {}),
            title_must=str(b.get("title_must") or ""), detail=bool(b.get("detail", True)),
            encoding=str(b.get("encoding") or ""), skip_url=str(b.get("skip_url") or ""),
        ))
    return out


# ---------------------------------------------------------------- 상세 화면 읽기

_AMOUNT_LABEL = (
    r"(추\s*정\s*가\s*격|기\s*초\s*금\s*액|배\s*정\s*예\s*산|소\s*요\s*예\s*산|사\s*업\s*예\s*산|사\s*업\s*비"
    r"|사\s*업\s*금\s*액|용\s*역\s*금\s*액|계\s*약\s*금\s*액|예\s*산\s*액|예\s*산)"
)
_AMOUNT = re.compile(
    # 이름과 숫자 사이에 ':'·'금'·'(부가세 포함)'·'금 일천오백만원정(' 같은 글이 끼어도 24자까지는 본다
    _AMOUNT_LABEL + r"[^0-9\n]{0,24}?([\d,]+(?:\.\d+)?)\s*(억\s*원|천\s*만\s*원|백\s*만\s*원|만\s*원|천\s*원|원)"
)
_UNITS = {"억원": 100_000_000, "천만원": 10_000_000, "백만원": 1_000_000, "만원": 10_000, "천원": 1_000, "원": 1}
_METHOD_LABEL = re.compile(r"계\s*약\s*(?:체\s*결\s*)?방\s*법\s*[:：]?\s*([^\n]{0,40})")
_METHOD_WORDS = ("수의", "협상", "경쟁", "적격심사")
_DEADLINE_LABEL = re.compile(
    r"(?:입\s*찰|제\s*안\s*서?|서\s*류|접\s*수|제\s*출|참\s*가\s*신\s*청)[^\n:：]{0,12}?"
    r"(?:마\s*감|기\s*한|기\s*간|종\s*료)\s*(?:일\s*시|일\s*자|일)?"
)
_TIME = re.compile(r"(오전|오후)?\s*(\d{1,2})\s*(?::|시)\s*(\d{2})?")


@dataclass
class AgencyDetail:
    price: int | None = None
    price_label: str = ""  # 추정가격(부가세 제외) / 예산(부가세 포함)
    method: str = ""
    close_at: datetime | None = None


def _amount(number: str, unit: str) -> int | None:
    try:
        value = float(number.replace(",", ""))
    except ValueError:
        return None
    won = int(round(value * _UNITS.get(re.sub(r"\s+", "", unit), 1)))
    return won if won >= 100_000 else None


def parse_detail_text(text: str, today: date) -> AgencyDetail:
    """상세 화면 글자에서 금액·계약방법·마감 일시를 찾는다. 못 찾은 것은 비워 둔다."""
    text = re.sub(r"[ \t\r\f\v\xa0]+", " ", text or "")
    d = AgencyDetail()
    m = _AMOUNT.search(text)
    if m:
        d.price = _amount(m[2], m[3])
        if d.price:
            d.price_label = "추정가격" if "추정" in re.sub(r"\s+", "", m[1]) else "예산"
    m = _METHOD_LABEL.search(text)
    label = re.split(r"[/,;]|\s{2,}|\d+\.\s", m[1])[0].strip(" .:") if m else ""
    squashed = re.sub(r"\s+", "", text)
    if any(w in label for w in _METHOD_WORDS):  # '계약방법: 제한경쟁입찰(협상에 의한 계약)' → 적힌 그대로
        d.method = label
    else:
        found = [w for w in ("수의계약", "협상에의한계약", "제한경쟁", "일반경쟁") if w in squashed]
        # 본문에 '수의계약'과 '경쟁'이 함께 나오면 어느 쪽인지 알 수 없어서 비워 둔다 (잘못 '바로지원'이 되지 않게)
        if found == ["수의계약"]:
            d.method = "수의계약"
        elif found and "수의계약" not in found:
            d.method = found[0].replace("협상에의한계약", "협상에 의한 계약")
    d.close_at = _deadline(text, today)
    return d


def _deadline(text: str, today: date) -> datetime | None:
    for m in _DEADLINE_LABEL.finditer(text):
        window = text[m.end(): m.end() + 80]
        dates = find_all_dates(window, today)
        if not dates:
            continue
        tilde = re.search(r"[~∼～〜]", window)
        # '2026. 11. 20. ~ 11. 28. 14:00' 처럼 기간이면 '~' 뒤 날짜, 아니면 첫 날짜
        idx, (pos, day) = next(((i, (p, dd)) for i, (p, dd) in enumerate(dates) if tilde and p > tilde.start()),
                               (0, dates[0]))
        # 시각은 그 날짜 뒤, 다음 날짜나 '~' 전까지에서만 찾는다 (날짜 숫자 뒤에는 '.'·'월' 이 와서 시각으로 안 잡힘)
        end = min([p for p, _ in dates[idx + 1:]] + [pos + 40])
        if tilde and tilde.start() > pos:
            end = min(end, tilde.start())
        t = _TIME.search(window, pos, end)
        hour, minute = 23, 59
        if t and int(t[2]) <= 24:
            hour, minute = int(t[2]), int(t[3] or 0)
            if t[1] == "오후" and hour < 12:
                hour += 12
            hour = min(hour, 23)
        return datetime.combine(day, time(hour, minute))
    return None


def detail_text(html: bytes | str) -> str:
    soup = BeautifulSoup(html, "lxml")
    for tag in soup(["script", "style", "noscript", "header", "footer", "nav"]):
        tag.decompose()
    return soup.get_text("\n")


def short_key(key: str) -> str:
    """게시글 식별값(주소일 때가 많음) → 공고번호로 쓸 짧은 값.

    주소 끝 칸에 숫자가 있으면 그 칸 (…/posts/62251 → 62251, …/bns/PBANC_00000000000519 → PBANC_00000000000519),
    아니면 쿼리의 마지막 숫자 값 (view.do?bbsSn=9105 → 9105), 그것도 없으면 주소의 해시."""
    if not key.startswith("http"):
        return key
    parts = urlsplit(key)
    last = parts.path.rstrip("/").rsplit("/", 1)[-1]
    if re.search(r"\d", last) and "." not in last:
        return last
    numbers = [v for _, v in parse_qsl(parts.query) if re.fullmatch(r"\d{2,}", v)]
    if numbers:
        return numbers[-1]
    return hashlib.sha1(key.encode()).hexdigest()[:12]


# ---------------------------------------------------------------- 등급


def agency_tier(b: Bid, cfg: CampConfig) -> str:
    """금액이나 마감을 모르면 검토. 알면 나라장터와 같은 규칙(수의계약·금액)으로 판정."""
    if b.close_at is None or b.price is None:
        return TIER_REVIEW
    return tier_of(replace(b, regions=b.regions if b.regions is not None else []), cfg)


# ---------------------------------------------------------------- 접속


class AgencyClient:
    def __init__(self, http: Http | None = None, sleep=_time.sleep, clock=_time.monotonic):
        # 기관 홈페이지는 국내 중계 없이 바로 읽는다 (해외 접속이 되는 곳만 목록에 넣는다)
        self.http = http or Http(min_interval=1.0, max_seconds=60, kr_proxy="")
        self.calls = 0
        self._robots: dict[str, RobotFileParser | None] = {}
        self._last: dict[str, float] = {}
        self.sleep, self.clock = sleep, clock

    def _origin(self, url: str) -> str:
        parts = urlsplit(url)
        return f"{parts.scheme}://{parts.netloc}"

    def _wait(self, origin: str) -> None:
        """robots.txt 의 Crawl-delay(예: 한국청년기업가정신재단 10초)만큼 요청 사이를 띄운다."""
        rp = self._robots.get(origin)
        delay = (rp.crawl_delay(ROBOTS_AGENT) if rp is not None else None) or 0
        last = self._last.get(origin)
        if delay and last is not None:
            gap = float(delay) - (self.clock() - last)
            if gap > 0:
                self.sleep(gap)
        self._last[origin] = self.clock()

    def allowed(self, url: str) -> bool:
        origin = self._origin(url)
        if origin not in self._robots:
            self._robots[origin] = self._load_robots(origin)
        rp = self._robots[origin]
        return True if rp is None else rp.can_fetch(ROBOTS_AGENT, url)

    def _load_robots(self, origin: str) -> RobotFileParser | None:
        """robots.txt 를 읽는다. 없으면(4xx) 제한 없음(None), 서버 오류(5xx)면 모두 막힌 것으로 본다."""
        rp = RobotFileParser(origin + "/robots.txt")
        try:
            self.calls += 1
            resp = self.http.get(origin + "/robots.txt")
        except requests.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else 0
            if 400 <= status < 500:
                return None
            rp.disallow_all = True
            return rp
        text = resp.content.decode(resp.encoding or "utf-8", errors="replace")
        if "<html" in text[:500].lower():  # robots.txt 대신 안내 화면을 주는 사이트
            return None
        rp.parse(text.splitlines())
        return rp

    def get(self, url: str, encoding: str = "") -> tuple[str, str]:
        """(html, 최종 주소). robots.txt 가 막으면 RobotsBlocked."""
        if not self.allowed(url):
            raise RobotsBlocked(f"robots.txt 가 막음: {url}")
        self._wait(self._origin(url))
        self.calls += 1
        resp = self.http.get(url)
        return decode(resp.content, encoding or resp.encoding), resp.url


def decode(content: bytes, encoding: str | None) -> str:
    """응답 글자 풀기. 서버가 인코딩을 안 알려 주면(ISO-8859-1 기본값) UTF-8, 안 되면 EUC-KR(CP949)."""
    if encoding and encoding.lower() not in ("iso-8859-1", "latin-1"):
        return content.decode(encoding, errors="replace")
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("cp949", errors="replace")


# ---------------------------------------------------------------- 수집


def collect_agencies(store: CampStore, cfg: CampConfig, now: datetime, client: AgencyClient | None,
                     out: CampOutcome, diag: dict) -> int:
    """기관 게시판마다 읽어 camp_bids 에 저장한다. 한 기관이 실패해도 나머지는 읽는다.

    돌려주는 값: 읽으려 한 게시판 수 (모든 수집처가 실패했는지 판단할 때 쓴다)."""
    boards = boards_of(cfg)
    client = client or AgencyClient()
    info: dict = {}
    for board in boards:
        stat = {"rows": 0, "matched": 0, "new": 0, "detail_errors": 0}
        info[board.name] = stat
        try:
            _collect_board(board, store, cfg, now, client, out, stat)
        except RobotsBlocked as exc:
            log.warning("%s: %s", board.name, exc)
            stat["error"] = "robots.txt 가 수집을 막음"
            out.errors.append(f"{board.name}: robots.txt 가 수집을 막아 읽지 않음")
        except Exception as exc:  # noqa: BLE001 - 한 기관이 막혀도 다른 곳은 알린다
            log.exception("%s 수집 실패", board.name)
            stat["error"] = f"{type(exc).__name__}: {exc}"[:200]
            out.errors.append(f"{board.name}: {type(exc).__name__}: {exc}"[:300])
        store.commit()
    out.agency_calls = client.calls
    diag["agencies"] = info
    return len(boards)


def _collect_board(board: Board, store: CampStore, cfg: CampConfig, now: datetime, client: AgencyClient,
                   out: CampOutcome, stat: dict) -> None:
    now_naive = now.replace(tzinfo=None)
    today = now_naive.date()
    # 게시판 목록은 날짜로 거를 수 없어서 늘 처음 실행 기간만큼 본다 (이미 저장한 공고는 공고번호로 건너뜀)
    since = today - timedelta(days=cfg.first_lookback_days)
    opts = board.options or {}
    seen: set[str] = set()
    for page in range(1, max(1, board.pages) + 1):
        url = board.url if page == 1 else (set_query(board.url, **{opts["page_param"]: page})
                                           if opts.get("page_param") else None)
        if url is None:
            break
        html, final_url = client.get(url, board.encoding)
        rows = parse_board(html, final_url, opts, today)
        if page == 1 and not rows:
            raise RuntimeError(f"게시판 목록을 읽지 못함 (행 0건): {url}")
        fresh = [r for r in rows if r.key not in seen
                 and not (board.skip_url and re.search(board.skip_url, r.url or ""))]
        seen.update(r.key for r in fresh)
        stat["rows"] += len(fresh)
        for r in fresh:
            _handle_row(r, board, store, cfg, now, client, out, stat, since)
        if not fresh or all(r.posted and r.posted < since for r in fresh):
            break


def _handle_row(r: BoardRow, board: Board, store: CampStore, cfg: CampConfig, now: datetime,
                client: AgencyClient, out: CampOutcome, stat: dict, since: date) -> None:
    now_naive = now.replace(tzinfo=None)
    if r.posted and r.posted < since:
        return
    out.agency_scanned += 1
    if board.title_must and not re.search(board.title_must, r.title):
        return
    topic = topic_of(r.title, cfg)
    if topic is None:
        return
    out.agency_matched += 1
    stat["matched"] += 1
    bid_no = f"{board.id}-{short_key(r.key)}"
    if store.known(bid_no) is not None:
        return
    b = Bid(
        bid_no=bid_no, bid_ord="", title=r.title, org=board.name, demand_org=board.name,
        posted_at=datetime.combine(r.posted, time()) if r.posted else None,
        close_at=datetime.combine(r.deadline, time(23, 59)) if r.deadline else None,
        url=r.url, source=board.name, topic=topic,
    )
    if board.detail and r.detail_ok and r.url:
        try:
            html, _ = client.get(r.url, board.encoding)
            d = parse_detail_text(detail_text(html), now_naive.date())
            b.price, b.price_label, b.method = d.price, d.price_label, d.method
            b.close_at = d.close_at or b.close_at
        except Exception as exc:  # noqa: BLE001 - 상세를 못 읽어도 공고는 '검토'로 알린다
            log.warning("%s 상세 조회 실패 %s: %s", board.name, r.key, exc)
            stat["detail_errors"] += 1
    if b.close_at is not None and b.close_at < now_naive:
        return
    b.tier = agency_tier(b, cfg)
    store.save(b, now)
    stat["new"] += 1
