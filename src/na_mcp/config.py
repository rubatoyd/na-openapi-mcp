"""환경설정·자격증명·엔드포인트 — 국회도서관 자료검색 OpenAPI.

인증키는 코드/로그에 하드코딩하지 않고 `.env`(gitignore) 또는 OS 환경변수에서만 읽는다.
실측 근거는 docs/NA_API_GUIDE.md 참조 — 이 파일의 상수는 전부 라이브 왕복으로 확정했다.
"""
from __future__ import annotations

import logging
import os
import urllib.parse

from dotenv import load_dotenv

# .env 를 한 번 로드 (이미 설정된 환경변수는 덮어쓰지 않음)
load_dotenv(override=False)

BASE_URL = os.environ.get("NA_BASE_URL", "https://apis.data.go.kr/9720000")
SEARCH_BASIC_URL = f"{BASE_URL}/searchservice/basic"
SEARCH_DETAIL_URL = f"{BASE_URL}/searchservice/detail"
# 별도 데이터셋(data.go.kr 15098175, 개발단계 자동승인) — ❓ 라이브 미검증.
DETAIL_INFO_URL = f"{BASE_URL}/detailinfoservice/detail"
TOC_URL = f"{BASE_URL}/detailinfoservice/toc"

# ── 상한 (✅ 2026-09-07 라이브 실측) ──────────────────────────────────────────
# `pageno` 는 **99가 하드 상한**이다. displaylines 와 무관하다는 것을 크기 영향을 제거한
# 대조(displaylines=1)로 확인했다: 98✅ 99✅ 100❌ 101❌ 150❌.
PAGENO_MAX = int(os.environ.get("NA_PAGENO_MAX", "99"))

# `displaylines` 상한 1000. 초과값(2000·5000)은 오류가 아니라 **조용히 1000으로 절삭**된다.
# ⚠️ 공식 가이드(openapi.nanet.go.kr)는 100이라고 적고 있으나 실측 1000이 동작하며
#    고유 제어번호 1000건을 실제로 준다(빈값 0). 문서를 믿으면 호출 수가 10배가 된다.
MAX_DISPLAYLINES = int(os.environ.get("NA_MAX_DISPLAYLINES", "1000"))

# 한 검색식으로 회수 가능한 최대 = PAGENO_MAX × MAX_DISPLAYLINES.
# 자매 API(국립중앙도서관)의 500건 상한과 달리 매우 넉넉하다 — 대부분 분할이 필요 없다.
API_RECORD_CAP = PAGENO_MAX * MAX_DISPLAYLINES   # 99,000

# 전체 카탈로그 건수(✅ 실측) — 검색어가 무시됐을 때 돌아오는 수. 폴백 탐지의 기준점이다.
FULL_CATALOG_TOTAL = 13_097_591

# ── 검색항목 화이트리스트 ────────────────────────────────────────────────────
# 🔴 **미지원 값은 오류가 아니라 검색어를 통째로 무시하고 전체 카탈로그(13,097,591건)를 준다.**
#    실측: 저자명·ISBN·발행년도·zzz 전부 13,097,591. `저자명` 은 /detail 에서는 유효한
#    이름이라 오타가 아니라 **헷갈려서** 쓰기 쉽다 → 반드시 거부한다.
SEARCH_FIELDS_BASIC = ("기본검색", "전체", "자료명", "저자", "발행자", "키워드", "청구기호")

# 실측 건수(kwd=오욱환) — 각 항목이 서로 다른 값을 내므로 실제로 해석됨이 확정된다.
SEARCH_FIELD_EVIDENCE = {
    "전체": 105, "기본검색": 88, "자료명": 4, "저자": 76,
    "키워드": 7, "발행자": 0, "청구기호": 0,
}

# 흔한 오용 → 올바른 값 안내
SEARCH_FIELD_ALIASES = {
    "저자명": "저자",      # /detail 에서는 유효하나 /basic 에서는 전체 DB 반환
    "논문명": "자료명",
    "기사명": "자료명",
    "제목": "자료명",
    "title": "자료명",
    "author": "저자",
    "all": "전체",
}

