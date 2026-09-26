"""수집 대상 사이트 진단 도구.

config/sources.yaml 의 각 소스에 실제로 접속해 보고, 게시판 HTML 구조(표 머리글,
행별 링크/onclick, 폼, 페이지 이동 링크)를 요약 출력한다. 새 소스를 추가하거나
사이트 개편으로 수집이 깨졌을 때 GitHub Actions 로그로 구조를 확인하는 용도.

사용법:
    python scripts/probe.py                 # 전체 소스 접속 확인 + phase 1 상세 구조
    python scripts/probe.py --detail ID ... # 지정 소스 상세 구조
    python scripts/probe.py --url URL ...   # 임의 URL 상세 구조
    python scripts/probe.py --parse --url-file scripts/probe_urls.txt
                                            # 여러 URL 에 실제 게시판 파서를 돌려 결과만 짧게 출력
"""
from __future__ import annotations

import argparse
import logging
import time
import re
import sys
from pathlib import Path

import requests
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

from ulsan_jobs.collectors.board import _pick_rows, parse_board  # noqa: E402
from ulsan_jobs.http import Http  # noqa: E402
from ulsan_jobs.models import today_kst  # noqa: E402

HTTP = Http(min_interval=0.5, retries=0, max_seconds=40)
LINK_PATTERN: re.Pattern | None = None
SNIPPETS: list[str] = []
RAW = 0
RAW_FROM = ""
GREP: re.Pattern | None = None
FOLLOW_JS = False
AROUND: list[str] = ["접수기간", "모집기간", "신청기간", "마감"]

# 게시판 목록 외에 메뉴 구조(다른 게시판 번호)를 찾기 위해 보는 페이지
EXTRA_URLS = [
    "https://use.go.kr/job/index.do",
    "https://use.go.kr/after/index.do",
    # 울산시설공단 상세보기를 GET 으로 열 수 있는지 확인
    "https://www.uic.or.kr/uimc/notify/noti06/selectEmploymentArticle.do?bbsId=BBSMSTR_000000000022&recruitGb=RT01&employmentId=EMPLOY_0000000003134",
]
DEFAULT_LINK_PATTERN = r"bbsSn|noti0\d|usPrivateApply|BD_select|recruitGb"


def clip(text: str | None, n: int = 120) -> str:
    text = re.sub(r"\s+", " ", text or "").strip()
    return text if len(text) <= n else text[: n - 1] + "…"


def fetch(url: str) -> requests.Response:
    print(f"  … {time.strftime('%H:%M:%S')} GET {url}", flush=True)
    try:
        return HTTP.get(url)
    except requests.HTTPError as exc:  # 진단 목적이라 4xx/5xx 응답도 그대로 보여준다
        return exc.response


def describe_anchor(a) -> str:
    parts = [f"text={clip(a.get_text(), 70)!r}"]
    if a.get("href"):
        parts.append(f"href={clip(a['href'], 160)!r}")
    if a.get("onclick"):
        parts.append(f"onclick={clip(a['onclick'], 160)!r}")
    for attr in ("data-id", "data-seq", "data-no", "data-idx", "target", "title"):
        if a.get(attr):
            parts.append(f"{attr}={clip(a[attr], 60)!r}")
    return " ".join(parts)


