# -*- coding: utf-8 -*-
"""
표정리.py — 한글(HWP/HWPX) 문서 안의 모든 표 서식을 일괄 정리하는 프로그램

사용법
  1) 더블클릭 실행           → 파일 선택 창이 열립니다.
  2) python 표정리.py 문서.hwp → 지정한 문서를 정리합니다.

동작
  - 원본 문서는 절대 수정하지 않습니다.
  - 같은 폴더에 "<원본이름>_정리본.hwp(x)" 로 결과를 저장합니다.
  - 서식 규칙은 이 파일과 같은 폴더의 서식규칙.yaml 에서 읽습니다.
  - 셀 병합이 있는 표도 셀 단위로 하나씩 처리하므로 빠짐없이 정리됩니다.
  - 1행 1열(한 칸) 표는 그림틀로 간주하여 그림틀 서식을 적용하고
    번호 종류(개체속성-기타)를 '그림'으로 바꿉니다.
  - 사진(그림 개체)의 번호 종류는 '없음'으로 바꿉니다.
  - 규칙에 따라 표/그림틀/사진의 너비를 문서폭 또는 고정값으로 맞춥니다.
    (사진은 가로세로 비율을 유지한 채 조절됩니다)

전제 조건
  - Windows + 한글(한컴오피스) 설치
  - pip install pyhwpx pyyaml

본 제품은 한글과컴퓨터의 한/글 문서 파일(.hwp) 공개 문서를 참고하여 개발하였습니다.
"""
import os
import re
import sys
import traceback
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import yaml

# ------------------------------------------------------------------
# 규칙(설정) 읽기
# ------------------------------------------------------------------

LINE_TYPES = {
    "없음": 0, "실선": 1, "파선": 2, "점선": 3, "일점쇄선": 4,
    "이점쇄선": 5, "긴파선": 6, "원형점선": 7, "이중실선": 8,
    "얇고굵은이중선": 9, "굵고얇은이중선": 10, "삼중선": 11,
}
LINE_WIDTHS = {
    "0.1mm": 0, "0.12mm": 1, "0.15mm": 2, "0.2mm": 3, "0.25mm": 4,
    "0.3mm": 5, "0.4mm": 6, "0.5mm": 7, "0.6mm": 8, "0.7mm": 9,
    "1.0mm": 10, "1.5mm": 11, "2.0mm": 12, "3.0mm": 13, "4.0mm": 14,
    "5.0mm": 15,
}
NUMBERING_TYPES = {"없음": 0, "그림": 1, "표": 2, "수식": 3}


class 규칙오류(Exception):
    pass


def hex_to_hwp_color(hex_str: str) -> int:
    """'#RRGGBB' → 한글 내부 색상값(BGR 정수)"""
    s = hex_str.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", s):
        raise 규칙오류(f"색 값이 잘못되었습니다: '{hex_str}' (\"#RRGGBB\" 형식, 예: \"#000000\")")
    r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return r + g * 256 + b * 65536


def mm_to_hu(mm: float) -> int:
    """밀리미터 → HwpUnit (1인치 = 7200 HU = 25.4mm)"""
    return round(mm * 7200 / 25.4)


def parse_line_rule(d: dict, 이름: str) -> dict:
    """설정의 선 항목({종류, 굵기, 색}) → 내부 정수값"""
    try:
        종류 = LINE_TYPES[str(d.get("종류", "실선"))]
        굵기 = LINE_WIDTHS[str(d.get("굵기", "0.12mm"))]
        색 = hex_to_hwp_color(str(d.get("색", "#000000")))
    except KeyError as e:
        raise 규칙오류(f"[{이름}] 항목에 잘못된 값이 있습니다: {e}\n"
                     f"  허용 선 종류: {', '.join(LINE_TYPES)}\n"
                     f"  허용 선 굵기: {', '.join(LINE_WIDTHS)}")
    return {"type": 종류, "width": 굵기, "color": 색}


def parse_numbering_rule(value, 이름: str) -> int:
    """번호종류 값(없음/그림/표/수식) → 내부 정수값"""
    s = str(value)
    if s not in NUMBERING_TYPES:
        raise 규칙오류(f"[{이름}] 번호종류 값이 잘못되었습니다: '{s}'\n"
                     f"  허용 값: {', '.join(NUMBERING_TYPES)}")
    return NUMBERING_TYPES[s]