# `/detail` 의 `dbname` 21종 (📄 openapi.nanet.go.kr/S3002_03.html — ❓ 라이브 미검증)
# ⚠️ `학술지,잡지` 와 `표,그림DB` 는 **이름 안에 쉼표**가 있다.
DBNAMES = (
    "일반도서", "E-BOOK", "고서", "세미나자료", "웹자료", "학위논문",
    "국내기사", "국외기사", "학술지,잡지", "신문", "전자저널",
    "동영상자료", "오디오자료", "전자매체", "마이크로폼자료", "지도/기타자료",
    "외국법률번역DB", "국회회의록", "국회의안정보", "표,그림DB", "지식공유",
)


# ── `/detail` 의 dbname 별 검색항목 어휘 ──────────────────────────────────────
# 🔴 **`/basic` 과 어휘가 다르다.** 그리고 동작 방식도 정반대다(✅ 실측 확인):
#    · `/basic`  : 틀린 검색항목 → 오류 없이 **전체 카탈로그** 반환 (조용한 사고)
#    · `/detail` : 틀린 검색항목 또는 dbname → **ERR04** (시끄러운 실패)
#    실측: 학위논문+자료명 → ERR04 / 학위논문+논문명 → 112,360 ·
#          일반도서+논문명 → ERR04 / 일반도서+자료명 → 64,084 ·
#          국내기사+자료명 → ERR04 / 국내기사+기사명 → 411,980 ·
#          dbname='없는DB'·'전체' → ERR04
# ⚠️ ERR04 는 일시 오류 코드이기도 해서 재시도 대상이다 — 그래서 **호출 전에 여기서 막는다.**
#    막지 않으면 영구적인 사용자 오류에 재시도 3회 + 백오프를 낭비한다.
#
# 📄 출처: https://openapi.nanet.go.kr/S3002_03.html — ✅ 위 5개 조합은 라이브로 확인했다.
# ✅ **전수 실측 결과(2026-09-07)** — `scripts/probe_limits.py categories` 로 재현 가능.
# 🔴 문서(openapi.nanet.go.kr/S3002_03.html) 목록을 그대로 쓰면 안 된다. 실측과 크게 다르다:
#   ① 문서에 있으나 **거부되는** 이름이 많다 — `발행년도`·`본문언어`·`저작권허락`·`초록유무`·
#      `원문DB유무`·`음성지원유무`·`별치기호`·`DDC분류`·`학위구분`·`학위년도`·`처리상태`·
#      `제안일자`·`대수`·`위원회` 등. 형에 맞는 값(`발행년도,2020`)으로 재시험해도 ERR04다.
#      → 웹 UI 의 **표시/필터 항목**이지 API 검색 항목이 아니다.
#   ② 문서에 **없는데 동작하는** 이름이 있다 — 학위논문 `지도교수`
#      (문서의 `지도교수(2009~)` 는 ERR04).
#   ③ 슬래시가 든 이름은 **한 덩어리**다 — `수록지명/신문명`·`CIS/UNSA`.
#      `수록지명` 만 쓰면 학술지·신문에서는 ERR04(국내기사에서는 `수록지명` 이 맞다).
#   ④ `지식공유` 는 **어떤 항목도 통하지 않는다**(전체항목 포함) — dbname 자체가 무효로 보인다.
# ⚠️ 측정 시 **ERR04 재시도가 필수**다. 1회만 태우면 간헐 오류가 '거부'로 잘못 기록된다
#    (실제로 `세미나자료/전체항목` 이 1차 ERR04 → 재시도 후 total=5,432 로 뒤집혔다).
_BOOKISH = ("전체항목", "자료명", "저자명", "발행자", "키워드", "목차",
            "ISBN", "ISSN", "DDC", "CIS/UNSA", "청구기호")
