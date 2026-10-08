import pytest

from ulsan_jobs.classify import categorize, infer_district, infer_org_type, judge


@pytest.mark.parametrize(
    "title, keyword_filter, label, expected",
    [
        ("2026학년도 척과초 초등방과후 프로그램 개인위탁 독서논술 강사 모집", True, "", "모집중"),
        ("2026년 하반기 울산대공원 수영장 교육강사(프리랜서) 모집 공고", True, "", "모집중"),
        ("생활체육지도자 채용 공고", True, "", "모집중"),
        ("2026학년도 2학기 방과후학교 프로그램 운영 공고(바이올린)", True, "방과후강사(관련)", "모집중"),
        ("울산가온고등학교 영어과 기간제 교원 채용 공고", True, "기간제교원(보결)", None),
        ("울산시설공단 임원(이사장·비상임이사) 공개모집 공고", True, "", None),
        ("2026년 수영강습 회원모집", True, "", None),
        ("여성회관 2학기 수강생 모집", True, "", None),
        ("수영강사 채용 최종합격자 공고", True, "", None),
        ("방과후 강사 서류전형 결과 안내", True, "", None),
        ("공모 안내", False, "", "모집중"),  # 강사 전용 게시판은 키워드 검사 생략
        ("[마감] 울산동천고등학교 시간강사 채용 재공고(미술)", False, "강사", None),
        ("[주의: 접수 마감되었습니다.] 2026학년도 2학기 시간강사 채용 공고(일본어)", False, "강사", None),
        ("[서부청소년수련관]2026년 청소년방과후아카데 하반기 스포츠강사 최종적격", False, "", None),
        ("중구수영장 시간강사(프리랜서) 서류적격 대상자 발표 및 면접심사 시행 공고", True, "", None),
        ("청소년수련활동 인증프로그램 우수기관·지도자 포상 및 수기공모전 참가 안내", True, "", None),
        ("2026학년도 늘봄학교 프로그램 운영 용역 입찰공고(적격심사)", True, "", "모집중"),
        ("[중부청소년수련관]2026년 문화강좌 4분기 교육강사 긴급 위·수탁 모집 최종수탁자 공고(안)", False, "", None),
        ("[남부청소년수련관] 2026년 남부청소년수련관 주말 수영 강사(긴급) 위·수탁 대상 결정 공고", False, "", None),
        ("2026년 노동복지관 수영장안전관리 단시간근로자 서류심사 결정 공고(5차)", False, "", None),
        ("2026년 4학기 북구문화예술회관 아카데미 강의계획서 및 강사소개서", True, "", None),
        ("2026학년도 늘봄학교 자원봉사자 모집 공고", True, "", None),
        ("울산광역시남구자원봉사센터 교육강사 모집", True, "", "모집중"),
    ],
)
def test_judge(rules, title, keyword_filter, label, expected):
    assert judge(title, rules, keyword_filter, label) == expected


def test_keep_result_notices(rules):
    rules.keep_result_notices = True
    assert judge("수영강사 채용 최종합격자 공고", rules, True) == "결과공고"


def test_categorize(rules):
    assert categorize("척과초 초등방과후 프로그램 개인위탁 독서논술 강사 모집", "척과초등학교", rules) == "방과후·늘봄"
    assert categorize("독서논술 강사 모집", "척과초등학교", rules) == "학교 기타"
    assert categorize("수영장 교육강사 모집", "울산시설공단", rules) == "체육·수영"
    assert categorize("도서관 인문학 강사 모집", "", rules) == "평생교육·문화"
    assert categorize("2026학년도 기간제(시간강사)교원 (화학)채용 공고", "강동고등학교", rules) == "학교 시간강사"
    assert categorize("강사 모집", "", rules) == "기타"


def test_categorize_negative_terms(rules):
    # 공단 수영장의 시간강사는 학교 시간강사가 아니고, 청소년방과후아카데미는 학교 방과후가 아님
    assert categorize("중구수영장 시간강사(수영) 위촉 공고", "중구도시관리공단", rules) == "체육·수영"
    assert categorize("2026 청소년방과후아카데미 스포츠강사 (긴급) 위수탁", "울주군시설관리공단", rules) == "체육·수영"
    assert categorize("울산동천고등학교 시간강사 채용 공고(일본어)", "울산동천고등학교", rules) == "학교 시간강사"
    assert categorize("여성회관 교육강사(3학기 단기특강) 모집 공고", "울산광역시여성회관", rules) == "평생교육·문화"


def test_infer_org_type_and_district():
    assert infer_org_type("울산광역시남구도시관리공단") == "공단(체육시설)"
    assert infer_org_type("척과초등학교") == "교육청·학교"
    assert infer_org_type("울산문화예술회관") == "문화시설"
    assert infer_org_type("울산중구구립도서관") == "평생교육·도서관"
    assert infer_org_type("무엇", "기본") == "기본"
    assert infer_district("울산광역시 동구") == "동구"
    assert infer_district("울산광역시", "울산전체") == "울산전체"
