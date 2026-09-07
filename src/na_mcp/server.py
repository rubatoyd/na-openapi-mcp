"""국회도서관 자료검색 MCP 서버 (FastMCP).

⚠️ `mcp.server.fastmcp` 를 **조건부 import 하지 않는다.** mcp 2.0 은 이 모듈을 제거했으므로
   폴백을 두면 반쯤 동작하는 서버가 조용히 뜬다. pyproject 의 `mcp>=1.2.0,<2` 상한이
   유일한 방어선이고, 여기서는 실패를 시끄럽게 내는 편이 옳다.
   (자매 프로젝트 kci·scienceon·nl 이 이 상한 누락으로 각각 기동 불능을 겪었다.)
"""
from __future__ import annotations

import argparse
import functools
import os
import sys
from pathlib import Path

from mcp.server.fastmcp import FastMCP

from .client import NaClient, NaError
from .config import (
    ABSTRACT_FLAG_RATE,
    API_RECORD_CAP,
    DB_CATEGORIES,
    DBNAMES,
    EMPTY_FIELDS_BY_DB,
    HEAVY_DBNAMES,
    MISLEADING_FIELDS,
    NARRATIVE_TEXT_FIELDS,
    PLACEHOLDER_VALUES,
    TOC_ALWAYS_EMPTY_DBNAMES,
    MAX_DISPLAYLINES,
    PAGENO_MAX,
    SEARCH_FIELDS_BASIC,
    get_api_key,
    scrub,
)
from .exporters import export

mcp = FastMCP("na")

# 🔴 도구 호출 한 번의 **쿼터 상한**. 개발계정은 하루 10,000건인데, 초판은
#    `na_collect(terms=[…50개], max_records=10**9)` 이 최대 4,950회를 태울 수 있었다
#    (일일 한도의 절반, 30분 이상, 중간 취소 지점 없음 — 적대적 리뷰 지적).
MAX_CALLS_PER_TOOL_CALL = int(os.environ.get("NA_MAX_CALLS_PER_TOOL_CALL", "300"))


