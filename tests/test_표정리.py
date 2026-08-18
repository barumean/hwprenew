# -*- coding: utf-8 -*-
"""표정리.py 자동 검증 — 한글(HWP)이 없어도 돌아가는 순수 로직 전체.

실행:  python tests/test_표정리.py
(한글 COM이 필요한 부분은 Windows에서 실제 문서로 확인해야 합니다.)
"""
import contextlib
import importlib.util
import io
import shutil
import sys
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("표정리", ROOT / "표정리.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

import yaml                                                    # noqa: E402

HP = "http://www.hancom.co.kr/hwpml/2011/paragraph"
_ok = _fail = 0


def check(name, cond):
    global _ok, _fail
    if cond:
        _ok += 1
        print(f"  ok  {name}")
    else:
        _fail += 1
        print(f"  FAIL {name}")


def raises(name, fn, 조각=None):
    """규칙오류가 나야 하는 경우. 조각이 주어지면 메시지에 포함되는지도 확인."""
    try:
        fn()
    except m.규칙오류 as e:
        check(name, 조각 is None or 조각 in str(e))
    except Exception as e:                                      # 다른 예외는 실패
        check(name, False) or print(f"       (기대: 규칙오류, 실제: {type(e).__name__})")
    else:
        check(name, False)


def load_yaml(text):
    p = Path(tempfile.mktemp(suffix=".yaml"))
    p.write_text(text, encoding="utf-8")
    try:
        return m.load_rules(p)
    finally:
        p.unlink()


def make_hwpx(path, header, section):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("Contents/header.xml", header)
        z.writestr("Contents/section0.xml", section)


def cell(r, c, bf=None, inner=""):
    ref = f' borderFillIDRef="{bf}"' if bf else ""
    return (f'<hp:tc{ref}><hp:cellAddr rowAddr="{r}" colAddr="{c}"/>'
            f'<hp:cellSpan rowSpan="1" colSpan="1"/>{inner}</hp:tc>')


# ══════════════════════════════════════════════════════════════
print("\n[1] 색·단위 변환")
check("#FF0000 → BGR 0x0000FF", m.hex_to_hwp_color("#FF0000") == 0x0000FF)
check("#0000FF → BGR 0xFF0000", m.hex_to_hwp_color("#0000FF") == 0xFF0000)
check("mm_to_hu(25.4) == 7200", m.mm_to_hu(25.4) == 7200)
raises("잘못된 색은 규칙오류", lambda: m.hex_to_hwp_color("검정"), "색 값")
raises("색 오류에 항목 이름 표시", lambda: m.hex_to_hwp_color("xx", "안쪽선"), "[안쪽선]")

# ══════════════════════════════════════════════════════════════
print("\n[2] 서식규칙.yaml 파싱")
rules = m.load_rules(ROOT / "서식규칙.yaml")
check("저장소 규칙: 바깥선 위 = 실선 0.4mm 검정",
      rules["outer"]["Top"] == {"type": 1, "width": 6, "color": 0})
check("저장소 규칙: 바깥선 왼쪽 = 없음", rules["outer"]["Left"]["type"] == 0)
check("저장소 규칙: 머리글 1행 + 배경 #CCCCCC",
      rules["header_rows"] == 1 and rules["header_fill"] == 0xCCCCCC)
check("저장소 규칙: 머리글 아래선 = 이중실선 0.5mm",
      rules["header_bottom"] == {"type": 8, "width": 7, "color": 0})
check("저장소 규칙: 표 너비 = 문서폭", rules["table_width"] == {"mode": "doc_width"})
check("저장소 규칙: 그림틀 테두리색 #B3B3B3", rules["frame"]["border"]["color"] == 0xB3B3B3)
check("저장소 규칙: 사진 번호종류 없음", rules["photo_numbering"] == 0)
check("저장소 규칙: 글자처럼취급 켬", rules["treat_as_char"] == 1)
check("저장소 규칙: x스타일 제거 켬", rules["remove_x_styles"] is True)

BASE = "표서식:\n  바깥선:\n    왼쪽: {%s}\n"
check("선 종류 내부코드(0) → 없음으로 읽힘",
      load_yaml(BASE % "종류: 0")["outer"]["Left"]["type"] == 0)
check("없음이면 잘못된 굵기·색도 무시",
      load_yaml(BASE % "종류: 없음, 굵기: 0, 색: 0")["outer"]["Left"]["type"] == 0)
check("한 줄 표기(왼쪽: 없음) 허용",
      load_yaml("표서식:\n  바깥선:\n    왼쪽: 없음\n")["outer"]["Left"]["type"] == 0)
check("굵기 0.4 (mm 생략) 허용",
      load_yaml(BASE % "종류: 실선, 굵기: 0.4, 색: '#000000'")["outer"]["Left"]["width"] == 6)
check("굵기 '0.4 mm' (공백) 허용",
      load_yaml(BASE % "종류: 실선, 굵기: '0.4 mm', 색: '#000000'")["outer"]["Left"]["width"] == 6)
check("굵기 내부코드(6) → 0.4mm",
      load_yaml(BASE % "종류: 실선, 굵기: 6, 색: '#000000'")["outer"]["Left"]["width"] == 6)
check("굵기 1 은 mm 우선(1.0mm)",
      load_yaml(BASE % "종류: 실선, 굵기: 1, 색: '#000000'")["outer"]["Left"]["width"] == 10)
raises("없는 선 종류", lambda: load_yaml(BASE % "종류: 점선선"), "선 종류")
raises("없는 선 굵기(단위 명시)", lambda: load_yaml(BASE % "종류: 실선, 굵기: 9mm"), "선 굵기")
raises("오류에 파일 경로 안내", lambda: load_yaml(BASE % "종류: 점선선"), "파일:")
raises("번호종류 오타", lambda: load_yaml("사진:\n  번호종류: 그림표\n"), "번호종류")
raises("글자처럼취급 오타", lambda: load_yaml("개체위치:\n  글자처럼취급: 예\n"), "글자처럼취급")
raises("너비 오타", lambda: load_yaml("표서식:\n  너비: 15cm\n"), "너비 값")
raises("머리글 행수 오타",
       lambda: load_yaml("표서식:\n  머리글행: {사용: true, 행수: 두줄}\n"), "행수")
raises("규칙 파일 없음", lambda: m.load_rules(Path("/없는경로/서식규칙.yaml")), "찾을 수 없")
check("빈 파일은 기본값으로 통과", load_yaml("")["outer"]["Top"]["type"] == 1)
raises("최상위가 목록이면 오류", lambda: load_yaml("- a\n- b\n"), "형식")

# ══════════════════════════════════════════════════════════════
print("\n[3] 잘못된 값 자동 복구 (설정 창에서 열면 표준값으로)")
bad = {"표서식": {"바깥선": {"왼쪽": {"종류": 0, "굵기": 0, "색": 0},
                          "위": {"종류": 1, "굵기": 6, "색": "#000000"}},
                "안쪽선": {"종류": "실선", "굵기": "0.12", "색": "엉망"}}}
m.canonicalize_line_values(bad)
check("왼쪽 → {종류: 없음} (굵기·색 제거)", bad["표서식"]["바깥선"]["왼쪽"] == {"종류": "없음"})
check("위 → 실선 0.4mm", bad["표서식"]["바깥선"]["위"]["종류"] == "실선"
      and bad["표서식"]["바깥선"]["위"]["굵기"] == "0.4mm")
check("안쪽선 색 → #000000", bad["표서식"]["안쪽선"]["색"] == "#000000")
check("안쪽선 굵기 → 0.12mm", bad["표서식"]["안쪽선"]["굵기"] == "0.12mm")
check("복구 후 파싱 통과", m.parse_rules(bad)["outer"]["Left"]["type"] == 0)

# ══════════════════════════════════════════════════════════════
print("\n[4] 셀 서식 계산 (cell_plan)")


class Spec3x3:
    n_rows = n_cols = 3
    is_1x1 = False
    cells = {(r, c): (1, 1) for r in range(3) for c in range(3)}


s = Spec3x3()
sides, fill = m.cell_plan(s, (0, 0), rules)
check("머리글 셀: 위=바깥선, 아래=머리글 구분선, 배경=머리글색",
      sides["Top"] == rules["outer"]["Top"] and sides["Bottom"] == rules["header_bottom"]
      and fill == rules["header_fill"])
sides, fill = m.cell_plan(s, (1, 1), rules)
check("본문 첫 행: 위=머리글 구분선, 배경 지우기",
      sides["Top"] == rules["header_bottom"] and fill == "clear")
sides, _ = m.cell_plan(s, (2, 2), rules)
check("우하단 셀: 아래·오른쪽 = 바깥선",
      sides["Bottom"] == rules["outer"]["Bottom"] and sides["Right"] == rules["outer"]["Right"])


class Spec1xN:
    n_rows, n_cols = 1, 3
    is_1x1 = False
    cells = {(0, c): (1, 1) for c in range(3)}


sides, fill = m.cell_plan(Spec1xN(), (0, 1), rules)
check("1행짜리 표는 머리글 처리 안 함(배경 지우기)", fill == "clear")
check("1행짜리 표 아래 = 바깥선", sides["Bottom"] == rules["outer"]["Bottom"])

# ══════════════════════════════════════════════════════════════
print("\n[5] 왼쪽 테두리 색 후처리 (한글 COM 버그 우회)")
hdr = ('<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">'
       '<hh:borderFill id="3"><hh:leftBorder type="SOLID" color="#FF0000"/></hh:borderFill>'
       '<hh:borderFill id="4"><hh:leftBorder type="SOLID"/></hh:borderFill></hh:head>')
p = Path(tempfile.mktemp(suffix=".hwpx"))
make_hwpx(p, hdr, "<x/>")
m.patch_left_colors(p, {"3": "#000000", "4": "#B3B3B3"})
with zipfile.ZipFile(p) as z:
    out = z.read("Contents/header.xml").decode("utf-8")
p.unlink()
check("기존 color 교체", 'id="3"><hh:leftBorder type="SOLID" color="#000000"/>' in out)
check("color 속성이 없으면 추가", 'color="#B3B3B3"' in out)

sec = (f'<hp:sec xmlns:hp="{HP}">'
       f'<hp:tbl rowCnt="2" colCnt="2"><hp:tr>{cell(0,0,"5")}{cell(0,1,"9")}</hp:tr>'
       f'<hp:tr>{cell(1,0,"9")}{cell(1,1,"9")}</hp:tr></hp:tbl>'
       f'<hp:tbl rowCnt="2" colCnt="2"><hp:tr>{cell(0,0,"6")}{cell(0,1,"9")}</hp:tr>'
       f'<hp:tr>{cell(1,0,"9")}{cell(1,1,"9")}</hp:tr></hp:tbl></hp:sec>')
hdr3 = ('<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">'
        '<hh:borderFill id="5"><hh:leftBorder color="#FF0000"/></hh:borderFill>'
        '<hh:borderFill id="6"><hh:leftBorder color="#FF0000"/></hh:borderFill>'
        '<hh:borderFill id="9"><hh:leftBorder color="#000000"/></hh:borderFill></hh:head>')
p3 = Path(tempfile.mktemp(suffix=".hwpx"))
make_hwpx(p3, hdr3, sec)
r2 = dict(rules)
r2["outer"] = dict(rules["outer"])
r2["outer"]["Left"] = {"type": 1, "width": 1, "color": 0}      # 왼쪽에 선이 있는 규칙
check("보정 대상 산출", m.compute_left_patches(p3, r2) == {"5": "#000000", "6": "#000000"})
check("실패한 표는 보정 제외", m.compute_left_patches(p3, r2, {1}) == {"5": "#000000"})
p3.unlink()

# ══════════════════════════════════════════════════════════════
print("\n[6] 엑셀 잔재(x) 스타일 제거")
hdr2 = ('<hh:head xmlns:hh="http://www.hancom.co.kr/hwpml/2011/head">'
        '<hh:styles itemCnt="4">'
        '<hh:style id="0" name="바탕글" nextStyleIDRef="0"/>'
        '<hh:style id="1" name="xl24" nextStyleIDRef="1"/>'
        '<hh:style id="2" name="본문" nextStyleIDRef="2"><hh:sub/></hh:style>'
        '<hh:style id="3" name="개요" nextStyleIDRef="1"/></hh:styles></hh:head>')
p2 = Path(tempfile.mktemp(suffix=".hwpx"))
make_hwpx(p2, hdr2, '<s><p styleIDRef="1"/><p styleIDRef="2"/><p styleIDRef="3"/></s>')
removed = m.remove_x_styles_in_hwpx(p2)
with zipfile.ZipFile(p2) as z:
    h = z.read("Contents/header.xml").decode("utf-8")
    s2 = z.read("Contents/section0.xml").decode("utf-8")
p2.unlink()
check("x 스타일만 제거", removed == ["xl24"])
check("자식 있는 스타일 태그 보존", 'name="본문"' in h and "<hh:sub/>" in h)
check("남은 스타일 재번호", 'id="1" name="본문"' in h and 'id="2" name="개요"' in h)
check("itemCnt 갱신", 'itemCnt="3"' in h)
check("삭제된 스타일 참조 → 바탕글(0)", 'styleIDRef="0"' in s2 and 'styleIDRef="3"' not in s2)
check("삭제분을 가리키던 nextStyleIDRef → 자기 자신",
      'id="2" name="개요" nextStyleIDRef="2"' in h)

# ══════════════════════════════════════════════════════════════
print("\n[7] 표 너비 실측 검증 (report_widths)")
nested = (f'<hp:tbl rowCnt="2" colCnt="1"><hp:sz width="10000"/>'
          f'<hp:tr>{cell(0,0)}</hp:tr><hp:tr>{cell(1,0)}</hp:tr></hp:tbl>')
sec4 = (f'<hp:sec xmlns:hp="{HP}">'
        f'<hp:tbl rowCnt="2" colCnt="1"><hp:sz width="47900"/>'
        f'<hp:outMargin left="283" right="283"/>'
        f'<hp:tr>{cell(0,0)}</hp:tr><hp:tr>{cell(1,0)}</hp:tr></hp:tbl>'
        f'<hp:tbl rowCnt="1" colCnt="1"><hp:sz width="20000"/>'
        f'<hp:tr>{cell(0,0, inner=nested)}</hp:tr></hp:tbl></hp:sec>')
sect = ET.fromstring(sec4)
tbls = list(sect.iter(f"{{{HP}}}tbl"))
nested_ids = m.nested_tbl_ids(sect)
check("중첩 표만 중첩으로 식별",
      len(tbls) == 3 and id(tbls[2]) in nested_ids and id(tbls[0]) not in nested_ids)

p4 = Path(tempfile.mktemp(suffix=".hwpx"))
make_hwpx(p4, hdr3, sec4)
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    m.report_widths(p4, 48466, 20000, table_fit=True, frame_skip=True)
check("바깥여백 보정 + 그림틀 건너뜀 + 중첩 제외 → 1개 모두 일치",
      "표 1개 모두 목표 너비와 일치" in buf.getvalue())
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    m.report_widths(p4, 30000, None, table_fit=False)
check("불일치는 실측값과 함께 경고", "너비 불일치" in buf.getvalue() and "105.8mm" in buf.getvalue())
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    # 0번 표를 실패로 제외 → 그림틀 1개만 검사 대상으로 남는다
    m.report_widths(p4, 48466, 20000, table_fit=True, skip_idx={0})
check("실패한 표는 검증에서 제외", "표 1개 모두 목표 너비와 일치" in buf.getvalue())
buf = io.StringIO()
with contextlib.redirect_stdout(buf):
    m.report_widths(p4, None, None)
check("목표가 없으면 아무것도 출력 안 함", buf.getvalue() == "")
p4.unlink()

# ══════════════════════════════════════════════════════════════
print("\n[8] 처리 대상 수집 (파일·폴더)")
tdir = Path(tempfile.mkdtemp())
for name in ("a.hwp", "b.HWPX", "c_정리본.hwp", "_표정리_후처리.hwpx", "d.txt"):
    (tdir / name).write_text("")
(tdir / "sub").mkdir()
(tdir / "sub" / "e.hwp").write_text("")
check("폴더: hwp/hwpx만, 정리본·임시·하위폴더 제외",
      [f.name for f in m.collect_targets([tdir])] == ["a.hwp", "b.HWPX"])
check("직접 지정한 정리본은 존중 + 중복 제거",
      [f.name for f in m.collect_targets([tdir / "c_정리본.hwp", tdir, tdir / "a.hwp"])]
      == ["c_정리본.hwp", "a.hwp", "b.HWPX"])

# ══════════════════════════════════════════════════════════════
print("\n[9] 여러 문서 일괄 처리 (run_batch)")
calls = []


def fake_process(src, visible=False):
    calls.append(src.name)
    if src.name == "b.HWPX":
        raise RuntimeError("일부러 실패")
    return src


real_process = m.process
m.process = fake_process
try:
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        okc, failc = m.run_batch([tdir / "a.hwp", tdir / "b.HWPX", tdir / "없는파일.hwp"])
    check("성공/실패 집계", (okc, failc) == (1, 2))
    check("한 파일이 실패해도 계속 진행", calls == ["a.hwp", "b.HWPX"])
    check("배치 요약 출력", "성공 1개 / 실패 2개" in buf.getvalue())

    m.process = lambda src, visible=False: (_ for _ in ()).throw(m.규칙오류("규칙 문제"))
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            m.run_batch([tdir / "a.hwp", tdir / "b.HWPX"])
        check("규칙오류는 즉시 중단", False)
    except m.규칙오류:
        check("규칙오류는 즉시 중단(모든 문서 공통이므로)", True)
finally:
    m.process = real_process
    shutil.rmtree(tdir)

# ══════════════════════════════════════════════════════════════
print(f"\n{'─' * 50}")
print(f"통과 {_ok}건 / 실패 {_fail}건")
sys.exit(1 if _fail else 0)