DB_CATEGORIES: dict[str, tuple[str, ...]] = {
    "일반도서": _BOOKISH,
    "E-BOOK": _BOOKISH,
    "고서": _BOOKISH,
    "세미나자료": ("전체항목", "자료명", "저자명", "발행자", "키워드", "목차",
                "CIS/UNSA", "ISBN", "ISSN"),
    "웹자료": ("전체항목", "자료명", "저자명", "발행기관", "키워드", "목차",
             "ISBN", "ISSN", "DDC", "CIS/UNSA", "청구기호"),   # 발행자 ✗ → 발행기관
    "학위논문": ("전체항목", "논문명", "저자명", "발행자", "키워드", "목차",
              "전공", "DDC", "청구기호", "지도교수"),
    "국내기사": ("전체항목", "기사명", "저자명", "수록지명", "발행자", "키워드", "목차"),
    "국외기사": ("전체항목", "기사명", "저자명", "수록지명", "발행자", "키워드", "목차"),
    "학술지,잡지": ("전체항목", "수록지명/신문명", "발행자", "발행처", "목차",
                 "ISSN", "DDC", "청구기호"),
    "신문": ("전체항목", "수록지명/신문명", "발행자", "발행처", "목차",
           "ISSN", "DDC", "청구기호"),
    "전자저널": ("전체항목", "자료명", "저자명", "ISSN"),
    "동영상자료": _BOOKISH,
    "오디오자료": _BOOKISH,
    "전자매체": _BOOKISH,
    "마이크로폼자료": _BOOKISH,
    "지도/기타자료": _BOOKISH,
    "외국법률번역DB": ("전체항목", "번역법령명", "원법령명", "키워드", "국내관련법률", "목차"),
    "국회회의록": ("전체항목", "안건", "발언자", "내용", "키워드"),
    "국회의안정보": ("전체항목", "의안명", "대표 발의자", "공동 발의자",
                 "제안이유 및 주요내용", "키워드", "소관위원회"),
    "표,그림DB": ("전체항목", "표그림명", "출처"),
    "지식공유": (),      # ⚠️ 실측상 어떤 항목도 통하지 않는다 — 사실상 사용 불가
}

# 검색 항목이 아니라 **표시/필터 전용**인 이름들 — 쓰면 ERR04. 안내에 쓴다.
DISPLAY_ONLY_FIELDS = (
    "발행년도", "학위년도", "본문언어", "발행언어", "저작권허락", "초록유무",
    "원문DB유무", "음성지원유무", "별치기호", "DDC분류", "학위구분",
    "처리상태", "제안대수", "제안회기", "제안일자", "대수", "위원회", "회의", "회수", "기간",
    "내용형식", "내용연도", "국가", "법률주제", "상임위원회",
)


# ── 자료종별 census 로 확정된 사실 (✅ 2026-09-07, 표본 약 21,000건) ──────────
# 재현: `python scripts/probe_fields.py`. 전체 표 → docs/NA_API_GUIDE.md §8

# 🔴 **값 자리에 오는 안내문.** 분류기호·소장처가 아니라 안내 문구가 들어온다.
#    ⚠️ 표기가 흔들린다 — `전자형태로만 열람가능함`(공백 없음)과 `… 열람 가능함`(공백 있음)이
#       섞여 온다. 한쪽만 비교하면 계수가 어긋난다(웹자료 DDC 1,969건을 실채움으로 오산했다).
#       → **공백을 제거하고 비교한다.**
PLACEHOLDER_VALUES = (
    "전자형태로만열람가능함",   # E-BOOK DDC 99.95% · 웹자료 사실상 전건 · 동영상 청구기호 전건
    "해당논문없음",             # 학위논문 DDC·청구기호 17%
    "해당자료없음",             # 국내기사·세미나자료 자료실
    "해당사항없음",
    "목차정보없음",             # 고서 목차 본문 (parser 가 별도 처리)
)


