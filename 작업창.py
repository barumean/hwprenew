# -*- coding: utf-8 -*-
"""
작업창.py — 표정리.py를 더 직관적으로 실행하기 위한 간단한 GUI 작업창

사용법
  python 작업창.py

기능
  - 버튼으로 HWP/HWPX 파일 선택
  - 작업창에서 자주 쓰는 서식 규칙 편집
  - 선택한 파일을 표정리.py에 전달해 별도 프로세스로 실행
  - 처리 로그를 창 안에 실시간 표시
  - 완료 후 결과 파일과 폴더 열기 버튼 제공
"""
import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

import yaml


APP_DIR = Path(__file__).resolve().parent
SCRIPT = APP_DIR / "표정리.py"
CONFIG = APP_DIR / "서식규칙.yaml"

LINE_TYPES = [
    "없음", "실선", "파선", "점선", "일점쇄선", "이점쇄선", "긴파선", "원형점선",
    "이중실선", "얇고굵은이중선", "굵고얇은이중선", "삼중선",
]
LINE_WIDTHS = [
    "0.1mm", "0.12mm", "0.15mm", "0.2mm", "0.25mm", "0.3mm", "0.4mm", "0.5mm",
    "0.6mm", "0.7mm", "1.0mm", "1.5mm", "2.0mm", "3.0mm", "4.0mm", "5.0mm",
]
WIDTH_VALUES = ["유지", "문서폭", "120mm", "150mm", "180mm"]
NUMBERING_VALUES = ["없음", "그림", "표", "수식"]
ON_OFF_KEEP = ["켬", "끔", "유지"]
ON_OFF = ["켬", "끔"]
CLEAR_KEEP = ["지우기", "유지"]


