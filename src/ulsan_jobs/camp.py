"""캠프·교육 수주 공고: 나라장터 용역 입찰공고와 S2B 학교장터 견적요청에서 진로·취업·창업 캠프와
교육 운영 공고를 골라 메일로 보낸다. (S2B 수집은 s2b.py)

조달청_나라장터 입찰공고정보서비스 (공공데이터포털 15129394)
    목록  GET {BASE}/getBidPblancListInfoServc?inqryDiv=1&inqryBgnDt=YYYYMMDDHHMM&inqryEndDt=...&type=json
    지역  GET {BASE}/getBidPblancListInfoPrtcptPsblRgn?inqryDiv=2&bidNtceNo=...&type=json
    응답  {"response": {"header": {"resultCode": "00"}, "body": {"totalCount": N, "items": [...]}}}
          인증 오류 등은 XML(<OpenAPI_ServiceResponse>…<returnAuthMsg>)로 온다.
목록 API 에는 키워드 검색이 없어서 기간 안의 용역 공고를 모두 받아 공고명으로 거른다.
개발계정 한도는 오퍼레이션마다 하루 1,000회 (이 작업은 하루 수십 회 이하).

등급
    바로지원  참가 가능 지역(부산 또는 제한 없음) + (수의계약 또는 추정가격 small_max 이하)
    검토      참가 가능 지역이지만 금액이 큰 입찰
    참고      다른 지역 업체나 학교가 정한 업체만 참가 가능 (견적 기준·과업 참고, 영업 대상)

분야 (공고명으로 판정, config/camp.yaml)
    진로·취업·창업  include 단어, 또는 include_with 조합('캠프' + 직업·진학·꿈…)
    교육            education 단어('교육', 기관·시설 이름 속 '교육'은 빼고 봄)

환경변수 G2B_API_KEY: 공공데이터포털 일반 인증키 (Decoding·Encoding 어느 쪽이든 됨)
"""
from __future__ import annotations

import json
import logging
import os
import re
import sqlite3
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from html import escape
from pathlib import Path
from urllib.parse import unquote

import yaml

from .config import DEFAULT_CONFIG_DIR
from .http import Http
from .models import now_kst

log = logging.getLogger(__name__)

BASE = "https://apis.data.go.kr/1230000/ad/BidPublicInfoService"
LIST_OP = "getBidPblancListInfoServc"
REGION_OP = "getBidPblancListInfoPrtcptPsblRgn"
ROWS = 500
MAX_PAGES = 40

TIER_GO = "바로지원"
TIER_REVIEW = "검토"
TIER_REF = "참고"
TIER_CANCELED = "취소"
TIER_SKIP = "제외"  # 저장만 하고 알리지 않음 (다시 상세 조회하지 않으려고)
TIERS = (TIER_GO, TIER_REVIEW, TIER_REF)
TOPIC_CORE = "진로·취업·창업"
TOPIC_EDU = "교육"
TOPICS = (TOPIC_CORE, TOPIC_EDU)
SOURCE_G2B = "나라장터"
SOURCE_S2B = "S2B"
WEEKDAYS = "월화수목금토일"

SCHEMA = """
CREATE TABLE IF NOT EXISTS camp_bids (
    bid_no        TEXT PRIMARY KEY,
    bid_ord       TEXT,
    title         TEXT NOT NULL,
    org           TEXT,
    demand_org    TEXT,
    posted_at     TEXT,
    close_at      TEXT,
    price         INTEGER,
    price_label   TEXT,
    method        TEXT,
    regions       TEXT,
    tier          TEXT,
    notice_kind   TEXT,
    url           TEXT,
    first_seen_at TEXT NOT NULL,
    reported_at   TEXT
);
CREATE TABLE IF NOT EXISTS camp_runs (
    run_at   TEXT NOT NULL,
    scanned  INTEGER,
    matched  INTEGER,
    new      INTEGER,
    calls    INTEGER,
    errors   INTEGER,
    diag     TEXT
);
"""


class G2BError(RuntimeError):
    pass


