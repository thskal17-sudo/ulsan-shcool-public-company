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


@pytest.fixture(autouse=True)
def _no_neis(monkeypatch):
    """시험은 나이스(학교 주소)에 실제로 접속하지 않는다. 필요한 시험은 school_district.neis_addresses 를 다시 바꾼다."""
    from ulsan_jobs import school_district

    monkeypatch.setattr(school_district, "neis_addresses", lambda http, school: [])
