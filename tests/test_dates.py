from datetime import date

import pytest

from ulsan_jobs.dates import dday_label, extract_deadline, parse_date

TODAY = date(2026, 9, 26)


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2026-09-25", date(2026, 9, 25)),
        ("2026.09.25", date(2026, 9, 25)),
        ("2026. 9. 25.(금)", date(2026, 9, 25)),
        ("2026년 9월 25일", date(2026, 9, 25)),
        ("26-09-25", date(2026, 9, 25)),  # 고용24 regDt
        ("9. 25.", date(2026, 9, 25)),
        ("1. 5.", date(2027, 1, 5)),  # 연도 없음: 오늘과 가장 가까운 해
        ("", None),
        ("공지", None),
    ],
)
def test_parse_date(text, expected):
    assert parse_date(text, TODAY) == expected


@pytest.mark.parametrize(
    "text, expected",
    [
        ("2026.9.23.(수)~9.29(화) 12:00", date(2026, 9, 29)),
        ("2026. 9. 23.(수) 15:00 ~ 2026. 10. 1.(목) 10:00", date(2026, 10, 1)),
        ("접수기간 2026. 9. 23.(수) 15:00 ~ 2026. 10. 1.(목) 10:00까지 2026학년도 …", date(2026, 10, 1)),
        ("접수기간 : 2026-09-21 09시 00분 ~ 2026-09-24 13시 00분", date(2026, 9, 24)),
        ("접수기간: 2026. 9. 28.(월) 09:00 ~ 18:00", date(2026, 9, 28)),  # 당일 마감
        ("원서 접수 2026. 12. 28. ~ 1. 5.", date(2027, 1, 5)),  # 해를 넘김
        ("10월 5일(월)까지 방문 접수", date(2026, 10, 5)),
        ("방과후 강사 모집(~10.2.)", date(2026, 10, 2)),
        ("모집 공고", None),
    ],
)
def test_extract_deadline(text, expected):
    assert extract_deadline(text, TODAY) == expected


def test_detail_page_ignores_unlabeled_ranges():
    # 상세 페이지 전체에서는 '접수기간' 같은 표시 옆의 날짜만 믿는다
    text = "작성일 2026-08-25 조회수 905 접수기간 ~ 공고구분 서류 관련문의 052-290-7332 사업기간 2026. 9. 1. ~ 12. 31."
    assert extract_deadline(text, TODAY, anywhere=False) is None


def test_dday_label():
    assert dday_label(date(2026, 9, 29), TODAY) == "D-3"
    assert dday_label(TODAY, TODAY) == "D-day"
    assert dday_label(date(2026, 9, 25), TODAY) == "마감"
    assert dday_label(None, TODAY) == "원문확인"
