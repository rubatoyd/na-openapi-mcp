"""국회도서관 자료검색 API(XML) 응답 파서.

✅ 실측 봉투(2026-09-07):
    <response>
      <header><resultMsg>NORMAL_CODE</resultMsg><resultCode>00</resultCode></header>
      <total>5609</total>
      <recode><item><name>제어번호</name><value>KINX…</value></item>…</recode>
      …
    </response>

🔴 **공식 문서(.hwp)는 `<record>` 라고 적고 있으나 실제 태그는 `<recode>` 다**(오기로 보인다).
   문서대로 `record` 만 찾으면 **전건 0개를 회수하면서 total 은 정상**으로 보고한다 —
   이 프로젝트가 막겠다고 하는 바로 그 '조용한 절단'이다. 둘 다 받는다.
"""
from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from typing import Any

from .config import FULL_CATALOG_TOTAL, scrub
from .models import Record, clean_html, record_from_items


class ParseError(RuntimeError):
    """응답이 검색결과 봉투가 아니거나 API 가 오류를 보고했을 때."""


# 📄 공식 오류코드표(.hwp §2). ✅ 04·20 은 라이브로 관측했다.
ERROR_CODE_HINTS = {
    "01": "APPLICATION ERROR — 제공기관 애플리케이션 오류",
    "04": "HTTP ERROR — 게이트웨이/원본 서버 오류. ⚠️ 깊은 오프셋 요청에서 **간헐적으로** 발생하며 재시도로 대개 성공한다(실측: 같은 요청이 실패 후 3/3 성공).",
    "05": "SERVICE TIMEOUT — 응답 시간 초과",
    "10": "INVALID_REQUEST_PARAMETER_ERROR — 잘못된 요청 파라미터",
    "11": "NO MANDATORY REQUEST PARAMETERS ERROR — 필수 파라미터 누락",
    "12": "NO OPENAPI SERVICE ERROR — 해당 서비스가 없거나 폐기됨",
    "20": "SERVICE ACCESS DENIED ERROR — 서비스 접근거부(인증키 미전달 포함)",
    "22": "LIMITED NUMBER OF SERVICE REQUESTS EXCEEDS ERROR — 일일 요청한도 초과(개발계정 10,000건/일). 재시도 무의미.",
    "23": "SERVICE REQUEST PER SECOND EXCEEDS — 초당 요청한도 초과(30tps)",
    "30": "SERVICE KEY IS NOT REGISTERED ERROR — 등록되지 않은 인증키. 재시도 무의미.",
    "31": "DEADLINE HAS EXPIRED ERROR — 활용기간 만료. 재시도 무의미.",
    "32": "UNREGISTERED IP ERROR — 등록되지 않은 IP",
    "99": "UNKNOWN ERROR — 기타 오류",
}

# 재시도해도 소용없는 코드 — 쿼터·키·권한 문제. 나머지(특히 04)는 재시도 대상이다.
TERMINAL_CODES = {"10", "11", "12", "20", "22", "30", "31", "32"}

# 응답 봉투에서 **자격증명으로 보이는 항목은 지우고 내보낸다**.
# `na_status` 가 봉투를 도구 응답에 그대로 싣기 때문이다(→ LLM 트랜스크립트).
# 자매 프로젝트 kci 에서는 오류 봉투가 요청을 통째로 에코해 **인증키가 MCP 응답까지 샜다**.
# 이 API 가 에코하는지는 ❓ 미확정 — 확정 전까지 막아두는 쪽이 옳다.
_SECRET_KEYS = {"servicekey", "key", "apikey", "api_key", "authkey", "auth_key",
                "secret", "token"}


def _as_int(value: Any) -> int:
    """`total` 은 숫자로도 문자열("1,856")로도 올 수 있다 — 둘 다 받는다."""
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return value
    digits = re.sub(r"[^0-9]", "", str(value or ""))
    return int(digits) if digits else 0


