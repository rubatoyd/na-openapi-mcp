"""안전장치 회귀 — 검색항목 화이트리스트 · 인증키 취급 · 출력 경로 · 전송 기본값.

자매 프로젝트에서 값을 치른 결함들의 na 판. 경고 문자열은 테스트가 잘 닿지 않는데
**이 프로젝트의 존재 이유가 바로 그 경고**라 회귀로 고정한다.
"""
import importlib
import pathlib

import pytest

from na_mcp import config as C
from na_mcp import server as s
from na_mcp.exporters import export, safe_name
from na_mcp.models import Record

from . import samples as S


# ── 🔴 검색항목 화이트리스트 (이 API 최대의 함정) ────────────────────────────

@pytest.mark.parametrize("field", list(C.SEARCH_FIELDS_BASIC))
def test_valid_search_fields_pass(field):
    assert C.validate_search(f"{field},교육")


@pytest.mark.parametrize("field", ["저자명", "ISBN", "발행년도", "제목", "zzz", "존재하지않는필드"])
def test_unsupported_search_field_is_rejected(field):
    """🔴 실측: 미지원 항목은 오류가 아니라 **검색어를 무시하고 전체 13,097,591건**을 준다.

    조용히 넘기면 호출자가 그것을 '검색 결과'로 오인한다. 여기서 막는 것이 유일한 방어선이다.
    """
    with pytest.raises(C.SearchFieldError):
        C.validate_search(f"{field},오욱환")


def test_rejection_message_suggests_the_right_field():
    """`저자명` 은 상세검색에서는 유효해 헷갈리기 쉽다 — 대안을 알려줘야 한다."""
    with pytest.raises(C.SearchFieldError, match="저자"):
        C.validate_search("저자명,오욱환")


def test_search_without_comma_is_rejected():
    with pytest.raises(C.SearchFieldError):
        C.validate_search("그냥키워드")


def test_and_clauses_all_validated():
    """`|` 로 이어진 절이 여러 개면 **전부** 검증해야 한다(뒤쪽만 틀린 경우를 놓치기 쉽다)."""
    with pytest.raises(C.SearchFieldError):
        C.validate_search("전체,교육|저자명,홍길동")


# ── 인증키 ───────────────────────────────────────────────────────────────────

def test_build_url_appends_encoded_key_without_double_encoding(monkeypatch):
    """🔴 실측: 이 API 는 **Encoding 키를 URL 에 직접 결합**해야 한다.

    requests 의 params= 로 넘기면 `%2B` 가 `%252B` 로 이중 인코딩되어 ERR04 가 난다.
    키 결합을 build_url 한 곳에 몰아둔 이유가 이것이다.
    """
    monkeypatch.setenv("NA_API_KEY_ENCODED", "abc%2Bdef%3D%3D")
    monkeypatch.delenv("NA_API_KEY", raising=False)
    url = C.build_url("https://x/y", {"pageno": 1, "search": "전체,교육"})
    assert "serviceKey=abc%2Bdef%3D%3D" in url      # 그대로 실려야 한다
    assert "%252B" not in url                        # 이중 인코딩 금지


def test_decoded_key_is_encoded_once(monkeypatch):
    monkeypatch.delenv("NA_API_KEY_ENCODED", raising=False)
    monkeypatch.setenv("NA_API_KEY", "abc+def==")
    assert C.get_api_key_encoded() == "abc%2Bdef%3D%3D"


def test_scrub_removes_key_in_every_representation(monkeypatch):
    monkeypatch.setenv("NA_API_KEY", "abc+def==")
    monkeypatch.setenv("NA_API_KEY_ENCODED", "abc%2Bdef%3D%3D")
    msg = "실패 https://x/y?serviceKey=abc%2Bdef%3D%3D&pageno=1 (원문 abc+def==)"
    out = C.scrub(msg)
    assert "abc+def==" not in out and "abc%2Bdef%3D%3D" not in out
    assert "***KEY***" in out


def test_redact_masks():
    assert C.redact("1234567890") == "1234…90"
    assert C.redact(None) == "(none)"


