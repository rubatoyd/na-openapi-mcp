"""수집 결과를 xlsx/csv/json/sqlite 로 저장. (자매 프로젝트 nl/kci/scienceon 과 동일 패턴)"""
from __future__ import annotations

import csv
import json
import re
import sqlite3
from pathlib import Path
from typing import Sequence

from .models import COLUMNS, Record

# Windows 예약 장치명 — 확장자를 붙여도 파일로 만들 수 없다.
_RESERVED = {"CON", "PRN", "AUX", "NUL",
             *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(name: str, *, fallback: str = "na_output", limit: int = 60) -> str:
    """파일명으로 안전한 문자열로 정규화 — **디렉터리를 벗어날 수 없게** 만든다.

    ⚠️ 이 함수가 없으면 `name="../escaped"` 가 out_dir **밖에** 파일을 쓴다.
       자매 프로젝트 세 곳 모두에 있던 결함이고 적대적 검증에서 실제로 재현됐다.
       검색어가 그대로 파일명이 되는 경로도 있어 사용자 입력이 그대로 경로에 닿는다.
    """
    s = _UNSAFE.sub("_", str(name or ""))
    s = s.replace("..", "_").strip().strip(". ")   # 상위 이동·후행 점/공백(Windows) 제거
    s = re.sub(r"\s+", "_", s)[:limit].strip("._ ")
    if not s or s.upper().split(".")[0] in _RESERVED:
        s = fallback
    return s


def _rows(records: Sequence[Record]) -> list[dict]:
    return [r.to_row() for r in records]


def to_json(records: Sequence[Record], path: str) -> None:
    """정규화 행 + 원본 필드(raw) + 안내문 필드 목록을 함께 저장.

    `placeholder_fields` 는 값 자리에 안내문이 와서 정규화 필드를 비운 항목이다
    (E-BOOK 의 DDC 등). 빈 값이 '미입력'인지 '안내문'인지 구분하려면 필요하다.
    """
    data = [{**r.to_row(), "raw": r.raw,
             "placeholder_fields": r.placeholder_fields} for r in records]
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def to_csv(records: Sequence[Record], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:  # 엑셀 한글 호환 BOM
        w = csv.DictWriter(f, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        w.writerows(_rows(records))


def to_xlsx(records: Sequence[Record], path: str) -> None:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "records"
    ws.append(COLUMNS)
    for row in _rows(records):
        ws.append([row.get(c, "") for c in COLUMNS])
    wb.save(path)


def to_sqlite(records: Sequence[Record], path: str, *, table: str = "records") -> None:
    con = sqlite3.connect(path)
    try:
        cols = ", ".join(f'"{c}" TEXT' for c in COLUMNS)
        con.execute(f"DROP TABLE IF EXISTS {table}")  # 스냅샷: 재실행 시 누적 방지
        con.execute(f'CREATE TABLE {table} ({cols}, "raw" TEXT)')
        ph = ", ".join(["?"] * (len(COLUMNS) + 1))
        for r in records:
            row = r.to_row()
            con.execute(
                f'INSERT INTO {table} ({", ".join(COLUMNS)}, raw) VALUES ({ph})',
                [row.get(c, "") for c in COLUMNS] + [json.dumps(r.raw, ensure_ascii=False)],
            )
        con.commit()
    finally:
        con.close()


_EXPORTERS = {"json": to_json, "csv": to_csv, "xlsx": to_xlsx, "sqlite": to_sqlite}
_EXT = {"json": ".json", "csv": ".csv", "xlsx": ".xlsx", "sqlite": ".sqlite"}


def export(records: Sequence[Record], formats: Sequence[str], out_dir: str,
           name: str) -> list[str]:
    """formats 각각으로 out_dir/name.* 저장. 저장된 경로 목록 반환.

    `name` 은 사용자 입력(도구 인자·검색어)에서 오므로 **여기서** 정규화한다 —
    호출부마다 처리하면 한 곳을 빠뜨린다.
    """
    # 🔴 **쓰기 전에 전부 검증한다.** 초판은 쓰기 루프 안에서 검증해, `['json','bogus']`
    #    가 json 을 쓴 뒤 예외를 냈다 — 수집 레코드·axes·cap_hit 같은 meta 가 통째로
    #    사라지고 쿼터는 이미 쓴 뒤였다(적대적 리뷰 실측).
    if isinstance(formats, str):
        # ⚠️ 문자열을 넘기면 문자 단위로 순회해 '지원하지 않는 형식: j' 가 났다.
        formats = [formats]
    keys: list[str] = []
    for fmt in formats:
        key = str(fmt).lower().lstrip(".")
        if key == "db":
            key = "sqlite"
        if key not in _EXPORTERS:
            raise ValueError(
                f"지원하지 않는 출력형식: {fmt!r} (가능: {list(_EXPORTERS)}). "
                f"아무 파일도 쓰지 않았습니다.")
        keys.append(key)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    base = out.resolve()
    stem = safe_name(name)
    paths: list[str] = []
    for key in keys:
        p = (out / f"{stem}{_EXT[key]}").resolve()
        # 정규화를 뚫는 경로가 남아 있으면 여기서 멈춘다(이중 방어).
        if base != p.parent:
            raise ValueError(f"출력 경로가 지정 디렉터리를 벗어납니다: {p}")
        _EXPORTERS[key](records, str(p))
        paths.append(str(p))
    return paths
