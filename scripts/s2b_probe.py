"""S2B(학교장터) 공개 목록 구조 확인용 임시 진단 스크립트.

지정한 URL 과, 그 안의 iframe·스크립트·폼·링크 중 s2b.kr / pen.go.kr 주소를 한 단계 더 받아
out/probe/ 에 그대로 저장한다 (상태 코드·헤더는 index.txt).
"""
from __future__ import annotations

import hashlib
import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlsplit

import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
OUT = Path("out/probe")
OUT.mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})

seen: set[str] = set()
index: list[str] = []


def save(url: str, resp: requests.Response | None, err: str = "") -> str:
    name = hashlib.md5(url.encode()).hexdigest()[:10]
    if resp is not None:
        ext = ".js" if url.split("?")[0].endswith(".js") else ".html"
        (OUT / (name + ext)).write_bytes(resp.content)
        index.append(
            f"{name}{ext}\t{resp.status_code}\t{len(resp.content)}\t{resp.headers.get('content-type','')}\t{resp.url}\t<- {url}"
        )
    else:
        index.append(f"-\tERR\t0\t{err}\t{url}")
    return name


def fetch(url: str, depth: int, referer: str | None = None) -> None:
    if url in seen or len(seen) > 40:
        return
    seen.add(url)
    headers = {"Referer": referer} if referer else {}
    try:
        r = S.get(url, timeout=25, headers=headers)
    except Exception as exc:  # noqa: BLE001
        save(url, None, f"{type(exc).__name__}: {exc}"[:200])
        return
    save(url, r)
    if depth <= 0:
        return
    enc = r.encoding or "utf-8"
    if r.apparent_encoding and "euc" in (r.apparent_encoding or "").lower():
        enc = r.apparent_encoding
    text = r.content.decode(enc, errors="replace")
    cands = re.findall(r"""(?:src|href|action)\s*=\s*["']([^"'#]+)["']""", text, re.I)
    cands += re.findall(r"""["'](/S2BN[^"']+|https?://[^"']*s2b\.kr[^"']*)["']""", text)
    for c in cands:
        u = urljoin(r.url, c.strip())
        host = urlsplit(u).hostname or ""
        if not (host.endswith("s2b.kr") or host.endswith("pen.go.kr")):
            continue
        low = u.lower()
        if any(low.split("?")[0].endswith(x) for x in (".css", ".png", ".gif", ".jpg", ".ico", ".svg", ".woff", ".woff2")):
            continue
        if "pen.go.kr" in host and "cntnts" not in low and "s2b" not in low:
            continue
        fetch(u, depth - 1, r.url)


def main() -> None:
    urls = [line.strip() for line in Path(sys.argv[1]).read_text().splitlines() if line.strip() and not line.startswith("#")]
    for u in urls:
        fetch(u, 0)
    (OUT / "index.txt").write_text("\n".join(index) + "\n", encoding="utf-8")
    print("\n".join(index))


if __name__ == "__main__":
    main()