# ── 출력 경로 이탈 (자매 3곳 모두에 있던 결함) ───────────────────────────────

@pytest.mark.parametrize("bad", ["../escaped", "..\\escaped", "a/../../b", "CON", "  ..  ",
                                 "x/y", "<script>"])
def test_output_stays_inside_out_dir(tmp_path, bad):
    rec = Record(control_no="c1", title="t")
    paths = export([rec], ["csv"], str(tmp_path), bad)
    for p in paths:
        assert pathlib.Path(p).parent.resolve() == tmp_path.resolve()


def test_safe_name_never_empty():
    assert safe_name("") and safe_name("...") and safe_name("///")


def test_unknown_format_is_rejected(tmp_path):
    with pytest.raises(ValueError, match="지원하지 않는 출력형식"):
        export([Record()], ["docx"], str(tmp_path), "x")


# ── 전송 선택 (기본은 반드시 stdio) ──────────────────────────────────────────

@pytest.fixture
def spy(monkeypatch):
    calls = {}
    monkeypatch.setattr(s.mcp, "run", lambda **kw: calls.update(kw))
    host, port = s.mcp.settings.host, s.mcp.settings.port
    yield calls
    s.mcp.settings.host, s.mcp.settings.port = host, port


def test_default_is_stdio(spy):
    """기존 등록은 인자 없이 서버를 띄우므로 기본이 바뀌면 모든 사용자의 MCP 가 죽는다."""
    s.main([])
    assert spy["transport"] == "stdio"


@pytest.mark.parametrize("transport", ["sse", "streamable-http"])
def test_http_transports_selectable(spy, transport):
    s.main(["--transport", transport])
    assert spy["transport"] == transport


def test_unknown_args_do_not_kill_server(spy):
    s.main(["--bogus", "x"])
    assert spy["transport"] == "stdio"


def test_non_numeric_port_env_is_ignored(spy, monkeypatch):
    monkeypatch.setenv("NA_MCP_PORT", "abc")
    before = s.mcp.settings.port
    s.main([])
    assert s.mcp.settings.port == before


# ── 자매 프로젝트 공통 규율 ──────────────────────────────────────────────────

@pytest.mark.parametrize("mod", [p.stem for p in
                                 sorted(pathlib.Path(__file__).parents[1]
                                        .glob("src/na_mcp/*.py"))
                                 if p.stem != "__init__"])
def test_every_module_imports(mod):
    """문법 오류 조기 검출 — 자매 프로젝트에서 cli.py 가 깨진 채 테스트를 통과한 적이 있다."""
    importlib.import_module(f"na_mcp.{mod}")


def test_version_is_read_from_installed_metadata():
    """하드코딩하면 릴리스마다 pyproject 와 어긋난다(자매 프로젝트에서 실제로 방치됐다)."""
    import na_mcp
    assert na_mcp.__version__[0].isdigit()


def test_record_cap_matches_measured_limits():
    """✅ 실측: pageno<=99, displaylines<=1000 → 회수 한계 99,000."""
    assert C.PAGENO_MAX == 99
    assert C.MAX_DISPLAYLINES == 1000
    assert C.API_RECORD_CAP == 99_000


def test_safe_decorator_converts_exceptions_to_dict():
    """MCP 경계 밖으로 예외가 새면 클라이언트가 깨진다."""
    @s._safe
    def boom():
        raise RuntimeError("터짐")
    out = boom()
    assert isinstance(out, dict) and "error" in out and "터짐" in out["error"]


# ── 🔴 상세검색(/detail)은 dbname 별 검색항목 어휘를 강제한다 ────────────────
# ✅ 실측: 통합검색과 **정반대로 동작한다**.
#    /basic  : 틀린 검색항목 → 오류 없이 전체 카탈로그 반환(조용한 사고)
#    /detail : 틀린 검색항목·dbname → ERR04 (시끄러운 실패)
# ⚠️ ERR04 는 일시 오류 코드이기도 해 클라이언트가 재시도한다 → 호출 전에 막아야
#    영구적인 사용자 오류에 재시도 3회를 낭비하지 않는다.

