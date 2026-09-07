"""국회도서관 자료검색 API HTTP 클라이언트 — 페이징·재시도·절단 메타 산출.

설계의 핵심 두 가지(둘 다 라이브 실측에서 나왔다):

1. **`ERR04` 는 종료가 아니라 재시도 대상이다.** 깊은 오프셋에서 간헐적으로 나며 단조롭지
   않다(실측: displaylines=1000 에서 p30❌ p40✅ p50✅ p60❌, 같은 요청 3회 중 1회만 실패).
   이것을 종료 조건으로 쓰면 **수집이 임의 지점에서 조용히 잘린다.**
2. **`cap_hit` 과 `truncated` 를 분리한다.** 처방이 다르기 때문이다.
   - `truncated` → `max_records` 를 올리면 해결
   - `cap_hit`  → `pageno` 가 99를 넘어야 해서 **올려도 해결 안 됨** → 검색식을 쪼개야 함
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

from . import __version__
from .config import (
    API_RECORD_CAP,
    MAX_DISPLAYLINES,
    PAGENO_MAX,
    DETAIL_INFO_URL,
    HEAVY_DBNAMES,
    SEARCH_BASIC_URL,
    SEARCH_DETAIL_URL,
    TOC_ALWAYS_EMPTY_DBNAMES,
    TOC_URL,
    build_url,
    scrub,
    use_os_trust,
    validate_detail_search,
    validate_search,
)
from .models import Record
from .parser import (
    ApiError,
    ParseError,
    looks_like_ignored_search,
    parse_detail_response,
    parse_search_response,
    parse_toc_response,
)

log = logging.getLogger("na_mcp")


class NaError(RuntimeError):
    """네트워크·HTTP·파싱을 아우르는 클라이언트 오류 (인증키는 절대 싣지 않는다)."""


class NaClient:
    def __init__(self, *, throttle: float = 0.4, timeout: int = 30,
                 max_retries: int = 3) -> None:
        use_os_trust()          # ⚠️ 등록 명령줄이 아니라 코드에서 — .mcpb/바이너리 경로 대응
        self.throttle = max(0.0, throttle)
        self.timeout = timeout
        self.max_retries = max(1, max_retries)
        self._session = requests.Session()
        self._session.headers["User-Agent"] = (
            f"na-openapi-mcp/{__version__} (+https://github.com/rubatoyd/na-openapi-mcp)")
        self._last_call = 0.0

    # ── 저수준 ───────────────────────────────────────────────────────────────
    def _sleep(self) -> None:
        gap = time.monotonic() - self._last_call
        if gap < self.throttle:
            time.sleep(self.throttle - gap)
        self._last_call = time.monotonic()

    def _call(self, endpoint: str, params: dict) -> bytes:
        """1회 호출 → 본문 **바이트**.

        🔴 **`resp.text` 를 쓰지 않는다.** 이 API 는 `Content-Type: application/xml` 에
           charset 을 붙이지 않아 requests 가 chardet 추측에 의존한다. 실측에서
           `dbname=고서` 응답이 **`ptcp154`(카자흐 키릴)로 추측**돼 한글이 통째로 깨졌다
           (`제어번호` → `м ңм–ҙлІҲнҳё`). 일반도서가 멀쩡했던 것은 우연히 utf-8 로
           추측됐기 때문이지 옳아서가 아니다 — **조용한 데이터 손상**이다.
           바이트를 그대로 넘기면 ElementTree 가 XML 선언(`encoding="UTF-8"`)을 존중한다.

        ⚠️ **`raise_for_status()` 를 쓰지 않는다** — requests 가 인증키가 든 전체 URL 을
           예외 메시지에 박는다(자매 프로젝트에서 실제 누출 경로였다). 상태코드만 본다.
        ⚠️ requests 예외도 **타입만** 남긴다 — 본문에 URL 이 실린다.
        """
        self._sleep()
        url = build_url(endpoint, params)       # 인증키 결합은 여기 한 곳에서만
        try:
            resp = self._session.get(url, timeout=self.timeout)
        except requests.RequestException as e:
            raise NaError(f"요청 실패: {type(e).__name__}") from None
        if resp.status_code != 200:
            # 401 등은 본문에 오류 봉투가 실려 오므로 파서가 코드를 뽑도록 넘긴다.
            body = resp.content or b""
            if not body.strip():
                raise NaError(f"HTTP {resp.status_code} — 빈 응답")
            return body
        return resp.content or b""

    def _fetch_page(self, endpoint: str, params: dict, *, expect_records: bool = True
                    ) -> tuple[int, list[Record], dict]:
        """재시도를 포함한 1페이지 조회.

        `ERR04` 처럼 일시적인 코드만 재시도한다. 쿼터·키 오류(22·30·31…)는 즉시 포기한다 —
        재시도하면 쿼터만 더 태운다.
        """
        last: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                return parse_search_response(self._call(endpoint, params),
                                             expect_records=expect_records)
            except ApiError as e:
                last = e
                if not e.retryable:
                    raise NaError(str(e)) from None
                log.warning("일시 오류 [%s] — 재시도 %d/%d", e.code, attempt + 1, self.max_retries)
            except (ParseError, NaError) as e:
                last = e
                log.warning("응답 오류(%s) — 재시도 %d/%d",
                            type(e).__name__, attempt + 1, self.max_retries)
            if attempt < self.max_retries - 1:
                time.sleep(self.throttle * (2 ** attempt) + 0.3)   # 지수 백오프
        raise NaError(scrub(str(last) if last else "알 수 없는 오류"))

    # ── 단일 검색 ────────────────────────────────────────────────────────────
    def search_meta(self, search: str, *, max_records: int = 100,
                    dbname: str | None = None, option: str | None = None,
                    page_size: int | None = None,
                    extra_params: dict | None = None) -> tuple[list[Record], dict[str, Any]]:
        """검색 후 (레코드, 메타). 메타에 절단 사유를 **반드시** 실어 보낸다.

        `dbname` 이 주어지면 `/detail`(상세검색), 없으면 `/basic`(통합검색)을 쓴다.
        ⚠️ 검색항목 화이트리스트 검증을 여기서 통과시킨다 — 미지원 항목은 오류가 아니라
           **전체 카탈로그 1,300만 건**을 돌려주므로 조용히 넘기면 안 된다.
        """
        endpoint = SEARCH_DETAIL_URL if dbname else SEARCH_BASIC_URL
        if dbname:
            # /detail 은 dbname 마다 검색항목 어휘가 다르다. 틀리면 ERR04 인데 그 코드는
            # 일시 오류이기도 해서 재시도를 낭비한다 → 호출 전에 막는다.
            validate_detail_search(dbname, search)
        else:
            validate_search(search)

        size = min(max(1, page_size or MAX_DISPLAYLINES), MAX_DISPLAYLINES)
        # ⚠️ 일부 자료종은 레코드가 무거워 500건 요청에서 ConnectionError 로 끊긴다(실측).
        #    호출자가 명시하지 않았으면 안전한 값으로 낮춘다.
        cap = HEAVY_DBNAMES.get(dbname or "")
        if cap and size > cap:
            size = cap
        want = max(1, int(max_records))

        records: list[Record] = []
        seen: set[str] = set()
        meta: dict[str, Any] = {
            "search": search, "endpoint": "detail" if dbname else "basic",
            "dbname": dbname, "option": option,
            "page_size": size, "requested": want,
            "pages_fetched": 0, "failed_pages": [],
        }

        total = 0
        for page in range(1, PAGENO_MAX + 1):
            if len(records) >= want:
                break
            params = {"pageno": page, "displaylines": size, "search": search}
            if dbname:
                params["dbname"] = dbname
            if option:
                params["option"] = option
            if extra_params:
                params.update(extra_params)

            try:
                total, page_recs, env = self._fetch_page(endpoint, params,
                                                         expect_records=(page == 1))
            except NaError as e:
                # 재시도를 다 쓰고도 실패한 페이지는 **결손으로 기록**하고 계속한다.
                # 조용히 멈추면 부분 수집을 전수로 오인한다.
                meta["failed_pages"].append({"pageno": page, "error": scrub(str(e))})
                if page == 1:
                    raise
                break

            meta["pages_fetched"] += 1
            meta.setdefault("envelope", env)
            new = 0
            for r in page_recs:
                k = r.dedup_key()
                if k in seen:
                    continue
                seen.add(k)
                records.append(r)
                new += 1
                if len(records) >= want:
                    break
            if not page_recs or new == 0:
                break                      # 페이지네이션 안전장치(새 레코드 0이면 종료)
            if total and len(records) >= min(total, API_RECORD_CAP):
                break                      # 다 받았다 — 끝을 지난 페이지를 부르지 않는다
                                           # (쿼터 낭비 + 빈 응답 재시도를 유발했다)

        meta["total"] = total
        meta["fetched"] = len(records)

        # ── 절단 사유 분리 ───────────────────────────────────────────────────
        # truncated: 더 받을 수 있는데 max_records 에서 멈춤 → 올리면 해결
        meta["truncated"] = total > len(records) and len(records) >= want
        # cap_hit: API 구조상 더 못 받음(pageno<=99) → 올려도 해결 안 됨, 검색식을 쪼개야 함
        meta["cap_hit"] = total > API_RECORD_CAP
        meta["api_record_cap"] = API_RECORD_CAP
        if meta["cap_hit"]:
            meta["cap_note"] = (
                f"total {total:,}건이 회수 한계 {API_RECORD_CAP:,}건"
                f"(pageno 최대 {PAGENO_MAX} × {MAX_DISPLAYLINES}건)을 넘습니다. "
                f"max_records 를 올려도 {API_RECORD_CAP:,}건 이상은 받을 수 없습니다 — "
                f"검색식을 좁히거나(dbname·발행년도) 검색어를 쪼개세요."
            )
        if meta["failed_pages"]:
            meta["incomplete_note"] = (
                f"{len(meta['failed_pages'])}개 페이지가 재시도 후에도 실패해 결손입니다 — "
                f"전수가 아닙니다.")
        if dbname in TOC_ALWAYS_EMPTY_DBNAMES:
            meta["toc_note"] = (
                f"'{dbname}' 은 `목차` 가 전건 'N' 입니다(census 실측) — "
                f"na_toc 를 불러도 빈 결과이므로 호출하지 마세요(쿼터만 소모).")
        if looks_like_ignored_search(total):
            meta["ignored_search_warning"] = (
                f"total 이 전체 카탈로그 규모({total:,})입니다 — 검색어가 무시됐을 가능성이 "
                f"큽니다. 검색항목 이름을 확인하세요.")
        return records, meta

    def search(self, search: str, **kw) -> list[Record]:
        """레코드만 필요할 때의 얇은 래퍼 (메타가 필요하면 search_meta 를 쓸 것)."""
        return self.search_meta(search, **kw)[0]

    # ── 다중 검색어 합집합 ───────────────────────────────────────────────────
    def search_terms_meta(self, terms: list[str], *, max_records: int = 100,
                          dbname: str | None = None, option: str | None = None,
                          page_size: int | None = None,
                          extra_params: dict | None = None,
                          ) -> tuple[list[Record], dict[str, Any]]:
        """검색어들을 각각 조회해 **합집합**으로 모은다.

        이 API 의 `search` 는 `|` 가 **AND** 이므로 OR(합집합)은 클라이언트에서 만들어야 한다.
        """
        merged: list[Record] = []
        seen: set[str] = set()
        axes: list[dict] = []
        stopped_early = False
        unsearched: list[str] = []

        for i, term in enumerate(terms):
            remaining = max_records - len(merged)
            if remaining <= 0:
                # ⚠️ 마지막 검색어에서 한도를 채운 것은 조기 중단이 아니다
                #    (자매 프로젝트에서 이 오탐으로 전수 코퍼스에 절단 경고가 붙었다).
                stopped_early = i < len(terms)
                unsearched = list(terms[i:])
                break
            recs, m = self.search_meta(term, max_records=remaining, dbname=dbname,
                                       option=option, page_size=page_size,
                                       extra_params=extra_params)
            new = 0
            for r in recs:
                k = r.dedup_key()
                if k in seen:
                    continue
                seen.add(k)
                merged.append(r)
                new += 1
            axes.append({"term": term, "total": m.get("total", 0),
                         "fetched": m.get("fetched", 0), "new": new,
                         "cap_hit": m.get("cap_hit", False),
                         "failed_pages": len(m.get("failed_pages", []))})

        meta: dict[str, Any] = {
            "terms": terms, "terms_searched": [a["term"] for a in axes], "axes": axes,
            "fetched": len(merged), "requested": max_records,
            "stopped_early": stopped_early, "terms_unsearched": unsearched,
            "cap_hit_terms": [a["term"] for a in axes if a["cap_hit"]],
            "incomplete_terms": [a["term"] for a in axes if a["failed_pages"]],
            "api_record_cap": API_RECORD_CAP,
        }
        if stopped_early:
            # 🔴 `max_records` 는 **검색어 전체에 걸친 예산**이라 앞 검색어가 다 써버리면
            #    뒤 검색어는 아예 조회되지 않는다. 이것을 알리지 않으면 호출자는
            #    '검색어 N개의 합집합'을 받았다고 오인한다(조용한 절단).
            meta["stopped_early_note"] = (
                f"max_records({max_records:,})를 앞선 검색어에서 모두 소진해 "
                f"{len(unsearched)}개 검색어를 **조회하지 않았습니다**: {', '.join(unsearched)}. "
                f"합집합이 완전하지 않습니다 — max_records 를 올리거나 검색어를 나눠 실행하세요."
            )
        if meta["cap_hit_terms"]:
            meta["cap_note"] = (
                f"다음 검색어가 회수 한계({API_RECORD_CAP:,}건)에 걸렸습니다 — 전수가 아닙니다: "
                f"{', '.join(meta['cap_hit_terms'])}")
        if meta["incomplete_terms"]:
            meta["incomplete_note"] = (
                f"다음 검색어에서 결손 페이지가 있습니다: {', '.join(meta['incomplete_terms'])}")
        return merged, meta

    def search_terms(self, terms: list[str], **kw) -> list[Record]:
        return self.search_terms_meta(terms, **kw)[0]

    # ── 상세정보 · 목차 (detailinfoservice — data.go.kr 15098175 별도 활용신청) ──
    def _lookup(self, endpoint: str, controlno: str, parse, *, what: str):
        """제어번호 1건 조회 공통부.

        🔴 **없는 제어번호도 `ERR04` 로 온다**(실측) — 전용 '자료 없음' 코드가 없다.
           그런데 `ERR04` 는 깊은 페이지의 일시 오류 코드이기도 해서 재시도 대상이다.
           단건 조회에서 3회 재시도는 대부분 낭비이므로 **2회로 줄이고**, 실패 시
           '제어번호가 잘못됐을 가능성'을 메시지에 명시한다(코드만으로는 구분 불가).
        """
        cn = (controlno or "").strip()
        if not cn:
            raise NaError("controlno 가 비었습니다 — 검색 결과의 `제어번호` 를 넣으세요.")
        attempts = min(2, self.max_retries)
        last: Exception | None = None
        for attempt in range(attempts):
            try:
                return parse(self._call(endpoint, {"controlno": cn}))
            except ApiError as e:
                last = e
                if not e.retryable:
                    raise NaError(str(e)) from None
                log.warning("%s 조회 일시 오류 [%s] — 재시도 %d/%d",
                            what, e.code, attempt + 1, attempts)
            except (ParseError, NaError) as e:
                last = e
            if attempt < attempts - 1:
                time.sleep(self.throttle * 2 + 0.3)
        raise NaError(scrub(
            f"{what} 조회 실패(controlno={cn}): {last}. "
            f"⚠️ 이 API 는 **존재하지 않는 제어번호도 ERR04 로 응답한다**(실측) — "
            f"일시 오류와 구분되지 않으므로 제어번호를 먼저 확인하세요."))

    def detail(self, controlno: str) -> tuple[dict[str, str], dict[str, Any]]:
        """상세정보 항목조회 — 검색 결과보다 필드가 많다(ISBN·DDC·별치기호·자료실 등)."""
        return self._lookup(DETAIL_INFO_URL, controlno, parse_detail_response, what="상세정보")

    def toc(self, controlno: str) -> tuple[str, dict[str, Any]]:
        """목차정보 항목조회.

        ⚠️ 목차가 없는 자료도 정상이다 — 검색·상세 결과의 `목차` 필드가 'Y' 인 것만 부르면
           헛호출(과 쿼터 낭비)을 줄일 수 있다.
        """
        return self._lookup(TOC_URL, controlno, parse_toc_response, what="목차")