def is_placeholder(value: str | None) -> bool:
    """값이 데이터가 아니라 안내 문구인가 (공백 무시 비교).

    ⚠️ **부분일치로 판정하면 안 된다.** 초판은 `ph in flat` 이라, 그 문구를 **포함한**
       정상 자료명·키워드까지 통째로 비웠다(적대적 리뷰 지적). 예컨대
       `해당사항없음 처리 실태 연구` 같은 표제가 빈 문자열이 된다.
    → **값 전체가 안내문일 때만** 참으로 본다. 다만 실측상 짧은 별치기호 접두가 붙는다
       (`EB 전자형태로만 열람 가능함`·`TM 해당 논문 없음`·`VM …`) — 그것만 허용한다.
    """
    if not value:
        return False
    flat = "".join(str(value).split())
    for ph in PLACEHOLDER_VALUES:
        if flat == ph:
            return True
        if flat.endswith(ph):
            prefix = flat[: -len(ph)]
            if prefix.isalnum() and prefix.isupper() and len(prefix) <= 3:
                return True     # 별치기호 접두(EB·ER·VM·TM·TD …)
    return False


# 🔴 **수락되지만 어떤 검색어로도 0건**인 (dbname, 검색항목) 조합 — ✅ 적대적 리뷰 실측.
#    ERR04 가 아니라 정상 200 + total=0 이라 화이트리스트 판정법
#    ("ERR04=거부 / total=N=유효")이 **유효로 기록해 버렸다.** 그 결과
#    `na_search(dbname='일반도서', search='목차,교육')` 이 오류도 경고도 없이 0건을 준다 —
#    이 저장소가 막겠다고 선언한 '조용한 절단'과 같은 실패 양식이다.
#    ⚠️ 거부하지 않고 **경고**한다: API 가 실제로 수락하는 값이고, 색인이 채워지면
#       동작할 수 있다. 다만 지금은 쓸모없다는 사실을 반드시 알려야 한다.
#    실측: 동작 = 학위논문(346,565)·국내기사(302,024)·마이크로폼자료(75).
ALWAYS_ZERO_CATEGORIES = {
    "일반도서": ("목차",), "세미나자료": ("목차",), "웹자료": ("목차",),
    "고서": ("목차",), "동영상자료": ("목차",), "E-BOOK": ("목차",),
    "학술지,잡지": ("목차",), "신문": ("목차",), "국외기사": ("목차",),
    "외국법률번역DB": ("목차",),
}


def zero_yield_fields(dbname: str | None, search: str) -> list[str]:
    """이 조합에서 **항상 0건**으로 실측된 검색항목들을 돌려준다(경고용)."""
    if not dbname:
        return []
    dead = ALWAYS_ZERO_CATEGORIES.get(dbname, ())
    used = [c.split(",", 1)[0].strip() for c in (search or "").split("|") if "," in c]
    return [f for f in used if f in dead]


# 🔴 `목차` 가 **상수 'N'** 인 자료종 — `na_toc` 호출이 통째로 무의미하다(쿼터만 쓴다).
TOC_ALWAYS_EMPTY_DBNAMES = frozenset({
    "E-BOOK", "학술지,잡지", "신문", "국외기사", "동영상자료",
})

# ⚠️ `displaylines=500` 에서 ConnectionError 로 끊기는 자료종(레코드가 무겁다).
HEAVY_DBNAMES = {"외국법률번역DB": 200, "표,그림DB": 200}