def parse_width_rule(value) -> dict:
    """너비 규칙 파싱: '유지' / '문서폭' / '150mm' 등"""
    s = str(value).strip()
    if s == "유지":
        return {"mode": "keep"}
    if s == "문서폭":
        return {"mode": "doc_width"}
    m = re.match(r'^(\d+(?:\.\d+)?)\s*mm$', s, re.I)
    if m:
        return {"mode": "fixed", "mm": float(m.group(1))}
    raise 규칙오류(f"너비 값이 잘못되었습니다: '{value}'\n"
                 f"  허용 값: 유지, 문서폭, 숫자mm (예: 150mm)")


def load_rules(config_path: Path) -> dict:
    if not config_path.exists():
        raise 규칙오류(f"서식 규칙 파일을 찾을 수 없습니다: {config_path}")
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    표 = raw.get("표서식") or {}

    바깥 = 표.get("바깥선") or {}
    rules = {
        "outer": {
            "Top":    parse_line_rule(바깥.get("위") or {}, "바깥선.위"),
            "Bottom": parse_line_rule(바깥.get("아래") or {}, "바깥선.아래"),
            "Left":   parse_line_rule(바깥.get("왼쪽") or {}, "바깥선.왼쪽"),
            "Right":  parse_line_rule(바깥.get("오른쪽") or {}, "바깥선.오른쪽"),
        },
        "inner": parse_line_rule(표.get("안쪽선") or {}, "안쪽선"),
        "header_rows": 0,
        "header_fill": None,
        "header_bottom": None,
        "clear_body_bg": (표.get("본문셀") or {}).get("배경", "지우기") == "지우기",
        "table_width": parse_width_rule(표.get("너비", "유지")),
    }
    머리글 = 표.get("머리글행") or {}
    if 머리글.get("사용", False):
        rules["header_rows"] = int(머리글.get("행수", 1))
        rules["header_fill"] = hex_to_hwp_color(str(머리글.get("배경색", "#CCCCCC")))
        아래선 = 머리글.get("아래선")
        if 아래선:
            rules["header_bottom"] = parse_line_rule(아래선, "머리글행.아래선")

    # 그림틀(1x1 표) 규칙
    틀 = raw.get("그림틀") or {}
    rules["frame"] = {
        "skip": str(틀.get("처리", "정리")) == "건너뛰기",
        "border": parse_line_rule(틀.get("테두리") or {"종류": "실선", "굵기": "0.1mm", "색": "#B3B3B3"},
                                  "그림틀.테두리"),
        "clear_bg": str(틀.get("배경", "지우기")) == "지우기",
        "numbering": parse_numbering_rule(틀.get("번호종류", "그림"), "그림틀"),
        "width": parse_width_rule(틀.get("너비", "유지")),
    }
    # 사진(그림 개체) 규칙
    사진 = raw.get("사진") or {}
    rules["photo_numbering"] = parse_numbering_rule(사진.get("번호종류", "없음"), "사진")
    rules["photo_width"] = parse_width_rule(사진.get("너비", "유지"))

    # 개체 위치: 글자처럼 취급 (켬=1 / 끔=0 / 유지=None)
    개체 = raw.get("개체위치") or {}
    취급 = str(개체.get("글자처럼취급", "유지"))
    if 취급 not in ("켬", "끔", "유지"):
        raise 규칙오류(f"[개체위치] 글자처럼취급 값이 잘못되었습니다: '{취급}' (허용: 켬, 끔, 유지)")
    rules["treat_as_char"] = {"켬": 1, "끔": 0}.get(취급)

    # 스타일 정리
    스타일 = raw.get("스타일정리") or {}
    rules["remove_x_styles"] = str(스타일.get("x스타일제거", "켬")) == "켬"
    return rules


# ------------------------------------------------------------------
# 문서 분석 (hwpx 임시 변환 → 표/셀 구조 파악)
# ------------------------------------------------------------------

NS = {
    "hp": "http://www.hancom.co.kr/hwpml/2011/paragraph",
}
HP = f"{{{NS['hp']}}}"


