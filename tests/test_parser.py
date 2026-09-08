"""파서 회귀 — 문서와 실제가 어긋나는 지점을 고정한다."""
import pytest

from na_mcp.parser import ApiError, ParseError, looks_like_ignored_search, parse_search_response

from . import samples as S


# ── 🔴 핵심: 실제 태그는 `recode` 다 ──────────────────────────────────────────

def test_recode_tag_is_parsed():
    """공식 문서는 `<record>` 라고 적었지만 실제는 `<recode>` 다.

    문서대로 `record` 만 찾으면 **전건 0개 회수 + total 은 정상** — 조용한 절단이 된다.
    """
    total, recs, env = parse_search_response(S.BASIC_OK)
    assert total == 5609
    assert len(recs) == 2
    assert env["record_tag"] == "recode"


def test_doc_shape_record_tag_also_parsed():
    """문서 형태(`<record>` + item 자식 순서 value→name)도 받아준다 — 상류가 문서대로 고칠 수 있다."""
    total, recs, env = parse_search_response(S.DOC_SHAPE_RECORD)
    assert total == 572
    assert len(recs) == 1
    assert env["record_tag"] == "record"
    assert recs[0].control_no == "KINX2021157581"   # 문서판 이름 `controlno` 도 매핑된다


def test_item_child_order_is_not_assumed():
    """`<item>` 자식 순서는 문서(value→name)와 실응답(name→value)이 다르다 — 태그명으로 찾는다."""
    _, doc_recs, _ = parse_search_response(S.DOC_SHAPE_RECORD)
    _, live_recs, _ = parse_search_response(S.BASIC_OK)
    assert doc_recs[0].pub_year == "2022"
    assert live_recs[0].pub_year == "2026"


# ── 필드 매핑 ────────────────────────────────────────────────────────────────

def test_korean_field_names_map_to_columns():
    _, recs, _ = parse_search_response(S.BASIC_OK)
    art, book = recs
    assert art.control_no == "KINX2026037525"
    assert art.title.startswith("디지털 매체와")      # 기사명 → title
    assert art.authors == "최여진"                   # 저자명 → authors
    assert art.journal.startswith("한국초등교육")      # 수록지명 → journal
    assert art.has_fulltext == "Y" and art.fulltext_available()
    # 자료종이 다르면 필드 이름도 다르다 — 같은 컬럼으로 모여야 한다
    assert book.title.startswith("교육불평등과")       # 자료명 → title
    assert book.call_no == "370.951 -26-7"
    assert book.isbn == "9791168616059"
    assert book.has_fulltext == "N" and not book.fulltext_available()


def test_raw_preserves_every_original_name():
    """정규화가 버린 값을 되찾을 수 있어야 한다 — 미지 필드도 raw 에 남는다."""
    _, recs, _ = parse_search_response(S.BASIC_OK)
    assert recs[0].raw["본문언어"] == "kor"
    assert recs[0].raw["저작권허락"] == "N"
    assert "제어번호" in recs[0].raw


def test_dedup_key_uses_control_no():
    _, recs, _ = parse_search_response(S.BASIC_OK)
    assert recs[0].dedup_key() == "cn:KINX2026037525"
    assert recs[0].dedup_key() != recs[1].dedup_key()


# ── 0건 / 오류 ───────────────────────────────────────────────────────────────

def test_zero_results_is_not_an_error():
    """결과 0건이면 `<recode>` 가 아예 없다 — 오류로 올리면 오타 검색이 매번 오류가 된다."""
    total, recs, _ = parse_search_response(S.EMPTY_OK)
    assert total == 0 and recs == []


def test_error_envelope_has_different_root():
    with pytest.raises(ApiError) as e:
        parse_search_response(S.NO_KEY_ERR)
    assert e.value.code == "20"
    assert not e.value.retryable          # 접근거부는 재시도 무의미