class ApiError(ParseError):
    """API 가 오류 봉투를 돌려줬을 때. `code` 로 재시도 가능 여부를 판단한다."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.retryable = code not in TERMINAL_CODES
        super().__init__(message)


def _check_error_envelope(root: ET.Element) -> None:
    """게이트웨이 오류 봉투(`<OpenAPI_ServiceResponse>`)를 ApiError 로 올린다.

    ✅ 성공과 오류는 **루트 元소가 다르다** — 이것이 가장 확실한 판별 기준이다.
       성공: `<response>` / 오류: `<OpenAPI_ServiceResponse>`
    """
    if root.tag == "response":
        return
    code = (root.findtext(".//returnReasonCode") or "").strip()
    msg = (root.findtext(".//returnAuthMsg") or root.findtext(".//errMsg") or "").strip()
    hint = ERROR_CODE_HINTS.get(code.zfill(2), "")
    raise ApiError(
        code.zfill(2) if code else "??",
        scrub(f"국회도서관 API 오류 [{code or '?'}] {msg}" + (f" — {hint}" if hint else "")),
    )


def _envelope(root: ET.Element, total: int) -> dict[str, Any]:
    """진단용 봉투 메타 — 레코드를 뺀 최상위 정보. 자격증명형 키는 제거한다."""
    env: dict[str, Any] = {"total": total}
    header = root.find("./header")
    if header is not None:
        for child in header:
            if child.tag.strip().lower() in _SECRET_KEYS:
                continue
            env[child.tag] = (child.text or "").strip()
    for child in root:
        # item·toc 는 본문이지 봉투가 아니다 — 넣으면 상세조회 봉투에 빈 "item" 이 낀다.
        if child.tag in ("header", "recode", "record", "total", "item", "toc"):
            continue
        if child.tag.strip().lower() in _SECRET_KEYS:
            continue
        env.setdefault(child.tag, (child.text or "").strip())
    return env


def _items_of(rec_el: ET.Element) -> list[tuple[str, str]]:
    """`<recode>` 안의 `<item>` 들을 (name, value) 목록으로.

    ⚠️ `<item>` 자식 순서는 문서(value→name)와 실응답(name→value)이 다르다.
       **순서에 의존하지 않고 태그명으로** 찾는다.
    """
    out: list[tuple[str, str]] = []
    for it in rec_el.findall("./item"):
        out.append(((it.findtext("name") or "").strip(),
                    (it.findtext("value") or "").strip()))
    return out



def _to_root(body: str | bytes) -> ET.Element:
    """본문 → XML 루트. **바이트는 디코드하지 않고 그대로 넘긴다.**

    🔴 ElementTree 는 XML 선언의 `encoding="UTF-8"` 을 읽어 정확히 디코드한다.
       우리가 먼저 `str` 로 바꾸면(특히 `requests.text` 의 chardet 추측으로) 깨진다 —
       실측에서 `dbname=고서` 응답이 `ptcp154` 로 추측돼 한글이 통째로 망가졌다.
    """
    if isinstance(body, (bytes, bytearray)):
        if not bytes(body).strip():
            raise ParseError("빈 응답 — 네트워크 또는 서버 오류로 보입니다.")
        try:
            return ET.fromstring(bytes(body))
        except ET.ParseError as e:
            head = bytes(body)[:300].decode("utf-8", "replace")
            raise ParseError(scrub(f"XML 파싱 실패({e}) — 응답 앞부분: {head!r}")) from None
    text = (body or "").strip()
    if not text:
        raise ParseError("빈 응답 — 네트워크 또는 서버 오류로 보입니다.")
    try:
        return ET.fromstring(text)
    except ET.ParseError as e:
        head = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", text[:300])).strip()
        raise ParseError(scrub(f"XML 파싱 실패({e}) — 응답 앞부분: {head!r}")) from None


def parse_search_response(body: str | bytes, *, expect_records: bool = True
                          ) -> tuple[int, list[Record], dict[str, Any]]:
    """검색 응답 → (total, 레코드 목록, 봉투 메타).

    `expect_records`: 이 응답에 레코드가 **있어야 하는가**. 첫 페이지에서는 True 다.

    ⚠️ 첫 페이지에서 `total>0` 인데 레코드가 0건이면 **오류로 본다.** 빈 목록으로 조용히
       통과시키면 태그명 변경(`recode`↔`record`) 같은 사고가 '결과 0건'으로 둔갑한다.
    ⚠️ 그러나 **끝을 지난 페이지는 정상적으로 비어 있다**(total 은 그대로 오고 `<recode>` 만
       없다). 이때까지 오류로 올리면 마지막 페이지마다 재시도를 세 번씩 태우고 수집이
       실패한 것처럼 보인다 — 실제로 그렇게 만들었다가 잡았다. 그래서 호출자가 문맥을 준다.
    """
    root = _to_root(body)
    _check_error_envelope(root)

    total = _as_int(root.findtext("./total"))

    # 🔴 실제 태그는 `recode`(문서의 `record` 는 오기). 둘 다 받되 어느 쪽이 왔는지 기록한다.
    rec_els = root.findall("./recode")
    tag_seen = "recode"
    if not rec_els:
        rec_els = root.findall("./record")
        tag_seen = "record" if rec_els else "none"

    records = [record_from_items(_items_of(el)) for el in rec_els]

    result_code = (root.findtext("./header/resultCode") or "").strip()
    if result_code and result_code not in ("00", "0"):
        msg = (root.findtext("./header/resultMsg") or "").strip()
        hint = ERROR_CODE_HINTS.get(result_code.zfill(2), "")
        raise ApiError(result_code.zfill(2),
                       scrub(f"국회도서관 API 오류 [{result_code}] {msg}"
                             + (f" — {hint}" if hint else "")))

    if expect_records and total > 0 and not records:
        raise ParseError(
            f"total 은 {total:,}건인데 레코드가 0개입니다 — 정상적인 '결과 없음'이 아닙니다. "
            f"응답의 레코드 태그가 바뀌었을 수 있습니다(기대: <recode> 또는 <record>). "
            f"최상위 자식 태그: {', '.join(dict.fromkeys(c.tag for c in root)) or '(없음)'}"
        )

    env = _envelope(root, total)
    env["record_tag"] = tag_seen
    return total, records, env


def looks_like_ignored_search(total: int) -> bool:
    """검색어가 무시되고 전체 카탈로그가 반환된 정황인가.

    🔴 미지원 검색항목을 쓰면 이 API 는 오류 없이 **전체 13,097,591건**을 준다.
       `config.validate_search()` 가 1차 방어선이고, 이것은 2차(사후) 탐지다 —
       카탈로그 규모가 늘어도 근처 값이면 잡히도록 여유를 둔다.
    """
    return total >= FULL_CATALOG_TOTAL * 0.95


# ── 상세정보조회 서비스 (detailinfoservice) ──────────────────────────────────
# ✅ 실측(2026-09-07). 검색 서비스와 **봉투 모양이 다르다**:
#   detail: <response><header/><item><name>·<value></item>…</response>
#           → `<recode>` 래퍼가 **없고** item 이 최상위에 평탄하게 온다.
#   toc   : <response><toc>…</toc><header/></response>
#           → ⚠️ `<toc>` 가 `<header>` **앞**에 온다. 위치 의존 파싱 금지.
# ⚠️ 문서(.docx)는 여기서도 `<value>`→`<name>` 순서로 적었으나 실제는 name→value 다.

# 🔴 목차의 실제 주력 구분자는 **`<p>`** 다(실측 10/10건이 `<p>` 만 씀, 항목당 2개씩).
#    초판은 `<br>` 만 줄바꿈으로 바꾸고 `<p>` 는 _TAG 로 그냥 지워서 **목차 전체가 한 줄로
#    뭉개졌다** — 130~570개 항목이 공백 없이 이어붙었다. 블록 태그를 전부 줄바꿈으로 본다.
_BLOCK = re.compile(r"<\s*/?\s*(?:br|p|div|li|tr|h[1-6])(?=[ /	>])[^>]*>", re.I)
_TAG = re.compile(r"<[^>]+>")

# 목차 본문이 이 말로만 채워져 오는 자료가 있다 — 플래그는 Y 인데 실제 목차는 없다.
TOC_SENTINEL = "목차정보없음"


def parse_detail_response(body: str | bytes) -> tuple[dict[str, str], dict[str, Any]]:
    """상세정보 응답 → (name→value 매핑, 봉투 메타).

    ⚠️ `item` 이 하나도 없으면 오류로 본다 — 빈 dict 로 통과시키면 '자료 없음'과
       '스키마 변경'을 구분할 수 없다.
    """
    root = _to_root(body)
    _check_error_envelope(root)

    result_code = (root.findtext("./header/resultCode") or "").strip()
    if result_code and result_code not in ("00", "0"):
        msg = (root.findtext("./header/resultMsg") or "").strip()
        raise ApiError(result_code.zfill(2),
                       scrub(f"국회도서관 API 오류 [{result_code}] {msg} — "
                             + ERROR_CODE_HINTS.get(result_code.zfill(2), "")))

    fields: dict[str, str] = {}
    for it in root.findall("./item"):
        # 상세정보에는 하이라이트가 관측되지 않았지만(검색이 아니므로) 같은 정제를 건다.
        name = clean_html(it.findtext("name"))
        value = clean_html(it.findtext("value"))
        if not name:
            continue
        fields[name] = f"{fields[name]}; {value}" if fields.get(name) else value

    if not fields:
        raise ParseError(
            "상세정보 응답에 `item` 이 없습니다 — 스키마가 바뀌었거나 제어번호가 잘못됐습니다. "
            f"최상위 자식 태그: {', '.join(dict.fromkeys(c.tag for c in root)) or '(없음)'}")

    env = {k: v for k, v in _envelope(root, 0).items() if k != "total"}
    return fields, env


def parse_toc_response(body: str | bytes) -> tuple[str, dict[str, Any]]:
    """목차 응답 → (목차 텍스트, 봉투 메타).

    ⚠️ 목차 본문은 **HTML 이 이스케이프되어** 들어온다(`&lt;p&gt;`·`&lt;BR&gt;`).
       XML 파서가 엔티티를 풀어주므로 그 뒤에 `<BR>` 을 줄바꿈으로 바꾸고 나머지 태그를 지운다.
       그냥 쓰면 한 줄짜리 태그 범벅이 되어 사람도 LLM 도 읽기 어렵다.
    ⚠️ 목차가 **없는 자료도 정상**이다(검색 결과의 `목차` 필드가 'N'). 빈 문자열을 돌려준다.
    """
    root = _to_root(body)
    _check_error_envelope(root)

    raw = root.findtext("./toc") or ""       # 위치가 아니라 태그명으로 찾는다
    toc = _TAG.sub("", _BLOCK.sub("\n", raw))
    # 항목마다 `<p>` 가 2개씩 붙어 빈 줄이 생긴다 — 연속 빈 줄만 하나로 접는다.
    # ⚠️ **행 앞 들여쓰기는 보존한다.** 목차의 계층(장→절→항)이 선행 공백으로만 표현되기
    #    때문이다(실측: '제1장 …' → ' 1. …' → '  [1] …'). 각 줄에 strip() 을 걸면
    #    구조 정보가 통째로 사라진다 — 초판이 그렇게 하고 있었다.
    lines = [ln.rstrip() for ln in toc.split("\n")]
    out: list[str] = []
    for ln in lines:
        if ln.strip() or (out and out[-1].strip()):
            out.append(ln)
    toc = "\n".join(out).strip("\n")
    env = {k: v for k, v in _envelope(root, 0).items() if k != "total"}
    # 🔴 **`목차정보없음` 센티널** — 검색 결과의 `목차` 플래그가 'Y' 인데 본문이
    #    `목차정보없음` 의 반복인 자료가 있다(✅ 실측: 고서 목차=Y 27건 중 표본 5/5 전부).
    #    길이가 최대 792자까지 나와 `if toc:` 검사를 그대로 통과하므로, 걸러내지 않으면
    #    **'목차 있음'으로 오인**하고 코퍼스에 의미 없는 문자열이 쌓인다.
    #    뒤에 `[내용누락;p.165-165]`·`[원문불량;p.51]` 같은 주기가 붙기도 한다.
    lines_nonempty = [ln.strip() for ln in toc.split("\n") if ln.strip()]
    if lines_nonempty and all(ln.startswith(TOC_SENTINEL) for ln in lines_nonempty):
        env["toc_sentinel"] = toc[:120]      # 원문은 진단용으로 남긴다
        toc = ""
    env["has_toc"] = bool(toc)
    return toc, env