class TableSpec:
    """표 하나의 구조: 크기와 (앵커행, 앵커열) → (행병합, 열병합) 맵"""

    def __init__(self, tbl_el):
        self.n_rows = int(tbl_el.get("rowCnt"))
        self.n_cols = int(tbl_el.get("colCnt"))
        self.cells = {}
        for tr in tbl_el.findall(f"{HP}tr"):          # 직계 행만 (중첩 표 제외)
            for tc in tr.findall(f"{HP}tc"):
                addr = tc.find("hp:cellAddr", NS)
                span = tc.find("hp:cellSpan", NS)
                r, c = int(addr.get("rowAddr")), int(addr.get("colAddr"))
                self.cells[(r, c)] = (int(span.get("rowSpan")), int(span.get("colSpan")))

    @property
    def is_1x1(self):
        return self.n_rows == 1 and self.n_cols == 1


def analyze_tables(hwp, src: Path) -> list:
    """열려 있는 문서를 임시 hwpx로 저장해 표 구조 목록을 만든다(문서 순서)."""
    tmp = src.parent / "_표정리_분석용.hwpx"
    if tmp.exists():
        tmp.unlink()
    if not hwp.save_as(str(tmp), format="HWPX"):
        raise RuntimeError("분석용 임시 저장에 실패했습니다.")
    try:
        specs = []
        with zipfile.ZipFile(tmp) as z:
            for name in sorted(n for n in z.namelist()
                               if re.fullmatch(r"Contents/section\d+\.xml", n)):
                section = ET.fromstring(z.read(name))
                for tbl in section.iter(f"{HP}tbl"):
                    specs.append(TableSpec(tbl))
        return specs
    finally:
        # save_as 이후에는 임시 파일이 '현재 문서'가 되어 잠겨 있으므로
        # 원본을 다시 열어 잠금을 풀고 임시 파일을 지운다.
        reopened = hwp.open(str(src))
        try:
            tmp.unlink()
        except OSError:
            pass
        if not reopened:
            raise RuntimeError("분석 후 원본 문서를 다시 열지 못했습니다.")


# ------------------------------------------------------------------
# 왼쪽 테두리 색 후처리
#   한글 NEO의 자동화 인터페이스에는 '왼쪽 테두리 색' 항목이 누락되어
#   있어(한컴 버그) 표의 맨 왼쪽 가장자리 색만 지정되지 않는다.
#   → 저장 전에 hwpx 내부 XML에서 해당 색만 직접 고친다.
# ------------------------------------------------------------------

def intended_left_color(spec, addr, rules):
    """셀의 왼쪽 선이 가져야 할 색('#RRGGBB') 또는 None(선 없음/무관)"""
    if spec.is_1x1:
        if rules["frame"]["skip"]:
            return None
        rule = rules["frame"]["border"]
    else:
        rule = cell_plan(spec, addr, rules)[0]["Left"]
    if rule["type"] == 0:               # 선 없음이면 색 무관
        return None
    v = rule["color"]
    return f"#{v & 255:02X}{(v >> 8) & 255:02X}{(v >> 16) & 255:02X}"


def compute_left_patches(hwpx_path: Path, rules, skip_idx=frozenset()) -> dict:
    """hwpx를 읽어 '테두리정의 id → 올바른 왼쪽 선 색' 목록을 만든다.
    skip_idx: 서식 적용에 실패해 건너뛴 표의 순번(0-기준) — 색 보정도 하지 않는다."""
    patches, conflicts = {}, set()
    tbl_idx = -1
    with zipfile.ZipFile(hwpx_path) as z:
        header = ET.fromstring(z.read("Contents/header.xml"))
        HH = "{http://www.hancom.co.kr/hwpml/2011/head}"
        actual = {}
        for bf in header.iter(f"{HH}borderFill"):
            el = bf.find(f"{HH}leftBorder")
            if el is not None:
                actual[bf.get("id")] = el.get("color", "").upper()
        for name in sorted(n for n in z.namelist()
                           if re.fullmatch(r"Contents/section\d+\.xml", n)):
            section = ET.fromstring(z.read(name))
            for tbl in section.iter(f"{HP}tbl"):
                tbl_idx += 1
                if tbl_idx in skip_idx:
                    continue
                spec = TableSpec(tbl)
                for tr in tbl.findall(f"{HP}tr"):
                    for tc in tr.findall(f"{HP}tc"):
                        a = tc.find("hp:cellAddr", NS)
                        addr = (int(a.get("rowAddr")), int(a.get("colAddr")))
                        want = intended_left_color(spec, addr, rules)
                        if want is None:
                            continue
                        ref = tc.get("borderFillIDRef")
                        if actual.get(ref, want) == want.upper():
                            continue    # 이미 올바름
                        if ref in patches and patches[ref] != want:
                            conflicts.add(ref)
                            continue
                        patches[ref] = want
    for ref in conflicts:
        patches.pop(ref, None)
    return patches