def test_http_error_04_is_retryable():
    """🔴 04 를 종료 조건으로 쓰면 깊은 페이지에서 수집이 임의로 잘린다(실측: 간헐 발생)."""
    with pytest.raises(ApiError) as e:
        parse_search_response(S.HTTP_ERR_04)
    assert e.value.code == "04"
    assert e.value.retryable


# ── 조용한 절단 방지 ─────────────────────────────────────────────────────────

def test_total_positive_but_no_records_raises_on_first_page():
    """태그가 또 바뀌면 **시끄럽게** 실패해야 한다 — 이 프로젝트의 존재 이유다."""
    with pytest.raises(ParseError, match="레코드가 0개"):
        parse_search_response(S.UNKNOWN_TAG, expect_records=True)


def test_past_the_end_page_is_allowed_to_be_empty():
    """끝을 지난 페이지는 정상적으로 비어 있다 — 오류로 올리면 마지막 페이지마다 재시도한다.

    실제로 그렇게 만들었다가 검색어 하나당 3회씩 쿼터를 태우는 것을 잡았다.
    """
    total, recs, _ = parse_search_response(S.UNKNOWN_TAG, expect_records=False)
    assert total == 5609 and recs == []


# ── 자격증명 누출 방지 ───────────────────────────────────────────────────────

def test_envelope_strips_credential_keys():
    """`na_status` 가 봉투를 도구 응답에 싣는다 — 인증키가 에코되면 LLM 트랜스크립트로 샌다.

    자매 프로젝트 kci 에서 실제로 발생한 사고다.
    """
    with pytest.raises(ApiError):
        parse_search_response(S.ECHO_KEY_ERR)   # resultCode=10 이라 오류로 올라간다


def test_ignored_search_detection():
    """미지원 검색항목 → 전체 카탈로그 반환. 사후 탐지선이 동작하는지."""
    assert looks_like_ignored_search(13_097_591)
    assert not looks_like_ignored_search(5_609)


# ── 상세정보조회 서비스 (detailinfoservice) ─────────────────────────────────
from na_mcp.parser import parse_detail_response, parse_toc_response  # noqa: E402


def test_detail_items_are_flat_not_wrapped():
    """🔴 검색과 봉투가 다르다 — `<recode>` 래퍼 없이 `<item>` 이 최상위에 온다."""
    fields, env = parse_detail_response(S.DETAIL_OK)
    assert fields["제어번호"] == "KDMT12026000034521"
    assert fields["논문명"] == "한국 교육불평등의 제도적 경로"
    assert fields["지도교수"] == "유성상"        # 검색 카테고리명(`지도교수(2009~)`)과 다르다
    assert len(fields) == 8


def test_detail_envelope_has_no_body_leftovers():
    """봉투에 본문 태그(item/toc)가 섞이면 안 된다 — 빈 'item' 키가 끼던 것을 고쳤다."""
    _fields, env = parse_detail_response(S.DETAIL_OK)
    assert env.get("resultCode") == "00"
    assert "item" not in env and "toc" not in env


def test_detail_doc_shape_child_order_also_works():
    """문서는 value→name 순으로 적었다 — 순서가 아니라 태그명으로 찾아야 한다."""
    fields, _ = parse_detail_response(S.DETAIL_DOC_SHAPE)
    assert fields["제어번호"] == "MONO1201027232"
    assert fields["DB"] == "인터넷자료"


def test_detail_without_items_raises():
    """빈 dict 로 통과시키면 '자료 없음'과 '스키마 변경'을 구분할 수 없다."""
    with pytest.raises(ParseError, match="item"):
        parse_detail_response(S.EMPTY_OK)


