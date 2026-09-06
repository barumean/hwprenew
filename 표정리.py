# -*- coding: utf-8 -*-
"""
표정리.py — 한글(HWP/HWPX) 문서 안의 모든 표 서식을 일괄 정리하는 프로그램

사용법
  1) 더블클릭 실행 → 작업창(GUI)이 열립니다.
     파일을 선택하고 [서식 설정]을 확인한 뒤 [정리 시작]을 누릅니다.
  2) python 표정리.py 문서1.hwp 문서2.hwpx 폴더 ...
     → 나열한 파일과 폴더(바로 아래의 .hwp/.hwpx)를 일괄 처리합니다.
       탐색기에서 파일들을 표정리.py 아이콘 위로 끌어다 놓아도 됩니다.
  3) python 표정리.py --보기 문서.hwp
     → 한글 창을 띄운 채 실행합니다. 처리가 멈출 때 어떤 대화상자
       (보안 승인, 암호 입력, 문서 복구 등)가 떠 있는지 확인하는 진단용.

  ─ 작업창(GUI)과 처리 엔진(명령줄)이 이 한 파일에 모두 들어 있습니다.
    작업창은 실제 처리를 이 파일의 하위 프로세스로 실행합니다.

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
import threading
import time
import traceback
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass

import yaml

# tkinter는 GUI(작업창)에서만 쓴다. 처리 엔진/명령줄 실행에는 필요 없으므로
# 없는 환경(서버 등)에서도 엔진이 동작하도록 가져오기 실패를 허용한다.
try:
    import tkinter as tk
    from tkinter import colorchooser, filedialog, messagebox, ttk
    _TK_OK = True
except Exception:
    _TK_OK = False

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


def hex_to_hwp_color(hex_str: str, 이름: str = None) -> int:
    """'#RRGGBB' → 한글 내부 색상값(BGR 정수)"""
    s = str(hex_str).strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", s):
        where = f"[{이름}] " if 이름 else ""
        raise 규칙오류(f"{where}색 값이 잘못되었습니다: '{hex_str}' "
                     f'("#RRGGBB" 형식, 예: "#000000")')
    r, g, b = int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)
    return r + g * 256 + b * 65536


def mm_to_hu(mm: float) -> int:
    """밀리미터 → HwpUnit (1인치 = 7200 HU = 25.4mm)"""
    return round(mm * 7200 / 25.4)


# 선 굵기: 숫자(mm)로도 찾을 수 있게 역인덱스를 만들어 둔다 (0.4 → "0.4mm")
_WIDTH_BY_MM = {float(k[:-2]): v for k, v in LINE_WIDTHS.items()}


def norm_line_type(value, 이름: str) -> int:
    """선 종류 → 내부 코드. 이름('실선')과 내부 코드(0~11) 모두 허용."""
    s = str(value).strip()
    if s in LINE_TYPES:
        return LINE_TYPES[s]
    # 내부 코드가 그대로 저장된 파일(예: 종류: 0)도 읽어 준다
    if not isinstance(value, bool) and re.fullmatch(r"\d+", s) and int(s) in LINE_TYPES.values():
        return int(s)
    raise 규칙오류(f"[{이름}] 선 종류 값이 잘못되었습니다: '{value}'\n"
                 f"  허용 값: {', '.join(LINE_TYPES)}")


def norm_line_width(value, 이름: str) -> int:
    """선 굵기 → 내부 코드. '0.4mm', '0.4 mm', 0.4 모두 허용."""
    s = str(value).strip().lower().replace(" ", "")
    if s in LINE_WIDTHS:
        return LINE_WIDTHS[s]
    m = re.fullmatch(r"(\d+(?:\.\d+)?)(?:mm)?", s)
    if m and float(m.group(1)) in _WIDTH_BY_MM:
        return _WIDTH_BY_MM[float(m.group(1))]
    # 내부 코드가 그대로 저장된 파일(예: 굵기: 6)도 읽어 준다.
    # 단 'mm'을 붙여 쓴 값(9mm 등)은 굵기 목록에 없으면 그대로 오류.
    if re.fullmatch(r"\d+", s) and int(s) in LINE_WIDTHS.values():
        return int(s)
    raise 규칙오류(f"[{이름}] 선 굵기 값이 잘못되었습니다: '{value}'\n"
                 f"  허용 값: {', '.join(LINE_WIDTHS)}")


def parse_line_rule(d, 이름: str) -> dict:
    """설정의 선 항목({종류, 굵기, 색}) → 내부 정수값.

    종류가 '없음'이면 굵기·색은 의미가 없으므로 아예 확인하지 않는다.
    (설정 창에서도 '없음'이면 굵기·색 입력이 비활성화된다)"""
    if not isinstance(d, dict):
        d = {} if d is None else {"종류": d}    # "왼쪽: 없음" 처럼 한 줄로 쓴 경우
    종류 = norm_line_type(d.get("종류", "실선"), 이름)
    if 종류 == LINE_TYPES["없음"]:
        return {"type": 0, "width": 0, "color": 0}
    return {
        "type": 종류,
        "width": norm_line_width(d.get("굵기", "0.12mm"), 이름),
        "color": hex_to_hwp_color(d.get("색", "#000000"), 이름),
    }


# 설정 파일에서 선(종류/굵기/색) 항목이 들어 있는 자리
LINE_PATHS = (
    ("표서식", "안쪽선"),
    ("표서식", "바깥선", "위"),
    ("표서식", "바깥선", "아래"),
    ("표서식", "바깥선", "왼쪽"),
    ("표서식", "바깥선", "오른쪽"),
    ("표서식", "머리글행", "아래선"),
    ("그림틀", "테두리"),
)
_TYPE_NAME_BY_CODE = {v: k for k, v in LINE_TYPES.items()}
_WIDTH_NAME_BY_CODE = {v: k for k, v in LINE_WIDTHS.items()}


def canonicalize_line_values(rules: dict) -> None:
    """설정에 내부 코드(예: 종류: 0)나 '0.4' 같은 표기가 섞여 있어도
    사람이 읽는 이름('없음', '0.4mm')으로 제자리에서 정리한다.
    읽을 수 없는 값은 표준값으로 되돌린다. (설정 창에서 열면 자동 복구)"""
    for path in LINE_PATHS:
        node = rules
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if not isinstance(node, dict):
            continue
        try:
            node["종류"] = _TYPE_NAME_BY_CODE[norm_line_type(node.get("종류", "실선"), "선")]
        except 규칙오류:
            node["종류"] = "실선"
        if node["종류"] == "없음":      # 선 없음이면 굵기·색은 지운다
            node.pop("굵기", None)
            node.pop("색", None)
            continue
        try:
            node["굵기"] = _WIDTH_NAME_BY_CODE[norm_line_width(node.get("굵기", "0.12mm"), "선")]
        except 규칙오류:
            node["굵기"] = "0.12mm"
        try:
            hex_to_hwp_color(node.get("색", "#000000"))
        except 규칙오류:
            node["색"] = "#000000"


# 문단 정렬 이름 → 한글 액션 이름
ALIGN_ACTIONS = {
    "왼쪽": "ParagraphShapeAlignLeft",
    "가운데": "ParagraphShapeAlignCenter",
    "오른쪽": "ParagraphShapeAlignRight",
    "양쪽": "ParagraphShapeAlignJustify",
    "배분": "ParagraphShapeAlignDistribute",
    "나눔": "ParagraphShapeAlignDivision",
}
CAPTION_SIDES = {"위": "Top", "아래": "Bottom", "왼쪽": "Left", "오른쪽": "Right", "유지": None}


def parse_text_rule(d, 이름: str) -> dict:
    """글자·문단 서식 항목({스타일, 글꼴, 크기, 진하게, 정렬}) 파싱.
    값이 '유지'면 그 항목은 건드리지 않는다."""
    if not isinstance(d, dict):
        d = {}

    def 지정(키, 기본="유지"):
        v = d.get(키, 기본)
        return None if str(v).strip() in ("유지", "") else v

    정렬 = 지정("정렬")
    if 정렬 is not None and str(정렬).strip() not in ALIGN_ACTIONS:
        raise 규칙오류(f"[{이름}] 정렬 값이 잘못되었습니다: '{정렬}'\n"
                     f"  허용 값: 유지, {', '.join(ALIGN_ACTIONS)}")
    크기 = 지정("크기")
    if 크기 is not None:
        mo = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(?:pt|포인트)?", str(크기).strip(), re.I)
        if not mo:
            raise 규칙오류(f"[{이름}] 글자 크기 값이 잘못되었습니다: '{크기}' (예: 11pt)")
        크기 = float(mo.group(1))
    진하게 = str(d.get("진하게", "유지")).strip()
    if 진하게 not in ("켬", "끔", "유지"):
        raise 규칙오류(f"[{이름}] 진하게 값이 잘못되었습니다: '{진하게}' (허용: 켬, 끔, 유지)")
    스타일 = 지정("스타일")
    return {
        "style": str(스타일).strip() if 스타일 is not None else None,
        "font": str(지정("글꼴")).strip() if 지정("글꼴") is not None else None,
        "size": 크기,
        "bold": {"켬": True, "끔": False}.get(진하게),
        "align": ALIGN_ACTIONS[str(정렬).strip()] if 정렬 is not None else None,
    }


def text_rule_is_noop(rule) -> bool:
    """아무것도 바꾸지 않는(모두 '유지') 서식 규칙인가?"""
    return not (rule["style"] or rule["font"] or rule["size"]
                or rule["bold"] is not None or rule["align"])


def parse_caption_rule(d, 이름: str) -> dict:
    """캡션 항목({사용, 위치, 번호, 글자}) 파싱"""
    if not isinstance(d, dict):
        d = {}
    사용 = str(d.get("사용", "끔")).strip()
    번호 = str(d.get("번호", "켬")).strip()
    for 값, 키 in ((사용, "사용"), (번호, "번호")):
        if 값 not in ("켬", "끔"):
            raise 규칙오류(f"[{이름}] {키} 값이 잘못되었습니다: '{값}' (허용: 켬, 끔)")
    위치 = str(d.get("위치", "위")).strip()
    if 위치 not in CAPTION_SIDES:
        raise 규칙오류(f"[{이름}] 위치 값이 잘못되었습니다: '{위치}'\n"
                     f"  허용 값: {', '.join(CAPTION_SIDES)}")
    return {"use": 사용 == "켬", "side": CAPTION_SIDES[위치], "number": 번호 == "켬",
            "text": parse_text_rule(d.get("글자"), f"{이름}.글자")}


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
    """서식규칙.yaml을 읽어 내부 규칙으로 변환한다."""
    if not config_path.exists():
        raise 규칙오류(f"서식 규칙 파일을 찾을 수 없습니다: {config_path}")
    with open(config_path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise 규칙오류(f"서식 규칙 파일의 형식이 잘못되었습니다: {config_path}")
    try:
        return parse_rules(raw)
    except 규칙오류 as e:
        # 어느 파일을 고쳐야 하는지 함께 알려 준다
        raise 규칙오류(f"{e}\n\n  파일: {config_path}\n"
                     f"  (작업창의 [서식 설정]에서 값을 고른 뒤 저장하면 바로잡힙니다)")


def parse_rules(raw: dict) -> dict:
    """규칙 dict(서식규칙.yaml 내용) → 내부 규칙. 잘못된 값은 규칙오류."""
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
        try:
            rules["header_rows"] = int(머리글.get("행수", 1))
        except (TypeError, ValueError):
            raise 규칙오류(f"[머리글행] 행수 값이 잘못되었습니다: "
                         f"'{머리글.get('행수')}' (숫자를 입력하세요)")
        rules["header_fill"] = hex_to_hwp_color(머리글.get("배경색", "#CCCCCC"), "머리글행.배경색")
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

    # 표 안 글자 서식 (머리글행 / 본문셀)
    글자 = raw.get("글자서식") or {}
    사용 = str(글자.get("사용", "끔")).strip()
    if 사용 not in ("켬", "끔"):
        raise 규칙오류(f"[글자서식] 사용 값이 잘못되었습니다: '{사용}' (허용: 켬, 끔)")
    rules["cell_text"] = {
        "use": 사용 == "켬",
        "header": parse_text_rule(글자.get("머리글행"), "글자서식.머리글행"),
        "body": parse_text_rule(글자.get("본문셀"), "글자서식.본문셀"),
    }

    # 표의 좌우 위치 = 표가 놓인 문단의 정렬/스타일
    rules["table_pos"] = parse_text_rule(raw.get("표위치"), "표위치")

    # 캡션(번호 제목)
    캡션 = raw.get("캡션") or {}
    rules["caption"] = {
        "table": parse_caption_rule(캡션.get("표"), "캡션.표"),
        "frame": parse_caption_rule(캡션.get("그림틀"), "캡션.그림틀"),
    }

    # 스타일 정리
    스타일 = raw.get("스타일정리") or {}
    rules["remove_x_styles"] = str(스타일.get("x스타일제거", "켬")) == "켬"
    return rules


def wanted_style_names(rules) -> list:
    """규칙에서 쓰기로 한 스타일 이름 목록(중복 제거, 순서 유지)."""
    이름들 = [rules["table_pos"]["style"],
             rules["cell_text"]["header"]["style"], rules["cell_text"]["body"]["style"],
             rules["caption"]["table"]["text"]["style"],
             rules["caption"]["frame"]["text"]["style"]]
    본 = []
    for n in 이름들:
        if n and n not in 본:
            본.append(n)
    return 본


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
        # 이미 캡션이 달려 있는지(있으면 새로 만들지 않고 서식만 맞춘다)
        self.has_caption = tbl_el.find(f"{HP}caption") is not None
        self.nested = False
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


def make_temp_path(folder: Path, stem: str) -> Path:
    """임시 hwpx 파일 경로를 만든다.

    이전 실행이 비정상 종료하면 잔재 임시 파일이 좀비 한글 프로세스에
    잠긴 채 남을 수 있다. 지울 수 있으면 지우고 재사용하되, 잠겨 있으면
    실행을 중단하는 대신 번호를 붙인 다른 이름을 쓴다."""
    for n in range(100):
        p = folder / (stem + (str(n) if n else "") + ".hwpx")
        if not p.exists():
            return p
        try:
            p.unlink()
            return p
        except OSError:
            print(f"  ※ 이전 실행의 임시 파일이 잠겨 있어 다른 이름 사용: {p.name}\n"
                  f"     (작업 관리자에서 한글 프로세스 종료 후 직접 지워 주세요)")
    raise RuntimeError("임시 파일 이름을 만들 수 없습니다.")


def nested_tbl_ids(section) -> set:
    """구역 XML에서 '표 안에 든 표'(중첩 표) 요소들의 id 집합을 만든다."""
    nested = set()
    for tbl in section.iter(f"{HP}tbl"):
        for sub in tbl.iter(f"{HP}tbl"):
            if sub is not tbl:
                nested.add(id(sub))
    return nested


def read_style_ids(z) -> dict:
    """header.xml에서 '스타일 이름 → 스타일 번호' 표를 만든다.

    (pyhwpx의 set_style(이름)은 내부적으로 임시 파일을 만들기 때문에
    이름 대신 번호로 지정하려고 미리 읽어 둔다)"""
    HH = "{http://www.hancom.co.kr/hwpml/2011/head}"
    header = ET.fromstring(z.read("Contents/header.xml"))
    ids = {}
    for st in header.iter(f"{HH}style"):
        name = st.get("name")
        if name and st.get("id") is not None:
            ids[name] = int(st.get("id"))
    return ids


def analyze_tables(hwp, src: Path):
    """열려 있는 문서를 임시 hwpx로 저장해 표 구조 목록과
    스타일 이름→번호 표를 만든다(표는 문서 순서)."""
    tmp = make_temp_path(src.parent, "_표정리_분석용")
    if not hwp.save_as(str(tmp), format="HWPX"):
        raise RuntimeError("분석용 임시 저장에 실패했습니다.")
    try:
        specs = []
        with zipfile.ZipFile(tmp) as z:
            style_ids = read_style_ids(z)
            for name in sorted(n for n in z.namelist()
                               if re.fullmatch(r"Contents/section\d+\.xml", n)):
                section = ET.fromstring(z.read(name))
                nested = nested_tbl_ids(section)
                for tbl in section.iter(f"{HP}tbl"):
                    spec = TableSpec(tbl)
                    spec.nested = id(tbl) in nested   # 중첩 표는 너비 조절 제외
                    specs.append(spec)
        return specs, style_ids
    finally:
        # save_as 이후에는 임시 파일이 '현재 문서'가 되어 잠겨 있으므로
        # 원본을 다시 열어 잠금을 풀고 임시 파일을 지운다.
        reopened = open_document(hwp, src)
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


def report_widths(hwpx_path: Path, table_target, frame_target,
                  table_fit: bool = False, frame_fit: bool = False,
                  frame_skip: bool = False, skip_idx=frozenset()) -> None:
    """저장된 hwpx에서 표 너비를 실측해 목표값과 대조한 결과를 출력한다.

    (컨트롤 속성값이 아니라 실제 저장된 레이아웃 값을 읽으므로,
    너비 적용이 겉으로만 성공한 경우까지 잡아낸다)
    - table_fit/frame_fit: 목표가 '문서폭'이면 표마다 바깥 여백을 빼고 비교
    - frame_skip: 그림틀을 규칙상 건너뛰었으면 검사에서도 제외
    - skip_idx: 서식 적용에 실패해 건너뛴 표 순번(0-기준) 제외
    중첩 표(표 안의 표)는 너비 조절 대상이 아니므로 검사하지 않는다."""
    if table_target is None and frame_target is None:
        return
    total = bad = 0
    tbl_idx = -1
    with zipfile.ZipFile(hwpx_path) as z:
        for name in sorted(n for n in z.namelist()
                           if re.fullmatch(r"Contents/section\d+\.xml", n)):
            section = ET.fromstring(z.read(name))
            nested = nested_tbl_ids(section)
            for tbl in section.iter(f"{HP}tbl"):
                tbl_idx += 1
                if tbl_idx in skip_idx or id(tbl) in nested:
                    continue
                is_1x1 = TableSpec(tbl).is_1x1
                if is_1x1 and frame_skip:
                    continue
                target = frame_target if is_1x1 else table_target
                sz = tbl.find(f"{HP}sz")
                if target is None or sz is None:
                    continue
                if (frame_fit if is_1x1 else table_fit):
                    om = tbl.find(f"{HP}outMargin")   # 표 바깥 여백만큼 좁아진다
                    if om is not None:
                        target -= (int(om.get("left", 0) or 0)
                                   + int(om.get("right", 0) or 0))
                total += 1
                if abs(int(sz.get("width")) - target) > WIDTH_TOL:
                    bad += 1
                    mm = round(int(sz.get("width")) * 25.4 / 7200, 1)
                    print(f"  ⚠ 표 너비 불일치: 실측 {mm}mm "
                          f"(목표 {round(target * 25.4 / 7200, 1)}mm)")
    if total:
        if bad:
            print(f"  ⚠ 너비 실측 검증: 표 {total}개 중 {bad}개가 목표와 다릅니다")
        else:
            print(f"  너비 실측 검증: 표 {total}개 모두 목표 너비와 일치")


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


def ensure_edit_mode(hwp) -> None:
    """읽기 전용/양식 모드로 열린 문서를 편집 모드로 되돌린다.

    편집 불가 상태에서는 셀 선택 같은 편집 액션이 조용히 무시되어
    '셀 0개' 실패가 표마다 반복되므로, 문서를 연 직후에 바로잡는다."""
    try:
        if hwp.EditMode == 1:
            return
    except Exception:
        return                          # 이 속성을 못 읽는 버전이면 그냥 진행
    try:
        hwp.EditMode = 1
    except Exception:
        pass
    try:
        if hwp.EditMode == 1:
            print("  문서가 편집 불가 상태로 열려 편집 모드로 전환했습니다.", flush=True)
            return
    except Exception:
        return
    raise RuntimeError(
        "문서가 편집 불가 상태(읽기 전용 또는 양식 모드)입니다.\n"
        "  이 문서를 다른 한글 창에서 열어 두었다면 닫고 다시 실행해 주세요.\n"
        "  (배포용 문서나 편집 제한이 걸린 문서는 정리할 수 없습니다)")


def open_document(hwp, path) -> bool:
    """문서를 열고 편집 모드를 확인한다."""
    if not hwp.open(str(path)):
        return False
    ensure_edit_mode(hwp)
    return True


def cell_entry_diagnosis(hwp) -> str:
    """표 진입 실패 원인 파악용 현재 상태 요약."""
    bits = []
    for 이름, fn in (("편집모드", lambda: hwp.EditMode),
                    ("셀안", lambda: bool(hwp.hwp.CellShape)),
                    ("위치표시", lambda: hwp.hwp.KeyIndicator())):
        try:
            bits.append(f"{이름}={fn()!r}")
        except Exception as e:
            bits.append(f"{이름}=?({type(e).__name__})")
    return ", ".join(bits)


def enter_table(hwp, ctrl):
    """해당 표의 첫 셀에 캐럿을 놓는다(블록 없음).

    표 안으로 들어가지 못하면 예외를 낸다. (예전에는 조용히 실패해
    '셀 0개만 방문됨' 이라는 엉뚱한 메시지가 나왔다)"""
    for 방법 in ("ShapeObjTableSelCell", "ShapeObjTextBoxEdit"):
        hwp.set_pos_by_set(ctrl.GetAnchorPos(0))
        hwp.hwp.FindCtrl()
        hwp.HAction.Run(방법)
        if 방법 == "ShapeObjTableSelCell":
            hwp.HAction.Run("Cancel")   # 셀 블록만 해제(캐럿은 셀 안에 남음)
        if current_cell_addr(hwp) is not None:
            return
    raise RuntimeError(f"표 안으로 캐럿을 옮기지 못했습니다 ({cell_entry_diagnosis(hwp)})")


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


def apply_cell(hwp, sides: dict, fill, text=None, style_ids=None):
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

    # 셀 안 글자 서식 (블록을 새로 잡아 적용한다 — 위 블록에 이어서 하면
    # TableCellBlock이 선택 영역을 넓혀 옆 셀까지 번질 수 있다)
    if text is not None and not text_rule_is_noop(text):
        hwp.HAction.Run("TableCellBlock")
        try:
            apply_text_rule(hwp, text, style_ids or {})
        finally:
            hwp.HAction.Run("Cancel")


def apply_text_rule(hwp, rule, style_ids: dict) -> None:
    """현재 선택 영역(또는 캐럿 문단)에 글자·문단 서식을 적용한다."""
    if rule["style"]:
        번호 = style_ids.get(rule["style"])
        if 번호 is not None:            # 문서에 없는 스타일이면 건너뛴다
            hwp.set_style(번호)
    kw = {}
    if rule["font"]:
        kw["FaceName"] = rule["font"]
    if rule["size"]:
        kw["Height"] = rule["size"]
    if rule["bold"] is not None:
        kw["Bold"] = rule["bold"]
    if kw:
        hwp.set_font(**kw)
    if rule["align"]:
        hwp.HAction.Run(rule["align"])


def apply_table_position(hwp, ctrl, rule, style_ids: dict) -> bool:
    """표가 놓인 문단의 정렬·스타일을 맞춘다(= 표의 좌우 위치).

    글자처럼 취급된 표는 문단 정렬이 곧 표의 좌우 위치가 된다."""
    if text_rule_is_noop(rule):
        return False
    hwp.set_pos_by_set(ctrl.GetAnchorPos(0))
    apply_text_rule(hwp, rule, style_ids)
    return True


def ensure_caption(hwp, ctrl, rule, style_ids: dict, has_caption: bool) -> bool:
    """캡션(번호 제목)이 없으면 만들고, 글자 서식과 위치를 맞춘다.

    새로 만들었으면 True. 이미 있으면 내용은 그대로 두고 서식만 맞춘다."""
    if not rule["use"]:
        return False
    hwp.set_pos_by_set(ctrl.GetAnchorPos(0))
    hwp.hwp.FindCtrl()
    # 캡션이 없으면 새로 만들고, 있으면 그 캡션 안으로 들어간다
    hwp.HAction.Run("ShapeObjAttachCaption")
    try:
        if not has_caption and rule["number"]:
            hwp.HAction.Run("ShapeObjInsertCaptionNum")     # "표 1" 같은 번호
        if not text_rule_is_noop(rule["text"]):
            apply_text_rule(hwp, rule["text"], style_ids)
    finally:
        hwp.HAction.Run("CloseEx")                          # 캡션 편집 끝내기
    if rule["side"]:                                        # 캡션 위치(위/아래)
        hwp.set_pos_by_set(ctrl.GetAnchorPos(0))
        hwp.hwp.FindCtrl()
        pset = hwp.HParameterSet.HShapeObject
        hwp.HAction.GetDefault("TablePropertyDialog", pset.HSet)
        pset.ShapeCaption.Side = hwp.hwp.SideType(rule["side"])
        hwp.HAction.Execute("TablePropertyDialog", pset.HSet)
    return not has_caption


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


WIDTH_TOL = 30                                  # 너비 허용 오차(HwpUnit, ≈0.1mm)


def resize_table(hwp, ctrl, target_hu: int, fit_doc: bool = False) -> bool:
    """표 전체 너비를 target_hu(HwpUnit)로 변경. 실제 변경 시 True 반환.

    표의 너비는 ctrl.Properties의 Width 대입으로도, 표/셀 속성 액션의
    Width 항목으로도 바뀌지 않는다(표 너비는 열 너비의 합으로 재계산됨).
    동작하는 방법은 pyhwpx의 set_table_width와 같은 원리 — 표를 HWPML로
    추출해 셀 너비를 비율대로 고친 뒤 같은 자리에 다시 넣는 것뿐이다.
    (set_table_width를 직접 쓰지 않는 이유: 내부의 SelectCtrlFront
    무한 루프가 그림이 든 그림틀에서 멈출 수 있어, 표 컨트롤을 직접
    선택하는 유한 동작으로 재구현했다)

    fit_doc: 목표가 '문서폭'이면 표 바깥 여백을 빼서 실제로 도달 가능한
    값으로 보정한다.

    주의: 재삽입 과정에서 기존 표 컨트롤이 삭제되므로, 이 함수를 부른
    뒤에는 ctrl 참조를 절대 다시 사용하면 안 된다(죽은 참조)."""
    enter_table(hwp, ctrl)
    if fit_doc:
        try:
            target_hu -= (hwp.get_table_outside_margin_left(as_="hwpunit")
                          + hwp.get_table_outside_margin_right(as_="hwpunit"))
        except Exception:
            pass
    try:
        cur = hwp.CellShape.Item("Width")       # 캐럿 기준 현재 표 너비
    except Exception:
        cur = None
    if not cur:
        print("      ※ 현재 표 너비를 읽지 못해 너비 조절을 건너뜁니다")
        return False
    if abs(cur - target_hu) <= WIDTH_TOL:
        return False                            # 이미 목표 너비
    try:
        # 표 컨트롤을 직접 선택해 HWPML로 추출하고 셀 너비를 비율 조정
        hwp.set_pos_by_set(ctrl.GetAnchorPos(0))
        hwp.hwp.FindCtrl()                      # 개체 선택 상태
        orig_text = hwp.GetTextFile("HWPML2X", "saveblock")
        block = ET.fromstring(orig_text)
        ratio = target_hu / cur
        for cell in block.iter("CELL"):
            w = cell.get("Width")
            if w:
                cell.set("Width", str(round(int(w) * ratio)))
        new_text = ET.tostring(block, encoding="UTF-16").decode("utf-16")

        # 조판 부호 표시 상태에서 삭제·삽입해야 위치가 정확하다 (pyhwpx 방식)
        prop = hwp.ViewProperties
        old_flag = prop.Item("OptionFlag")
        if old_flag not in (2, 6):
            prop.SetItem("OptionFlag", 6)
            hwp.ViewProperties = prop
        deleted = False
        try:
            hwp.set_pos_by_set(ctrl.GetAnchorPos(0))
            hwp.hwp.FindCtrl()
            hwp.HAction.Run("Delete")           # 이 시점부터 ctrl은 죽은 참조
            deleted = True
            hwp.SetTextFile(new_text, format="HWPML2X", option="insertfile")
        except Exception:
            if deleted:                         # 삽입 실패 → 원래 표로 복구
                try:
                    hwp.SetTextFile(orig_text, format="HWPML2X", option="insertfile")
                    print("      ※ 너비 적용에 실패해 원래 표로 복구했습니다")
                except Exception:
                    print("      ⚠ 너비 조절 중 표 복구까지 실패 — 결과물에서 이 표를 꼭 확인하세요!")
            raise
        finally:
            prop = hwp.ViewProperties
            prop.SetItem("OptionFlag", old_flag)
            hwp.ViewProperties = prop
        return True
    except Exception as e:
        # 너비 조절 실패는 표 서식(테두리/배경)과 무관 — 표를 실패로 만들지 않는다
        print(f"      ※ 너비 조절 실패({type(e).__name__}: {e}) — 서식은 적용됨")
        return False


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


def extras(hwp, ctrl, spec, rules, style_ids, 종류: str):
    """표 위치와 캡션을 적용한다. (적용한 위치 수, 새로 만든 캡션 수) 반환.

    테두리·배경 정리와는 별개의 부가 작업이라, 실패해도 표 전체를
    실패로 만들지 않고 경고만 남긴다."""
    위치수 = 캡션수 = 0
    try:
        if apply_table_position(hwp, ctrl, rules["table_pos"], style_ids):
            위치수 = 1
    except Exception as e:
        print(f"      ※ 표 위치 조정 실패({type(e).__name__}: {e})")
    try:
        if ensure_caption(hwp, ctrl, rules["caption"][종류], style_ids, spec.has_caption):
            캡션수 = 1
    except Exception as e:
        print(f"      ※ 캡션 처리 실패({type(e).__name__}: {e})")
    return 위치수, 캡션수


def format_table(hwp, ctrl, spec: TableSpec, rules, style_ids=None) -> int:
    """표 하나를 셀 단위로 정리. 처리한 셀 수를 반환."""
    enter_table(hwp, ctrl)
    done = 0
    글자 = rules["cell_text"]
    머리글 = rules["header_rows"] if spec.n_rows > rules["header_rows"] else 0
    max_steps = 4 * len(spec.cells) + 16
    for addr in walk_cells(hwp, max_steps):
        if addr not in spec.cells:      # 구조 분석과 불일치 → 안전하게 중단
            raise RuntimeError(f"셀 주소 불일치: {addr}")
        sides, fill = cell_plan(spec, addr, rules)
        text = None
        if 글자["use"]:
            text = 글자["header"] if addr[0] < 머리글 else 글자["body"]
        apply_cell(hwp, sides, fill, text, style_ids)
        done += 1
    if done != len(spec.cells):
        raise RuntimeError(f"셀 {len(spec.cells)}개 중 {done}개만 방문됨")
    return done


# ------------------------------------------------------------------
# 실행
# ------------------------------------------------------------------

def collect_targets(paths) -> list:
    """파일/폴더가 섞인 목록을 실제 처리 대상 문서 목록으로 확장한다.

    폴더는 바로 아래의 .hwp/.hwpx만 찾고(하위 폴더 제외), 이 프로그램이
    만드는 결과물(_정리본)과 임시 파일(_표정리_*)은 건너뛴다.
    직접 지정한 파일은 이름과 무관하게 그대로 존중한다."""
    targets, seen = [], set()
    for raw in paths:
        p = Path(raw)
        if p.is_dir():
            found = [f for f in sorted(p.iterdir())
                     if f.suffix.lower() in (".hwp", ".hwpx")
                     and not f.stem.endswith("_정리본")
                     and not f.name.startswith("_표정리_")]
        else:
            found = [p]
        for f in found:
            r = f.resolve()
            if r not in seen:
                seen.add(r)
                targets.append(r)
    return targets


def run_batch(files, visible: bool = False) -> tuple:
    """여러 문서를 차례로 처리한다. (성공, 실패) 개수를 반환."""
    ok = fail = 0
    many = len(files) > 1
    for i, f in enumerate(files, 1):
        if many:
            print(f"\n━━━ [{i}/{len(files)}] {f.name} ━━━")
        try:
            if not f.exists():
                raise FileNotFoundError(f"파일이 없습니다: {f}")
            process(f, visible=visible)
            ok += 1
        except 규칙오류:
            raise                       # 규칙 오류는 모든 문서 공통 → 즉시 중단
        except Exception:
            fail += 1
            print(f"[오류] {f.name} 처리 실패:\n{traceback.format_exc()}")
    if many:
        print(f"\n배치 완료: 성공 {ok}개 / 실패 {fail}개")
    return ok, fail


def process(src: Path, visible: bool = False) -> Path:
    from pyhwpx import Hwp

    rules = load_rules(Path(__file__).parent / "서식규칙.yaml")

    out = src.with_name(src.stem + "_정리본" + src.suffix)
    fmt = "HWPX" if src.suffix.lower() == ".hwpx" else "HWP"

    print("※ 처리 중 한글 팝업창이 나오면 모두 [확인]을 눌러주세요.", flush=True)
    print("한글 실행 중..." + (" (창 표시 모드)" if visible else ""), flush=True)
    # new=True: 사용자가 직접 열어 둔 한글 창이나 이전 실행의 잔재 인스턴스에
    # 붙지 않도록 항상 독립된 새 인스턴스를 만든다. (기본값 new=False는 기존
    # 창에 연결해 그 창을 숨기고 문서를 열고 마지막에 종료해 버린다)
    # 한글이 떠 있지 않은 상태의 첫 실행은 초기화가 오래 걸려 연결이
    # 실패할 수 있으므로 재시도한다.
    hwp = None
    for attempt in range(3):
        if attempt:
            print(f"  한글 연결 재시도 ({attempt}/2)...", flush=True)
            time.sleep(3)
        try:
            hwp = Hwp(new=True, visible=visible)
            break
        except Exception as e:
            last_err = e
    if hwp is None:
        raise RuntimeError(
            f"한글을 실행하지 못했습니다: {last_err}\n"
            "  한글(한컴오피스) 설치 여부와, 작업 관리자에 남아 있는 "
            "한글 프로세스가 없는지 확인해 주세요.")
    tmp2 = None                         # 후처리 임시 파일 (종료 후 정리용)
    try:
        try:
            # 0x2FFF1 = 각종 대화상자에 자동으로 기본(확인/예)으로 응답
            # (0x00010000 은 일부만 처리해 팝업에서 멈추는 원인이었음)
            hwp.hwp.SetMessageBoxMode(0x2FFF1)
        except Exception:
            pass
        print(f"문서 여는 중: {src.name}", flush=True)
        if not open_document(hwp, src):
            raise RuntimeError("문서를 열 수 없습니다. (암호/배포용 문서이거나 다른 프로그램에서 사용 중일 수 있습니다)")

        print("문서 열기 완료 — 표 구조 분석 중...", flush=True)
        specs, style_ids = analyze_tables(hwp, src)
        빠진스타일 = [n for n in wanted_style_names(rules) if n not in style_ids]
        if 빠진스타일:
            print(f"  ※ 이 문서에 없는 스타일은 건너뜁니다: {', '.join(빠진스타일)}\n"
                  f"    (한글에서 서식 파일(.sty)을 먼저 불러오면 적용됩니다)")
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
        captions = positioned = 0
        failed_idx = set()              # 실패한 표의 순번(0-기준) — 후처리에서 제외
        for i, (ctrl, spec) in enumerate(zip(ctrls, specs), start=1):
            label = f"[{i}/{len(ctrls)}] {spec.n_rows}행x{spec.n_cols}열"
            try:
                # 중첩 표(표 안의 표)는 부모 셀보다 넓힐 수 없으므로 너비 조절 제외
                can_resize = not getattr(spec, "nested", False)
                if spec.is_1x1:
                    if rules["frame"]["skip"]:
                        print(f"  {label} — 그림틀 → 건너뜀(규칙)")
                    else:
                        format_frame(hwp, ctrl, rules)
                        p_ok, c_ok = extras(hwp, ctrl, spec, rules, style_ids, "frame")
                        positioned += p_ok
                        captions += c_ok
                        w_ok = (can_resize and frame_target is not None
                                and resize_table(hwp, ctrl, frame_target,
                                                 fit_doc=fw["mode"] == "doc_width"))
                        if w_ok:
                            resized += 1
                        frames += 1
                        print(f"  {label} — 그림틀 서식 적용" + ("＋너비조절" if w_ok else ""))
                    continue
                n = format_table(hwp, ctrl, spec, rules, style_ids)
                p_ok, c_ok = extras(hwp, ctrl, spec, rules, style_ids, "table")
                positioned += p_ok
                captions += c_ok
                w_ok = (can_resize and table_target is not None
                        and resize_table(hwp, ctrl, table_target,
                                         fit_doc=tw["mode"] == "doc_width"))
                if w_ok:
                    resized += 1
                done += 1
                print(f"  {label} — 셀 {n}개 정리 완료" + ("＋너비조절" if w_ok else ""))
            except Exception as e:
                failed += 1
                failed_idx.add(i - 1)
                print(f"  {label} — 실패({e}) → 건너뜀")

        # 표가 있는데 하나도 정리하지 못했다면 문서 전체의 문제다.
        # 아무것도 바뀌지 않은 '정리본'을 만들어 혼란을 주지 않도록 중단한다.
        if failed and not done and not frames:
            raise RuntimeError(
                f"표 {failed}개를 모두 정리하지 못했습니다 — 문서를 저장하지 않았습니다.\n"
                f"  현재 상태: {cell_entry_diagnosis(hwp)}\n"
                "  이 문서가 다른 한글 창에서 열려 있으면 닫고 다시 실행해 주세요.\n"
                "  계속 같은 증상이면 python 표정리.py --보기 문서.hwp 로 실행해\n"
                "  한글 화면에서 어떤 상태인지 확인해 주세요.")

        # 개체 공통 속성 정리: 사진 번호종류·너비 + 글자처럼 취급(표/그림)
        photos = photos_resized = tac_changed = 0
        tac = rules["treat_as_char"]
        ctrl = hwp.HeadCtrl
        while ctrl:
            try:
                is_pic = ctrl.UserDesc == "그림"
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
            except Exception as e:
                print(f"  개체 속성 변경 실패({e}) — 건너뜀")
            ctrl = ctrl.Next
        if photos:
            print(f"  사진 {photos}개 — 번호종류: 없음 적용")
        if photos_resized:
            print(f"  사진 {photos_resized}개 — 너비 조절(비율 유지)")
        if tac_changed:
            print(f"  개체 {tac_changed}개 — 글자처럼 취급 {'체크' if tac == 1 else '해제'}")

        # 후처리 (hwpx 직접 수정): 왼쪽 테두리 색 보정 + x 스타일 제거
        tmp2 = make_temp_path(src.parent, "_표정리_후처리")
        if not hwp.save_as(str(tmp2), format="HWPX"):
            raise RuntimeError("후처리용 임시 저장에 실패했습니다.")
        patches = compute_left_patches(tmp2, rules, failed_idx)
        report_widths(tmp2, table_target, frame_target,
                      table_fit=tw["mode"] == "doc_width",
                      frame_fit=fw["mode"] == "doc_width",
                      frame_skip=rules["frame"]["skip"],
                      skip_idx=failed_idx)
        hwp.HAction.Run("FileNew")      # 임시 파일 잠금 해제(빈 문서로 전환)
        if patches:
            patch_left_colors(tmp2, patches)
            print(f"  왼쪽 테두리 색 보정 {len(patches)}건")
        if rules["remove_x_styles"]:
            removed = remove_x_styles_in_hwpx(tmp2)
            if removed:
                print(f"  엑셀 잔재 스타일 제거: {', '.join(removed)}")
        if not open_document(hwp, tmp2):
            raise RuntimeError("후처리 파일을 다시 열지 못했습니다.")
        if os.path.exists(out):
            os.remove(out)
        if not hwp.save_as(str(out), format=fmt):
            raise RuntimeError("저장에 실패했습니다.")
        # tmp2 삭제는 한글 종료 후(잠금 해제 확실)의 finally에서 수행한다.
        if captions or positioned:
            print(f"  캡션 새로 넣음 {captions}개 / 표 위치 조정 {positioned}개")
        print(f"\n완료: 표 {done}개 / 그림틀 {frames}개 / 너비조절 {resized}개 / 사진 {photos}개 / 실패 {failed}개")
        print(f"저장 위치: {out}")
        return out
    finally:
        # 한글 종료. quit()은 내부에서 빈 문서 정리(clear)를 먼저 하는데
        # 이 단계가 COM 오류로 실패하면 곧장 Quit을 호출해 앱을 닫는다.
        # 팝업 등으로 종료가 멈출 수 있으므로 별도 스레드에서 시도하고,
        # 제한 시간 안에 끝나지 않아도 다음 단계로 넘어간다. (본체의
        # os._exit가 프로세스를 확실히 끝내 작업창이 완료를 감지하게 함)
        def _do_quit():
            try:
                hwp.quit()
            except Exception:
                try:
                    hwp.hwp.Quit()
                except Exception:
                    pass
        t = threading.Thread(target=_do_quit, daemon=True)
        t.start()
        t.join(timeout=10)
        if t.is_alive():
            print("※ 한글 종료가 지연됩니다 — 팝업이 떠 있으면 [확인]을 누르거나\n"
                  "  남은 한글 창을 직접 닫아 주세요(작업은 이미 저장됨).")
        # 임시 파일은 한글이 파일 잠금을 완전히 풀어야 지워지므로
        # 종료 후에 재시도하며 정리한다.
        if tmp2 is not None and tmp2.exists():
            for _ in range(10):
                try:
                    tmp2.unlink()
                    break
                except OSError:
                    time.sleep(0.5)
            else:
                print(f"※ 임시 파일이 남았습니다(다음 실행 때 자동 정리 시도): {tmp2.name}")


def run_cli(paths, visible: bool) -> None:
    """명령줄/파일선택 모드: 대상 목록을 확장해 일괄 처리하고 결과를 출력."""
    try:
        targets = collect_targets(paths)
        if not targets:
            print("처리할 .hwp/.hwpx 문서를 찾지 못했습니다.")
        else:
            run_batch(targets, visible)
    except 규칙오류 as e:
        print(f"\n[서식규칙.yaml 오류]\n{e}")
    except Exception:
        print("\n[오류가 발생했습니다]")
        traceback.print_exc()
    finally:
        # 더블클릭/드래그앤드롭 실행 시 창이 바로 닫혀 결과를 못 보는 것 방지.
        # 단, 작업창이 하위 프로세스로 호출(출력이 파이프로 연결)한 경우에는
        # 멈추지 않는다. (콘솔 없는 환경의 RuntimeError도 무시)
        if sys.stdout.isatty():
            try:
                input("\n엔터 키를 누르면 창이 닫힙니다...")
            except (EOFError, RuntimeError):
                pass


# ------------------------------------------------------------------
# 작업창(GUI) — 파일 선택·서식 설정·진행 로그를 제공하고, 실제 처리는
#   이 파일(표정리.py)을 하위 프로세스로 호출해 수행한다(한글 COM 격리).
#   tkinter가 없는 환경에서는 이 아래 클래스들이 정의되지 않는다.
# ------------------------------------------------------------------

# 표준 서식 프리셋 기본값 — 서식규칙.yaml 이 없거나 일부 항목이 비어도
# 작업창의 [서식 설정]에 이 값들이 기본으로 채워진다(서식 샘플 기준).
DEFAULT_RULES = {
    "표서식": {
        "너비": "문서폭",
        "바깥선": {
            "위":     {"종류": "실선", "굵기": "0.4mm", "색": "#000000"},
            "아래":   {"종류": "실선", "굵기": "0.4mm", "색": "#000000"},
            "왼쪽":   {"종류": "없음"},
            "오른쪽": {"종류": "없음"},
        },
        "안쪽선": {"종류": "실선", "굵기": "0.12mm", "색": "#000000"},
        "머리글행": {"사용": True, "행수": 1, "배경색": "#CCCCCC",
                    "아래선": {"종류": "이중실선", "굵기": "0.5mm", "색": "#000000"}},
        "본문셀": {"배경": "지우기"},
    },
    "그림틀": {
        "너비": "문서폭", "처리": "정리",
        "테두리": {"종류": "실선", "굵기": "0.1mm", "색": "#B3B3B3"},
        "배경": "지우기", "번호종류": "그림",
    },
    "사진": {"번호종류": "없음", "너비": "유지"},
    "글자서식": {
        "사용": "켬",
        "머리글행": {"스타일": "표위타이틀", "정렬": "가운데"},
        "본문셀": {"스타일": "표내용", "정렬": "유지"},
    },
    "표위치": {"정렬": "가운데", "스타일": "유지"},
    "캡션": {
        "표": {"사용": "켬", "위치": "위", "번호": "켬",
              "글자": {"스타일": "표-번호제목", "정렬": "유지"}},
        "그림틀": {"사용": "켬", "위치": "아래", "번호": "켬",
                 "글자": {"스타일": "그림-번호제목", "정렬": "유지"}},
    },
    "개체위치": {"글자처럼취급": "켬"},
    "스타일정리": {"x스타일제거": "켬"},
}


def _deep_merge(base: dict, over: dict) -> dict:
    """base(기본값) 위에 over(사용자 값)를 재귀적으로 덮어쓴 새 dict를 만든다."""
    import copy
    result = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(result.get(k), dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = copy.deepcopy(v)
    return result


HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")

if _TK_OK:
    # 콤보박스 선택지 — 엔진의 정의(dict)를 단일 출처로 재사용(순서 유지)
    _GUI_LINE_TYPES = list(LINE_TYPES)
    _GUI_LINE_WIDTHS = list(LINE_WIDTHS)
    _GUI_NUMBERING = list(NUMBERING_TYPES)
    _WIDTH_VALUES = ["유지", "문서폭", "120mm", "150mm", "180mm"]
    _ON_OFF_KEEP = ["켬", "끔", "유지"]
    _ON_OFF = ["켬", "끔"]
    _CLEAR_KEEP = ["지우기", "유지"]
    _ALIGN_VALUES = ["유지"] + list(ALIGN_ACTIONS)
    _SIDE_VALUES = list(CAPTION_SIDES)
    # 표준 서식(.sty)에 들어 있는 표 관련 스타일 이름 — 직접 입력도 가능
    _STYLE_NAMES = ["유지", "표위타이틀", "표왼타이틀", "표내용", "표내용(가운데)",
                    "표-번호제목", "그림-번호제목", "표-그림위치", "자료", "바탕글"]

    class RuleEditor(tk.Toplevel):
        """서식규칙.yaml의 자주 쓰는 항목을 폼으로 편집하는 설정 창."""

        def __init__(self, master, config_path: Path, on_saved):
            super().__init__(master)
            self.config_path = config_path
            self.title("서식 설정")
            self.geometry("760x660")
            self.minsize(680, 580)
            self.transient(master)
            self.grab_set()

            self.on_saved = on_saved
            self.rules = self._load_rules()
            self.vars = {}
            self._line_bases = []           # 종류/굵기/색 한 벌짜리 선 항목들의 기준 경로
            self._build_ui()

        def _load_rules(self):
            """기본값(DEFAULT_RULES) 위에 저장된 yaml을 덮어써, 모든 항목이
            표준값으로 채워진 규칙을 만든다."""
            user = {}
            if self.config_path.exists():
                with open(self.config_path, encoding="utf-8") as f:
                    user = yaml.safe_load(f) or {}
                if not isinstance(user, dict):
                    user = {}
            merged = _deep_merge(DEFAULT_RULES, user)
            canonicalize_line_values(merged)     # 잘못된 값은 표준값으로 자동 복구
            return merged

        def _build_ui(self):
            root = ttk.Frame(self, padding=14)
            root.pack(fill="both", expand=True)
            ttk.Label(root, text="서식 설정", font=("맑은 고딕", 15, "bold")).pack(anchor="w")
            ttk.Label(root, text="표준 서식이 기본으로 채워져 있습니다. 바꿀 항목만 수정한 뒤 저장하세요.",
                      foreground="#555555").pack(anchor="w", pady=(2, 10))

            notebook = ttk.Notebook(root)
            notebook.pack(fill="both", expand=True)
            self._build_table_tab(notebook)
            self._build_frame_tab(notebook)
            self._build_object_tab(notebook)
            self._build_cell_text_tab(notebook)
            self._build_text_style_tab(notebook)
            self._build_raw_tab(notebook)

            buttons = ttk.Frame(root)
            buttons.pack(fill="x", pady=(12, 0))
            ttk.Button(buttons, text="저장", command=self.save).pack(side="right")
            ttk.Button(buttons, text="취소", command=self.destroy).pack(side="right", padx=(0, 8))

        # ── 탭 구성 ──
        def _build_table_tab(self, notebook):
            tab = ttk.Frame(notebook, padding=12)
            notebook.add(tab, text="표 서식")
            표 = self.rules["표서식"]
            바깥 = 표["바깥선"]
            머리글 = 표["머리글행"]

            self._combo_row(tab, "표 너비", ("표서식", "너비"), 표["너비"], _WIDTH_VALUES, 0,
                            "문서폭 / 유지 / 150mm 처럼 직접 입력도 가능")
            # 안쪽선(종류/굵기/색) — 종류가 '없음'이면 굵기·색 비활성화
            self._line_rows(tab, "안쪽선", ("표서식", "안쪽선"), 표["안쪽선"], start_row=1)

            border_box = ttk.LabelFrame(tab, text="바깥선 (종류가 ‘없음’이면 굵기·색은 비활성)")
            border_box.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(12, 6))
            for idx, key in enumerate(("위", "아래", "왼쪽", "오른쪽")):
                rule = 바깥.get(key) or {}
                ttk.Label(border_box, text=key).grid(row=idx, column=0, sticky="w", padx=8, pady=4)
                self._line_cells(border_box, ("표서식", "바깥선", key), rule, idx)

            self._combo_row(tab, "머리글 사용", ("표서식", "머리글행", "사용"),
                            "켬" if 머리글.get("사용", True) else "끔", _ON_OFF, 5)
            self._entry_row(tab, "머리글 행수", ("표서식", "머리글행", "행수"), str(머리글.get("행수", 1)), 6)
            self._color_row(tab, "머리글 배경색", ("표서식", "머리글행", "배경색"), 머리글.get("배경색", "#CCCCCC"), 7)
            self._combo_row(tab, "본문 배경", ("표서식", "본문셀", "배경"), 표["본문셀"].get("배경", "지우기"), _CLEAR_KEEP, 8)
            tab.columnconfigure(1, weight=1)

        def _build_frame_tab(self, notebook):
            tab = ttk.Frame(notebook, padding=12)
            notebook.add(tab, text="그림틀")
            틀 = self.rules["그림틀"]
            self._combo_row(tab, "그림틀 너비", ("그림틀", "너비"), 틀["너비"], _WIDTH_VALUES, 0)
            self._combo_row(tab, "처리", ("그림틀", "처리"), 틀["처리"], ["정리", "건너뛰기"], 1)
            self._line_rows(tab, "테두리", ("그림틀", "테두리"), 틀["테두리"], start_row=2)
            self._combo_row(tab, "배경", ("그림틀", "배경"), 틀["배경"], _CLEAR_KEEP, 5)
            self._combo_row(tab, "번호종류", ("그림틀", "번호종류"), 틀["번호종류"], _GUI_NUMBERING, 6)
            tab.columnconfigure(1, weight=1)

        def _build_object_tab(self, notebook):
            tab = ttk.Frame(notebook, padding=12)
            notebook.add(tab, text="사진/공통")
            사진, 개체 = self.rules["사진"], self.rules["개체위치"]
            self._combo_row(tab, "사진 번호종류", ("사진", "번호종류"), 사진["번호종류"], _GUI_NUMBERING, 0)
            self._combo_row(tab, "사진 너비", ("사진", "너비"), 사진["너비"], _WIDTH_VALUES, 1,
                            "너비를 바꾸면 높이는 비율 유지로 자동 조절")
            self._combo_row(tab, "글자처럼 취급", ("개체위치", "글자처럼취급"), 개체["글자처럼취급"], _ON_OFF_KEEP, 2)
            tab.columnconfigure(1, weight=1)

        def _build_cell_text_tab(self, notebook):
            """표 안 글자 서식 · 표 위치 · 캡션"""
            tab = ttk.Frame(notebook, padding=12)
            notebook.add(tab, text="글자·캡션")
            글자 = self.rules["글자서식"]
            머리 = 글자.get("머리글행") or {}
            본문 = 글자.get("본문셀") or {}
            위치 = self.rules["표위치"]
            캡션 = self.rules["캡션"]

            self._combo_row(tab, "표 안 글자 서식", ("글자서식", "사용"),
                            글자.get("사용", "켬"), _ON_OFF, 0,
                            "스타일은 그 이름이 문서에 있을 때만 적용됩니다")
            for row, (제목, 경로, 값) in enumerate((
                    ("머리글 행", ("글자서식", "머리글행"), 머리),
                    ("본문 셀", ("글자서식", "본문셀"), 본문)), start=1):
                box = ttk.LabelFrame(tab, text=제목)
                box.grid(row=row, column=0, columnspan=3, sticky="ew", pady=4)
                self._combo_row(box, "스타일", 경로 + ("스타일",),
                                값.get("스타일", "유지"), _STYLE_NAMES, 0)
                self._combo_row(box, "정렬", 경로 + ("정렬",),
                                값.get("정렬", "유지"), _ALIGN_VALUES, 1)
                box.columnconfigure(1, weight=1)

            box = ttk.LabelFrame(tab, text="표 위치 (표가 놓인 문단의 정렬)")
            box.grid(row=3, column=0, columnspan=3, sticky="ew", pady=4)
            self._combo_row(box, "정렬", ("표위치", "정렬"), 위치.get("정렬", "가운데"),
                            _ALIGN_VALUES, 0)
            self._combo_row(box, "스타일", ("표위치", "스타일"), 위치.get("스타일", "유지"),
                            _STYLE_NAMES, 1)
            box.columnconfigure(1, weight=1)

            for row, (key, 기본위치) in enumerate((("표", "위"), ("그림틀", "아래")), start=4):
                항목 = 캡션.get(key) or {}
                글 = 항목.get("글자") or {}
                box = ttk.LabelFrame(tab, text=f"캡션 — {key}")
                box.grid(row=row, column=0, columnspan=3, sticky="ew", pady=4)
                self._combo_row(box, "사용", ("캡션", key, "사용"),
                                항목.get("사용", "켬"), _ON_OFF, 0)
                self._combo_row(box, "위치", ("캡션", key, "위치"),
                                항목.get("위치", 기본위치), _SIDE_VALUES, 1)
                self._combo_row(box, "번호 넣기", ("캡션", key, "번호"),
                                항목.get("번호", "켬"), _ON_OFF, 2)
                self._combo_row(box, "스타일", ("캡션", key, "글자", "스타일"),
                                글.get("스타일", "유지"), _STYLE_NAMES, 3)
                box.columnconfigure(1, weight=1)
            tab.columnconfigure(1, weight=1)

        def _build_text_style_tab(self, notebook):
            tab = ttk.Frame(notebook, padding=12)
            notebook.add(tab, text="글자스타일")
            self._combo_row(tab, "x스타일 제거", ("스타일정리", "x스타일제거"),
                            self.rules["스타일정리"]["x스타일제거"], _ON_OFF, 0)
            ttk.Label(tab, text=("엑셀 표를 붙여넣으면 이름이 'x'로 시작하는 잔재 스타일이 남습니다.\n"
                                 "‘켬’으로 두면 이런 스타일을 목록에서 제거합니다. 해당 문단은\n"
                                 "‘바탕글’로 연결되며 글자 모양(글꼴·크기 등)은 그대로 유지됩니다."),
                      foreground="#555555", justify="left").grid(
                          row=1, column=0, columnspan=3, sticky="w", pady=(10, 0))
            tab.columnconfigure(1, weight=1)

        def _build_raw_tab(self, notebook):
            tab = ttk.Frame(notebook, padding=12)
            notebook.add(tab, text="원본 YAML")
            ttk.Label(tab, text="고급 사용자는 아래 내용을 직접 수정할 수 있습니다.",
                      foreground="#555555").pack(anchor="w")
            frame = ttk.Frame(tab)
            frame.pack(fill="both", expand=True, pady=(8, 0))
            self.raw_text = tk.Text(frame, wrap="none")
            sy = ttk.Scrollbar(frame, orient="vertical", command=self.raw_text.yview)
            sx = ttk.Scrollbar(frame, orient="horizontal", command=self.raw_text.xview)
            self.raw_text.configure(yscrollcommand=sy.set, xscrollcommand=sx.set)
            self.raw_text.grid(row=0, column=0, sticky="nsew")
            sy.grid(row=0, column=1, sticky="ns")
            sx.grid(row=1, column=0, sticky="ew")
            frame.rowconfigure(0, weight=1)
            frame.columnconfigure(0, weight=1)
            self.raw_text.insert("1.0", yaml.safe_dump(self.rules, allow_unicode=True, sort_keys=False))

        # ── 선 항목(종류/굵기/색) 한 벌 + 없음일 때 비활성화 ──
        def _line_rows(self, parent, label, base, rule, start_row):
            """세로 3줄 배치(표 서식 탭용)."""
            tvar = self._combo_row(parent, f"{label} 종류", base + ("종류",), rule.get("종류", "실선"),
                                   _GUI_LINE_TYPES, start_row)
            wcombo = self._combo_row(parent, f"{label} 굵기", base + ("굵기",), rule.get("굵기", "0.12mm"),
                                     _GUI_LINE_WIDTHS, start_row + 1)
            chandle = self._color_row(parent, f"{label} 색", base + ("색",), rule.get("색", "#000000"),
                                      start_row + 2)
            self._wire_line(base, self.vars[base + ("종류",)], wcombo, chandle)

        def _line_cells(self, parent, base, rule, row):
            """가로 한 줄 배치(바깥선 박스용)."""
            tvar_combo = self._combo(parent, base + ("종류",), rule.get("종류", "실선"), _GUI_LINE_TYPES, row, 1)
            wcombo = self._combo(parent, base + ("굵기",), rule.get("굵기", "0.4mm"), _GUI_LINE_WIDTHS, row, 2)
            chandle = self._color(parent, base + ("색",), rule.get("색", "#000000"), row, 3)
            self._wire_line(base, self.vars[base + ("종류",)], wcombo, chandle)

        def _wire_line(self, base, type_var, width_combo, color_handle):
            """종류가 '없음'이면 굵기 콤보와 색 위젯을 비활성화한다."""
            self._line_bases.append(base)
            deps = [width_combo, color_handle["entry"], color_handle["button"]]
            swatch = color_handle["swatch"]

            def apply(*_):
                off = str(type_var.get()).strip() == "없음"
                for w in deps:
                    try:
                        w.configure(state="disabled" if off else "normal")
                    except tk.TclError:
                        pass
                swatch.configure(bg="#EEEEEE" if off else self._safe_color(color_handle["var"].get()))
            type_var.trace_add("write", apply)
            apply()

        # ── 위젯 헬퍼 ──
        def _combo_row(self, parent, label, path, value, values, row, help_text=None):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=5)
            combo = self._combo(parent, path, value, values, row, 1)
            if help_text:
                ttk.Label(parent, text=help_text, foreground="#666666").grid(
                    row=row, column=2, sticky="w", padx=(8, 0))
            return combo

        def _entry_row(self, parent, label, path, value, row):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=5)
            return self._entry(parent, path, value, row, 1)

        def _color_row(self, parent, label, path, value, row):
            ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=5)
            return self._color(parent, path, value, row, 1)

        @staticmethod
        def _safe_color(value):
            v = str(value).strip()
            return v if HEX_RE.match(v) else "#FFFFFF"

        def _combo(self, parent, path, value, values, row, col):
            var = tk.StringVar(value=str(value))
            self.vars[path] = var
            box = ttk.Combobox(parent, textvariable=var, values=values)
            box.grid(row=row, column=col, sticky="ew", padx=(8, 0), pady=4)
            return box

        def _entry(self, parent, path, value, row, col, width=None):
            var = tk.StringVar(value=str(value))
            self.vars[path] = var
            entry = ttk.Entry(parent, textvariable=var, width=width)
            entry.grid(row=row, column=col, sticky="ew", padx=(8, 0), pady=4)
            return entry

        def _color(self, parent, path, value, row, col, width=9):
            """색 입력칸 + 실제 색 미리보기 + [색 선택] 버튼. 핸들(dict) 반환."""
            var = tk.StringVar(value=str(value))
            self.vars[path] = var
            box = ttk.Frame(parent)
            box.grid(row=row, column=col, sticky="w", padx=(8, 0), pady=4)
            entry = ttk.Entry(box, textvariable=var, width=width)
            entry.pack(side="left")
            swatch = tk.Label(box, width=3, relief="sunken", bg=self._safe_color(var.get()))
            swatch.pack(side="left", padx=(6, 0), fill="y")
            var.trace_add("write", lambda *_: swatch.configure(bg=self._safe_color(var.get())))

            def pick():
                _, hexval = colorchooser.askcolor(color=self._safe_color(var.get()),
                                                  parent=self, title="색 선택")
                if hexval:
                    var.set(hexval.upper())
            button = ttk.Button(box, text="색 선택", width=8, command=pick)
            button.pack(side="left", padx=(6, 0))
            return {"var": var, "entry": entry, "button": button, "swatch": swatch}

        # ── 저장 ──
        def save(self):
            try:
                raw_content = self.raw_text.get("1.0", "end").strip()
                raw_rules = yaml.safe_load(raw_content) if raw_content else {}
                if not isinstance(raw_rules, dict):
                    raise ValueError("YAML 최상위 구조는 객체여야 합니다.")
                self.rules = raw_rules
                for path, var in self.vars.items():
                    self._set_value(path, var.get())
                self._normalize_types()
                # 선 종류가 '없음'이면 굵기·색은 의미 없으므로 지워 깔끔하게 저장
                for base in self._line_bases:
                    node = self._get_node(base)
                    if isinstance(node, dict) and str(node.get("종류")) == "없음":
                        node.pop("굵기", None)
                        node.pop("색", None)
                # 저장 전에 엔진과 같은 방식으로 검증한다. 여기서 걸러야
                # 정리 실행 도중 규칙오류로 멈추는 일이 없다.
                parse_rules(self.rules)
                with open(self.config_path, "w", encoding="utf-8") as f:
                    yaml.safe_dump(self.rules, f, allow_unicode=True, sort_keys=False)
            except Exception as exc:
                messagebox.showerror("설정 저장 실패", str(exc))
                return
            self.on_saved()
            messagebox.showinfo("설정 저장", "서식규칙.yaml에 저장했습니다.")
            self.destroy()

        def _get_node(self, path):
            cur = self.rules
            for key in path:
                if not isinstance(cur, dict):
                    return None
                cur = cur.get(key)
            return cur

        def _set_value(self, path, value):
            cur = self.rules
            for key in path[:-1]:
                cur = cur.setdefault(key, {})
            cur[path[-1]] = value

        def _normalize_types(self):
            header = self.rules.setdefault("표서식", {}).setdefault("머리글행", {})
            header["사용"] = str(header.get("사용", "켬")) == "켬"
            try:
                header["행수"] = int(header.get("행수", 1))
            except (TypeError, ValueError):
                raise ValueError("머리글 행수는 숫자로 입력해야 합니다.")

    class WorkWindow(tk.Tk):
        """파일 선택·서식 설정·실행 로그를 제공하는 메인 작업창."""

        def __init__(self, script_path: Path, config_path: Path):
            super().__init__()
            self.script_path = script_path
            self.config_path = config_path
            self.title("HWP 표 서식 정리 작업창")
            self.geometry("820x620")
            self.minsize(720, 540)

            self.selected_file = tk.StringVar(value="")
            self.status = tk.StringVar(value="정리할 HWP/HWPX 파일을 선택하세요.")
            self.output_file = None
            self.worker = None
            self.log_queue = __import__("queue").Queue()
            self._build_ui()
            self.after(100, self._drain_log_queue)

        def _build_ui(self):
            root = ttk.Frame(self, padding=16)
            root.pack(fill="both", expand=True)
            ttk.Label(root, text="HWP 표 서식 일괄정리", font=("맑은 고딕", 16, "bold")).pack(anchor="w")
            ttk.Label(root, text="파일을 선택하고 [서식 설정]을 확인한 뒤 [정리 시작]을 누르면 *_정리본 파일을 생성합니다.").pack(
                anchor="w", pady=(4, 14))

            file_row = ttk.Frame(root)
            file_row.pack(fill="x")
            ttk.Entry(file_row, textvariable=self.selected_file).pack(side="left", fill="x", expand=True)
            ttk.Button(file_row, text="파일 선택", command=self.pick_file).pack(side="left", padx=(8, 0))

            action_row = ttk.Frame(root)
            action_row.pack(fill="x", pady=12)
            self.settings_button = ttk.Button(action_row, text="서식 설정", command=self.open_settings)
            self.settings_button.pack(side="left")
            self.folder_button = ttk.Button(action_row, text="결과 폴더 열기",
                                            command=self.open_output_folder, state="disabled")
            self.folder_button.pack(side="left", padx=(8, 0))
            self.clear_button = ttk.Button(action_row, text="로그 지우기", command=self.clear_log)
            self.clear_button.pack(side="left", padx=(8, 0))
            # 오른쪽: 주 실행 버튼(초록색 강조)
            self.start_button = tk.Button(
                action_row, text="▶ 정리 시작", command=self.start,
                bg="#2e7d32", fg="white", activebackground="#1b5e20", activeforeground="white",
                disabledforeground="#dddddd", font=("맑은 고딕", 11, "bold"),
                relief="raised", bd=2, padx=18, pady=5, cursor="hand2")
            self.start_button.pack(side="right")

            ttk.Label(root, textvariable=self.status).pack(anchor="w")
            self.progress = ttk.Progressbar(root, mode="indeterminate")
            self.progress.pack(fill="x", pady=(8, 8))

            log_frame = ttk.LabelFrame(root, text="작업 로그")
            log_frame.pack(fill="both", expand=True)
            self.log = tk.Text(log_frame, wrap="word", height=18)
            scroll = ttk.Scrollbar(log_frame, orient="vertical", command=self.log.yview)
            self.log.configure(yscrollcommand=scroll.set)
            self.log.pack(side="left", fill="both", expand=True)
            scroll.pack(side="right", fill="y")

            ttk.Label(root, foreground="#666666",
                      text="※ 처리 중 한글 팝업창이 나오면 모두 [확인]을 눌러주세요. "
                           "멈추면 명령창에서 python 표정리.py --보기 문서.hwp 로 확인할 수 있습니다.").pack(
                          anchor="w", pady=(8, 0))

        def open_settings(self):
            RuleEditor(self, self.config_path, on_saved=self._settings_saved)

        def _settings_saved(self):
            self.status.set("서식 설정을 저장했습니다. 정리 시작을 누르면 새 설정이 적용됩니다.")
            self._append_log("\n⚙ 서식 설정 저장 완료: 서식규칙.yaml\n")

        def pick_file(self):
            path = filedialog.askopenfilename(
                title="표 서식을 정리할 한글 문서를 선택하세요",
                filetypes=[("한글 문서", "*.hwp *.hwpx"), ("모든 파일", "*.*")])
            if path:
                self.selected_file.set(path)
                self.output_file = self._expected_output(Path(path))
                self.status.set("파일 선택 완료. 필요하면 서식 설정을 확인한 뒤 정리 시작을 누르세요.")
                self.folder_button.configure(state="disabled")

        def start(self):
            src = Path(self.selected_file.get())
            if not src.exists():
                messagebox.showwarning("파일 확인", "정리할 HWP/HWPX 파일을 먼저 선택하세요.")
                return
            if self.worker and self.worker.is_alive():
                messagebox.showinfo("진행 중", "이미 작업이 진행 중입니다.")
                return
            self.output_file = self._expected_output(src)
            self.start_button.configure(state="disabled")
            self.settings_button.configure(state="disabled")
            self.folder_button.configure(state="disabled")
            self.progress.start(10)
            self.status.set("정리 작업 실행 중...")
            self._append_log(f"\n▶ 시작: {src}\n")
            self.worker = threading.Thread(target=self._run_process, args=(src,), daemon=True)
            self.worker.start()

        def _run_process(self, src: Path):
            import subprocess
            cmd = [sys.executable, str(self.script_path), str(src)]
            env = os.environ.copy()
            env["PYTHONIOENCODING"] = "utf-8"
            try:
                proc = subprocess.Popen(
                    cmd, cwd=str(self.script_path.parent),
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    text=True, encoding="utf-8", errors="replace", env=env)
                for line in proc.stdout:
                    self.log_queue.put(("log", line))
                self.log_queue.put(("done", proc.wait()))
            except Exception as exc:
                self.log_queue.put(("error", str(exc)))

        def _drain_log_queue(self):
            import queue as _q
            try:
                while True:
                    kind, payload = self.log_queue.get_nowait()
                    if kind == "log":
                        self._append_log(payload)
                    elif kind == "done":
                        self._finish(payload)
                    else:
                        self._append_log(f"\n[작업창 오류] {payload}\n")
                        self._finish(1)
            except _q.Empty:
                pass
            self.after(100, self._drain_log_queue)

        def _finish(self, code: int):
            self.progress.stop()
            self.start_button.configure(state="normal")
            self.settings_button.configure(state="normal")
            if code == 0 and self.output_file and self.output_file.exists():
                self.status.set(f"완료: {self.output_file.name}")
                self.folder_button.configure(state="normal")
                self._append_log(f"\n✅ 완료: {self.output_file}\n")
            elif code == 0:
                self.status.set("프로세스는 종료되었지만 결과 파일을 확인하지 못했습니다.")
                self._append_log("\n⚠ 결과 파일을 확인하지 못했습니다. 로그를 확인하세요.\n")
            else:
                self.status.set("작업 실패. 로그를 확인하세요.")
                self._append_log("\n❌ 작업 실패. 로그를 확인하세요.\n")

        def open_output_folder(self):
            if not self.output_file:
                return
            folder = self.output_file.parent
            if sys.platform.startswith("win"):
                os.startfile(folder)
            elif sys.platform == "darwin":
                __import__("subprocess").Popen(["open", str(folder)])
            else:
                __import__("subprocess").Popen(["xdg-open", str(folder)])

        def clear_log(self):
            self.log.delete("1.0", "end")

        def _append_log(self, text: str):
            self.log.insert("end", text)
            self.log.see("end")

        @staticmethod
        def _expected_output(src: Path) -> Path:
            return src.with_name(src.stem + "_정리본" + src.suffix)


def run_workwindow() -> None:
    """작업창(GUI)을 띄운다. tkinter가 없으면 명령줄 사용을 안내한다."""
    if not _TK_OK:
        print("GUI(작업창)를 사용할 수 없습니다(tkinter 미설치).\n"
              "  명령줄로 파일을 지정해 실행하세요:  python 표정리.py 문서.hwp")
        return
    here = Path(__file__).resolve()
    WorkWindow(here, here.parent / "서식규칙.yaml").mainloop()


def main():
    args = sys.argv[1:]
    # --보기: 한글 창을 띄운 채 실행 (숨김 모드에서 멈출 때 어떤 대화상자가
    #         떠 있는지 눈으로 확인하는 진단용)
    visible = any(a in ("--보기", "--visible") for a in args)
    args = [a for a in args if a not in ("--보기", "--visible")]

    if args:                            # 파일/폴더를 나열한 명령줄 모드
        run_cli(args, visible)
        return
    run_workwindow()                    # 인자 없음(더블클릭) → 작업창


if __name__ == "__main__":
    main()
    # 확실한 프로세스 종료. 한글 COM 서버가 응답 불능이 된 경우
    # 파이썬 종료 단계(COM 해제)에서 멈춰 프로세스가 살아남을 수 있고,
    # 그러면 작업창이 완료를 감지하지 못한 채 계속 돌게 된다.
    # 필요한 출력은 이미 끝났으므로 버퍼만 비우고 즉시 종료한다.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)