def dump_structure(html: bytes, base_url: str) -> None:
    soup = BeautifulSoup(html, "lxml")
    print(f"  <title>: {clip(soup.title.get_text() if soup.title else '')}")

    tables = soup.find_all("table")
    print(f"  tables: {len(tables)}")
    for ti, table in enumerate(tables):
        rows = table.find_all("tr")
        if len(rows) < 2:
            continue
        cls = " ".join(table.get("class", []))
        print(f"  [table {ti}] class={cls!r} summary={clip(table.get('summary'), 80)!r} rows={len(rows)}")
        headers = [clip(th.get_text(), 20) for th in table.find_all("th")][:12]
        print(f"    th: {headers}")
        shown = 0
        for tr in rows:
            tds = tr.find_all("td")
            if not tds:
                continue
            cells = [clip(td.get_text(), 40) for td in tds]
            print(f"    row class={' '.join(tr.get('class', []))!r} cells={cells}")
            print(f"      td classes={[' '.join(td.get('class', [])) for td in tds]}")
            for a in tr.find_all("a"):
                print(f"      a: {describe_anchor(a)}")
            if tr.get("onclick"):
                print(f"      tr onclick={clip(tr['onclick'], 160)!r}")
            shown += 1
            if shown >= 4:
                break

    if not tables:
        # 표가 없는 목록형(ul/li, div) 게시판
        print("  no tables; list-like anchors:")
        for ul in soup.find_all(["ul", "ol"]):
            anchors = ul.find_all("a")
            if len(anchors) >= 5 and len(ul.find_all("li")) >= 5:
                print(f"  [list] class={' '.join(ul.get('class', []))!r} parent={ul.parent.name}.{' '.join(ul.parent.get('class', []))}")
                for li in ul.find_all("li")[:4]:
                    print(f"    li text={clip(li.get_text(' '), 150)!r}")
                    for a in li.find_all("a")[:2]:
                        print(f"      a: {describe_anchor(a)}")

    print("  forms:")
    for form in soup.find_all("form"):
        hidden = [
            f"{i.get('name')}={clip(i.get('value'), 30)}"
            for i in form.find_all("input", {"type": "hidden"})
        ][:15]
        print(
            f"    form id={form.get('id')!r} name={form.get('name')!r} "
            f"action={form.get('action')!r} method={form.get('method')!r} hidden={hidden}"
        )

    print("  paging anchors:")
    for a in soup.find_all("a"):
        if re.fullmatch(r"\s*[2-4]\s*", a.get_text() or ""):
            print(f"    {describe_anchor(a)}")

    # onclick 에서 쓰이는 JS 함수 정의 (상세보기 URL 조립 방식 확인용)
    used = set()
    for tag in soup.find_all(onclick=True):
        m = re.match(r"\s*(?:javascript:)?\s*([A-Za-z_$][\w$.]*)\s*\(", tag["onclick"])
        if m:
            used.add(m.group(1).split(".")[-1])
    for a in soup.find_all("a", href=True):
        m = re.match(r"\s*javascript:\s*([A-Za-z_$][\w$.]*)\s*\(", a["href"])
        if m:
            used.add(m.group(1).split(".")[-1])
    scripts = "\n".join(s.get_text() for s in soup.find_all("script") if not s.get("src"))
    for name in sorted(used):
        m = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", scripts)
        if m:
            body = scripts[m.start(): m.start() + 600]
            print(f"  js {name}: {clip(body, 600)}")
        else:
            print(f"  js {name}: (inline definition not found)")

    if LINK_PATTERN is not None:
        print(f"  anchors matching {LINK_PATTERN.pattern!r}:")
        seen = set()
        for a in soup.find_all("a"):
            key = (a.get("href") or "") + "|" + (a.get("onclick") or "")
            if LINK_PATTERN.search(key) and key not in seen:
                seen.add(key)
                print(f"    {describe_anchor(a)}")
                if len(seen) >= 60:
                    break

    for selector in SNIPPETS:
        for el in soup.select(selector)[:2]:
            print(f"  snippet {selector!r}: {clip(str(el), 1500)}")

    body_text = clip(soup.get_text(" "), 200000)
    for kw in AROUND:
        i = body_text.find(kw)
        if i >= 0:
            print(f"  text near {kw!r}: {body_text[max(0, i - 60): i + 200]!r}")


def js_defs(soup, code: str) -> list[str]:
    """onclick/href 의 javascript 호출에 쓰인 함수 정의를 찾아 돌려준다."""
    out = []
    scripts = "\n".join(sc.get_text() for sc in soup.find_all("script") if not sc.get("src"))
    for name in dict.fromkeys(re.findall(r"([A-Za-z_$][\w$]*)\s*\(", code or "")):
        m = re.search(r"function\s+" + re.escape(name) + r"\s*\([^)]*\)\s*\{", scripts)
        if m:
            out.append(clip(scripts[m.start(): m.start() + 500], 500))
    return out


BOARD_LINK = re.compile(r"공지|채용|모집|강사|알림|소식|notice|board|bbs|Board|Bbs", re.I)


def grep_report(label: str, text: str, limit: int = 8) -> None:
    hits = list(GREP.finditer(text))
    if hits:
        print(f"  grep {label}: {len(hits)} hits")
    for m in hits[:limit]:
        print(f"    … {clip(text[max(0, m.start() - 150): m.end() + 350], 500)}")


