"""정규화된 국회도서관 레코드 스키마.

🔴 이 API 의 레코드는 **고정 스키마가 아니다.** `<recode>` 안에 `<item><name>·<value></item>`
   쌍이 들어오고, `name` 집합이 자료종(도서·학위논문·기사·회의록…)마다 다르다.
   따라서 정규화는 **알려진 이름만 매핑**하고 나머지는 전부 `raw` 에 보존한다.

✅ 자료종 12종 실측 완료(2026-09-07) — 표제 필드만 해도 이름이 이렇게 갈린다:
  일반도서·고서·E-BOOK·웹자료·세미나자료·동영상 = `자료명` / 학위논문 = `논문명` /
  국내·국외기사 = `기사명` / 학술지·잡지·신문 = **`수록지명/신문명`**(슬래시 포함 한 덩어리) /
  전자저널 = `저널명` / 국회회의록 = `안건` / 국회의안정보 = `의안명` /
  외국법률번역DB = `번역법령명` / 표,그림DB = `표그림명`
⚠️ 같은 뜻인데 이름이 다른 것도 있다 — 도서 `원문DB유무` ↔ 기사·학위논문 `원본DB유무`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .config import is_placeholder

# 표 출력(csv/xlsx/sqlite) 시 컬럼 순서
# ⚠️ **NAME_MAP 이 매핑하는 필드는 전부 여기 있어야 한다.** 빠지면 `to_row()` 를 쓰는
#    csv·xlsx 와 MCP 응답에서 통째로 사라진다 — 초판에서 11개가 그렇게 유실됐다
#    (`지도교수`·`소관위원회`·`처리상태`·`발행국`·`간행빈도`·`별치기호` 등).
#    회귀 테스트가 NAME_MAP ⊆ COLUMNS 를 고정한다.
COLUMNS = [
    "source", "control_no", "title", "authors", "advisor", "publisher",
    "pub_year", "pub_year_raw", "pub_info", "pub_country", "journal", "frequency",
    "keywords", "call_no", "class_no", "shelf_mark",
    "isbn", "issn", "language", "db_name", "location", "source_info",
    "degree", "university", "major",
    "committee", "status", "assembly_term", "meeting",
    # ⚠️ `toc_status`·`toc_chars` 는 `has_toc` **바로 뒤**에 둔다. `has_toc` 는 검색이 준
    #    Y/N 플래그이고 `toc_status` 는 실제로 받아본 결과다 — 둘이 어긋나는 행
    #    (`has_toc=Y` 인데 `toc_status=empty`)이 가장 중요한 사실이라 나란히 보여야 한다.
    #    🔴 목차 **본문**(`toc_text`)은 여기 넣지 않는다: 수천 자라 xlsx 셀 상한(32,767)에
    #    걸리고 csv 를 비대하게 만들며 MCP 미리보기 50건이 컨텍스트를 통째로 태운다.
    #    본문은 json·sqlite 로만 나간다(`to_row()` 가 COLUMNS 만 보므로 자동으로 빠진다).
    "has_toc", "toc_status", "toc_chars",
    "has_abstract", "has_fulltext", "has_audio", "copyright_ok",
    "detail_url", "placeholder_fields",
]

# `<item><name>` → 정규화 필드. **여러 이름이 한 필드로 모인다**(자료종마다 이름이 다르므로).
# ⚠️ 여기 없는 이름도 버리지 않는다 — `raw` 에 그대로 남는다.
NAME_MAP: dict[str, str] = {
    # 식별자
    "제어번호": "control_no", "controlno": "control_no",   # 문서는 controlno, 실응답은 제어번호
    # 표제 — 자료종마다 이름이 다르다
    "자료명": "title", "기사명": "title", "논문명": "title", "의안명": "title",
    "안건": "title", "표그림명": "title", "번역법령명": "title",
    "자료명/저자사항": "title",          # 문서 예시의 합침 형태(구버전)
    # ✅ 자료종별 실측(2026-09-07) — 표제 필드 이름이 DB마다 다르다
    "수록지명/신문명": "title",          # 학술지·잡지·신문: 이 레코드 자체가 그 誌名이다
    "저널명": "title",                  # 전자저널
    # 저자
    "저자명": "authors", "저자": "authors", "발언자": "authors",
    "대표 발의자": "authors",
    # ⚠️ 검색 **카테고리명**은 `지도교수(2009~)` 인데 상세정보 응답의 **필드명**은 `지도교수` 다.
    "지도교수(2009~)": "advisor", "지도교수": "advisor",
    # 발행
    "발행자": "publisher", "발행처": "publisher", "발행기관": "publisher",
    "발행년도": "pub_year", "발행년": "pub_year", "학위년도": "pub_year",
    "내용연도": "pub_year", "제안일자": "pub_year", "제공년도": "pub_year",
    "발행사항": "pub_info", "발행국": "pub_country",
    # 수록·분류
    "수록지명": "journal", "키워드": "keywords",
    "청구기호": "call_no", "DDC": "class_no", "DDC분류": "class_no",
    "ISBN": "isbn", "ISSN": "issn", "별치기호": "shelf_mark",
    "본문언어": "language", "발행언어": "language",
    "DB": "db_name", "DB Title": "db_name", "자료실": "location", "출처": "source_info",
    "간행빈도": "frequency",
    # 학위논문
    "학위": "degree", "학위구분": "degree", "대학": "university", "전공": "major",
    # 유무 플래그 (Y/N)
    "목차": "has_toc", "초록유무": "has_abstract",
    "원문DB유무": "has_fulltext", "원본DB유무": "has_fulltext",
    "음성지원유무": "has_audio", "저작권허락": "copyright_ok",
    # 의안·회의록
    "소관위원회": "committee", "위원회": "committee", "처리상태": "status",
    "대수": "assembly_term", "회의": "meeting",
}

_DIGITS = re.compile(r"\D")
_WS = re.compile(r"\s+")
# 🔴 **하이라이트 태그만** 지운다. 임의의 `<…>` 를 지우면 안 된다 —
#    이 카탈로그의 **표제에 꺾쇠가 실제로 쓰인다**: `<표 124> 심폐소생술 교육 경험률`,
#    `<그림 3> 추이`(표,그림DB 는 표제가 거의 전부 이 형태다). 초판이 `<[^>]+>` 로 싹 지워
#    라이브 표본 1,000건 중 18건의 표제가 손상됐고, 도구 응답에는 raw 가 없어 복구도 불가능했다.
#    → API 가 실제로 넣는 마크업(실측: `font`)과 흔한 강조 태그로 **화이트리스트**를 좁힌다.
_HIGHLIGHT_TAG = re.compile(
    r"</?\s*(?:font|span|b|strong|em|i|mark|u)(?=[\s/>])[^>]*>", re.IGNORECASE)


def clean_html(text: str | None) -> str:
    """검색 하이라이트 마크업만 제거. **데이터의 꺾쇠는 보존한다.**

    🔴 **검색 서비스(`/basic`·`/detail`)는 매칭된 필드에 `<font color="red">…</font>` 를
       끼워 보낸다**(✅ 실측). 지우지 않으면 제목·저자 비교, 중복제거, xlsx/csv 내보내기가
       전부 오염된다(저자명이 `<font color="red">양연동</font>` 로 저장된다).
       ⚠️ 상세정보조회(`detailinfoservice`)에는 하이라이트가 없다 — 검색이 아니기 때문이다.

    🔴 **그러나 임의의 `<…>` 를 지우면 데이터가 깨진다.** 표,그림DB 의 표제는 거의 전부
       `<표 124> …` 형태이고 일반도서에도 섞여 있다. 그래서 태그 이름을 화이트리스트로 막는다.

    ⚠️ **공백은 접지 않는다.** 이 API 의 `키워드` 는 여러 칸 공백을 **구분자로** 쓰므로
       (`주제어A   주제어B`) 공백을 접으면 경계가 사라진다. 자매 프로젝트(국립중앙도서관)는
       토큰마다 span 이 붙어 공백 정규화가 필요했으나, 여기는 필드 전체를 한 번 감싸는
       형태라 태그만 지우면 충분하다(실측).
    """
    if text is None:
        return ""
    return _HIGHLIGHT_TAG.sub("", str(text)).strip()


def normalize_pub_year(value: str | None) -> str:
    """발행연도 문자열에서 **4자리 연도**만 추출. 확정할 수 없으면 빈 문자열.

    ✅ census 로 확인된 비정상 값(전부 여기서 빈 문자열이 된다):
      · `201u` — MARC **불확정 연도** 표기. 고서·일반도서·학술지에서 1~2% 나온다.
      · `''`   — 고서는 23% 가 빈값이다.
      · `0`    — 외국법률번역DB 는 날짜 '없음'을 **`0`** 으로 쓴다
                 (`관보발행일`·`폐지일` 400건 전부 `0`). 정수 변환하면 1970년이 된다.
      · `2021. 4. 30` 같은 날짜 문자열 — `제안일자`(YYYYMMDD) 등이 이 경로로 온다.
    """
    digits = _DIGITS.sub("", str(value or ""))[:4]
    if len(digits) == 4 and 1000 <= int(digits) <= 2100:
        return digits
    return ""


def _yn(value: str) -> str:
    """Y/N 플래그 정규화 — 값이 예상 밖이면 원문을 그대로 남긴다(임의 해석 금지)."""
    v = (value or "").strip().upper()
    return v if v in ("Y", "N") else (value or "").strip()


@dataclass
class Record:
    """국회도서관 자료 1건 (정규화). 원본 name/value 전체는 `raw` 에 보존한다."""

    source: str = "na_search"
    control_no: str = ""     # 제어번호 — 상세조회(controlno)의 입력이자 중복제거 1차 키
    title: str = ""
    authors: str = ""
    publisher: str = ""
    pub_year: str = ""
    pub_year_raw: str = ""
    journal: str = ""        # 수록지명
    keywords: str = ""
    call_no: str = ""        # 청구기호
    class_no: str = ""       # DDC
    isbn: str = ""
    issn: str = ""
    language: str = ""       # 본문언어 (kor 등)
    db_name: str = ""        # DB — ⚠️ 실응답 100건 표본에는 없었다(문서 예시에만 존재)
    location: str = ""       # 자료실
    pub_country: str = ""    # 발행국 (학술지·신문)
    frequency: str = ""      # 간행빈도 (학술지·신문)
    pub_info: str = ""       # 발행사항 (상세정보조회에만 등장)
    source_info: str = ""    # 출처
    committee: str = ""      # 소관위원회·위원회 (국회의안정보·회의록)
    status: str = ""         # 처리상태
    assembly_term: str = ""  # 대수
    meeting: str = ""        # 회의
    shelf_mark: str = ""     # 별치기호
    degree: str = ""
    university: str = ""
    major: str = ""
    advisor: str = ""
    has_toc: str = ""        # 목차 Y/N — 검색이 **준** 플래그다(실제로 받아본 결과가 아니다)
    # ── 목차 보강 결과 (na_collect(toc_max=...) 를 켰을 때만 채워진다) ──────
    toc_status: str = ""     # config.TOC_STATUS_VALUES 중 하나. 빈칸 = 보강 미실행
    toc_chars: int = 0       # 확보한 본문 길이. has_toc=Y 인데 0 이면 플래그가 거짓이었다
    toc_text: str = ""       # 🔴 COLUMNS 에 없다 — json·sqlite 로만 나간다(위 주석 참조)
    has_abstract: str = ""   # 초록유무 Y/N
    has_fulltext: str = ""   # 원본DB유무 Y/N
    has_audio: str = ""      # 음성지원유무 Y/N
    copyright_ok: str = ""   # 저작권허락 Y/N
    detail_url: str = ""     # 제어번호로 조립한 국회도서관 상세 링크
    raw: dict[str, Any] = field(default_factory=dict)   # 원본 name→value 전체
    # 🔴 값 자리에 안내문이 와서 **정규화 필드를 비운** 원본 필드명들.
    #    (E-BOOK 의 DDC 99.95% 가 `전자형태로만 열람 가능함`, 학위논문 17% 가 `해당 논문 없음`)
    #    원문은 `raw` 에 그대로 있으므로 손실은 없고, 여기 이름이 있으면 '데이터 없음'이다.
    placeholder_fields: list[str] = field(default_factory=list)

    def to_row(self) -> dict[str, Any]:
        """평탄화된 표 한 행(dict).

        `placeholder_fields` 는 목록이므로 표 출력용으로 `;` 결합한다 —
        빈 값이 '미입력'인지 '안내문'인지 csv·xlsx 에서도 구분되게 하기 위해서다.
        """
        row = {col: getattr(self, col, "") for col in COLUMNS}
        row["placeholder_fields"] = ";".join(self.placeholder_fields)
        return row

    def fulltext_available(self) -> bool:
        """원문 DB 가 있는가 (원본DB유무 == Y)."""
        return self.has_fulltext.strip().upper() == "Y"

    def dedup_key(self) -> str:
        """항상 동일 타입(str) 키 — 키스페이스 분리로 우연한 충돌 방지.

        `제어번호` 는 실측 표본 100/100 에 존재하므로 1차 키로 충분하다(빈값 0).
        그래도 결측 대비 폴백을 둔다.
        """
        if self.control_no:
            return "cn:" + self.control_no
        if len(self.isbn) >= 10:
            return "isbn:" + re.sub(r"[^0-9Xx]", "", self.isbn).upper()
        return "tt:" + _WS.sub(" ", self.title.strip().lower()) + "|" + self.pub_year

    def haystack(self) -> str:
        """부분일치 필터용 전체 텍스트(소문자)."""
        parts = [self.title, self.authors, self.publisher, self.journal,
                 self.keywords, self.call_no, self.isbn, self.location]
        parts += [str(v) for v in self.raw.values() if isinstance(v, str)]
        return "\n".join(parts).lower()

    def matches(self, subs) -> bool:
        """subs(문자열 또는 목록) 중 하나라도 부분일치하면 True (대소문자 무시).

        빈 필터(None/빈 리스트)는 '필터 없음 = 전부 통과'로 처리한다.
        """
        if isinstance(subs, str):
            subs = [subs]
        subs = [s for s in (subs or []) if s and s.strip()]
        if not subs:
            return True
        hay = self.haystack()
        return any(s.lower() in hay for s in subs)


def record_from_items(items: list[tuple[str, str]], *, source: str = "na_search") -> Record:
    """`(name, value)` 목록 → Record.

    같은 `name` 이 여러 번 오면 `; ` 로 이어붙인다(키워드 등에서 실제로 발생 가능).
    """
    rec = Record(source=source)
    raw: dict[str, Any] = {}
    for name, value in items:
        name = clean_html(name)
        original = (value or "").strip()
        value = clean_html(value)          # 하이라이트 마크업 제거
        if not name:
            continue
        # raw 에는 **원문 그대로** 남긴다 — 어느 필드가 매칭됐는지가 정보이기도 하다.
        raw[name] = f"{raw[name]}; {original}" if name in raw and raw[name] else original
        attr = NAME_MAP.get(name)
        if not attr:
            continue                      # 미지 필드는 raw 에만 — 버리지 않는다
        if is_placeholder(value):
            # 🔴 안내문은 데이터가 아니다. 정규화 필드에 넣으면 DDC 로 집계할 때
            #    `전자형태로만 열람 가능함` 이 분류기호 하나로 잡힌다(E-BOOK 은 이게 전건이다).
            #    원문은 raw 에 남아 있으므로 되찾을 수 있다.
            rec.placeholder_fields.append(name)
            continue
        if attr in ("has_toc", "has_abstract", "has_fulltext", "has_audio", "copyright_ok"):
            setattr(rec, attr, _yn(value))
        elif attr == "pub_year":
            if not rec.pub_year:
                rec.pub_year_raw = value
                rec.pub_year = normalize_pub_year(value)
        elif hasattr(rec, attr):
            cur = getattr(rec, attr)
            setattr(rec, attr, f"{cur}; {value}" if cur else value)
        else:
            pass                          # NAME_MAP 에 있으나 필드가 없는 경우 raw 로만 보존
    rec.raw = raw
    if rec.control_no:
        # 국회도서관 상세 페이지 — ❓ 링크 형식은 미검증(제어번호 기반 조회 URL).
        rec.detail_url = f"https://dl.nanet.go.kr/search/searchInnerDetail.do?controlNo={rec.control_no}"
    return rec
