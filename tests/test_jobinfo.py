from ulsan_jobs.jobinfo import extract_info

# HWP 공고문을 글자로 풀면 표의 칸이 한 줄씩 나온다
NOTICE_TABLE = """
2026년 하반기 울산대공원 수영장 교육강사(프리랜서) 추가 위촉 공고
1. 모집분야 및 인원
모집분야
모집인원
위촉기간
수영(초급반)
2명
2026. 10. 1. ~ 2026. 12. 31.
2. 지원자격
- 수영 관련 자격증 소지자
- 강습 경력 1년 이상인 자
3. 접수기간 : 2026. 9. 22.(월) ~ 9. 26.(금)
4. 제출서류 : 이력서 1부, 자격증 사본 1부
5. 접수방법 : 이메일 접수 (swim@uic.or.kr)
"""

PAGE_BULLETS = """
홈 > 알림마당 > 공지사항
○ 교육기간: 2026.10.6.~12.15. (10주)
○ 수업시간: 매주 화·목 14:00~15:30
○ 교육대상: 초등 3~4학년 20명
○ 모집인원: 1명
○ 지원자격: 관련 자격증 소지자
문의: 052-000-0000
E-mail: webmaster@ulsan.go.kr
"""


def test_table_layout_from_attachment():
    info = extract_info(NOTICE_TABLE)
    assert "field" not in info  # 모집 분야는 표 칸 맞추기에만 쓴다
    assert info["headcount"] == 2
    assert info["schedule"] == "2026. 10. 1. ~ 2026. 12. 31."
    assert info["qualification"] == "수영 관련 자격증 소지자\n강습 경력 1년 이상인 자"
    assert info["documents"] == "이력서 1부, 자격증 사본 1부"
    assert info["email"] == "swim@uic.or.kr"


def test_labeled_lines_from_page():
    info = extract_info(None, PAGE_BULLETS)
    assert info["schedule"] == "2026.10.6.~12.15. (10주) / 매주 화·목 14:00~15:30"
    assert info["target"] == "초등 3~4학년 20명"
    assert info["headcount"] == 1
    assert info["qualification"] == "관련 자격증 소지자"
    assert "email" not in info  # 사이트 바닥글 관리자 메일은 접수 이메일이 아님


def test_attachment_wins_over_page():
    info = extract_info("위촉기간: 2026. 11. 1. ~ 11. 30.", PAGE_BULLETS)
    assert info["schedule"] == "2026. 11. 1. ~ 11. 30."
    assert info["target"] == "초등 3~4학년 20명"  # 첨부에 없는 칸은 페이지에서


def test_spaced_labels_and_values_on_next_line():
    info = extract_info("위 촉 기 간\n2026. 10. 1.(수) ~ 12. 31.(수)\n모 집 인 원\n○명")
    assert info["schedule"] == "2026. 10. 1.(수) ~ 12. 31.(수)"
    assert "headcount" not in info  # 인원이 숫자로 없으면 비움


def test_ambiguous_or_sentence_labels_are_ignored():
    text = "\n".join([
        "지원자격을 갖춘 자는 누구나 지원할 수 있습니다.",  # 문장 속 낱말
        "위촉기간 중 결격사유가 발생하면 해촉합니다.",  # 일정 값에 숫자가 없음
        "모집인원: 과목별 각 1명",  # 과목별 인원은 한 숫자로 못 씀
    ])
    assert extract_info(text) == {}


def test_long_values_are_capped():
    info = extract_info("교육대상: " + "가" * 100)
    assert len(info["target"]) == 60 and info["target"].endswith("…")


def test_school_hiring_period_under_table_header():
    # 학교 시간강사 공고: '채용기간' 머리 칸 아래 다른 칸들 뒤에 날짜가 온다 (울산여고·대송고 공고문 모양)
    ulsan_girls = "\n".join([
        "1. 모집 내용", "교과", "인원", "채용기간", "비고", "국어", "1명",
        "2026.10.19.(월) ~ 10.23.(금)/5일", "장기재직휴가 대체", "2. 지원 자격", "- 중등교원자격증 소지자",
    ])
    assert extract_info(ulsan_girls)["schedule"] == "2026.10.19.(월) ~ 10.23.(금)/5일"
    daesong = "\n".join([
        "1. 모집내용", "과목", "채용기간", "생물", "1", "2026. 10. 23.(금)", "~ 2026. 10. 26.(월) (4일)",
        "화학", "1", "2026. 10. 27.(화)", "~ 2026. 10. 30.(금) (5일)", "2. 응시자격",
    ])
    assert extract_info(daesong)["schedule"] == "2026. 10. 23.(금) ~ 2026. 10. 26.(월) (4일)"


def test_schedule_search_stops_at_next_section():
    # 채용기간 칸이 비어 있으면 다음 항목의 접수기간 날짜를 가져오지 않는다
    text = "채용기간\n추후 안내\n2. 접수기간\n2026. 9. 22. ~ 9. 29."
    assert "schedule" not in extract_info(text)


def test_misaligned_table_value_is_not_a_schedule():
    # 울산마이스터고 공고문: '강사료 / 계약기간' 머리 칸 두 개 뒤의 값 칸은 표 첫 줄('TPM전기', '1')이라
    # 일정이 '1'로 들어갔다. 날짜 모양이 아니면 버리고 뒤의 '바. 계약기간:' 줄을 쓴다
    text = "\n".join([
        "1. 모집 분야 및 인원",
        "모집", "분야", "모집", "인원", "대상", "학년", "운영요일 및", "시간", "강사료", "계약기간",
        "TPM전기", "1", "1", "수요일", "19:20~21:00", "2", "시간당", "강사료", "40,000원",
        "2026. 10. 14.", "~ 2026. 11. 27",
        "2. 모집 세부 사항",
        "나. 서류 접수: 2026년 9월 28일(월) ∼ 2026년 10월 1일(목) 16:30까지",
        "바. 계약기간: 2026년 10월 14일∼2026년 11월 27일",
        "3. 지원 조건 ※ 응시 연령 제한 없음",
        "가. 해당 분야에 전문적인 능력을 가진 자 또는 자격증 소지자",
        "4. 제출서류",
    ])
    info = extract_info(text)
    assert info["schedule"] == "2026년 10월 14일∼2026년 11월 27일"
    assert info["qualification"] == "※ 응시 연령 제한 없음\n해당 분야에 전문적인 능력을 가진 자 또는 자격증 소지자"


def test_schedule_needs_a_date():
    assert "schedule" not in extract_info("운영기간: 1")
    assert "schedule" not in extract_info("교육기간: 19:20~21:00")
    assert extract_info("운영기간: 10월 ~ 12월")["schedule"] == "10월 ~ 12월"