def parse_report(label: str, url: str) -> None:
    """게시판 파서를 그대로 돌려서 몇 건을 어떻게 읽는지만 짧게 보여준다."""
    print("-" * 100)
    print(f"PARSE {label}")
    try:
        r = fetch(url)
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR {type(exc).__name__}: {clip(str(exc), 300)}")
        return
    print(f"  status={r.status_code} bytes={len(r.content)} final={r.url}")
    if len(r.content) < 1500:
        print(f"  body: {clip(r.content.decode('utf-8', 'replace'), 1500)}")
        return
    soup = BeautifulSoup(r.content, "lxml")
    print(f"  <title>: {clip(soup.title.get_text() if soup.title else '')}")
    rows = parse_board(r.content, r.url, {}, today_kst())
    _, headers = _pick_rows(soup, None)
    print(f"  rows={len(rows)} headers={headers[:10]}")
    for row in rows[:5]:
        print(
            f"    · {clip(row.title, 70)!r} posted={row.posted} deadline={row.deadline} "
            f"org={row.org!r} label={row.label!r} detail_ok={row.detail_ok}"
        )
        print(f"      url={clip(row.url, 220)} key={clip(row.key, 60)!r}")
    if rows and not rows[0].detail_ok:
        a = next((tr.find("a") for tr in _pick_rows(soup, None)[0] if tr.find("a")), None)
        if a is not None:
            print(f"    first anchor: {describe_anchor(a)}")
            for d in js_defs(soup, (a.get("onclick") or "") + (a.get("href") or "")):
                print(f"    js: {d}")
    for form in soup.find_all("form"):
        hidden = [i.get("name") for i in form.find_all("input", {"type": "hidden"})][:12]
        if form.get("action") or hidden:
            print(f"  form id={form.get('id')!r} action={clip(form.get('action'), 100)!r} method={form.get('method')!r} hidden={hidden}")
    pages = [describe_anchor(a) for a in soup.find_all("a") if re.fullmatch(r"\s*[23]\s*(페이지)?\s*", a.get_text() or "")]
    for p in pages[:2]:
        print(f"  page: {p}")
    if pages:
        a = next(a for a in soup.find_all("a") if re.fullmatch(r"\s*2\s*(페이지)?\s*", a.get_text() or ""))
        for d in js_defs(soup, (a.get("onclick") or "") + (a.get("href") or "")):
            print(f"  page js: {d}")
    if GREP is not None:
        grep_report("page", r.content.decode("utf-8", "replace"))
        if FOLLOW_JS:
            from urllib.parse import urljoin, urlsplit

            host = urlsplit(r.url).hostname
            for sc in soup.find_all("script", src=True):
                js_url = urljoin(r.url, sc["src"])
                if urlsplit(js_url).hostname != host:
                    continue
                try:
                    js = HTTP.get(js_url).content.decode("utf-8", "replace")
                except Exception as exc:  # noqa: BLE001
                    print(f"  js {js_url}: {type(exc).__name__}")
                    continue
                grep_report(js_url, js)
    if RAW:
        html = str(soup.body or soup)
        start = max(0, html.find(RAW_FROM) - 200) if RAW_FROM and RAW_FROM in html else 0
        print(f"  raw[{start}:]: {clip(html[start:], RAW)}")
        srcs = [sc.get("src") for sc in soup.find_all("script") if sc.get("src")]
        print(f"  script src: {srcs[:15]}")
    if not rows:
        seen = set()
        for a in soup.find_all("a", href=True):
            text = clip(a.get_text(), 40)
            if BOARD_LINK.search(text + a["href"]) and a["href"] not in seen and len(seen) < 25:
                seen.add(a["href"])
                print(f"  link: {describe_anchor(a)}")


