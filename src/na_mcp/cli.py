"""국회도서관 자료검색 CLI — MCP 서버와 **동일한 공용 코어**를 쓴다.

⚠️ CLI 와 MCP 의 기능이 어긋나면 안 된다(자매 프로젝트에서 CLI 인자 3종이 빠져 지적됐다).
   새 인자를 server.py 에 추가하면 여기도 같이 추가할 것.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .client import NaClient, NaError
from .config import (
    API_RECORD_CAP,
    DBNAMES,
    MAX_DISPLAYLINES,
    PAGENO_MAX,
    SEARCH_FIELDS_BASIC,
    get_api_key,
    scrub,
)
from .exporters import export


def _p(*a) -> None:
    print(*a)


def cmd_status(_args) -> int:
    if get_api_key() is None:
        _p("✗ NA_API_KEY 미설정 — .env 또는 환경변수로 설정하세요.")
        _p("  발급: https://www.data.go.kr '국회 국회도서관_자료검색 서비스' 활용신청")
        return 1
    try:
        recs, meta = NaClient().search_meta("전체,도서관", max_records=1, page_size=1)
    except NaError as e:
        _p(f"✗ 왕복 실패: {scrub(str(e))}")
        return 1
    _p("✓ 인증키 유효 — 자료검색 정상 응답")
    _p(f"  전체 건수(전체,도서관): {meta.get('total', 0):,}")
    _p(f"  회수 한계: {API_RECORD_CAP:,}건 (pageno 최대 {PAGENO_MAX} × {MAX_DISPLAYLINES}건/페이지)")
    return 0


def cmd_search(args) -> int:
    client = NaClient(throttle=args.throttle)
    try:
        records, meta = client.search_meta(
            args.search, max_records=args.max_records, dbname=args.dbname,
            option=args.option, page_size=args.page_size)
    except (NaError, ValueError) as e:
        _p(f"✗ {scrub(str(e))}")
        return 1
    _p(f"total={meta['total']:,}  회수={meta['fetched']:,}  "
       f"truncated={meta['truncated']}  cap_hit={meta['cap_hit']}")
    for note in ("cap_note", "incomplete_note", "ignored_search_warning"):
        if meta.get(note):
            _p(f"⚠️  {meta[note]}")
    for r in records:
        _p(f"  [{r.control_no}] {r.title[:70]} / {r.authors[:24]} ({r.pub_year})")
    return 0


def cmd_collect(args) -> int:
    terms = args.terms or ([args.search] if args.search else [])
    if not terms:
        _p("✗ --terms 또는 --search 가 필요합니다 (형식: `검색항목,키워드`)")
        return 1
    client = NaClient(throttle=args.throttle)
    try:
        records, meta = client.search_terms_meta(
            terms, max_records=args.max_records, dbname=args.dbname,
            option=args.option, page_size=args.page_size)
    except (NaError, ValueError) as e:
        _p(f"✗ {scrub(str(e))}")
        return 1

    if args.contains:
        records = [r for r in records if r.matches(args.contains)]
    if args.year_from or args.year_to:
        lo, hi = args.year_from or 0, args.year_to or 9999
        records = [r for r in records
                   if r.pub_year.isdigit() and lo <= int(r.pub_year) <= hi]

    _p(f"수집 {len(records):,}건 "
       f"(검색어 {len(meta['terms_searched'])}/{len(terms)}개 조회)")
    for a in meta["axes"]:
        _p(f"  - {a['term']}: total={a['total']:,} 회수={a['fetched']:,} 신규={a['new']:,}"
           + ("  ⚠️ cap_hit" if a["cap_hit"] else ""))
    for note in ("stopped_early_note", "cap_note", "incomplete_note"):
        if meta.get(note):
            _p(f"⚠️  {meta[note]}")

    if not args.no_save and records:
        out = args.out_dir or str(Path.home() / "na-output")
        stem = args.name or terms[0].split(",", 1)[-1]
        paths = export(records, args.formats, out, stem)
        for p in paths:
            _p(f"  저장: {p}")
    if args.json:
        _p(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


def cmd_detail(args) -> int:
    try:
        fields, _env = NaClient().detail(args.controlno)
    except (NaError, ValueError) as e:
        _p(f"✗ {scrub(str(e))}")
        return 1
    _p(f"제어번호 {args.controlno} — 필드 {len(fields)}개")
    for k, v in fields.items():
        _p(f"  {k:<12} = {v[:100]}")
    return 0


def cmd_toc(args) -> int:
    try:
        toc, env = NaClient().toc(args.controlno)
    except (NaError, ValueError) as e:
        _p(f"✗ {scrub(str(e))}")
        return 1
    if not toc:
        _p(f"제어번호 {args.controlno} — 목차 없음(정상. 검색 결과의 `목차` 가 'N' 인 자료)")
        return 0
    _p(f"제어번호 {args.controlno} — 목차 {len(toc):,}자")
    _p(toc)
    return 0


def cmd_fields(_args) -> int:
    _p("통합검색(/basic) 검색항목 — 이 7종만 유효:")
    for f in SEARCH_FIELDS_BASIC:
        _p(f"  · {f}")
    _p("\n🔴 그 밖의 값은 오류가 아니라 검색어를 무시하고 전체 카탈로그 13,097,591건을 반환한다.")
    _p("   특히 `저자명` 은 상세검색에서는 유효하나 통합검색에서는 `저자` 를 써야 한다.")
    _p(f"\n상세검색(/detail) dbname {len(DBNAMES)}종:")
    for d in DBNAMES:
        _p(f"  · {d}")
    _p("\n상세검색은 검색항목 어휘가 DB마다 다르다 → https://openapi.nanet.go.kr/S3002_03.html")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="na", description="국회도서관 자료검색 CLI")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="인증키·연결 점검").set_defaults(fn=cmd_status)
    sub.add_parser("fields", help="검색항목·dbname 목록").set_defaults(fn=cmd_fields)

    d = sub.add_parser("detail", help="제어번호 1건 상세정보")
    d.add_argument("controlno", help="검색 결과의 제어번호 (예: MONO12026000012887)")
    d.set_defaults(fn=cmd_detail)

    tc = sub.add_parser("toc", help="제어번호 1건 목차")
    tc.add_argument("controlno")
    tc.set_defaults(fn=cmd_toc)

    def common(p):
        p.add_argument("--dbname", choices=list(DBNAMES), help="지정 시 상세검색(/detail)")
        p.add_argument("--option", help="상세검색 옵션 (예: 발행년도,2000|발행년도,2010)")
        p.add_argument("--page-size", type=int, default=None,
                       help=f"페이지당 건수 (최대 {MAX_DISPLAYLINES})")
        p.add_argument("--throttle", type=float, default=0.4)

    s = sub.add_parser("search", help="검색")
    s.add_argument("search", help="`검색항목,키워드` (예: 전체,교육불평등)")
    s.add_argument("--max-records", type=int, default=20)
    common(s)
    s.set_defaults(fn=cmd_search)

    c = sub.add_parser("collect", help="합집합 수집 후 저장")
    c.add_argument("--terms", nargs="+", help="검색어 여러 개 (합집합)")
    c.add_argument("--search", help="단일 검색어")
    c.add_argument("--max-records", type=int, default=1000)
    c.add_argument("--contains", nargs="+", help="로컬 부분일치 필터")
    c.add_argument("--year-from", type=int)
    c.add_argument("--year-to", type=int)
    c.add_argument("--formats", nargs="+", default=["xlsx", "csv", "json"])
    c.add_argument("--out-dir")
    c.add_argument("--name")
    c.add_argument("--no-save", action="store_true")
    c.add_argument("--json", action="store_true", help="메타를 JSON 으로 출력")
    common(c)
    c.set_defaults(fn=cmd_collect)

    args = ap.parse_args(argv if argv is not None else sys.argv[1:])
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