@pytest.mark.parametrize("dbname,field,ok", [
    ("학위논문", "논문명", True),      # ✅ 실측 total=112,360
    ("학위논문", "자료명", False),     # ✅ 실측 ERR04
    ("일반도서", "자료명", True),      # ✅ 실측 total=64,084
    ("일반도서", "논문명", False),     # ✅ 실측 ERR04
    ("국내기사", "기사명", True),      # ✅ 실측 total=411,980
    ("국내기사", "자료명", False),     # ✅ 실측 ERR04
    ("국회의안정보", "의안명", True),   # ✅ 실측 total=3,742
    ("국회회의록", "안건", True),      # ✅ 실측 total=1,384
    ("학위논문", "전체항목", True),     # ✅ 실측 total=475,252
])
def test_detail_category_vocabulary_is_enforced(dbname, field, ok):
    call = lambda: C.validate_detail_search(dbname, f"{field},교육")  # noqa: E731
    if ok:
        assert call()
    else:
        with pytest.raises(C.SearchFieldError):
            call()


@pytest.mark.parametrize("bad", ["없는DB", "전체", "도서"])
def test_detail_rejects_unknown_dbname(bad):
    """✅ 실측: dbname='없는DB'·'전체' 는 ERR04 로 실패한다."""
    with pytest.raises(C.SearchFieldError, match="dbname"):
        C.validate_detail_search(bad, "전체항목,교육")


def test_every_dbname_has_a_category_list():
    """DBNAMES 와 DB_CATEGORIES 가 어긋나면 검증이 통째로 무력해진다."""
    assert set(C.DBNAMES) == set(C.DB_CATEGORIES)


def test_detail_vocabulary_differs_from_basic():
    """두 어휘를 같은 것으로 착각하면 안 된다 — 이 API 함정의 핵심이다."""
    assert "저자명" in C.DB_CATEGORIES["일반도서"]      # /detail 에서는 유효
    assert "저자명" not in C.SEARCH_FIELDS_BASIC        # /basic 에서는 무효(전체 DB 반환)


# ── 🔴 인코딩: requests 의 chardet 추측을 신뢰하면 안 된다 ───────────────────
# 실측: `Content-Type: application/xml` 에 charset 이 없어 `resp.text` 가 chardet 추측에
# 의존했고, `dbname=고서` 응답이 **ptcp154(카자흐 키릴)** 로 추측돼 한글이 통째로 깨졌다
# (`제어번호` → `м\xa0ңм–ҙлІҲнҳё`). 일반도서가 멀쩡했던 것은 우연이지 옳아서가 아니다.
# → 클라이언트는 **bytes** 를 넘기고 ElementTree 가 XML 선언을 존중하게 한다.

def test_bytes_are_parsed_via_xml_declaration():
    from na_mcp.parser import parse_search_response
    raw = S.BASIC_OK.encode("utf-8")
    total, recs, _env = parse_search_response(raw)
    assert total == 5609
    assert recs[0].control_no == "KINX2026037525"
    assert recs[0].authors == "최여진"          # 한글이 깨지지 않아야 한다


def test_client_returns_bytes_not_text(monkeypatch):
    """`resp.text` 로 되돌아가면 조용한 문자 깨짐이 재발한다 — 회귀로 고정."""
    import na_mcp.client as C

    class FakeResp:
        status_code = 200
        content = S.BASIC_OK.encode("utf-8")
        headers: dict = {}

        @property
        def text(self):                      # 쓰이면 안 된다
            raise AssertionError("resp.text 를 쓰면 chardet 추측으로 한글이 깨진다")

    client = C.NaClient(throttle=0)
    monkeypatch.setattr(client._session, "get", lambda *a, **k: FakeResp())
    monkeypatch.setenv("NA_API_KEY_ENCODED", "dummykeyvalue")
    body = client._call("https://x/y", {"pageno": 1})
    assert isinstance(body, (bytes, bytearray))


# ── 실측으로 바로잡은 dbname × 검색항목 화이트리스트 ─────────────────────────