class RuleEditor(tk.Toplevel):
    """서식규칙.yaml의 자주 쓰는 항목을 폼으로 편집하는 설정 창."""

    def __init__(self, master, on_saved):
        super().__init__(master)
        self.title("서식 설정")
        self.geometry("720x640")
        self.minsize(640, 560)
        self.transient(master)
        self.grab_set()

        self.on_saved = on_saved
        self.rules = self._load_rules()
        self.vars = {}
        self._build_ui()

    def _load_rules(self):
        if CONFIG.exists():
            with open(CONFIG, encoding="utf-8") as f:
                return yaml.safe_load(f) or {}
        return {}

    def _build_ui(self):
        root = ttk.Frame(self, padding=14)
        root.pack(fill="both", expand=True)

        ttk.Label(root, text="서식 설정", font=("맑은 고딕", 15, "bold")).pack(anchor="w")
        ttk.Label(
            root,
            text="자주 바꾸는 항목만 보기 좋게 편집합니다. 저장하면 서식규칙.yaml에 반영됩니다.",
            foreground="#555555",
        ).pack(anchor="w", pady=(2, 10))

        notebook = ttk.Notebook(root)
        notebook.pack(fill="both", expand=True)

        self._build_table_tab(notebook)
        self._build_frame_tab(notebook)
        self._build_object_tab(notebook)
        self._build_raw_tab(notebook)

        buttons = ttk.Frame(root)
        buttons.pack(fill="x", pady=(12, 0))
        ttk.Button(buttons, text="저장", command=self.save).pack(side="right")
        ttk.Button(buttons, text="취소", command=self.destroy).pack(side="right", padx=(0, 8))

    def _build_table_tab(self, notebook):
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="표 서식")
        표 = self.rules.setdefault("표서식", {})
        바깥 = 표.setdefault("바깥선", {})
        머리글 = 표.setdefault("머리글행", {})
        본문 = 표.setdefault("본문셀", {})

        self._combo_row(tab, "표 너비", ("표서식", "너비"), 표.get("너비", "문서폭"), WIDTH_VALUES, 0,
                        "문서폭, 유지, 150mm 같은 값을 직접 입력할 수도 있습니다.")
        self._combo_row(tab, "안쪽선 종류", ("표서식", "안쪽선", "종류"), (표.get("안쪽선") or {}).get("종류", "실선"), LINE_TYPES, 1)
        self._combo_row(tab, "안쪽선 굵기", ("표서식", "안쪽선", "굵기"), (표.get("안쪽선") or {}).get("굵기", "0.12mm"), LINE_WIDTHS, 2)
        self._entry_row(tab, "안쪽선 색", ("표서식", "안쪽선", "색"), (표.get("안쪽선") or {}).get("색", "#000000"), 3)

        border_box = ttk.LabelFrame(tab, text="바깥선")
        border_box.grid(row=4, column=0, columnspan=3, sticky="ew", pady=(12, 6))
        border_box.columnconfigure(1, weight=1)
        for idx, (label, key) in enumerate((("위", "위"), ("아래", "아래"), ("왼쪽", "왼쪽"), ("오른쪽", "오른쪽"))):
            rule = 바깥.get(key) or {}
            ttk.Label(border_box, text=label).grid(row=idx, column=0, sticky="w", padx=8, pady=4)
            self._combo(border_box, ("표서식", "바깥선", key, "종류"), rule.get("종류", "실선"), LINE_TYPES, idx, 1)
            self._combo(border_box, ("표서식", "바깥선", key, "굵기"), rule.get("굵기", "0.4mm"), LINE_WIDTHS, idx, 2)
            self._entry(border_box, ("표서식", "바깥선", key, "색"), rule.get("색", "#000000"), idx, 3, width=10)

        self._combo_row(tab, "머리글 사용", ("표서식", "머리글행", "사용"), "켬" if 머리글.get("사용", True) else "끔", ON_OFF, 5)
        self._entry_row(tab, "머리글 행수", ("표서식", "머리글행", "행수"), str(머리글.get("행수", 1)), 6)
        self._entry_row(tab, "머리글 배경색", ("표서식", "머리글행", "배경색"), 머리글.get("배경색", "#CCCCCC"), 7)
        self._combo_row(tab, "본문 배경", ("표서식", "본문셀", "배경"), 본문.get("배경", "지우기"), CLEAR_KEEP, 8)
        tab.columnconfigure(1, weight=1)

    def _build_frame_tab(self, notebook):
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="그림틀")
        틀 = self.rules.setdefault("그림틀", {})
        테두리 = 틀.setdefault("테두리", {})

        self._combo_row(tab, "그림틀 너비", ("그림틀", "너비"), 틀.get("너비", "문서폭"), WIDTH_VALUES, 0)
        self._combo_row(tab, "처리", ("그림틀", "처리"), 틀.get("처리", "정리"), ["정리", "건너뛰기"], 1)
        self._combo_row(tab, "테두리 종류", ("그림틀", "테두리", "종류"), 테두리.get("종류", "실선"), LINE_TYPES, 2)
        self._combo_row(tab, "테두리 굵기", ("그림틀", "테두리", "굵기"), 테두리.get("굵기", "0.1mm"), LINE_WIDTHS, 3)
        self._entry_row(tab, "테두리 색", ("그림틀", "테두리", "색"), 테두리.get("색", "#B3B3B3"), 4)
        self._combo_row(tab, "배경", ("그림틀", "배경"), 틀.get("배경", "지우기"), CLEAR_KEEP, 5)
        self._combo_row(tab, "번호종류", ("그림틀", "번호종류"), 틀.get("번호종류", "그림"), NUMBERING_VALUES, 6)
        tab.columnconfigure(1, weight=1)

    def _build_object_tab(self, notebook):
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="사진/공통")
        사진 = self.rules.setdefault("사진", {})
        개체 = self.rules.setdefault("개체위치", {})
        스타일 = self.rules.setdefault("스타일정리", {})

        self._combo_row(tab, "사진 번호종류", ("사진", "번호종류"), 사진.get("번호종류", "없음"), NUMBERING_VALUES, 0)
        self._combo_row(tab, "사진 너비", ("사진", "너비"), 사진.get("너비", "유지"), WIDTH_VALUES, 1,
                        "사진 너비를 바꾸면 높이는 비율 유지로 자동 조절됩니다.")
        self._combo_row(tab, "글자처럼 취급", ("개체위치", "글자처럼취급"), 개체.get("글자처럼취급", "켬"), ON_OFF_KEEP, 2)
        self._combo_row(tab, "x스타일 제거", ("스타일정리", "x스타일제거"), 스타일.get("x스타일제거", "켬"), ON_OFF, 3)
        tab.columnconfigure(1, weight=1)

    def _build_raw_tab(self, notebook):
        tab = ttk.Frame(notebook, padding=12)
        notebook.add(tab, text="원본 YAML")
        ttk.Label(tab, text="고급 사용자는 아래 내용을 직접 수정할 수 있습니다.", foreground="#555555").pack(anchor="w")
        frame = ttk.Frame(tab)
        frame.pack(fill="both", expand=True, pady=(8, 0))
        self.raw_text = tk.Text(frame, wrap="none")
        scroll_y = ttk.Scrollbar(frame, orient="vertical", command=self.raw_text.yview)
        scroll_x = ttk.Scrollbar(frame, orient="horizontal", command=self.raw_text.xview)
        self.raw_text.configure(yscrollcommand=scroll_y.set, xscrollcommand=scroll_x.set)
        self.raw_text.grid(row=0, column=0, sticky="nsew")
        scroll_y.grid(row=0, column=1, sticky="ns")
        scroll_x.grid(row=1, column=0, sticky="ew")
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        self.raw_text.insert("1.0", yaml.safe_dump(self.rules, allow_unicode=True, sort_keys=False))

    def _combo_row(self, parent, label, path, value, values, row, help_text=None):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=5)
        self._combo(parent, path, value, values, row, 1)
        if help_text:
            ttk.Label(parent, text=help_text, foreground="#666666").grid(row=row, column=2, sticky="w", padx=(8, 0))

    def _entry_row(self, parent, label, path, value, row):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky="w", pady=5)
        self._entry(parent, path, value, row, 1)

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
            with open(CONFIG, "w", encoding="utf-8") as f:
                yaml.safe_dump(self.rules, f, allow_unicode=True, sort_keys=False)
        except Exception as exc:
            messagebox.showerror("설정 저장 실패", str(exc))
            return
        self.on_saved()
        messagebox.showinfo("설정 저장", "서식규칙.yaml에 저장했습니다.")
        self.destroy()

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
        except ValueError:
            raise ValueError("머리글 행수는 숫자로 입력해야 합니다.")