@dataclass
class CampConfig:
    include: list[str]
    exclude: list[str]
    home_regions: list[str] = field(default_factory=lambda: ["부산"])
    small_max: int = 20_000_000
    lookback_days: int = 2
    first_lookback_days: int = 10
    closing_soon_days: int = 3
    include_with: dict[str, list[str]] = field(default_factory=dict)
    edu_words: list[str] = field(default_factory=list)
    edu_ignore: list[str] = field(default_factory=list)
    s2b: dict = field(default_factory=dict)


def load_camp_config(config_dir: Path = DEFAULT_CONFIG_DIR) -> CampConfig:
    data = yaml.safe_load((config_dir / "camp.yaml").read_text(encoding="utf-8")) or {}
    return CampConfig(
        include=[str(w) for w in data.get("include", [])],
        exclude=[str(w) for w in data.get("exclude", [])],
        home_regions=[str(w) for w in data.get("home_regions", ["부산"])],
        small_max=int(data.get("small_max", 20_000_000)),
        lookback_days=int(data.get("lookback_days", 2)),
        first_lookback_days=int(data.get("first_lookback_days", 10)),
        closing_soon_days=int(data.get("closing_soon_days", 3)),
        include_with={str(k): [str(w) for w in (v or [])] for k, v in (data.get("include_with") or {}).items()},
        edu_words=[str(w) for w in (data.get("education") or {}).get("words", [])],
        edu_ignore=[str(w) for w in (data.get("education") or {}).get("ignore", [])],
        s2b=dict(data.get("s2b") or {}),
    )


@dataclass
class Bid:
    bid_no: str
    bid_ord: str
    title: str
    org: str = ""
    demand_org: str = ""
    posted_at: datetime | None = None
    close_at: datetime | None = None
    price: int | None = None
    price_label: str = ""  # 추정가격(부가세 제외) / 예산(부가세 포함)
    method: str = ""  # 계약체결방법 · 입찰방법
    notice_kind: str = ""  # 등록 / 변경 / 취소 / 재공고
    url: str = ""
    regions: list[str] | None = None  # 참가가능지역. [] = 제한 없음, None = 모름
    tier: str = ""
    source: str = SOURCE_G2B
    topic: str = ""  # TOPIC_CORE / TOPIC_EDU

    @property
    def is_sole_source(self) -> bool:
        return "수의" in self.method or bool(re.search(r"수의|견적", self.title))

    @property
    def region_text(self) -> str:
        if self.regions is None:
            return "확인필요"
        return ", ".join(self.regions) if self.regions else "제한없음"


# ---------------------------------------------------------------- 판정