def test_documented_but_broken_categories_are_rejected():
    """문서에 있으나 실측 ERR04 인 이름들 — 통과시키면 재시도만 낭비한다."""
    for db, cat in (("학위논문", "지도교수(2009~)"), ("학위논문", "학위구분"),
                    ("학위논문", "학위년도"), ("일반도서", "DDC분류"),
                    ("일반도서", "별치기호"), ("일반도서", "발행년도"),
                    ("국회의안정보", "처리상태"), ("국회회의록", "위원회")):
        with pytest.raises(C.SearchFieldError):
            C.validate_detail_search(db, f"{cat},교육")


def test_undocumented_but_working_categories_are_allowed():
    """문서에 없지만 실측 동작 — 막으면 쓸 수 있는 검색을 못 하게 된다."""
    assert C.validate_detail_search("학위논문", "지도교수,손준종")       # total=67
    assert C.validate_detail_search("학술지,잡지", "수록지명/신문명,교육")  # 슬래시 한 덩어리


def test_display_only_fields_get_a_specific_hint():
    """`발행년도` 는 검색항목이 아니라 표시/필터 항목 — 대안(option)을 알려줘야 한다."""
    with pytest.raises(C.SearchFieldError, match="option"):
        C.validate_detail_search("일반도서", "발행년도,2020")


def test_unusable_dbname_is_reported_as_such():
    """`지식공유` 는 전체항목조차 통하지 않는다 — '항목이 틀렸다'가 아니라 'DB가 무효'다."""
    with pytest.raises(C.SearchFieldError, match="어떤 검색항목도"):
        C.validate_detail_search("지식공유", "전체항목,교육")


# ── 자료종별 census 반영분 회귀 (표본 약 21,000건, docs/NA_API_GUIDE.md §8) ──

def test_placeholder_matching_ignores_whitespace():
    """🔴 표기가 흔들린다 — 공백 유무 두 벌이 섞여 온다.

    한쪽만 비교했다가 웹자료 DDC 1,969건을 '실채움'으로 오산했다.
    """
    assert C.is_placeholder("전자형태로만 열람 가능함")
    assert C.is_placeholder("전자형태로만 열람가능함")     # 공백 없는 표기
    assert C.is_placeholder("EB 전자형태로만 열람 가능함")  # 접두 붙은 형태
    assert C.is_placeholder("해당 논문 없음")
    assert C.is_placeholder("해당자료 없음")
    assert not C.is_placeholder("658.80028563")
    assert not C.is_placeholder("") and not C.is_placeholder(None)


def test_placeholder_empties_field_but_keeps_raw():
    """안내문은 데이터가 아니다 — 정규화 필드는 비우되 원문은 raw 에 남는다."""
    from na_mcp.models import record_from_items
    rec = record_from_items([("제어번호", "X1"), ("자료명", "책"),
                             ("DDC", "전자형태로만 열람 가능함"),
                             ("청구기호", "EB 전자형태로만 열람가능함")])
    assert rec.class_no == "" and rec.call_no == ""
    assert rec.raw["DDC"] == "전자형태로만 열람 가능함"      # 손실 없음
    assert set(rec.placeholder_fields) == {"DDC", "청구기호"}


def test_real_classification_survives():
    """정제가 과잉이면 안 된다 — 진짜 분류기호는 그대로."""
    from na_mcp.models import record_from_items
    rec = record_from_items([("DDC", "370.951"), ("청구기호", "370.951 -26-7")])
    assert rec.class_no == "370.951" and rec.call_no == "370.951 -26-7"
    assert rec.placeholder_fields == []


@pytest.mark.parametrize("bad", ["201u", "", "0", "19--", "미상"])
def test_non_year_values_normalize_to_empty(bad):
    """✅ census: `201u`(MARC 불확정 연도) 고서·일반도서·학술지에서 1~23%,
    `0`(외국법률번역DB 의 날짜 '없음') 400건 전부. 정수 변환하면 1970년이 된다."""
    from na_mcp.models import normalize_pub_year
    assert normalize_pub_year(bad) == ""