def patch_left_colors(hwpx_path: Path, patches: dict) -> None:
    """hwpx 안 header.xml의 왼쪽 선 색을 직접 수정한다."""
    with zipfile.ZipFile(hwpx_path) as z:
        entries = [(info, z.read(info.filename)) for info in z.infolist()]
    for i, (info, data) in enumerate(entries):
        if info.filename != "Contents/header.xml":
            continue
        text = data.decode("utf-8")
        for bf_id, color in patches.items():
            pattern = (r'(<hh:borderFill[^>]*\bid="' + re.escape(bf_id)
                       + r'"[^>]*>.*?)(<hh:leftBorder\b[^>]*?)(\s*/?>)')

            def repl(m, color=color):
                tag = m.group(2)
                if 'color="' in tag:
                    tag = re.sub(r'color="[^"]*"', f'color="{color}"', tag, count=1)
                else:
                    tag += f' color="{color}"'
                return m.group(1) + tag + m.group(3)

            text = re.sub(pattern, repl, text, count=1, flags=re.S)
        entries[i] = (info, text.encode("utf-8"))
    with zipfile.ZipFile(hwpx_path, "w") as z:
        for info, data in entries:
            z.writestr(info.filename, data, compress_type=info.compress_type)


# ------------------------------------------------------------------
# x 스타일 제거 (엑셀 붙여넣기 잔재)
#   이름이 x/X로 시작하는 스타일을 목록에서 삭제하고, 남은 스타일의
#   번호를 당긴 뒤 문단들의 스타일 참조를 바로잡는다.
#   (삭제된 스타일을 쓰던 문단은 '바탕글'(0)로 연결 — 문단 자체의
#    모양 정보는 별도로 저장되어 있어 겉모습은 변하지 않는다)
# ------------------------------------------------------------------

def remove_x_styles_in_hwpx(hwpx_path: Path) -> list:
    """hwpx에서 x로 시작하는 스타일 제거. 제거한 스타일 이름 목록을 반환."""
    with zipfile.ZipFile(hwpx_path) as z:
        entries = [(info, z.read(info.filename)) for info in z.infolist()]

    removed, mapping = [], {}
    header_i = next((i for i, (info, _) in enumerate(entries)
                     if info.filename == "Contents/header.xml"), None)
    if header_i is None:
        return []
    text = entries[header_i][1].decode("utf-8")
    m = re.search(r'(<hh:styles\b[^>]*>)(.*?)(</hh:styles>)', text, re.S)
    if not m:
        return []

    tags = re.findall(r'<hh:style\b[^>]*/>|<hh:style\b[^>]*>.*?</hh:style>',
                      m.group(2), re.S)
    kept = []
    for tag in tags:
        sid = re.search(r'\bid="(\d+)"', tag).group(1)
        name_m = re.search(r'\bname="([^"]*)"', tag)
        name = name_m.group(1) if name_m else ""
        if name[:1] in ("x", "X"):
            removed.append(name)
            mapping[sid] = None         # 삭제 표식
        else:
            kept.append((sid, tag))
    if not removed:
        return []

    for new_id, (old_id, _) in enumerate(kept):
        mapping[old_id] = str(new_id)

    new_tags = []
    for old_id, tag in kept:
        new_id = mapping[old_id]
        tag = re.sub(r'\bid="\d+"', f'id="{new_id}"', tag, count=1)
        nm = re.search(r'nextStyleIDRef="(\d+)"', tag)
        if nm:
            nxt = mapping.get(nm.group(1)) or new_id
            tag = re.sub(r'nextStyleIDRef="\d+"', f'nextStyleIDRef="{nxt}"', tag, count=1)
        new_tags.append(tag)

    open_tag = re.sub(r'itemCnt="\d+"', f'itemCnt="{len(new_tags)}"', m.group(1))
    text = text[:m.start()] + open_tag + "".join(new_tags) + m.group(3) + text[m.end():]
    info = entries[header_i][0]
    entries[header_i] = (info, text.encode("utf-8"))

    def remap_ref(match):
        return f'styleIDRef="{mapping.get(match.group(1)) or "0"}"'

    for i, (info, data) in enumerate(entries):
        if re.fullmatch(r"Contents/section\d+\.xml", info.filename):
            stext = data.decode("utf-8")
            stext = re.sub(r'styleIDRef="(\d+)"', remap_ref, stext)
            entries[i] = (info, stext.encode("utf-8"))

    with zipfile.ZipFile(hwpx_path, "w") as z:
        for info, data in entries:
            z.writestr(info.filename, data, compress_type=info.compress_type)
    return removed


