"""S2B 견적요청 목록 POST 조회·상세 보기 진단 (임시)."""
import json
from pathlib import Path

import requests

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36"
OUT = Path("out/probe")
OUT.mkdir(parents=True, exist_ok=True)
S = requests.Session()
S.headers.update({"User-Agent": UA, "Accept-Language": "ko-KR,ko;q=0.9"})
B = "https://www.s2b.kr/S2BNCustomer/"
index = []
for i, req in enumerate(json.loads(Path("scripts/s2b_probe2.json").read_text(encoding="utf-8"))):
    data = {k: str(v).encode("euc-kr") for k, v in (req.get("data") or {}).items()}
    try:
        if req.get("method") == "GET":
            r = S.get(B + req["path"], params=data, timeout=30)
        else:
            r = S.post(B + req["path"], data=data, timeout=30)
        (OUT / f"r{i:02d}.html").write_bytes(r.content)
        index.append(f"r{i:02d}.html\t{r.status_code}\t{len(r.content)}\t{r.url}\t{req.get('note','')}")
    except Exception as exc:  # noqa: BLE001
        index.append(f"r{i:02d}\tERR\t{exc}\t{req.get('note','')}")
(OUT / "index.txt").write_text("\n".join(index) + "\n", encoding="utf-8")
print("\n".join(index))