class WorkWindow(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("HWP 표 서식 정리 작업창")
        self.geometry("800x600")
        self.minsize(720, 520)

        self.selected_file = tk.StringVar(value="")
        self.status = tk.StringVar(value="정리할 HWP/HWPX 파일을 선택하세요.")
        self.output_file = None
        self.worker = None
        self.proc = None
        self.log_queue = queue.Queue()

        self._build_ui()
        self.after(100, self._drain_log_queue)

    def _build_ui(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)

        title = ttk.Label(root, text="HWP 표 서식 일괄정리", font=("맑은 고딕", 16, "bold"))
        title.pack(anchor="w")

        desc = ttk.Label(
            root,
            text="파일을 선택하고 [서식 설정]을 확인한 뒤 [정리 시작]을 누르면 *_정리본 파일을 생성합니다.",
        )
        desc.pack(anchor="w", pady=(4, 14))

        file_row = ttk.Frame(root)
        file_row.pack(fill="x")
        ttk.Entry(file_row, textvariable=self.selected_file).pack(side="left", fill="x", expand=True)
        ttk.Button(file_row, text="파일 선택", command=self.pick_file).pack(side="left", padx=(8, 0))

        action_row = ttk.Frame(root)
        action_row.pack(fill="x", pady=12)
        self.start_button = ttk.Button(action_row, text="정리 시작", command=self.start)
        self.start_button.pack(side="left")
        self.settings_button = ttk.Button(action_row, text="서식 설정", command=self.open_settings)
        self.settings_button.pack(side="left", padx=(8, 0))
        self.folder_button = ttk.Button(action_row, text="결과 폴더 열기", command=self.open_output_folder, state="disabled")
        self.folder_button.pack(side="left", padx=(8, 0))
        self.clear_button = ttk.Button(action_row, text="로그 지우기", command=self.clear_log)
        self.clear_button.pack(side="left", padx=(8, 0))

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

        note = ttk.Label(
            root,
            text="※ 설정은 서식규칙.yaml에 저장됩니다. 한글/pyhwpx 문제로 멈추면 `python 표정리.py --보기 문서.hwp`를 사용하세요.",
            foreground="#666666",
        )
        note.pack(anchor="w", pady=(8, 0))

    def open_settings(self):
        RuleEditor(self, on_saved=self._settings_saved)

    def _settings_saved(self):
        self.status.set("서식 설정을 저장했습니다. 정리 시작을 누르면 새 설정이 적용됩니다.")
        self._append_log("\n⚙ 서식 설정 저장 완료: 서식규칙.yaml\n")

    def pick_file(self):
        path = filedialog.askopenfilename(
            title="표 서식을 정리할 한글 문서를 선택하세요",
            filetypes=[("한글 문서", "*.hwp *.hwpx"), ("모든 파일", "*.*")],
        )
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
        cmd = [sys.executable, str(SCRIPT), str(src)]
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        try:
            self.proc = subprocess.Popen(
                cmd,
                cwd=str(APP_DIR),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
            )
            assert self.proc.stdout is not None
            for line in self.proc.stdout:
                self.log_queue.put(("log", line))
            code = self.proc.wait()
            self.log_queue.put(("done", code))
        except Exception as exc:
            self.log_queue.put(("error", str(exc)))

    def _drain_log_queue(self):
        try:
            while True:
                kind, payload = self.log_queue.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "done":
                    self._finish(payload)
                elif kind == "error":
                    self._append_log(f"\n[작업창 오류] {payload}\n")
                    self._finish(1)
        except queue.Empty:
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
            subprocess.Popen(["open", str(folder)])
        else:
            subprocess.Popen(["xdg-open", str(folder)])

    def clear_log(self):
        self.log.delete("1.0", "end")

    def _append_log(self, text: str):
        self.log.insert("end", text)
        self.log.see("end")

    @staticmethod
    def _expected_output(src: Path) -> Path:
        return src.with_name(src.stem + "_정리본" + src.suffix)


if __name__ == "__main__":
    WorkWindow().mainloop()