# ------------------------------------------------------------------
# 한글 제어
# ------------------------------------------------------------------

def collect_tables(hwp):
    tables = []
    ctrl = hwp.HeadCtrl
    while ctrl:
        if ctrl.CtrlID == "tbl":
            tables.append(ctrl)
        ctrl = ctrl.Next
    return tables


def enter_table(hwp, ctrl):
    """해당 표의 첫 셀에 캐럿을 놓는다(블록 없음)."""
    hwp.set_pos_by_set(ctrl.GetAnchorPos(0))
    hwp.hwp.FindCtrl()
    hwp.HAction.Run("ShapeObjTableSelCell")
    hwp.HAction.Run("Cancel")


ADDR_RE = re.compile(r"\(([A-Z]+)(\d+)\)")


def current_cell_addr(hwp):
    """KeyIndicator에서 현재 셀 주소 (행idx, 열idx) 0-기준으로 반환. 셀 밖이면 None."""
    try:
        ki = hwp.hwp.KeyIndicator()
    except Exception:
        return None
    m = ADDR_RE.search(str(ki[-1]))
    if not m:
        return None
    letters, num = m.group(1), int(m.group(2))
    col = 0
    for ch in letters:
        col = col * 26 + (ord(ch) - ord("A") + 1)
    return (num - 1, col - 1)


def walk_cells(hwp, max_steps: int):
    """현재 표의 모든 셀을 (행,열) 주소와 함께 순회한다. 병합 셀 중복 방문은 제외."""
    visited = set()
    prev = None
    for _ in range(max_steps):
        addr = current_cell_addr(hwp)
        pos = hwp.get_pos()
        if addr is None:
            return
        if (addr, pos) == prev:
            return                      # 더 이상 이동 없음 = 마지막 셀
        if pos not in visited:
            visited.add(pos)
            yield addr
        prev = (addr, pos)
        hwp.HAction.Run("TableRightCell")


def apply_cell(hwp, sides: dict, fill):
    """캐럿이 있는 셀 하나에 테두리 4방향과 배경을 적용.
    fill: 색상 정수(칠하기) / "clear"(지우기) / None(그대로 둠)

    주의: '왼쪽 테두리 색'은 한글 NEO 자동화 인터페이스의 누락(버그)으로
    여기서 지정할 수 없다 → 저장 단계의 hwpx 후처리에서 보정한다."""
    hwp.HAction.Run("TableCellBlock")

    pset = hwp.HParameterSet.HCellBorderFill
    hwp.HAction.GetDefault("CellBorderFill", pset.HSet)
    for side, rule in sides.items():
        pset.HSet.SetItem(f"BorderType{side}", rule["type"])
        pset.HSet.SetItem(f"BorderWidth{side}", rule["width"])
        if side != "Left":
            pset.HSet.SetItem(f"BorderColor{side}", rule["color"])
    hwp.HAction.Execute("CellBorderFill", pset.HSet)

    if fill is not None:
        pset = hwp.HParameterSet.HCellBorderFill
        hwp.HAction.GetDefault("CellFill", pset.HSet)
        if fill == "clear":
            pset.FillAttr.type = 0      # NullBrush(채우기 없음)
            pset.FillAttr.WindowsBrush = 0
        else:
            pset.FillAttr.type = 1      # WinBrush(단색 채우기)
            pset.FillAttr.WindowsBrush = 1
            pset.FillAttr.WinBrushFaceColor = fill
            pset.FillAttr.WinBrushHatchColor = 0
            pset.FillAttr.WinBrushFaceStyle = -1
        hwp.HAction.Execute("CellFill", pset.HSet)

    hwp.HAction.Run("Cancel")