# 🔴 해당 자료종에서 **표본 전건 값이 비어 있던** 필드.
#    `if k in fields` 는 모든 자료종에서 항상 True 라 판별력이 0이다.
# ⚠️ **'모집단에 없다'가 아니라 '이 표본에 없었다'** 이다 — 적대적 리뷰가 고서 `ISBN` 을
#    795건 재측정해 10건(영인 총서 계열)을 찾아내 목록에서 뺐다. 표본이 192건이었고
#    1.26% 라면 0건이 나올 확률이 약 9% 다. 나머지 항목도 같은 성격의 한정 주장이다.
EMPTY_FIELDS_BY_DB = {
    "학위논문": ("전공",),
    "학술지,잡지": ("발행국",),
    "신문": ("ISSN", "발행국", "간행빈도"),
    "전자저널": ("저자명", "본문언어", "자료실"),
    "웹자료": ("CIS/UNSA",),
    "세미나자료": ("CIS/UNSA",),
    "E-BOOK": ("ISSN", "초록유무", "자료실"),
    "고서": ("ISSN", "초록유무"),   # ISBN 은 반증됨(영인 총서에 존재)
    "국외기사": ("초록유무",),
    "국회회의록": ("발행자", "자료실"),
    "국회의안정보": ("자료실",),
    "외국법률번역DB": ("자료실",),
    "표,그림DB": ("자료실",),
}

# 🔴 이름과 내용이 다른 필드 — 그대로 믿으면 안 된다.
MISLEADING_FIELDS = {
    "국회회의록/발언자": "상수 `대한민국 국회` (1,000건 전부). 실제 발언자가 아니다 — "
                       "발언자별 분석은 `내용` 본문을 파싱해야 한다.",
    "국회의안정보/처리상태": "고유값 3개(`일부개정`·`제정`…). 가결/부결이 아니라 개정 종류에 가깝다.",
    "CIS/UNSA": "국제기구 문헌번호가 아니라 DDC 형식 분류번호(`811.33`).",
    "전자저널/DB Title": "제공 플랫폼(`Scholar`·`KISS` 등 16종).",
}

# ✅ 서술형 본문(초록·요약에 해당)을 주는 자료종은 **둘뿐**이다.
NARRATIVE_TEXT_FIELDS = {
    "국회의안정보": "제안이유 및 주요내용",   # 982/1,000
    "국회회의록": "내용",                    # 789/1,000
}

# 🔴 `초록유무` 는 자료종마다 극단적으로 다르고, **Y 여도 본문을 받을 방법이 없다**
#    (`/abstract`·`/summary` 등 후보 엔드포인트 전부 HTTP 400 — 실측).
ABSTRACT_FLAG_RATE = {
    "학위논문": 0.345, "국내기사": 0.302, "일반도서": 0.012, "세미나자료": 0.001,
    "E-BOOK": 0.0, "고서": 0.0, "국외기사": 0.0,
}


class SearchFieldError(ValueError):
    """검색항목이 화이트리스트에 없을 때 — 조용히 넘기면 전체 카탈로그를 결과로 오인한다."""