def test_toc_is_unescaped_and_tags_become_newlines():
    """🔴 목차는 HTML 이 이스케이프되어 온다 — 풀지 않으면 태그 범벅 한 줄이 된다."""
    toc, env = parse_toc_response(S.TOC_OK)
    assert "&lt;" not in toc and "<BR>" not in toc and "<p>" not in toc
    assert "추천의 글" in toc and "들어가면서" in toc
    assert "\n" in toc                       # <BR> 이 줄바꿈이 됐다
    assert env["has_toc"] is True


def test_toc_position_is_not_assumed():
    """⚠️ `<toc>` 가 `<header>` **앞**에 온다(detail 과 순서가 다르다) — 태그명으로 찾는다."""
    toc, _ = parse_toc_response(S.TOC_OK)
    assert toc.startswith("추천의 글")


def test_empty_toc_is_normal():
    """목차가 없는 자료도 정상이다 — 오류로 올리면 'N' 자료마다 실패한다."""
    toc, env = parse_toc_response(S.TOC_EMPTY)
    assert toc == "" and env["has_toc"] is False


def test_detail_error_envelope_raises_apierror():
    """없는 제어번호는 ERR04 로 온다(전용 '자료 없음' 코드가 없다)."""
    with pytest.raises(ApiError) as e:
        parse_detail_response(S.HTTP_ERR_04)
    assert e.value.code == "04"


# ── 🔴 검색 하이라이트 마크업 제거 ──────────────────────────────────────────
# 실제 검색 결과에서 저자명이 `<font color="red">양연동</font>` 으로 나온 것을 잡았다.
# 초판 파서에 정제가 없어 마크업이 정규화 필드·중복제거·xlsx/csv 로 그대로 흘렀다.

def test_highlight_markup_is_stripped_from_fields():
    _total, recs, _env = parse_search_response(S.HIGHLIGHTED)
    r = recs[0]
    assert r.authors == "양연동"              # <font …> 가 사라져야 한다
    assert "<" not in r.authors and "font" not in r.authors
    assert r.title.startswith("경계선지능 학습자")
    assert r.advisor == "손준종"


def test_raw_keeps_the_original_markup():
    """`raw` 는 원문을 남긴다 — 어느 필드가 매칭됐는지가 그 자체로 정보다."""
    _total, recs, _env = parse_search_response(S.HIGHLIGHTED)
    assert '<font color="red">' in recs[0].raw["저자명"]


def test_keyword_multi_space_separator_survives():
    """⚠️ 공백을 접으면 안 된다 — `키워드` 는 여러 칸 공백을 **구분자로** 쓴다.

    자매 프로젝트(국립중앙도서관)는 토큰마다 span 이 붙어 공백 정규화가 필요했지만,
    여기는 필드 전체를 한 번 감싸는 형태라 태그만 지우면 된다. 사실을 이식하지 말 것.
    """
    _total, recs, _env = parse_search_response(S.HIGHLIGHTED)
    assert "경계선지능   느린학습자" in recs[0].keywords


def test_dedup_key_unaffected_by_markup():
    """마크업이 남으면 제목 기반 폴백 중복제거가 어긋난다."""
    _total, recs, _env = parse_search_response(S.HIGHLIGHTED)
    assert recs[0].dedup_key() == "cn:KDMT12025000021882"


def test_clean_html_handles_none_and_plain():
    from na_mcp.models import clean_html
    assert clean_html(None) == ""
    assert clean_html("  평문  ") == "평문"
    assert clean_html("<b>a</b><i>b</i>") == "ab"


# ── 🔴 목차 센티널: 플래그는 Y 인데 본문이 `목차정보없음` 뿐인 자료 ────────────
# ✅ 실측(고서 목차=Y 27건 중 표본 5/5). 최대 792자까지 나와 `if toc:` 를 통과하므로
#    걸러내지 않으면 '목차 있음'으로 오인하고 코퍼스에 쓰레기가 쌓인다.