def cell_plan(spec: TableSpec, addr, rules):
    """셀 하나가 가져야 할 테두리/배경 계산"""
    r, c = addr
    rs, cs = spec.cells.get(addr, (1, 1))
    # 본문 행이 하나도 안 남는 표(예: 1행짜리)는 머리글 처리를 하지 않는다
    h = rules["header_rows"] if spec.n_rows > rules["header_rows"] else 0
    outer, inner, hb = rules["outer"], rules["inner"], rules["header_bottom"]

    sides = {}
    sides["Top"] = outer["Top"] if r == 0 else (hb if (hb and r == h) else inner)
    if r + rs == spec.n_rows:
        sides["Bottom"] = outer["Bottom"]
    elif hb and r + rs == h:
        sides["Bottom"] = hb
    else:
        sides["Bottom"] = inner
    sides["Left"] = outer["Left"] if c == 0 else inner
    sides["Right"] = outer["Right"] if c + cs == spec.n_cols else inner

    if r < h and rules["header_fill"] is not None:
        fill = rules["header_fill"]
    elif rules["clear_body_bg"]:
        fill = "clear"
    else:
        fill = None
    return sides, fill


def set_numbering_type(ctrl, value: int) -> bool:
    """개체속성 → 기타 → 번호 종류 변경 (없음=0, 그림=1, 표=2, 수식=3)"""
    props = ctrl.Properties
    if props.Item("NumberingType") == value:
        return False                    # 이미 원하는 값
    props.SetItem("NumberingType", value)
    ctrl.Properties = props
    return True


def get_edit_width(hwp) -> int:
    """편집 영역 폭(HwpUnit) = 용지폭 - 좌여백 - 우여백 - 제본여백"""
    act = hwp.hwp.CreateAction("PageSetup")
    pset = act.CreateSet()
    act.GetDefault(pset)
    pd = pset.Item("PageDef")
    try:
        gutter = pd.Item("GutterLen")
    except Exception:
        gutter = 0
    return (pd.Item("PaperWidth") - pd.Item("LeftMargin")
            - pd.Item("RightMargin") - gutter)


def resize_table(ctrl, target_hu: int) -> bool:
    """표 컨트롤의 너비를 target_hu(HwpUnit)로 변경. 변경 시 True 반환."""
    props = ctrl.Properties
    if props.Item("Width") == target_hu:
        return False
    props.SetItem("Width", target_hu)
    ctrl.Properties = props
    return True


def resize_picture(ctrl, target_hu: int) -> bool:
    """그림 개체의 너비를 target_hu로 변경(높이는 원래 비율 유지). 변경 시 True."""
    props = ctrl.Properties
    cur_w = props.Item("Width")
    cur_h = props.Item("Height")
    if not cur_w or cur_w == target_hu:
        return False
    props.SetItem("Width", target_hu)
    props.SetItem("Height", round(cur_h * target_hu / cur_w))
    ctrl.Properties = props
    return True


def format_frame(hwp, ctrl, rules) -> None:
    """1x1 그림틀: 테두리/배경 정리 + 번호 종류를 '그림'으로"""
    frame = rules["frame"]
    enter_table(hwp, ctrl)
    # 분석(XML) 순서와 컨트롤 순서가 어긋났을 때 큰 표를 그림틀로
    # 오인해 첫 셀만 서식이 바뀌는 사고 방지 — 실제 1x1인지 확인
    addrs = list(walk_cells(hwp, 8))
    if addrs != [(0, 0)]:
        raise RuntimeError(f"1x1 표가 아닙니다(셀 {addrs}) — 분석 결과와 불일치")
    enter_table(hwp, ctrl)
    sides = {s: frame["border"] for s in ("Left", "Right", "Top", "Bottom")}
    apply_cell(hwp, sides, "clear" if frame["clear_bg"] else None)
    if frame["numbering"] is not None:
        set_numbering_type(ctrl, frame["numbering"])