def validate_search(search: str) -> str:
    """`search` 문자열의 검색항목을 검증한다. 통과하면 원문 그대로 반환.

    형식: `검색항목,키워드` 를 `|` 로 연결(AND). 예: `전체,홍길동|자료명,교육`.
    """
    if not search or not search.strip():
        raise SearchFieldError("search 가 비었습니다 — `검색항목,키워드` 형식이어야 합니다.")
    # 🔴 **정규화한 문자열을 돌려주고, 호출자는 그것을 전송해야 한다.**
    #    초판은 검증할 때만 strip 하고 원문을 그대로 보냈다. 그 결과 ` 전체 ,교육` 이
    #    검증을 통과한 뒤 서버에는 공백이 붙은 채 전달돼 **검색항목이 인식되지 않고
    #    전체 카탈로그 13,097,591건**이 돌아왔다(적대적 리뷰가 라이브로 잡았다).
    clauses: list[str] = []
    for clause in search.split("|"):
        clause = clause.strip()
        if not clause:
            continue
        if "," not in clause:
            raise SearchFieldError(
                f"검색어 형식 오류: {clause!r} — `검색항목,키워드` 여야 합니다"
                f"(예: 전체,홍길동). 사용 가능한 검색항목: {', '.join(SEARCH_FIELDS_BASIC)}"
            )
        field = clause.split(",", 1)[0].strip()
        if field not in SEARCH_FIELDS_BASIC:
            hint = SEARCH_FIELD_ALIASES.get(field)
            extra = f" '{field}' 대신 '{hint}' 를 쓰세요." if hint else ""
            raise SearchFieldError(
                f"지원하지 않는 검색항목: {field!r}.{extra} "
                f"사용 가능: {', '.join(SEARCH_FIELDS_BASIC)}. "
                f"⚠️ 이 API 는 미지원 검색항목을 오류로 알리지 않고 **검색어를 무시한 채 "
                f"전체 카탈로그({FULL_CATALOG_TOTAL:,}건)를 반환**하므로 여기서 막습니다."
            )
        keyword = clause.split(",", 1)[1].strip()
        if not keyword:
            raise SearchFieldError(
                f"검색어가 비었습니다: {clause!r} — `{field},키워드` 형태로 값을 주세요.")
        clauses.append(f"{field},{keyword}")
    if not clauses:
        # 🔴 `search='|'` 처럼 **절이 전부 비면** 위 루프가 통째로 건너뛰어 통과했다.
        #    그대로 전송하면 검색어 없는 질의가 되어 전체 카탈로그가 온다(적대적 리뷰 실측).
        raise SearchFieldError(
            f"유효한 검색절이 하나도 없습니다: {search!r} — `검색항목,키워드` 형식이어야 합니다. "
            f"⚠️ 이대로 보내면 전체 카탈로그({FULL_CATALOG_TOTAL:,}건)가 반환됩니다.")
    return "|".join(clauses)


def validate_detail_search(dbname: str, search: str) -> str:
    """상세검색(`/detail`)의 `dbname` + 검색항목 조합을 사전 검증한다.

    ✅ 실측: 틀린 조합은 **ERR04** 로 실패한다(예: 학위논문+자료명, 일반도서+논문명).
    ⚠️ ERR04 는 깊은 오프셋의 **일시 오류 코드이기도** 해서 클라이언트가 재시도한다.
       영구적인 사용자 오류에 재시도 3회와 백오프를 낭비하지 않도록 **여기서 먼저 막는다.**
    """
    if dbname not in DB_CATEGORIES:
        raise SearchFieldError(
            f"지원하지 않는 dbname: {dbname!r}. 사용 가능 {len(DB_CATEGORIES)}종: "
            f"{', '.join(DB_CATEGORIES)}"
        )
    allowed = DB_CATEGORIES[dbname]
    if not allowed:
        raise SearchFieldError(
            f"'{dbname}' 은 실측상 **어떤 검색항목도 통하지 않습니다**(전체항목 포함). "
            f"dbname 자체가 무효로 보이므로 다른 DB를 쓰세요.")
    if not search or not search.strip():
        # 🔴 빈 search 검사가 없어 `dbname` 만 주면 ERR04 재시도를 태우고 원인을 오진했다.
        raise SearchFieldError(
            f"search 가 비었습니다 — `검색항목,키워드` 형식이어야 합니다. "
            f"'{dbname}' 의 검색항목: {', '.join(allowed)}")
    clauses: list[str] = []      # 정규화한 절만 모아 전송한다(validate_search 와 같은 이유)
    for clause in search.split("|"):
        clause = clause.strip()
        if not clause:
            continue
        if "," not in clause:
            raise SearchFieldError(
                f"검색어 형식 오류: {clause!r} — `검색항목,키워드` 여야 합니다. "
                f"'{dbname}' 의 검색항목: {', '.join(allowed)}"
            )
        field = clause.split(",", 1)[0].strip()
        if field not in allowed:
            extra = ""
            if field in DISPLAY_ONLY_FIELDS:
                extra = (f" ⚠️ '{field}' 는 웹 UI 의 **표시/필터 항목**이지 API 검색항목이 "
                         f"아닙니다(실측: 형에 맞는 값으로도 ERR04). "
                         f"연도 범위는 `option='발행년도,2000|발행년도,2010'` 으로 거세요.")
            raise SearchFieldError(
                f"'{dbname}' 에서 쓸 수 없는 검색항목: {field!r}.{extra} "
                f"⚠️ 상세검색은 **DB마다 검색항목 어휘가 다릅니다**"
                f"(학위논문=논문명·지도교수, 일반도서=자료명, 국내기사=기사명, "
                f"학술지·신문='수록지명/신문명' 한 덩어리). "
                f"'{dbname}' 에서 가능: {', '.join(allowed)}"
            )
        keyword = clause.split(",", 1)[1].strip()
        if not keyword:
            raise SearchFieldError(
                f"검색어가 비었습니다: {clause!r} — `{field},키워드` 형태로 값을 주세요.")
        clauses.append(f"{field},{keyword}")
    if not clauses:
        raise SearchFieldError(
            f"유효한 검색절이 하나도 없습니다: {search!r} — `검색항목,키워드` 형식이어야 합니다.")
    return "|".join(clauses)