def attach_report(url: str, template: str | None) -> None:
    """상세 페이지의 첨부 공고문을 내려받아 꺼낸 글자 중 접수·기간 관련 줄과 찾은 마감일을 보여준다."""
    from ulsan_jobs.attachments import extract_text, find_attachments
    from ulsan_jobs.dates import extract_deadline
    from ulsan_jobs.detail import page_soup

    print("-" * 100)
    print(f"ATTACH {url}")
    try:
        soup = page_soup(fetch(url).content)
    except Exception as exc:  # noqa: BLE001
        print(f"  ERROR {type(exc).__name__}: {clip(str(exc), 300)}")
        return
    found = find_attachments(soup, url, template)
    print(f"  attachments: {[(a.name, clip(a.url, 120)) for a in found]}")
    for att in found[:2]:
        try:
            content = fetch(att.url).content
        except Exception as exc:  # noqa: BLE001
            print(f"  {att.name}: ERROR {type(exc).__name__}: {clip(str(exc), 200)}")
            continue
        text = extract_text(content)
        print(f"  {att.name}: {len(content)}B head={content[:8]!r} text={len(text or '')}자 "
              f"deadline={extract_deadline(text, today_kst(), anywhere=False) if text else None}")
        for line in (text or "").splitlines():
            if re.search(r"접\s*수|신\s*청|모\s*집|기\s*간|마\s*감|제\s*출|~", line) and line.strip():
                print(f"    | {clip(line, 200)}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--detail", nargs="*", default=None, help="상세 구조를 볼 소스 id")
    parser.add_argument("--url", nargs="*", default=[], help="상세 구조를 볼 임의 URL")
    parser.add_argument("--links", default=None, help="이 정규식에 맞는 링크를 모두 출력")
    parser.add_argument("--snippet", nargs="*", default=[], help="이 CSS 셀렉터에 맞는 요소의 HTML 출력")
    parser.add_argument("--around", nargs="*", default=None, help="본문에서 이 단어 주변 글 출력")
    parser.add_argument("--no-reach", action="store_true", help="전체 소스 접속 확인을 건너뜀")
    parser.add_argument("--parse", action="store_true", help="--url 들에 게시판 파서를 돌려 결과만 짧게 출력")
    parser.add_argument("--url-file", default=None, help="URL 목록 파일 (한 줄에 하나, # 은 주석)")
    parser.add_argument("--raw", type=int, default=0, help="--parse 에서 본문 HTML 을 이 글자 수만큼 출력")
    parser.add_argument("--raw-from", default="", help="--raw 출력을 이 글자가 처음 나오는 곳부터 시작")
    parser.add_argument("--grep", default=None, help="--parse 에서 HTML 에 이 정규식이 나오는 곳을 출력")
    parser.add_argument("--follow-js", action="store_true", help="--grep 을 같은 사이트의 외부 JS 파일에도 적용")
    parser.add_argument("--browser-ua", action="store_true", help="봇 표시 없는 일반 브라우저 User-Agent 사용")
    parser.add_argument("--legacy-tls", action="store_true", help="--url 호스트에 구형 TLS 허용")
    parser.add_argument("--attach", action="store_true", help="--url(상세 페이지)의 첨부 공고문을 내려받아 글자와 마감일 확인")
    parser.add_argument("--attach-template", default=None, help="--attach 에서 쓸 첨부 주소 틀 (sources.yaml 의 attachment_template)")
    args = parser.parse_args()
    global RAW, RAW_FROM, GREP, FOLLOW_JS
    RAW, RAW_FROM = args.raw, args.raw_from
    GREP = re.compile(args.grep) if args.grep else None
    FOLLOW_JS = args.follow_js
    if args.browser_ua:
        HTTP.session.headers["User-Agent"] = (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
        )
    if args.url_file:
        for line in (ROOT / args.url_file).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith("http"):
                args.url.append(line.split()[0])
    if args.legacy_tls:
        from urllib.parse import urlsplit

        for url in args.url:
            HTTP.allow_legacy_tls(urlsplit(url).hostname or "")
    if args.attach:
        for url in args.url:
            attach_report(url, args.attach_template)
        return 0
    if args.parse:
        logging.basicConfig(level=logging.WARNING, format="  [%(levelname)s] %(message)s")
        for url in args.url:
            parse_report(url, url)
        return 0
    logging.basicConfig(level=logging.INFO, format="  [%(levelname)s] %(message)s")
    global LINK_PATTERN
    LINK_PATTERN = re.compile(args.links or DEFAULT_LINK_PATTERN)
    SNIPPETS.extend(args.snippet)
    if args.around is not None:
        AROUND[:] = args.around

    sources = yaml.safe_load((ROOT / "config" / "sources.yaml").read_text(encoding="utf-8"))["sources"]
    detail_ids = set(args.detail) if args.detail is not None else {s["id"] for s in sources if s.get("phase") == 1}

    print("=" * 100)
    print("REACHABILITY")
    print("=" * 100)
    detail_targets: list[tuple[str, str]] = []
    for s in [] if args.no_reach else sources:
        url = s.get("url")
        if not url or s.get("collector", "").endswith("_api"):
            print(f"- {s['id']:<22} SKIP (url 없음 또는 API)")
            continue
        try:
            r = fetch(url)
            print(f"- {s['id']:<22} {r.status_code} {len(r.content):>8}B final={r.url}")
            if s["id"] in detail_ids:
                detail_targets.append((s["id"], url))
        except Exception as exc:  # noqa: BLE001 - 진단 도구라 모든 오류를 보고
            print(f"- {s['id']:<22} ERROR {type(exc).__name__}: {clip(str(exc), 400)}")

    targets = detail_targets + [(u, u) for u in (args.url or [])]
    if args.detail is None and not args.url and not args.no_reach:
        targets += [(u, u) for u in EXTRA_URLS]

    for label, url in targets:
        print("=" * 100)
        print(f"DETAIL {label}  {url}")
        print("=" * 100)
        try:
            r = fetch(url)
            print(f"  status={r.status_code} final={r.url} content-type={r.headers.get('content-type')}")
            dump_structure(r.content, r.url)
        except Exception as exc:  # noqa: BLE001
            print(f"  ERROR {type(exc).__name__}: {clip(str(exc), 400)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