def format_table(hwp, ctrl, spec: TableSpec, rules) -> int:
    """표 하나를 셀 단위로 정리. 처리한 셀 수를 반환."""
    enter_table(hwp, ctrl)
    done = 0
    max_steps = 4 * len(spec.cells) + 16
    for addr in walk_cells(hwp, max_steps):
        if addr not in spec.cells:      # 구조 분석과 불일치 → 안전하게 중단
            raise RuntimeError(f"셀 주소 불일치: {addr}")
        sides, fill = cell_plan(spec, addr, rules)
        apply_cell(hwp, sides, fill)
        done += 1
    if done != len(spec.cells):
        raise RuntimeError(f"셀 {len(spec.cells)}개 중 {done}개만 방문됨")
    return done


# ------------------------------------------------------------------
# 실행
# ------------------------------------------------------------------

def pick_file() -> str:
    import tkinter as tk
    from tkinter import filedialog
    root = tk.Tk()
    root.withdraw()
    path = filedialog.askopenfilename(
        title="표 서식을 정리할 한글 문서를 선택하세요",
        filetypes=[("한글 문서", "*.hwp *.hwpx"), ("모든 파일", "*.*")],
    )
    root.destroy()
    return path


def process(src: Path) -> Path:
    from pyhwpx import Hwp

    rules = load_rules(Path(__file__).parent / "서식규칙.yaml")

    out = src.with_name(src.stem + "_정리본" + src.suffix)
    fmt = "HWPX" if src.suffix.lower() == ".hwpx" else "HWP"

    print(f"문서 여는 중: {src.name}")
    hwp = Hwp(visible=False)
    try:
        try:
            hwp.hwp.SetMessageBoxMode(0x00010000)   # 대화상자 자동 처리
        except Exception:
            pass
        if not hwp.open(str(src)):
            raise RuntimeError("문서를 열 수 없습니다. (암호/배포용 문서이거나 다른 프로그램에서 사용 중일 수 있습니다)")

        specs = analyze_tables(hwp, src)
        ctrls = collect_tables(hwp)
        if len(specs) != len(ctrls):
            raise RuntimeError(f"표 개수 불일치(분석 {len(specs)} vs 문서 {len(ctrls)}) — 처리를 중단합니다.")
        print(f"표 {len(ctrls)}개 발견")

        # 너비 조절 목표값 계산
        edit_w = None
        tw, fw, pw = rules["table_width"], rules["frame"]["width"], rules["photo_width"]
        if "doc_width" in (tw["mode"], fw["mode"], pw["mode"]):
            edit_w = get_edit_width(hwp)
            edit_mm = round(edit_w * 25.4 / 7200, 1)
            print(f"편집 영역 폭: {edit_mm}mm")

        def width_target(rule):
            return (edit_w if rule["mode"] == "doc_width"
                    else mm_to_hu(rule["mm"]) if rule["mode"] == "fixed"
                    else None)

        table_target = width_target(tw)
        frame_target = width_target(fw)
        photo_target = width_target(pw)

        done = frames = resized = failed = 0
        failed_idx = set()              # 실패한 표의 순번(0-기준) — 후처리에서 제외
        for i, (ctrl, spec) in enumerate(zip(ctrls, specs), start=1):
            label = f"[{i}/{len(ctrls)}] {spec.n_rows}행x{spec.n_cols}열"
            try:
                if spec.is_1x1:
                    if rules["frame"]["skip"]:
                        print(f"  {label} — 그림틀 → 건너뜀(규칙)")
                    else:
                        format_frame(hwp, ctrl, rules)
                        w_ok = frame_target is not None and resize_table(ctrl, frame_target)
                        if w_ok:
                            resized += 1
                        frames += 1
                        print(f"  {label} — 그림틀 서식 적용" + ("＋너비조절" if w_ok else ""))
                    continue
                n = format_table(hwp, ctrl, spec, rules)
                w_ok = table_target is not None and resize_table(ctrl, table_target)
                if w_ok:
                    resized += 1
                done += 1
                print(f"  {label} — 셀 {n}개 정리 완료" + ("＋너비조절" if w_ok else ""))
            except Exception as e:
                failed += 1
                failed_idx.add(i - 1)
                print(f"  {label} — 실패({e}) → 건너뜀")

        # 개체 공통 속성 정리: 사진 번호종류·너비 + 글자처럼 취급(표/그림)
        photos = photos_resized = tac_changed = 0
        tac = rules["treat_as_char"]
        ctrl = hwp.HeadCtrl
        while ctrl:
            is_pic = ctrl.UserDesc == "그림"
            try:
                if is_pic and rules["photo_numbering"] is not None:
                    if set_numbering_type(ctrl, rules["photo_numbering"]):
                        photos += 1
                if is_pic and photo_target is not None:
                    if resize_picture(ctrl, photo_target):
                        photos_resized += 1
                if tac is not None and (ctrl.CtrlID == "tbl" or is_pic):
                    props = ctrl.Properties
                    if props.Item("TreatAsChar") != tac:
                        props.SetItem("TreatAsChar", tac)
                        ctrl.Properties = props
                        tac_changed += 1
            except Exception:
                print(f"  개체 속성 변경 실패({ctrl.UserDesc}) — 건너뜀")
            ctrl = ctrl.Next
        if photos:
            print(f"  사진 {photos}개 — 번호종류: 없음 적용")
        if photos_resized:
            print(f"  사진 {photos_resized}개 — 너비 조절(비율 유지)")
        if tac_changed:
            print(f"  개체 {tac_changed}개 — 글자처럼 취급 {'체크' if tac == 1 else '해제'}")

        # 후처리 (hwpx 직접 수정): 왼쪽 테두리 색 보정 + x 스타일 제거
        tmp2 = src.with_name("_표정리_후처리.hwpx")
        if tmp2.exists():
            tmp2.unlink()
        if not hwp.save_as(str(tmp2), format="HWPX"):
            raise RuntimeError("후처리용 임시 저장에 실패했습니다.")
        try:
            patches = compute_left_patches(tmp2, rules, failed_idx)
            hwp.HAction.Run("FileNew")      # 임시 파일 잠금 해제
            if patches:
                patch_left_colors(tmp2, patches)
                print(f"  왼쪽 테두리 색 보정 {len(patches)}건")
            if rules["remove_x_styles"]:
                removed = remove_x_styles_in_hwpx(tmp2)
                if removed:
                    print(f"  엑셀 잔재 스타일 제거: {', '.join(removed)}")
            if not hwp.open(str(tmp2)):
                raise RuntimeError("후처리 파일을 다시 열지 못했습니다.")
            if os.path.exists(out):
                os.remove(out)
            if not hwp.save_as(str(out), format=fmt):
                raise RuntimeError("저장에 실패했습니다.")
        finally:
            try:
                hwp.HAction.Run("FileNew")   # tmp2 잠금 해제 후 삭제
                tmp2.unlink()
            except OSError:
                pass
        print(f"\n완료: 표 {done}개 / 그림틀 {frames}개 / 너비조절 {resized}개 / 사진 {photos}개 / 실패 {failed}개")
        print(f"저장 위치: {out}")
        return out
    finally:
        hwp.quit()


def main():
    interactive = len(sys.argv) < 2
    try:
        src = sys.argv[1] if not interactive else pick_file()
        if not src:
            print("파일이 선택되지 않았습니다.")
            return
        src = Path(src).resolve()
        if not src.exists():
            print(f"파일이 없습니다: {src}")
            return
        process(src)
    except 규칙오류 as e:
        print(f"\n[서식규칙.yaml 오류]\n{e}")
    except Exception:
        print("\n[오류가 발생했습니다]")
        traceback.print_exc()
    finally:
        if interactive:
            try:
                input("\n엔터 키를 누르면 창이 닫힙니다...")
            except EOFError:
                pass


if __name__ == "__main__":
    main()