# ── 인증키 ───────────────────────────────────────────────────────────────────
# 🔑 data.go.kr 은 "일반 인증키"를 Encoding/Decoding 두 벌로 준다.
#    ✅ **실측 결론: 이 API 는 Encoding 키를 URL 에 직접 결합해야 한다.**
#       Decoding 키를 requests 의 params= 로 넘기면 `%2B` 가 `%252B` 로 이중 인코딩되어
#       ERR04(HTTP_ERROR) 가 난다. 대조군(키 없음)은 HTTP 401 SERVICE_KEY_IS_NULL 이라
#       '키가 전달은 됐으나 값이 틀린' 상태임이 구분된다.

def get_api_key_encoded() -> str | None:
    """URL 결합용 **Encoding(퍼센트인코딩)** 인증키 — 없으면 None."""
    enc = (os.environ.get("NA_API_KEY_ENCODED") or "").strip()
    if enc:
        return enc
    dec = (os.environ.get("NA_API_KEY") or "").strip()
    return urllib.parse.quote(dec, safe="") if dec else None


def get_api_key() -> str | None:
    """원문(Decoding) 인증키 — 보유 여부 확인용."""
    dec = (os.environ.get("NA_API_KEY") or "").strip()
    if dec:
        return dec
    enc = (os.environ.get("NA_API_KEY_ENCODED") or "").strip()
    return urllib.parse.unquote(enc) if enc else None


def require_api_key_encoded() -> str:
    key = get_api_key_encoded()
    if not key:
        raise RuntimeError(
            "NA_API_KEY 가 설정되지 않았습니다 — 국회도서관 자료검색 API 는 인증키가 필요합니다. "
            ".env(.env.example 참고) 또는 OS 환경변수로 설정하세요. "
            "발급: https://www.data.go.kr 에서 '국회 국회도서관_자료검색 서비스' 활용신청."
        )
    return key


def build_url(endpoint: str, params: dict) -> str:
    """인증키를 **URL 문자열에 직접 결합**한 완전한 요청 URL.

    ⚠️ 인증키를 `params=` 로 넘기지 않는 이유가 여기 있다(이중 인코딩). 이 함수 **한 곳**에서만
       키를 붙여 실수를 구조적으로 막는다. 나머지 파라미터는 정상 인코딩한다.
    """
    key = require_api_key_encoded()
    query = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None},
                                   encoding="utf-8")
    return f"{endpoint}?serviceKey={key}" + (f"&{query}" if query else "")


def redact(key: str | None) -> str:
    """로그용 마스킹 (인증키 노출 방지)."""
    if not key:
        return "(none)"
    return f"{key[:4]}…{key[-2:]}" if len(key) > 6 else "***"