def test_real_years_survive():
    from na_mcp.models import normalize_pub_year
    assert normalize_pub_year("2026") == "2026"
    assert normalize_pub_year("20260330") == "2026"       # 제안일자(YYYYMMDD)


def test_toc_less_dbnames_are_declared():
    """이 자료종들은 `목차` 가 전건 'N' 이라 na_toc 호출이 무의미하다."""
    assert C.TOC_ALWAYS_EMPTY_DBNAMES == {
        "E-BOOK", "학술지,잡지", "신문", "국외기사", "동영상자료"}
    for db in C.TOC_ALWAYS_EMPTY_DBNAMES:
        assert db in C.DB_CATEGORIES          # 오타로 죽은 항목이 되지 않도록


def test_heavy_dbnames_lower_page_size():
    """⚠️ 500건 요청에서 ConnectionError 로 끊기는 자료종은 자동으로 낮춘다."""
    assert C.HEAVY_DBNAMES["외국법률번역DB"] == 200
    for db in C.HEAVY_DBNAMES:
        assert db in C.DB_CATEGORIES


def test_empty_field_map_names_real_dbnames():
    """구조적으로 빈 필드 표가 실제 dbname·검색항목과 어긋나면 안내가 거짓이 된다."""
    for db in C.EMPTY_FIELDS_BY_DB:
        assert db in C.DB_CATEGORIES


def test_na_fields_surfaces_census():
    """census 결과가 도구로 노출되지 않으면 호출자가 같은 함정을 다시 밟는다."""
    out = s.na_fields()
    assert "목차_전건없음_자료종" in out
    assert out["서술형_본문_있는_자료종"] == {"국회의안정보": "제안이유 및 주요내용",
                                              "국회회의록": "내용"}
    assert "초록" in out["초록_경고"]


# ── 릴리스 메타 일관성 (자매 프로젝트에서 릴리스마다 버전이 어긋난 전례) ──────
# ⚠️ 릴리스 워크플로의 guard job 이 같은 검사를 하지만, **여기서도** 고정한다 —
#    태그를 밀고 나서 실패를 알면 이미 늦다(GitHub Release 가 반쯤 만들어진다).

def _release_versions():
    import json
    import re
    root = pathlib.Path(__file__).parents[1]
    pv = re.search(r'^version = "([^"]+)"',
                   (root / "pyproject.toml").read_text(encoding="utf-8"), re.M).group(1)
    out = {"pyproject.toml": pv}
    for f in ("packaging/binary/manifest.json", "mcpb/manifest.json", "server.json"):
        out[f] = json.loads((root / f).read_text(encoding="utf-8"))["version"]
    return out


def test_release_metadata_versions_agree():
    seen = _release_versions()
    assert len(set(seen.values())) == 1, f"버전 불일치: {seen}"


def test_server_json_download_url_matches_version():
    """identifier 의 태그가 version 과 다르면 레지스트리가 없는 자산을 가리킨다."""
    import json
    root = pathlib.Path(__file__).parents[1]
    sj = json.loads((root / "server.json").read_text(encoding="utf-8"))
    pkg = sj["packages"][0]
    assert f"/v{pkg['version']}/" in pkg["identifier"], pkg["identifier"]
    assert pkg["fileSha256"] == "__MCPB_SHA256__", \
        "sha256 은 릴리스 시 주입된다 — 저장소에는 자리표시자로 남아야 한다"


def test_mcpb_manifests_declare_every_tool():
    """도구를 추가하고 manifest 를 안 고치면 번들 사용자에게는 안 보인다."""
    import asyncio
    import json
    root = pathlib.Path(__file__).parents[1]
    registered = {t.name for t in asyncio.run(s.mcp.list_tools())}
    for f in ("packaging/binary/manifest.json", "mcpb/manifest.json"):
        declared = {t["name"] for t in
                    json.loads((root / f).read_text(encoding="utf-8"))["tools"]}
        assert declared == registered, f"{f}: 선언 {declared} vs 등록 {registered}"