def _estimate_calls(max_records: int, page_size: int | None, n_terms: int = 1) -> int:
    size = min(max(1, page_size or MAX_DISPLAYLINES), MAX_DISPLAYLINES)
    per_term = min(PAGENO_MAX, -(-max(1, int(max_records)) // size))
    return per_term * max(1, n_terms)


def _quota_guard(max_records: int, page_size: int | None, n_terms: int = 1):
    """예상 호출 수가 상한을 넘으면 **호출 전에** 거부한다(실행 후 후회 방지)."""
    est = _estimate_calls(max_records, page_size, n_terms)
    if est <= MAX_CALLS_PER_TOOL_CALL:
        return None
    return {
        "error": f"이 요청은 최대 약 {est:,}회 API 호출이 필요합니다 — 한 번의 도구 호출 상한"
                 f"({MAX_CALLS_PER_TOOL_CALL:,}회)을 넘습니다. 개발계정 일일 한도는 10,000건입니다.",
        "hint": "max_records 를 줄이거나, terms 를 나눠 여러 번 호출하거나, "
                "page_size 를 키워(최대 1000) 페이지 수를 줄이세요.",
        "estimated_calls": est, "limit": MAX_CALLS_PER_TOOL_CALL,
    }


def _safe(fn):
    """도구는 **항상 JSON 직렬화 가능한 dict** 를 반환 — 어떤 예외도 도구 밖으로 누수 금지.

    네트워크/SSL/HTTP/파싱 예외는 물론 자격증명 누락(RuntimeError)도 여기서 잡는다.
    `scrub()` 을 한 번 더 걸어 인증키가 메시지에 남지 않게 한다(이중 방어).
    """
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            return {"error": scrub(f"{type(e).__name__}: {e}")}
    return wrapper


# 도구 안전성 힌트(MCP annotations) — 디렉터리 심사·클라이언트 표시에 쓰인다.
_READ = {"readOnlyHint": True, "openWorldHint": True}
_WRITE = {"readOnlyHint": False, "destructiveHint": False, "openWorldHint": True}

_NO_KEY = {
    "error": "NA_API_KEY 미설정 — 국회도서관 자료검색에는 인증키가 필요합니다.",
    "hint": "https://www.data.go.kr 에서 '국회 국회도서관_자료검색 서비스' 활용신청 후 "
            "발급받은 인증키를 .env 또는 환경변수 NA_API_KEY 로 설정하세요.",
}


@mcp.tool(annotations=_READ)
@_safe
def na_status() -> dict:
    """연결 점검 — 인증키 보유 여부 + 자료검색 API 실제 왕복 1회."""
    info: dict = {
        "has_api_key": get_api_key() is not None,
        "api": "국회도서관 자료검색 (apis.data.go.kr/9720000/searchservice)",
        "api_record_cap": API_RECORD_CAP,
        "pageno_max": PAGENO_MAX,
        "max_displaylines": MAX_DISPLAYLINES,
    }
    if not info["has_api_key"]:
        info["ok"] = False
        info["note"] = _NO_KEY["error"]
        info["hint"] = _NO_KEY["hint"]
        return info
    try:
        recs, meta = NaClient().search_meta("전체,도서관", max_records=1, page_size=1)
        info["ok"] = True
        info["probe"] = {"search": "전체,도서관", "total": meta.get("total"),
                         "returned": len(recs), "record_tag": meta.get("envelope", {}).get("record_tag")}
        info["note"] = "인증키 유효 — 자료검색 정상 응답."
    except NaError as e:
        info["ok"] = False
        info["note"] = scrub(str(e))
    return info


@mcp.tool(annotations=_READ)
@_safe
def na_search(search: str, max_records: int = 20, dbname: str | None = None,
              option: str | None = None, page_size: int | None = None,
              extra_params: dict | None = None) -> dict:
    """[자료검색] 국회도서관의 도서·학위논문·국내외 기사 등 목록DB를 검색한다.

    search: **`검색항목,키워드`** 형식이 필수다(그냥 키워드만 넣으면 오류).
      `|` 로 여러 개를 연결하면 **AND** 로 묶인다. 예: `전체,교육|자료명,불평등`.
      OR(합집합)이 필요하면 `na_collect(terms=[…])` 를 쓸 것 — 이 API 에 OR 문법은 없다.

      **검색항목(통합검색 전용 7종)**: 기본검색 · 전체 · 자료명 · 저자 · 발행자 · 키워드 · 청구기호
      🔴 **그 밖의 값을 쓰면 오류가 나지 않고 검색어가 통째로 무시되어 전체 카탈로그
         13,097,591건이 반환된다.** 특히 `저자명` 은 상세검색(dbname 지정 시)에서는 유효한
         이름이라 헷갈리기 쉽다 — 통합검색에서는 반드시 `저자` 를 쓸 것.
         이 도구는 화이트리스트로 미리 막고 오류를 돌려준다.

    dbname: 지정하면 **상세검색**(`/detail`)으로 전환된다. 21종 중 정확한 문자열이어야 한다:
      일반도서 · E-BOOK · 고서 · 세미나자료 · 웹자료 · 학위논문 · 국내기사 · 국외기사 ·
      `학술지,잡지` · 신문 · 전자저널 · 동영상자료 · 오디오자료 · 전자매체 · 마이크로폼자료 ·
      `지도/기타자료` · 외국법률번역DB · 국회회의록 · 국회의안정보 · `표,그림DB` · 지식공유
      🔴 상세검색은 **검색항목 어휘가 DB마다 다르고**(학위논문=논문명, 일반도서=자료명,
         국내기사=기사명, 국회의안정보=의안명, 국회회의록=안건 …) 통합검색과 달리
         **틀리면 ERR04 로 실패한다**(실측). 이 도구가 호출 전에 검증해 막으므로
         정확한 어휘는 `na_fields` 로 확인할 것.

    option: 상세검색 전용. `발행년도,2000|발행년도,2010` (하나면 그 해부터 현재까지,
      둘이면 between), `원문유무,1`(유)/`0`(무).

    page_size: 한 페이지 건수(최대 1000). 기본은 최대치.
    max_records: 최대 회수 건수.

    ⚠️ **회수 한계 99,000건** — `pageno` 가 최대 99이고 페이지당 최대 1000건이다(실측).
       `total` 이 이를 넘으면 응답의 `cap_hit` 이 참이 되고, 그때는 max_records 를 올려도
       더 받을 수 없다(검색식을 쪼개야 한다). `truncated` 는 max_records 를 올리면 해결된다.
    """
    if get_api_key() is None:
        return dict(_NO_KEY)
    over = _quota_guard(max_records, page_size)
    if over:
        return over
    client = NaClient()
    records, meta = client.search_meta(search, max_records=max_records, dbname=dbname,
                                       option=option, page_size=page_size,
                                       extra_params=extra_params)
    return {"meta": meta, "records": [r.to_row() for r in records]}


@mcp.tool(annotations=_WRITE)
@_safe
def na_collect(terms: list[str] | None = None, search: str | None = None,
               max_records: int = 1000, dbname: str | None = None,
               option: str | None = None, page_size: int | None = None,
               contains: list[str] | None = None,
               year_from: int | None = None, year_to: int | None = None,
               formats: list[str] | None = None, out_dir: str | None = None,
               name: str | None = None, save: bool = True,
               extra_params: dict | None = None) -> dict:
    """[수집] 검색어들을 각각 조회해 **합집합**으로 모으고 파일로 저장한다.

    terms: `검색항목,키워드` 형식의 검색어 목록. 각각 개별 검색 후 **합집합**(OR)으로 병합한다.
      이 API 의 `|` 는 AND 이므로 OR 은 이렇게 만들어야 한다.
      예: `["전체,교육불평등", "전체,교육격차", "자료명,교육 형평성"]`
    search: 단일 검색어(terms 대신).

    ⚠️ `year_from`/`year_to`/`contains` 는 **로컬 후처리**다 — 이미 받은 레코드에만 걸리며
       회수 한계를 풀어주지 않는다. 서버측 연도 필터는 **상세검색의 `option`** 뿐이므로
       연도로 범위를 좁히려면 `dbname` 과 함께 `option="발행년도,2000|발행년도,2010"` 을 쓸 것.

    formats: xlsx/csv/json/sqlite (기본 3종). save=false 면 저장 없이 미리보기만.
    out_dir 미지정 시 홈의 na-output/.

    반환 메타의 `cap_hit_terms` 는 회수 한계(99,000건)에 걸린 검색어를, `incomplete_terms` 는
    재시도 후에도 실패한 페이지가 있는 검색어를 지목한다 — **둘 다 전수가 아니라는 뜻**이다.
    """
    if get_api_key() is None:
        return dict(_NO_KEY)
    term_list = [t for t in (terms or ([search] if search else [])) if t and t.strip()]
    if not term_list:
        return {"error": "terms 또는 search 중 하나는 있어야 합니다 "
                         "(형식: `검색항목,키워드`, 예: `전체,교육불평등`)."}

    over = _quota_guard(max_records, page_size, len(term_list))
    if over:
        return over
    client = NaClient()
    records, meta = client.search_terms_meta(term_list, max_records=max_records,
                                             dbname=dbname, option=option,
                                             page_size=page_size, extra_params=extra_params)

    before = len(records)
    if contains:
        records = [r for r in records if r.matches(contains)]
    if year_from is not None or year_to is not None:
        lo, hi = year_from or 0, year_to or 9999
        kept = []
        for r in records:
            y = int(r.pub_year) if r.pub_year.isdigit() else None
            if y is not None and lo <= y <= hi:
                kept.append(r)
        records = kept
    meta["filtered_out"] = before - len(records)
    meta["kept"] = len(records)
    meta["local_filters"] = {"contains": contains, "year_from": year_from,
                             "year_to": year_to,
                             "note": "로컬 후처리 — 회수 한계를 풀어주지 않는다"}

    if save and records:
        target = out_dir or str(Path.home() / "na-output")
        stem = name or (term_list[0].split(",", 1)[-1] if term_list else "na")
        meta["output_paths"] = export(records, formats or ["xlsx", "csv", "json"],
                                      target, stem)
    return {"meta": meta, "records": [r.to_row() for r in records[:50]],
            "preview_note": f"records 는 앞 50건 미리보기입니다(총 {len(records)}건). "
                            f"전체는 저장된 파일을 보세요." if len(records) > 50 else None}


@mcp.tool(annotations=_READ)
@_safe
def na_detail(controlno: str) -> dict:
    """[상세정보] 제어번호 1건의 서지정보를 조회한다.

    controlno: 검색 결과의 **`제어번호`** (예: `MONO12026000012887`, `KINX2026037525`).

    🔴 **검색 결과(`na_search` 의 raw)와 필드 집합·값이 완전히 동일하다** — ✅ 실측
       (일반도서 19 · 학위논문 18 · 국내기사 13 · 고서 19 · 웹자료 17개 전부 상세전용 필드 0).
       즉 **이미 검색한 자료라면 이 도구를 부를 이유가 없다.** 쿼터(10,000건/일)만 쓴다.
       쓸 자리는 **제어번호만 아는 자료를 조회할 때**다. 목차 본문이 필요하면 `na_toc` 를 쓸 것
       (그쪽은 검색에 없는 내용을 실제로 준다).

    ⚠️ **존재하지 않는 제어번호도 `ERR04` 로 응답한다**(실측) — 전용 '자료 없음' 코드가 없어
       일시 오류와 구분되지 않는다. 실패하면 제어번호부터 확인할 것.
    ⚠️ 별도 활용신청 대상이다(data.go.kr 15098175). 자료검색 키만으로는 접근할 수 없다.
    """
    if get_api_key() is None:
        return dict(_NO_KEY)
    fields, env = NaClient().detail(controlno)
    return {"controlno": controlno, "fields": fields, "field_count": len(fields),
            "envelope": env}


@mcp.tool(annotations=_READ)
@_safe
def na_toc(controlno: str) -> dict:
    """[목차] 제어번호 1건의 목차정보를 조회한다.

    controlno: 검색 결과의 **`제어번호`**.

    ⚠️ 원문은 HTML 이 이스케이프되어 오므로 이 도구가 태그를 풀어 줄바꿈 텍스트로 정리한다.
       실제 주력 구분자는 `<p>` 다(실측 10/10) — 계층은 **행 앞 들여쓰기**로 표현되므로
       그대로 보존해 돌려준다.
    ⚠️ **목차가 없는 자료도 정상이다.** 검색 결과의 `목차` 필드가 'Y' 인 자료만 부르면
       헛호출과 쿼터 낭비를 줄일 수 있다(개발계정 10,000건/일).

    🔴 **다음 자료종은 `목차` 가 전건 'N' 이라 이 도구를 부를 이유가 아예 없다**(census 실측):
       E-BOOK · 학술지,잡지 · 신문 · 국외기사 · 동영상자료.
    🔴 **`목차정보없음` 센티널** — 플래그가 'Y' 인데 본문이 그 말의 반복인 자료가 있다
       (고서에서 표본 5/5). 최대 792자라 길이 검사를 통과하므로 이 도구가 걸러 빈 문자열로
       돌려주고, 원문은 `envelope.toc_sentinel` 에 남긴다.
    """
    if get_api_key() is None:
        return dict(_NO_KEY)
    toc, env = NaClient().toc(controlno)
    return {"controlno": controlno, "toc": toc, "length": len(toc),
            "has_toc": env.get("has_toc", False), "envelope": env}


@mcp.tool(annotations=_READ)
@_safe
def na_fields() -> dict:
    """검색항목·dbname 등 이 API 에서 실제로 통하는 값 목록과 실측 근거."""
    from .config import SEARCH_FIELD_ALIASES, SEARCH_FIELD_EVIDENCE

    return {
        "통합검색_검색항목": list(SEARCH_FIELDS_BASIC),
        "실측근거_건수(kwd=오욱환)": SEARCH_FIELD_EVIDENCE,
        "흔한_오용": SEARCH_FIELD_ALIASES,
        "경고": "미지원 검색항목은 오류가 아니라 검색어를 무시하고 전체 카탈로그 "
                "13,097,591건을 반환한다. 반드시 위 목록의 값만 쓸 것.",
        "상세검색_dbname별_검색항목": {k: list(v) for k, v in DB_CATEGORIES.items()},
        "상세검색_주의": "🔴 상세검색은 **DB마다 검색항목 어휘가 다르다**"
                         "(학위논문=논문명, 일반도서=자료명, 국내기사=기사명, "
                         "국회의안정보=의안명, 국회회의록=안건). 틀리면 통합검색과 달리 "
                         "**ERR04 로 실패한다**(실측). 이 도구가 호출 전에 검증해 막는다.",
        "한계": {"pageno_max": PAGENO_MAX, "max_displaylines": MAX_DISPLAYLINES,
                 "회수_한계": API_RECORD_CAP,
                 "일일_트래픽": "개발계정 10,000건/일",
                 "무거운_자료종": HEAVY_DBNAMES},
        # ── 자료종별 census 결과 (표본 약 21,000건, docs/NA_API_GUIDE.md §8) ──
        "목차_전건없음_자료종": sorted(TOC_ALWAYS_EMPTY_DBNAMES),
        "서술형_본문_있는_자료종": NARRATIVE_TEXT_FIELDS,
        "구조적_빈_필드": EMPTY_FIELDS_BY_DB,
        "이름과_내용이_다른_필드": MISLEADING_FIELDS,
        "안내문_값": {
            "값들": list(PLACEHOLDER_VALUES),
            "주의": "표기가 흔들린다(`전자형태로만 열람가능함` / `… 열람 가능함`). "
                    "공백을 제거하고 비교해야 한다. 이 서버는 안내문을 정규화 필드에서 비우고 "
                    "`placeholder_fields` 에 원본 필드명을 남긴다(원문은 raw 에 보존).",
            "심한_자료종": "E-BOOK DDC 99.95% · 웹자료 사실상 전건 · 학위논문 17%",
        },
        "초록유무_비율": ABSTRACT_FLAG_RATE,
        "초록_경고": "🔴 `초록유무=Y` 여도 **초록 본문을 받을 방법이 없다** — "
                     "/abstract·/summary 등 후보 엔드포인트가 전부 HTTP 400(실측). "
                     "서술형 텍스트가 필요하면 국회의안정보·국회회의록을 쓸 것.",
        "국외기사_주의": "한국어 검색어로 0건이다(`전체항목,한국` → total=0). 영문으로만 검색된다.",
    }


def _env_port(name: str) -> int | None:
    raw = (os.environ.get(name) or "").strip()
    return int(raw) if raw.isdigit() else None


def main(argv: list[str] | None = None) -> None:
    """⚠️ 기본 전송은 **stdio 로 못박는다** — 바뀌면 기존 MCP 등록이 전부 죽는다."""
    parser = argparse.ArgumentParser(prog="na-mcp", add_help=True)
    parser.add_argument("--transport", default=os.environ.get("NA_MCP_TRANSPORT", "stdio"),
                        choices=["stdio", "sse", "streamable-http"])
    parser.add_argument("--host", default=os.environ.get("NA_MCP_HOST"))
    parser.add_argument("--port", type=int, default=_env_port("NA_MCP_PORT"))
    args, _unknown = parser.parse_known_args(argv if argv is not None else sys.argv[1:])

    if args.host:
        mcp.settings.host = args.host
    if args.port:
        mcp.settings.port = args.port
    mcp.run(transport=args.transport)


if __name__ == "__main__":
    main()