def secret_fragments() -> list[str]:
    """출력에서 지워야 할 인증키의 모든 표현형 — 오류 메시지 정제에 쓴다."""
    out: list[str] = []
    for raw in (os.environ.get("NA_API_KEY"), os.environ.get("NA_API_KEY_ENCODED")):
        raw = (raw or "").strip()
        if not raw:
            continue
        out += [raw, urllib.parse.quote(raw, safe=""), urllib.parse.quote_plus(raw),
                urllib.parse.unquote(raw)]
    return [s for s in dict.fromkeys(out) if len(s) > 8]


def scrub(text: str) -> str:
    """문자열에서 인증키를 제거 — 예외 메시지·도구 응답 경계에서 반드시 통과시킨다."""
    s = str(text)
    for frag in secret_fragments():
        s = s.replace(frag, "***KEY***")
    return s


class _ScrubFilter(logging.Filter):
    """로그 레코드에서 인증키를 지운다.

    🔴 §6-C 결정에 따라 키는 **반드시 쿼리스트링에 들어간다.** 그래서 urllib3 가 DEBUG
       레벨에서 찍는 요청 라인이 전부 키를 싣는다 — `scrub()` 이 닿지 않던 유일한
       반출 경로였다(적대적 리뷰가 실측으로 잡았다).
       로거를 끄지 않고 **값만 지운다** — 디버깅 능력을 뺏지 않기 위해서다.
    """

    def filter(self, record: logging.LogRecord) -> bool:  # noqa: A003
        try:
            if isinstance(record.msg, str):
                record.msg = scrub(record.msg)
            if record.args:
                if isinstance(record.args, tuple):
                    record.args = tuple(
                        scrub(a) if isinstance(a, str) else a for a in record.args)
                elif isinstance(record.args, dict):
                    record.args = {k: (scrub(v) if isinstance(v, str) else v)
                                   for k, v in record.args.items()}
        except Exception:  # noqa: BLE001 — 로깅이 예외를 내면 안 된다
            pass
        return True


_SCRUB_INSTALLED = False


def install_log_scrubber() -> None:
    """인증키를 찍을 수 있는 로거에 scrub 필터를 건다 (중복 설치 안 함).

    ⚠️ 라이브러리가 루트 로거를 건드리지 않는다 — 필터는 해당 로거에만 붙인다.
    """
    global _SCRUB_INSTALLED
    if _SCRUB_INSTALLED:
        return
    f = _ScrubFilter()
    for name in ("urllib3", "urllib3.connectionpool", "requests", "na_mcp"):
        logging.getLogger(name).addFilter(f)
    _SCRUB_INSTALLED = True


_TRUST_INJECTED = False


def use_os_trust() -> bool:
    """OS 신뢰 저장소(Windows/macOS)로 TLS 검증을 위임.

    교육망·사내망의 SSL 인터셉션은 자체서명 루트 CA를 OS 신뢰저장소에 심어둔다. requests 는
    기본적으로 certifi 만 보므로 그 CA를 모른다 → truststore 로 OS 저장소를 쓰면 **검증을
    끄지 않고도** 통과한다. `NA_OS_TRUST=0` 이면 비활성. (한 번만 주입)

    ⚠️ MCP 등록 명령줄이 아니라 **코드**에서 호출한다 — `.mcpb` 번들·PyInstaller 바이너리
       경로에도 적용되어야 하기 때문이다(자매 프로젝트 scienceon 이 등록 명령줄에만 두어
       번들 경로가 교육망에서 실패했다).
    """
    global _TRUST_INJECTED
    if _TRUST_INJECTED:
        return True
    if (os.environ.get("NA_OS_TRUST") or "1").strip().lower() in ("0", "false", "no"):
        return False
    try:
        import truststore

        truststore.inject_into_ssl()
        _TRUST_INJECTED = True
        return True
    except Exception as e:  # noqa: BLE001
        import logging

        logging.getLogger("na_mcp").warning(
            "truststore OS 신뢰저장소 주입 실패(%s) — TLS 인터셉션 망에서 인증서 오류 가능. "
            "대안: REQUESTS_CA_BUNDLE 로 루트 CA 지정.", type(e).__name__)
        return False
