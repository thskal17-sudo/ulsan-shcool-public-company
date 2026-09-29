import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

FIXTURES = Path(__file__).resolve().parent / "fixtures"


@pytest.fixture
def fixture_bytes():
    def read(name: str) -> bytes:
        return (FIXTURES / name).read_bytes()

    return read


@pytest.fixture
def rules():
    from ulsan_jobs.config import load_rules

    return load_rules(ROOT / "config")


class _NoS2B:
    """테스트 중에는 S2B 에 실제로 접속하지 않는다 (목록 0건)."""

    def __init__(self, *args, **kwargs):
        self.calls = 0

    def list_page(self, region, start, end, page):
        return [], 1

    def detail(self, code, kind):
        raise AssertionError("상세 조회가 일어나면 안 됨")


@pytest.fixture(autouse=True)
def _offline_s2b(monkeypatch):
    from ulsan_jobs import s2b

    monkeypatch.setattr(s2b, "S2BClient", _NoS2B)