TOC_SENTINEL_XML = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><response>\
<toc>&lt;p&gt;목차정보없음&lt;p&gt;&lt;p&gt;목차정보없음&lt;p&gt;&lt;p&gt;목차정보없음&lt;p&gt;</toc>\
<header><resultMsg>NORMAL_CODE</resultMsg><resultCode>00</resultCode></header></response>"""

TOC_SENTINEL_NOTE = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?><response>\
<toc>&lt;p&gt;목차정보없음[내용누락;p.165-165]&lt;p&gt;</toc>\
<header><resultMsg>NORMAL_CODE</resultMsg><resultCode>00</resultCode></header></response>"""


def test_toc_sentinel_is_treated_as_no_toc():
    toc, env = parse_toc_response(TOC_SENTINEL_XML)
    assert toc == ""
    assert env["has_toc"] is False
    assert "toc_sentinel" in env          # 원문은 진단용으로 남는다


def test_toc_sentinel_with_note_suffix():
    """`[내용누락;p.165-165]`·`[원문불량;p.51]` 주기가 붙어도 센티널이다."""
    toc, env = parse_toc_response(TOC_SENTINEL_NOTE)
    assert toc == "" and env["has_toc"] is False


def test_real_toc_is_not_mistaken_for_sentinel():
    """진짜 목차는 그대로 살아야 한다 — 센티널 검사가 과잉이면 안 된다."""
    toc, env = parse_toc_response(S.TOC_OK)
    assert "추천의 글" in toc and env["has_toc"] is True


def test_toc_paragraph_tag_becomes_newline():
    """🔴 실제 주력 구분자는 `<p>` 다(실측 10/10). `<br>` 만 처리하면 전체가 한 줄로 뭉갠다."""
    xml = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?><response><toc>"
           "&lt;p&gt;제1장 서론 1&lt;p&gt;&lt;p&gt; 1. 배경 3&lt;p&gt;</toc>"
           "<header><resultCode>00</resultCode></header></response>")
    toc, _ = parse_toc_response(xml)
    assert "\n" in toc
    assert toc.split("\n")[0] == "제1장 서론 1"
    assert " 1. 배경 3" in toc.split("\n")   # 계층 들여쓰기 보존


# ── 🔴 목차 파서에만 resultCode 검사가 빠져 있었다 (2026-09-08) ─────────────────
# 검색·상세 파서는 `./header/resultCode` 를 보는데 목차 파서만 안 봤다.
# `_check_error_envelope` 는 게이트웨이 봉투(루트가 <OpenAPI_ServiceResponse>)만 잡으므로,
# 루트가 <response> 인 채 코드만 비정상인 응답(ECHO_KEY_ERR = 10)은 그대로 통과했다.
# 그 응답에는 <toc> 가 없어 **오류가 '목차 없음'으로 조용히 바뀌었다.**

def test_toc_result_code_error_raises_instead_of_empty():
    xml = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?><response>"
           "<header><resultMsg>ERR</resultMsg><resultCode>10</resultCode></header>"
           "</response>")
    with pytest.raises(ApiError) as e:
        parse_toc_response(xml)
    assert e.value.code == "10"


def test_toc_result_code_error_is_not_reported_as_missing_toc():
    """이 실패 양식의 본질 — 오류를 '목차 없음'으로 바꿔 돌려주면 대량 보강에서
    실패 전건이 '목차 없는 자료'로 굳는다. 코드가 비정상이면 반드시 올라와야 한다."""
    xml = ("<?xml version=\"1.0\" encoding=\"UTF-8\"?><response>"
           "<toc></toc>"
           "<header><resultMsg>ERR</resultMsg><resultCode>22</resultCode></header>"
           "</response>")
    with pytest.raises(ApiError):
        parse_toc_response(xml)


def test_toc_result_code_00_still_passes():
    """정상 코드는 그대로 통과해야 한다 — 검사가 과잉이면 전건이 죽는다."""
    toc, env = parse_toc_response(S.TOC_OK)
    assert env["has_toc"] is True and "추천의 글" in toc