def _squash(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def topic_of(title: str, cfg: CampConfig) -> str | None:
    """공고명의 분야. 수집 대상이 아니면 None."""
    t = _squash(title)
    if any(_squash(w) in t for w in cfg.exclude):
        return None
    if any(_squash(w) in t for w in cfg.include):
        return TOPIC_CORE
    for word, mates in cfg.include_with.items():
        if _squash(word) in t and any(_squash(m) in t for m in mates):
            return TOPIC_CORE
    if cfg.edu_words:
        rest = t
        for w in sorted((_squash(w) for w in cfg.edu_ignore), key=len, reverse=True):
            rest = rest.replace(w, "/")
        if any(_squash(w) in rest for w in cfg.edu_words):
            return TOPIC_EDU
    return None


def matches(title: str, cfg: CampConfig) -> bool:
    return topic_of(title, cfg) is not None


def region_ok(regions: list[str] | None, cfg: CampConfig) -> bool | None:
    """참가 가능한가. regions 가 None(조회 실패)이면 None."""
    if regions is None:
        return None
    if not regions:
        return True
    return any("전국" in r or any(h in r for h in cfg.home_regions) for r in regions)


def small_enough(bid: Bid, cfg: CampConfig) -> bool:
    if bid.is_sole_source:
        return True
    if bid.price is None:
        return False
    price = bid.price if bid.price_label.startswith("추정") else round(bid.price / 1.1)  # 예산은 부가세 포함
    return price <= cfg.small_max


def tier_of(bid: Bid, cfg: CampConfig) -> str:
    ok = region_ok(bid.regions, cfg)
    if ok is False:
        return TIER_REF
    if ok is None:  # 지역을 못 읽음: 지원 여부를 사람이 원문에서 확인
        return TIER_REVIEW
    return TIER_GO if small_enough(bid, cfg) else TIER_REVIEW


# ---------------------------------------------------------------- API


def service_key() -> str:
    key = os.environ.get("G2B_API_KEY", "").strip()
    if not key:
        raise G2BError("G2B_API_KEY 미설정: 공공데이터포털 인증키를 GitHub Secrets 에 등록하세요.")
    # Encoding 키(%2B 등)를 넣었어도 requests 가 다시 인코딩하도록 한 번 풀어 둔다
    return unquote(key) if "%" in key else key


def parse_response(content: bytes) -> tuple[list[dict], int]:
    """JSON 응답 → (항목들, 전체 건수). 오류 응답이면 G2BError."""
    text = content.decode("utf-8", errors="replace").strip()
    if text.startswith("<"):
        raise G2BError(f"나라장터 API 오류: {_xml_error(text)}")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise G2BError(f"나라장터 API 응답을 읽지 못함: {text[:200]}") from exc
    resp = data.get("response", data)
    header = resp.get("header") or {}
    code = str(header.get("resultCode", "00"))
    if code not in ("00", "0"):
        raise G2BError(f"나라장터 API 오류 {code}: {header.get('resultMsg', '')}")
    body = resp.get("body") or {}
    items = body.get("items") or []
    if isinstance(items, dict):
        items = items.get("item") or []
    if isinstance(items, dict):
        items = [items]
    try:
        total = int(body.get("totalCount") or 0)
    except (TypeError, ValueError):
        total = len(items)
    return [i for i in items if isinstance(i, dict)], total


def _xml_error(text: str) -> str:
    try:
        root = ET.fromstring(text)
    except ET.ParseError:
        return text[:200]
    parts = [root.findtext(f".//{tag}") for tag in ("returnAuthMsg", "errMsg", "resultMsg", "returnReasonCode")]
    msg = " / ".join(p.strip() for p in parts if p and p.strip())
    if "SERVICE_KEY_IS_NOT_REGISTERED" in msg:
        msg += " (인증키가 아직 활성화되지 않았거나 이 API 활용신청이 승인되지 않음. 발급 직후면 1~2시간 뒤 다시 실행)"
    return msg or text[:200]


class G2BClient:
    def __init__(self, http: Http, key: str):
        self.http = http
        self.key = key
        self.calls = 0
        self.region_samples: list[dict] = []  # 진단용: 참가가능지역 응답 몇 건을 DB 에 남긴다

    def _get(self, op: str, params: dict) -> tuple[list[dict], int]:
        query = {"ServiceKey": self.key, "type": "json", **params}
        resp = self.http.get(f"{BASE}/{op}", params=query)
        self.calls += 1
        return parse_response(resp.content)

    def list_services(self, begin: datetime, end: datetime) -> list[dict]:
        rows: list[dict] = []
        for page in range(1, MAX_PAGES + 1):
            items, total = self._get(
                LIST_OP,
                {
                    "inqryDiv": "1",
                    "inqryBgnDt": begin.strftime("%Y%m%d%H%M"),
                    "inqryEndDt": end.strftime("%Y%m%d%H%M"),
                    "numOfRows": ROWS,
                    "pageNo": page,
                },
            )
            rows += items
            if not items or page * ROWS >= total:
                break
        return rows

    def regions(self, bid_no: str, bid_ord: str) -> list[str]:
        """참가가능지역명 목록 ([] = 제한 없음). 참가가능지역은 차수 단위라 bidNtceOrd 를 함께 보낸다."""
        items, total = self._get(
            REGION_OP,
            {"inqryDiv": "2", "bidNtceNo": bid_no, "bidNtceOrd": bid_ord or "000", "numOfRows": 100, "pageNo": 1},
        )
        if len(self.region_samples) < 5:
            self.region_samples.append({"bid_no": bid_no, "ord": bid_ord, "total": total, "items": items[:3]})
        same_ord = [i for i in items if not bid_ord or str(i.get("bidNtceOrd", bid_ord)) == bid_ord] or items
        names = []
        for i in same_ord:
            name = str(i.get("prtcptPsblRgnNm") or "").strip()
            if name and name not in names:
                names.append(name)
        return names


def _dt(text) -> datetime | None:
    if not text:
        return None
    s = str(text).strip()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d", "%Y%m%d%H%M%S", "%Y%m%d%H%M", "%Y%m%d"):
        try:
            return datetime.strptime(s, fmt)
        except ValueError:
            continue
    return None


def _won(value) -> int | None:
    try:
        n = int(float(str(value).replace(",", "")))
    except (TypeError, ValueError):
        return None
    return n if n >= 100_000 else None  # 0·1000원처럼 금액을 가려 둔 값은 모름으로 본다


def to_bid(item: dict) -> Bid:
    price, label = _won(item.get("presmptPrce")), "추정가격"
    if price is None:
        price, label = _won(item.get("asignBdgtAmt")) or _won(item.get("bdgtAmt")), "예산"
    method = " · ".join(
        dict.fromkeys(str(item.get(k) or "").strip() for k in ("cntrctCnclsMthdNm", "bidMethdNm") if item.get(k))
    )
    bid_no = str(item.get("bidNtceNo") or "").strip()
    return Bid(
        bid_no=bid_no,
        bid_ord=str(item.get("bidNtceOrd") or "").strip(),
        title=str(item.get("bidNtceNm") or "").strip(),
        org=str(item.get("ntceInsttNm") or "").strip(),
        demand_org=str(item.get("dminsttNm") or "").strip(),
        posted_at=_dt(item.get("bidNtceDt")),
        close_at=_dt(item.get("bidClseDt")),
        price=price,
        price_label=label if price else "",
        method=method,
        notice_kind=str(item.get("ntceKindNm") or "").strip(),
        url=str(item.get("bidNtceDtlUrl") or item.get("bidNtceUrl") or "").strip(),
    )


def latest_per_notice(bids: list[Bid]) -> list[Bid]:
    """같은 공고번호의 여러 차수(정정·재공고) 중 마지막 것만."""
    best: dict[str, Bid] = {}
    for b in bids:
        if not b.bid_no:
            continue
        cur = best.get(b.bid_no)
        if cur is None or (b.bid_ord, b.posted_at or datetime.min) >= (cur.bid_ord, cur.posted_at or datetime.min):
            best[b.bid_no] = b
    return list(best.values())


# ---------------------------------------------------------------- 저장


def _iso(d: datetime | None) -> str | None:
    return d.isoformat(sep=" ", timespec="minutes") if d else None


class CampStore:
    def __init__(self, path: Path | str):
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        cols = {r[1] for r in self.conn.execute("PRAGMA table_info(camp_bids)")}
        for col in ("source", "topic"):  # 9/29 추가된 칸 (기존 DB 는 나라장터·진로 분야로 본다)
            if col not in cols:
                self.conn.execute(f"ALTER TABLE camp_bids ADD COLUMN {col} TEXT")

    def close(self) -> None:
        self.conn.commit()
        self.conn.close()

    def is_empty(self, source: str = SOURCE_G2B) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM camp_bids WHERE COALESCE(source, ?) = ? LIMIT 1", (SOURCE_G2B, source)
        ).fetchone() is None

    def known(self, bid_no: str) -> sqlite3.Row | None:
        return self.conn.execute("SELECT * FROM camp_bids WHERE bid_no = ?", (bid_no,)).fetchone()

    def save(self, b: Bid, now: datetime) -> None:
        row = self.known(b.bid_no)
        values = (
            b.bid_ord, b.title, b.org, b.demand_org, _iso(b.posted_at), _iso(b.close_at), b.price, b.price_label,
            b.method, json.dumps(b.regions, ensure_ascii=False) if b.regions is not None else None, b.tier,
            b.notice_kind, b.url, b.source, b.topic,
        )
        if row is None:
            self.conn.execute(
                """INSERT INTO camp_bids (bid_ord, title, org, demand_org, posted_at, close_at, price, price_label,
                       method, regions, tier, notice_kind, url, source, topic, bid_no, first_seen_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (*values, b.bid_no, now.isoformat(timespec="seconds")),
            )
        else:
            self.conn.execute(
                """UPDATE camp_bids SET bid_ord = ?, title = ?, org = ?, demand_org = ?, posted_at = ?, close_at = ?,
                       price = ?, price_label = ?, method = ?, regions = COALESCE(?, regions), tier = ?,
                       notice_kind = ?, url = ?, source = ?, topic = ? WHERE bid_no = ?""",
                (*values, b.bid_no),
            )

    def unreported(self) -> list[Bid]:
        rows = self.conn.execute("SELECT * FROM camp_bids WHERE reported_at IS NULL ORDER BY close_at")
        return [_row_to_bid(r) for r in rows]

    def open_reported(self, now: datetime) -> list[Bid]:
        rows = self.conn.execute(
            "SELECT * FROM camp_bids WHERE reported_at IS NOT NULL AND close_at >= ? ORDER BY close_at",
            (_iso(now),),
        )
        return [_row_to_bid(r) for r in rows]

    def mark_reported(self, bid_nos: list[str], now: datetime) -> None:
        ts = now.isoformat(timespec="seconds")
        self.conn.executemany("UPDATE camp_bids SET reported_at = ? WHERE bid_no = ?", [(ts, n) for n in bid_nos])

    def log_run(self, out: "CampOutcome", diag: dict) -> None:
        self.conn.execute(
            "INSERT INTO camp_runs VALUES (?, ?, ?, ?, ?, ?, ?)",
            (out.now.isoformat(timespec="seconds"), out.scanned, out.matched, len(out.new), out.calls,
             out.region_errors, json.dumps(diag, ensure_ascii=False)),
        )
        # 진단 기록은 최근 60회만 남긴다
        self.conn.execute(
            "DELETE FROM camp_runs WHERE rowid NOT IN (SELECT rowid FROM camp_runs ORDER BY run_at DESC LIMIT 60)"
        )

    def commit(self) -> None:
        self.conn.commit()


def _row_to_bid(r: sqlite3.Row) -> Bid:
    return Bid(
        bid_no=r["bid_no"],
        bid_ord=r["bid_ord"] or "",
        title=r["title"],
        org=r["org"] or "",
        demand_org=r["demand_org"] or "",
        posted_at=_dt(r["posted_at"]),
        close_at=_dt(r["close_at"]),
        price=r["price"],
        price_label=r["price_label"] or "",
        method=r["method"] or "",
        notice_kind=r["notice_kind"] or "",
        url=r["url"] or "",
        regions=json.loads(r["regions"]) if r["regions"] else None,
        tier=r["tier"] or "",
        source=_col(r, "source") or SOURCE_G2B,
        topic=_col(r, "topic") or "",
    )


def _col(r: sqlite3.Row, name: str):
    return r[name] if name in r.keys() else None


# ---------------------------------------------------------------- 실행


@dataclass
class CampOutcome:
    now: datetime
    scanned: int = 0  # 기간 안 나라장터 용역 공고 수
    matched: int = 0  # 그중 키워드에 맞은 공고 수
    new: list[Bid] = field(default_factory=list)
    closing_soon: list[Bid] = field(default_factory=list)
    calls: int = 0
    region_errors: int = 0
    methods: dict[str, int] = field(default_factory=dict)  # 키워드 공고의 계약방법 분포 (진단용)
    s2b_scanned: int = 0  # 기간 안 S2B 용역 견적요청 수
    s2b_matched: int = 0
    s2b_calls: int = 0
    errors: list[str] = field(default_factory=list)  # 수집이 실패한 곳 (메일 아래에 표시)
    mailed: bool = False


def _collect_g2b(store: "CampStore", cfg: CampConfig, now: datetime, client: "G2BClient | None",
                 lookback_days: int | None, recheck: bool, out: CampOutcome, diag: dict) -> None:
    now_naive = now.replace(tzinfo=None)  # API 시각은 한국 시간(시간대 표기 없음)
    days = lookback_days or (cfg.first_lookback_days if store.is_empty(SOURCE_G2B) else cfg.lookback_days)
    begin = (now_naive - timedelta(days=days)).replace(hour=0, minute=0, second=0, microsecond=0)
    client = client or G2BClient(Http(min_interval=0.3, max_seconds=90), service_key())
    try:
        raw = client.list_services(begin, now_naive)
    finally:
        out.calls = client.calls
    raw_by_no = {str(i.get("bidNtceNo") or ""): i for i in raw}
    bids = latest_per_notice([to_bid(i) for i in raw])
    diag.update({"begin": begin.isoformat(), "list_region_fields": {}})
    # 진단: 목록의 참가제한 여부(bidPrtcptLmtYn) 분포. 제한 공고는 참가가능지역 API 가 지역명을 돌려줌
    # (9/29 확인: 서울 제한 공고 → ['서울특별시'], 제한 없는 공고 → 0건)
    flags: dict[str, int] = {}
    for i in raw:
        flag = str(i.get("bidPrtcptLmtYn") or "-")
        flags[flag] = flags.get(flag, 0) + 1
    diag["bidPrtcptLmtYn"] = flags
    out.scanned = len(bids)

    for b in bids:
        topic = topic_of(b.title, cfg)
        if topic is None:
            continue
        b.topic = topic
        out.matched += 1
        out.methods[b.method or "(없음)"] = out.methods.get(b.method or "(없음)", 0) + 1
        if len(diag["list_region_fields"]) < 5:  # 목록 응답에 지역 제한 칸이 있는지 진단
            fields = {k: v for k, v in raw_by_no.get(b.bid_no, {}).items()
                      if re.search(r"rgn|Rgn|lmt|Lmt|Lcl|lcl", k) and v not in (None, "")}
            diag["list_region_fields"][b.bid_no] = fields
        row = store.known(b.bid_no)
        if "취소" in b.notice_kind:
            if row is not None:  # 이미 알린 공고가 취소되면 마감임박 목록에서 빠지게 표시만 바꾼다
                b.tier = TIER_CANCELED
                store.save(b, now)
            continue
        if b.close_at is not None and b.close_at < now_naive:
            continue  # 이미 마감
        cached = row is not None and row["regions"] is not None
        if cached and not (recheck and row["reported_at"] is None):
            b.regions = json.loads(row["regions"])
        else:
            try:
                b.regions = client.regions(b.bid_no, b.bid_ord)
            except Exception as exc:  # noqa: BLE001 - 지역을 못 읽어도 공고는 알린다
                log.warning("참가가능지역 조회 실패 %s: %s", b.bid_no, exc)
                out.region_errors += 1
        b.tier = tier_of(b, cfg)
        store.save(b, now)
    out.calls = client.calls
    diag["methods"] = out.methods
    diag["region_samples"] = getattr(client, "region_samples", [])


def run_camp(
    *,
    db_path: Path,
    send_mail: bool,
    config_dir: Path = DEFAULT_CONFIG_DIR,
    now: datetime | None = None,
    client: G2BClient | None = None,
    lookback_days: int | None = None,
    recheck: bool = False,
    s2b_client=None,
) -> CampOutcome:
    """recheck: 아직 메일로 알리지 않은 공고의 참가가능지역을 저장된 값 대신 다시 조회한다.

    나라장터와 S2B 중 한 곳이 실패해도 다른 곳은 모아서 알린다 (둘 다 실패하면 G2BError)."""
    cfg = load_camp_config(config_dir)
    now = now or now_kst()
    now_naive = now.replace(tzinfo=None)
    store = CampStore(db_path)
    try:
        out = CampOutcome(now=now)
        diag: dict = {}
        tried = 1  # 나라장터
        try:
            _collect_g2b(store, cfg, now, client, lookback_days, recheck, out, diag)
        except G2BError as exc:
            log.error("나라장터 수집 실패: %s", exc)
            out.errors.append(f"나라장터: {exc}")
        store.commit()
        if cfg.s2b.get("enabled"):
            from . import s2b

            tried += 1
            try:
                s2b.collect_s2b(store, cfg, now, s2b_client, out, diag, lookback_days)
            except Exception as exc:  # noqa: BLE001 - S2B 가 막혀도 나라장터 공고는 알린다
                log.exception("S2B 수집 실패")
                out.errors.append(f"S2B: {type(exc).__name__}: {exc}"[:300])
            store.commit()
        if len(out.errors) >= tried:
            raise G2BError(" / ".join(out.errors))

        order = {t: i for i, t in enumerate(TIERS)}
        topic_order = {t: i for i, t in enumerate(TOPICS)}
        # 키워드 설정을 바꾸면 아직 안 알린 공고에도 바로 적용되게 다시 거른다
        # 마감이 지난 공고, 마감일을 모르는데 오래된 공고(직찰 등)는 알리지 않는다
        stale = now_naive - timedelta(days=cfg.first_lookback_days + 4)
        pending = []
        for b in store.unreported():
            b.topic = topic_of(b.title, cfg) or ""
            if (b.tier in TIERS and b.topic
                    and (b.close_at >= now_naive if b.close_at else (b.posted_at is None or b.posted_at >= stale))):
                pending.append(b)
        out.new = sorted(pending, key=lambda b: (order.get(b.tier, 9), topic_order.get(b.topic, 9),
                                                 b.close_at or datetime.max))
        diag["errors"] = out.errors
        store.log_run(out, diag)
        store.commit()
        soon_until = now_naive + timedelta(days=cfg.closing_soon_days + 1)
        out.closing_soon = [
            b for b in store.open_reported(now_naive)
            if b.tier in (TIER_GO, TIER_REVIEW) and b.close_at and b.close_at < soon_until.replace(hour=0, minute=0)
        ]

        if send_mail and out.new:
            from .mailer import MailConfig, build_message, send

            mail_cfg = MailConfig.from_env()
            msg = build_message(
                mail_cfg, camp_subject(out), camp_html(out, cfg), camp_text(out), None
            )
            send(mail_cfg, msg)
            store.mark_reported([b.bid_no for b in out.new], now_kst())
            store.commit()
            out.mailed = True
        return out
    finally:
        store.close()


# ---------------------------------------------------------------- 메일


def _count(bids: list[Bid], tier: str) -> int:
    return sum(1 for b in bids if b.tier == tier)


def camp_subject(out: CampOutcome) -> str:
    d = out.now.date()
    day = f"{d.month}/{d.day}({WEEKDAYS[d.weekday()]})"
    return (
        f"[캠프·교육 수주] {day} 바로지원 {_count(out.new, TIER_GO)}건 · 검토 {_count(out.new, TIER_REVIEW)}건"
        f" · 참고 {_count(out.new, TIER_REF)}건"
    )


def _money(b: Bid) -> str:
    if not b.price:
        return "-"
    man = b.price / 10_000
    text = f"{man:,.0f}만원" if man >= 1 else f"{b.price:,}원"
    return f"{text}<br><span style='color:#888;font-size:12px'>{escape(b.price_label)}</span>"


def _close(b: Bid, now: datetime) -> str:
    if not b.close_at:
        return "원문확인"
    days = (b.close_at.date() - now.date()).days
    tag = "D-day" if days == 0 else f"D-{days}" if days > 0 else "마감"
    return f"{tag}<br><span style='color:#888;font-size:12px'>{b.close_at:%m/%d %H:%M}</span>"


def _bid_table(title: str, note: str, bids: list[Bid], now: datetime) -> str:
    if not bids:
        return ""
    th = "style='background:#1f4e78;color:#fff;padding:4px 8px;text-align:left;white-space:nowrap'"
    td = "style='border-top:1px solid #ddd;padding:6px 8px;vertical-align:top'"
    rows = []
    for b in bids:
        org = escape(b.demand_org or b.org)
        if b.org and b.demand_org and b.org != b.demand_org:
            org += f"<br><span style='color:#888;font-size:12px'>공고: {escape(b.org)}</span>"
        link = f"<a href='{escape(b.url, quote=True)}'>{escape(b.title)}</a>" if b.url else escape(b.title)
        if b.topic == TOPIC_EDU:
            link = "<span style='color:#1f4e78;font-size:12px;font-weight:bold'>[교육]</span> " + link
        number = b.bid_no.removeprefix("S2B-")
        rows.append(
            "<tr>"
            f"<td {td} nowrap>{_close(b, now.replace(tzinfo=None))}</td>"
            f"<td {td}>{org}</td>"
            f"<td {td}>{link}<br><span style='color:#888;font-size:12px'>{escape(b.source)} · {escape(b.method or '-')}"
            f" · 공고번호 {escape(number)}</span></td>"
            f"<td {td} nowrap>{_money(b)}</td>"
            f"<td {td}>{escape(b.region_text)}</td>"
            "</tr>"
        )
    return (
        f"<h3 style='margin:18px 0 4px'>{escape(title)} ({len(bids)})</h3>"
        f"<p style='margin:0 0 6px;color:#555'>{escape(note)}</p>"
        "<table cellspacing='0' style='border-collapse:collapse;border:1px solid #ddd'>"
        f"<tr><th {th}>마감</th><th {th}>수요기관</th><th {th}>공고명</th><th {th}>금액</th><th {th}>참가지역</th></tr>"
        + "".join(rows)
        + "</table>"
    )


def camp_html(out: CampOutcome, cfg: CampConfig) -> str:
    home = "·".join(cfg.home_regions)
    small = f"{cfg.small_max // 10_000:,}만원"
    parts = [
        "<div style=\"font-family:'Malgun Gothic',sans-serif;font-size:14px;color:#222\">",
        f"<h2 style='margin:0 0 8px'>캠프·교육 수주 공고 (나라장터·S2B) {out.now.date().isoformat()}</h2>",
        f"<p>새 공고 <b>{len(out.new)}</b>건 (바로지원 {_count(out.new, TIER_GO)} · 검토 {_count(out.new, TIER_REVIEW)}"
        f" · 참고 {_count(out.new, TIER_REF)})</p>",
        "<p style='margin:0 0 6px;color:#555;font-size:13px'>표마다 진로·취업·창업 공고가 먼저, "
        "<b style='color:#1f4e78'>[교육]</b> 표시는 그 밖의 교육 운영 공고입니다.</p>",
    ]
    by_tier = {t: [b for b in out.new if b.tier == t] for t in TIERS}
    parts.append(_bid_table(
        "★ 바로지원", f"{home} 또는 지역 제한 없음 + 수의계약이거나 추정가격 {small} 이하", by_tier[TIER_GO], out.now
    ))
    parts.append(_bid_table(
        "검토", "참가 가능 지역이지만 금액이 큰 입찰, 또는 참가지역을 읽지 못한 공고 (원문 확인)", by_tier[TIER_REVIEW],
        out.now,
    ))
    parts.append(_bid_table(
        "참고", "다른 지역 업체나 학교가 미리 정한 업체('지정 업체')만 낼 수 있는 공고. 과업·금액은 견적 기준으로, "
        "지정 업체 공고를 낸 학교는 다음 학기 영업 대상으로 참고", by_tier[TIER_REF], out.now
    ))
    if out.closing_soon:
        parts.append(_bid_table(
            f"마감임박 (D-{cfg.closing_soon_days} 이내, 이미 알린 공고)", "지원 준비 중이면 마감 시각을 확인하세요.",
            out.closing_soon, out.now,
        ))
    if out.region_errors:
        parts.append(
            f"<p style='color:#b45309'>참가 가능 범위를 읽지 못한 공고 {out.region_errors}건은 '검토'로 넣었습니다.</p>"
        )
    if out.errors:
        parts.append(
            "<p style='color:#b45309'>⚠ 이번에 읽지 못한 곳: " + escape(" / ".join(out.errors))
            + " — 다음 실행 때 다시 읽습니다.</p>"
        )
    parts.append(
        "<p style='color:#888;font-size:12px;margin-top:16px'>"
        f"나라장터 용역 공고 {out.scanned:,}건 중 {out.matched}건, S2B 학교 용역 견적 {out.s2b_scanned:,}건 중"
        f" {out.s2b_matched}건이 키워드에 맞았습니다."
        " 키워드는 config/camp.yaml 에서 바꿀 수 있습니다. 이 메일은 GitHub Actions 에서 자동 발송됩니다.</p></div>"
    )
    return "".join(parts)


def camp_text(out: CampOutcome) -> str:
    lines = [camp_subject(out), ""]
    for b in out.new:
        close = f"{b.close_at:%m/%d %H:%M}" if b.close_at else "원문확인"
        price = f"{b.price:,}원" if b.price else "-"
        tag = "[교육] " if b.topic == TOPIC_EDU else ""
        lines.append(f"[{b.tier}] ~{close} | {b.source} | {b.demand_org or b.org} | {tag}{b.title} | {price}"
                     f" | {b.region_text}\n  {b.url}")
    return "\n".join(lines)
