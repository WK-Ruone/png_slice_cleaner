#!/usr/bin/env python3
"""Manual PNG slice-folder review and cleanup tool."""

from __future__ import annotations

import argparse
import csv
import io
import os
import re
import shutil
from datetime import datetime
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


DEFAULT_ROOT = Path(r"D:\SIAT\dataset\华西医院0603_PNG\华西医院0603_PNG")
IMAGE_EXTENSIONS = {".png", ".gif", ".ppm", ".pgm"}
STATE_FILENAME = ".png_slice_cleaner_state"
QUICK_PRELOAD_PATIENTS = 5
RECORD_FIELDS = ["病人ID", "问题描述", "更新时间"]
ISSUE_LABELS = {
    "brightness_inconsistent": "亮度不统一",
    "incomplete": "图像不完整",
    "blurry": "图像不清晰",
}


def natural_key(path: Path) -> list[object]:
    return [int(part) if part.isdigit() else part.lower() for part in re.split(r"(\d+)", path.name)]


def image_files(folder: Path) -> list[Path]:
    return sorted(
        (path for path in folder.iterdir() if path.is_file() and path.suffix.lower() in IMAGE_EXTENSIONS),
        key=natural_key,
    )


def load_photo(path: Path) -> tk.PhotoImage | None:
    try:
        return tk.PhotoImage(file=path)
    except tk.TclError:
        return None


def slice_folders(patient: Path) -> list[Path]:
    return sorted(
        (path for path in patient.iterdir() if path.is_dir() and path.name.lower().startswith("slice")),
        key=natural_key,
    )


def patient_folders(data_root: Path) -> list[Path]:
    return sorted((path for path in data_root.iterdir() if path.is_dir() and not path.name.startswith(".")), key=natural_key)


def last_patient_index(path: Path, patients: list[Path]) -> int:
    try:
        patient_id = path.read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError):
        return 0
    return next((index for index, patient in enumerate(patients) if patient.name == patient_id), 0)


def load_records(path: Path) -> dict[str, dict[str, str]]:
    if not path.exists():
        return {}
    raw = path.read_bytes()
    text = None
    for encoding in ("utf-8-sig", "gb18030", "utf-16"):
        try:
            text = raw.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        return {}
    records = {}
    for row in csv.DictReader(io.StringIO(text, newline="")):
        if row.get("病人ID"):
            record = {"patient_id": row["病人ID"], "updated_at": row.get("更新时间", "")}
            custom = []
            for issue in filter(None, (part.strip() for part in re.split(r"[；;]", row.get("问题描述", "")))):
                field = next((key for key, label in ISSUE_LABELS.items() if label == issue), None)
                if field:
                    record[field] = "1"
                else:
                    custom.append(issue)
            record["custom_issue"] = "；".join(custom)
            if record_has_issue(record):
                records[record["patient_id"]] = record
            continue
        if row.get("color_issue") == "1":
            row["brightness_inconsistent"] = "1"
        if row.get("patient_id") and record_has_issue(row):
            records[row["patient_id"]] = row
    return records


def record_has_issue(record: dict[str, str]) -> bool:
    return any(record.get(field) == "1" for field in ("brightness_inconsistent", "color_issue", "incomplete", "blurry")) or bool(
        record.get("custom_issue", "").strip()
    )


def sort_patients_by_issue(
    patients: list[Path], records: dict[str, dict[str, str]], issues_first: bool
) -> list[Path]:
    return sorted(
        patients,
        key=lambda patient: (
            0 if record_has_issue(records.get(patient.name, {})) == issues_first else 1,
            natural_key(patient),
        ),
    )


def record_signature(record: dict[str, str]) -> tuple[object, ...]:
    return tuple(record.get(field) == "1" for field in ISSUE_LABELS) + (record.get("custom_issue", "").strip(),)


def write_records(path: Path, records: dict[str, dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=RECORD_FIELDS, extrasaction="ignore")
        writer.writeheader()
        for patient_id in sorted(records):
            record = records[patient_id]
            if record_has_issue(record):
                issues = [label for field, label in ISSUE_LABELS.items() if record.get(field) == "1"]
                if record.get("custom_issue", "").strip():
                    issues.append(record["custom_issue"].strip())
                writer.writerow(
                    {
                        "病人ID": patient_id,
                        "问题描述": "；".join(issues),
                        "更新时间": record.get("updated_at", ""),
                    }
                )


class SliceCleaner:
    FRAME_MS = 40

    def __init__(self, root: tk.Tk, data_root: Path, records_path: Path):
        self.root = root
        self.data_root = data_root
        self.records_path = records_path
        self.records = load_records(records_path)
        self.records_dirty = False
        self.record_warning_shown = False
        self.state_path = data_root / STATE_FILENAME
        self.trash_root = data_root / ".slice_cleaner_trash"
        self.undo_stack: list[list[tuple[Path, Path]]] = []
        self.patients = patient_folders(data_root)
        self.patient_index = -1
        self.current_slices: list[Path] = []
        self.selected_slice: Path | None = None
        self.delete_vars: dict[Path, tk.BooleanVar] = {}
        self.preview_files: dict[Path, list[Path]] = {}
        self.preview_images: dict[Path, tk.PhotoImage] = {}
        self.preview_frames: dict[Path, list[tk.PhotoImage]] = {}
        self.preview_labels: dict[Path, list[ttk.Label]] = {}
        self.frame_cache: dict[str, dict[Path, list[tk.PhotoImage]]] = {}
        self.frame_cache_order: list[str] = []
        self.frame_cache_complete: set[str] = set()
        self.frame_cache_limit = 2
        self._image_files_cache: dict[Path, list[Path]] = {}
        self._cell_state: dict[Path, bool] = {}
        self._preload_after_id: str | None = None
        self._load_remaining_after_id: str | None = None
        self._overview_after_id: str | None = None
        self.frame_index = 0
        self.playing = True
        self.after_id: str | None = None
        self.detail_refs: list[tk.PhotoImage] = []
        self.overview_refs: list[tk.PhotoImage] = []
        self.overview_cells: dict[Path, tk.Frame] = {}
        self.overview_original_size = tk.BooleanVar(value=False)
        self.loading_mode = tk.StringVar(value="clean")
        self.issue_sort_issues_first: bool | None = None
        self.shortcut_selection_history: dict[bool, list[Path]] = {False: [], True: []}
        self.drag_start: Path | None = None
        self.drag_origin = (0, 0)
        self.drag_active = False
        self._navigation_bindtag = f"SliceCleanerNavigation{id(self)}"
        self.custom_placeholder = "输入自定义问题"
        self.custom_placeholder_visible = False

        root.title("PNG Slice 清洗")
        root.geometry("1420x940")
        root.minsize(1050, 720)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self._build_ui()
        self._refresh_table()
        if self.patients:
            self.show_patient(last_patient_index(self.state_path, self.patients))
        else:
            messagebox.showwarning("没有数据", f"未找到病人文件夹：\n{data_root}")

    def _build_ui(self) -> None:
        toolbar = ttk.Frame(self.root, padding=(8, 6))
        toolbar.pack(fill=tk.X)
        ttk.Button(toolbar, text="选择数据目录", command=self.choose_root).pack(side=tk.LEFT)
        mode = ttk.Frame(toolbar)
        mode.pack(side=tk.LEFT, padx=(12, 2))
        ttk.Label(mode, text="模式：").pack(side=tk.LEFT)
        ttk.Radiobutton(
            mode, text="数据清洗", value="clean", variable=self.loading_mode, command=self._change_loading_mode
        ).pack(side=tk.LEFT)
        ttk.Radiobutton(
            mode, text="快速浏览", value="quick", variable=self.loading_mode, command=self._change_loading_mode
        ).pack(side=tk.LEFT)
        ttk.Button(toolbar, text="◀ (Tab)", command=lambda: self.change_patient(-1)).pack(side=tk.LEFT, padx=(12, 2))
        ttk.Button(toolbar, text="▶ (空格)", command=lambda: self.change_patient(1)).pack(side=tk.LEFT, padx=2)
        self.patient_label = ttk.Label(toolbar, font=("Microsoft YaHei UI", 11, "bold"))
        self.patient_label.pack(side=tk.LEFT, padx=12)
        self.position_label = ttk.Label(toolbar)
        self.position_label.pack(side=tk.LEFT)
        ttk.Button(toolbar, text="暂停/播放", command=self.toggle_play).pack(side=tk.RIGHT)

        body = ttk.PanedWindow(self.root, orient=tk.HORIZONTAL)
        body.pack(fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))
        review = ttk.Frame(body)
        side = ttk.Frame(body, width=330)
        body.add(review, weight=5)
        body.add(side, weight=1)

        review_canvas = tk.Canvas(review, highlightthickness=0, width=1)
        review_scroll = ttk.Scrollbar(review, orient=tk.VERTICAL, command=review_canvas.yview)
        review_canvas.configure(yscrollcommand=review_scroll.set)
        review_scroll.pack(side=tk.RIGHT, fill=tk.Y)
        review_canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        review_inner = ttk.Frame(review_canvas)
        review_window = review_canvas.create_window((0, 0), window=review_inner, anchor=tk.NW)
        review_inner.bind(
            "<Configure>", lambda _e: review_canvas.configure(scrollregion=review_canvas.bbox("all"))
        )
        review_canvas.bind(
            "<Configure>", lambda e: review_canvas.itemconfigure(review_window, width=max(1, e.width))
        )
        self.review_canvas = review_canvas

        self.preview_rows: list[list[ttk.Label]] = []
        for title in ("正向：slice 小 → 大 [1 选择 / 3 取消]", "逆向：slice 大 → 小 [2 选择 / 4 取消]"):
            group = ttk.LabelFrame(review_inner, text=title, padding=5)
            group.pack(fill=tk.X, pady=(0, 4))
            labels: list[ttk.Label] = []
            for column in range(5):
                cell = tk.Frame(group, width=185, height=235, highlightthickness=2, highlightbackground="#b8bcc2")
                cell.grid(row=0, column=column, sticky="nsew", padx=3)
                cell.grid_propagate(False)
                label = ttk.Label(cell, anchor=tk.CENTER, compound=tk.TOP)
                label.pack(fill=tk.BOTH, expand=True)
                check = ttk.Checkbutton(cell, takefocus=False, state=tk.DISABLED)
                label.delete_check = check  # type: ignore[attr-defined]
                labels.append(label)
                group.columnconfigure(column, weight=1, uniform="preview")
            self.preview_rows.append(labels)

        overview_group = ttk.LabelFrame(review_inner, text="所有 slice 首帧亮度对比", padding=5)
        overview_group.pack(fill=tk.X, pady=(0, 4))
        ttk.Checkbutton(
            overview_group,
            text="原图大小",
            variable=self.overview_original_size,
            command=self._toggle_overview_size,
        ).pack(anchor=tk.W)
        self.overview_canvas = tk.Canvas(overview_group, height=100, highlightthickness=0)
        overview_scroll = ttk.Scrollbar(overview_group, orient=tk.HORIZONTAL, command=self.overview_canvas.xview)
        self.overview_canvas.configure(xscrollcommand=overview_scroll.set)
        self.overview_canvas.pack(fill=tk.X)
        overview_scroll.pack(fill=tk.X)
        self.overview_frame = ttk.Frame(self.overview_canvas)
        self.overview_window = self.overview_canvas.create_window((0, 0), window=self.overview_frame, anchor=tk.NW)
        self.overview_frame.bind(
            "<Configure>", lambda _event: self.overview_canvas.configure(scrollregion=self.overview_canvas.bbox("all"))
        )
        self.overview_canvas.bind(
            "<Configure>", lambda event: self.overview_canvas.itemconfigure(self.overview_window, height=max(1, event.height - 2))
        )

        detail_group = ttk.LabelFrame(review_inner, text="选中 slice 的全部单帧", padding=5)
        detail_group.pack(fill=tk.BOTH, expand=True)
        self.detail_canvas = tk.Canvas(detail_group, height=230, highlightthickness=0)
        detail_scroll = ttk.Scrollbar(detail_group, orient=tk.HORIZONTAL, command=self.detail_canvas.xview)
        self.detail_canvas.configure(xscrollcommand=detail_scroll.set)
        self.detail_canvas.pack(fill=tk.BOTH, expand=True)
        detail_scroll.pack(fill=tk.X)
        self.detail_frame = ttk.Frame(self.detail_canvas)
        self.detail_window = self.detail_canvas.create_window((0, 0), window=self.detail_frame, anchor=tk.NW)
        self.detail_frame.bind("<Configure>", self._update_detail_scroll)
        self.detail_canvas.bind("<Configure>", self._resize_detail_height)

        review_canvas.bind("<Enter>", lambda _e: review_canvas.bind_all("<MouseWheel>", self._on_review_wheel))
        review_canvas.bind("<Leave>", lambda _e: review_canvas.unbind_all("<MouseWheel>"))

        quality = ttk.LabelFrame(side, text="病人质量记录", padding=10)
        quality.pack(fill=tk.X)
        self.flags = {
            "brightness_inconsistent": tk.BooleanVar(),
            "incomplete": tk.BooleanVar(),
            "blurry": tk.BooleanVar(),
        }
        for field, text in (
            ("brightness_inconsistent", "亮度不统一 (Q)"),
            ("incomplete", "图像不完整 (W)"),
            ("blurry", "图像不清晰 (E)"),
        ):
            ttk.Checkbutton(quality, text=text, variable=self.flags[field], command=self.save_current).pack(anchor=tk.W, pady=3)
        ttk.Label(quality, text="自定义问题").pack(anchor=tk.W, pady=(8, 2))
        self.custom_text = tk.Text(quality, width=30, height=5, wrap=tk.WORD)
        self.custom_text.pack(fill=tk.X)
        self.custom_text_default_fg = self.custom_text.cget("fg")
        self.custom_text.bind("<FocusIn>", self._custom_focus_in)
        self.custom_text.bind("<FocusOut>", self._custom_focus_out)
        ttk.Button(quality, text="保存记录 (Ctrl+S)", command=self.save_current).pack(fill=tk.X, pady=(8, 3))
        ttk.Button(quality, text="打开 CSV (Ctrl+A)", command=self.open_records).pack(fill=tk.X, pady=(0, 3))
        ttk.Button(quality, text="打开 JSON (Ctrl+F)", command=self.open_current_json).pack(fill=tk.X, pady=(0, 3))
        ttk.Button(quality, text="清除当前记录 (Ctrl+X)", command=self._clear_current_record).pack(fill=tk.X, pady=(0, 3))

        actions = ttk.LabelFrame(side, text="Slice 操作", padding=10)
        actions.pack(fill=tk.X, pady=8)
        self.selected_label = ttk.Label(actions, text="已勾选 0 个", anchor=tk.CENTER, wraplength=290)
        self.selected_label.pack(fill=tk.X, pady=(0, 6))
        action_buttons = ttk.Frame(actions)
        action_buttons.pack(fill=tk.X)
        self.delete_button = ttk.Button(action_buttons, text="删除所选 (0) [D/Ctrl+D]", command=self.delete_selected, state=tk.DISABLED)
        self.delete_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 3))
        self.undo_button = ttk.Button(action_buttons, text="撤销删除 [Z/Ctrl+Z]", command=self.undo_delete, state=tk.DISABLED)
        self.undo_button.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(3, 0))

        table_group = ttk.LabelFrame(side, text="清洗记录表", padding=5)
        table_group.pack(fill=tk.BOTH, expand=True)
        self.table = ttk.Treeview(table_group, columns=("issues",), show="tree headings", height=12)
        self.table.heading("#0", text="编号 / 病人 ID", command=self._sort_by_patient_id)
        self.table.heading("issues", text="问题", command=self._sort_by_issues)
        self.table.column("#0", width=185, stretch=True)
        self.table.column("issues", width=45, anchor=tk.CENTER, stretch=False)
        scrollbar = ttk.Scrollbar(table_group, orient=tk.VERTICAL, command=self.table.yview)
        self.table.configure(yscrollcommand=scrollbar.set)
        self.table.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.table.bind("<<TreeviewSelect>>", self._table_select)
        self.table.bind("<Double-1>", self._table_double_click)

        self.root.bind("<Left>", lambda _event: self.change_patient(-1))
        self.root.bind("<Right>", lambda _event: self.change_patient(1))
        self.root.bind_class(self._navigation_bindtag, "<space>", self._next_patient_next)
        self.root.bind_class(self._navigation_bindtag, "<Tab>", self._next_patient_previous)
        self.root.bind_class(self._navigation_bindtag, "<KeyPress-1>", lambda event: self._select_next_shortcut(event, False))
        self.root.bind_class(self._navigation_bindtag, "<KeyPress-2>", lambda event: self._select_next_shortcut(event, True))
        self.root.bind_class(self._navigation_bindtag, "<KeyPress-3>", lambda event: self._clear_last_shortcut(event, False))
        self.root.bind_class(self._navigation_bindtag, "<KeyPress-4>", lambda event: self._clear_last_shortcut(event, True))
        self.root.bind_class(
            self._navigation_bindtag,
            "<q>",
            lambda event: self._select_quality_shortcut(event, "brightness_inconsistent"),
        )
        self.root.bind_class(
            self._navigation_bindtag,
            "<w>",
            lambda event: self._select_quality_shortcut(event, "incomplete"),
        )
        self.root.bind_class(
            self._navigation_bindtag,
            "<e>",
            lambda event: self._select_quality_shortcut(event, "blurry"),
        )
        self.root.bind_class(self._navigation_bindtag, "<Control-s>", self._save_record_shortcut)
        self.root.bind_class(self._navigation_bindtag, "<Control-a>", self._open_records_shortcut)
        self.root.bind_class(self._navigation_bindtag, "<Control-f>", self._open_json_shortcut)
        self.root.bind_class(self._navigation_bindtag, "<Control-x>", self._clear_record_shortcut)
        self.root.bind("<Control-d>", self._delete_shortcut)
        self.root.bind("<Control-z>", self._undo_shortcut)
        self.root.bind("<d>", self._delete_shortcut)
        self.root.bind("<z>", self._undo_shortcut)
        self._install_navigation_bindtag(self.root)
        self.root.bind_all("<ButtonRelease-1>", self._release_control_focus, add="+")

    def choose_root(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.data_root, title="选择病人数据根目录")
        if chosen:
            if not self.save_current():
                return
            self.data_root = Path(chosen)
            self.records_path = self.data_root / "清洗记录.csv"
            self.records = load_records(self.records_path)
            self.records_dirty = False
            self.record_warning_shown = False
            self.state_path = self.data_root / STATE_FILENAME
            self.trash_root = self.data_root / ".slice_cleaner_trash"
            self.undo_stack.clear()
            self._update_undo_button()
            self.frame_cache.clear()
            self.frame_cache_order.clear()
            self.frame_cache_complete.clear()
            self._image_files_cache.clear()
            self.patients = patient_folders(self.data_root)
            self.issue_sort_issues_first = None
            self.table.heading("issues", text="问题")
            self._refresh_table()
            self.show_patient(last_patient_index(self.state_path, self.patients))

    def show_patient(self, index: int) -> None:
        if not self.patients:
            return
        if self._overview_after_id:
            self.root.after_cancel(self._overview_after_id)
            self._overview_after_id = None
        if self._load_remaining_after_id:
            self.root.after_cancel(self._load_remaining_after_id)
            self._load_remaining_after_id = None
        if self.patient_index >= 0:
            self.save_current()
        self.patient_index = max(0, min(index, len(self.patients) - 1))
        patient = self.patients[self.patient_index]
        try:
            self.state_path.write_text(patient.name, encoding="utf-8")
        except OSError:
            pass
        self.current_slices = slice_folders(patient)
        self.selected_slice = None
        self.shortcut_selection_history = {False: [], True: []}
        self.delete_vars = {folder: tk.BooleanVar(self.root) for folder in self.current_slices}
        for row in self.preview_rows:
            for label in row:
                label.configure(image="")
                label.master.configure(highlightbackground="#b8bcc2")
                label.delete_check.configure(state=tk.DISABLED)  # type: ignore[attr-defined]
                label.delete_check.place_forget()  # type: ignore[attr-defined]
        self.preview_files.clear()
        self.preview_images.clear()
        self.preview_frames.clear()
        self.preview_labels.clear()
        self._cell_state.clear()
        self._image_files_cache.clear()
        self.frame_index = 0
        self.patient_label.configure(text=patient.name)
        self.position_label.configure(text=f"{self.patient_index + 1} / {len(self.patients)} · {len(self.current_slices)} slices")
        self._update_delete_status()
        self._clear_detail()
        self._clear_overview()
        self._load_record(patient.name)
        self._load_previews()
        self._load_overview()
        self.table.selection_set(patient.name)
        self.table.see(patient.name)
        if self.after_id:
            self.root.after_cancel(self.after_id)
        self._animate()
        if self._preload_after_id:
            self.root.after_cancel(self._preload_after_id)
        self._schedule_preload()

    def _image_files(self, folder: Path) -> list[Path]:
        cached = self._image_files_cache.get(folder)
        if cached is not None:
            return cached
        files = image_files(folder)
        self._image_files_cache[folder] = files
        return files

    def _touch_cache(self, name: str) -> None:
        if name in self.frame_cache_order:
            self.frame_cache_order.remove(name)
        self.frame_cache_order.append(name)

    def _evict_frame_cache(self) -> None:
        while len(self.frame_cache_order) > self.frame_cache_limit:
            name = self.frame_cache_order.pop(0)
            cache = self.frame_cache.pop(name, None)
            self.frame_cache_complete.discard(name)
            if cache:
                cache.clear()

    def _schedule_preload(self) -> None:
        remaining = len(self.patients) - self.patient_index - 1
        count = min(QUICK_PRELOAD_PATIENTS if self.loading_mode.get() == "quick" else 1, remaining)
        if count > 0:
            self._preload_after_id = self.root.after(
                50, lambda: self._preload_patient(self.patient_index + 1, count)
            )
        else:
            self._preload_after_id = None

    def _change_loading_mode(self) -> None:
        if self._preload_after_id:
            self.root.after_cancel(self._preload_after_id)
            self._preload_after_id = None
        if self.patient_index >= 0:
            self._touch_cache(self.patients[self.patient_index].name)
        self.frame_cache_limit = QUICK_PRELOAD_PATIENTS + 2 if self.loading_mode.get() == "quick" else 2
        self._evict_frame_cache()
        if self.patient_index >= 0:
            self._schedule_preload()

    def _preload_patient(self, index: int, count: int = 1) -> None:
        self._preload_after_id = None
        if not (0 <= index < len(self.patients)) or index == self.patient_index:
            return
        patient = self.patients[index]
        name = patient.name
        quick = self.loading_mode.get() == "quick"
        if name in self.frame_cache and (quick or name in self.frame_cache_complete):
            self._touch_cache(name)
            if count > 1:
                self._preload_after_id = self.root.after(1, lambda: self._preload_patient(index + 1, count - 1))
            return
        slices = slice_folders(patient)
        targets = slices if quick else list(dict.fromkeys(slices[:5] + list(reversed(slices[-5:]))))
        full_targets = set(slices[:2] + list(reversed(slices[-2:]))) if quick else set()
        cache = self.frame_cache.get(name, {})

        def step(folder_index: int, file_index: int | None = None) -> None:
            self._preload_after_id = None
            if self.loading_mode.get() != ("quick" if quick else "clean"):
                return
            if folder_index >= len(targets):
                self.frame_cache[name] = cache
                if not quick:
                    self.frame_cache_complete.add(name)
                self._touch_cache(name)
                self._evict_frame_cache()
                if count > 1:
                    self._preload_after_id = self.root.after(
                        1, lambda: self._preload_patient(index + 1, count - 1)
                    )
                return
            folder = targets[folder_index]
            files = image_files(folder)
            frames = cache.setdefault(folder, [])
            if file_index is None:
                file_index = len(frames)
            limit = len(files) if folder in full_targets else (min(1, len(files)) if quick else len(files))
            if file_index < limit:
                photo = load_photo(files[file_index])
                if photo is not None:
                    frames.append(photo)
                self._preload_after_id = self.root.after(1, lambda: step(folder_index, file_index + 1))
            else:
                self._preload_after_id = self.root.after(1, lambda: step(folder_index + 1))

        step(0)

    def _load_previews(self) -> None:
        first = self.current_slices[:5]
        last = list(reversed(self.current_slices[-5:]))
        patient_name = self.patients[self.patient_index].name
        for row_labels, folders in zip(self.preview_rows, (first, last)):
            for label, folder in zip(row_labels, folders):
                files = self._image_files(folder)
                self.preview_files[folder] = files
                self.preview_labels.setdefault(folder, []).append(label)
                cached_frames = self.frame_cache.get(patient_name, {}).get(folder)
                if cached_frames is not None:
                    frames = cached_frames
                    self.preview_frames[folder] = frames
                    photo = frames[0] if frames else None
                else:
                    photo = load_photo(files[0]) if files else None
                    if photo is not None:
                        self.preview_frames[folder] = [photo]
                        cache = self.frame_cache.get(patient_name)
                        if cache is None:
                            cache = {}
                            self.frame_cache[patient_name] = cache
                        cache[folder] = [photo]
                    else:
                        self.preview_frames[folder] = []
                if photo is not None:
                    self.preview_images[folder] = photo
                label.configure(image=photo or "", text=f"{folder.name}  ({len(files)})", cursor="hand2")
                label.bind("<ButtonPress-1>", lambda event, selected=folder: self._start_drag(event, selected))
                label.bind("<Control-ButtonPress-1>", lambda _event, selected=folder: self._toggle_delete(selected))
                label.bind("<Button-3>", lambda _event, selected=folder: self._clear_delete(selected))
                label.bind("<B1-Motion>", self._drag_select)
                label.bind("<ButtonRelease-1>", self._finish_drag)
                label.folder = folder  # type: ignore[attr-defined]
                label.master.folder = folder  # type: ignore[attr-defined]
                label.delete_check.configure(  # type: ignore[attr-defined]
                    variable=self.delete_vars[folder],
                    command=self._update_delete_status,
                    state=tk.NORMAL,
                )
                label.delete_check.bind(  # type: ignore[attr-defined]
                    "<Button-3>", lambda _event, selected=folder: self._clear_delete(selected)
                )
                label.delete_check.place(relx=1, x=-4, y=4, anchor=tk.NE)  # type: ignore[attr-defined]
            for label in row_labels[len(folders):]:
                label.configure(image="", text="")
                label.unbind("<ButtonPress-1>")
                label.unbind("<Control-ButtonPress-1>")
                label.unbind("<Button-3>")
                label.unbind("<B1-Motion>")
                label.unbind("<ButtonRelease-1>")
                label.folder = None  # type: ignore[attr-defined]
                label.master.folder = None  # type: ignore[attr-defined]
                label.delete_check.configure(state=tk.DISABLED)  # type: ignore[attr-defined]
                label.delete_check.place_forget()  # type: ignore[attr-defined]
        if patient_name in self.frame_cache_order:
            self.frame_cache_order.remove(patient_name)
        self.frame_cache_order.append(patient_name)
        self._evict_frame_cache()
        self._load_remaining_after_id = self.root.after(1, self._load_remaining_frames_async)

    def _load_remaining_frames_async(self) -> None:
        """动画启动后逐帧补齐 preview，避免一次解码整组图片卡住 UI。"""
        self._load_remaining_after_id = None
        patient_name = self.patients[self.patient_index].name
        cache = self.frame_cache.get(patient_name)
        if cache is None:
            return
        first = self.current_slices[:5]
        last = list(reversed(self.current_slices[-5:]))
        targets = list(dict.fromkeys(first + last))

        def step(folder_index: int, file_index: int | None = None) -> None:
            self._load_remaining_after_id = None
            if self.patient_index < 0 or self.patients[self.patient_index].name != patient_name:
                return
            if folder_index >= len(targets):
                self.frame_cache_complete.add(patient_name)
                return
            folder = targets[folder_index]
            files = self._image_files(folder)
            loaded = cache.get(folder, [])
            if file_index is None:
                file_index = len(loaded)
            if file_index < len(files):
                photo = load_photo(files[file_index])
                if photo is not None:
                    loaded.append(photo)
                    cache[folder] = loaded
                    self.preview_frames[folder] = loaded
                self._load_remaining_after_id = self.root.after(
                    1, lambda: step(folder_index, file_index + 1)
                )
            else:
                self._load_remaining_after_id = self.root.after(1, lambda: step(folder_index + 1))

        step(0)

    def _animate(self) -> None:
        self.after_id = self.root.after(self.FRAME_MS, self._animate)
        if self.playing:
            for folder, frames in self.preview_frames.items():
                if not frames:
                    continue
                for label in self.preview_labels.get(folder, []):
                    try:
                        label.configure(image=frames[self.frame_index % len(frames)])
                    except tk.TclError:
                        pass
            self.frame_index += 1

    def toggle_play(self) -> None:
        self.playing = not self.playing

    def _start_drag(self, event: tk.Event, folder: Path) -> None:
        self.drag_start = folder
        self.drag_origin = (event.x_root, event.y_root)
        self.drag_active = False

    def _install_navigation_bindtag(self, widget: tk.Misc) -> None:
        tags = widget.bindtags()
        if self._navigation_bindtag not in tags:
            widget.bindtags((self._navigation_bindtag, *tags))
        for child in widget.winfo_children():
            self._install_navigation_bindtag(child)

    def _release_control_focus(self, event: tk.Event) -> None:
        if not isinstance(event.widget, tk.Text):
            self.root.after_idle(self.root.focus_set)

    def _next_patient_next(self, event: tk.Event) -> str | None:
        if isinstance(event.widget, tk.Text):
            return None
        self.change_patient(1)
        self.root.focus_set()
        return "break"

    def _next_patient_previous(self, _event: tk.Event) -> str:
        self.change_patient(-1)
        self.root.focus_set()
        return "break"

    def _custom_focus_in(self, _event: tk.Event) -> None:
        if self.custom_placeholder_visible:
            self.custom_text.delete("1.0", tk.END)
            self.custom_text.configure(fg=self.custom_text_default_fg)
            self.custom_placeholder_visible = False

    def _custom_focus_out(self, _event: tk.Event) -> None:
        if not self.custom_placeholder_visible and not self.custom_text.get("1.0", tk.END).strip():
            self._show_custom_placeholder()
        self.save_current()

    def _show_custom_placeholder(self) -> None:
        self.custom_text.delete("1.0", tk.END)
        self.custom_text.insert("1.0", self.custom_placeholder)
        self.custom_text.configure(fg="#9aa0a6")
        self.custom_placeholder_visible = True

    def _set_custom_text(self, value: str) -> None:
        self.custom_text.configure(fg=self.custom_text_default_fg)
        self.custom_text.delete("1.0", tk.END)
        if value.strip():
            self.custom_text.insert("1.0", value)
            self.custom_placeholder_visible = False
        else:
            self._show_custom_placeholder()

    def _custom_issue_value(self) -> str:
        if self.custom_placeholder_visible:
            return ""
        return self.custom_text.get("1.0", tk.END).strip()

    def _select_next_shortcut(self, event: tk.Event, reverse: bool) -> str | None:
        if isinstance(event.widget, tk.Text):
            return None
        folders = list(reversed(self.current_slices[-5:])) if reverse else self.current_slices[:5]
        for folder in folders:
            if not self.delete_vars[folder].get():
                self.delete_vars[folder].set(True)
                self.shortcut_selection_history[reverse].append(folder)
                self._update_delete_status()
                break
        return "break"

    def _clear_last_shortcut(self, event: tk.Event, reverse: bool) -> str | None:
        if isinstance(event.widget, tk.Text):
            return None
        history = self.shortcut_selection_history[reverse]
        while history:
            folder = history.pop()
            if folder in self.delete_vars and self.delete_vars[folder].get():
                self.delete_vars[folder].set(False)
                self._update_delete_status()
                break
        return "break"

    def _select_quality_shortcut(self, event: tk.Event, field: str) -> str | None:
        if isinstance(event.widget, tk.Text):
            return None
        self.flags[field].set(not self.flags[field].get())
        self.save_current()
        return "break"

    def _save_record_shortcut(self, _event: tk.Event) -> str:
        self.save_current()
        return "break"

    def _open_records_shortcut(self, _event: tk.Event) -> str:
        self.open_records()
        return "break"

    def _open_json_shortcut(self, _event: tk.Event) -> str:
        self.open_current_json()
        return "break"

    def _clear_record_shortcut(self, _event: tk.Event) -> str:
        self._clear_current_record()
        return "break"

    def _drag_select(self, event: tk.Event) -> None:
        if self.drag_start is None:
            return
        if not self.drag_active and (
            abs(event.x_root - self.drag_origin[0]) >= 6 or abs(event.y_root - self.drag_origin[1]) >= 6
        ):
            self.drag_active = True
            self.delete_vars[self.drag_start].set(True)
        if not self.drag_active:
            return
        widget = self.root.winfo_containing(event.x_root, event.y_root)
        while widget is not None:
            folder = getattr(widget, "folder", None)
            if folder in self.delete_vars:
                self.delete_vars[folder].set(True)
                break
            widget = getattr(widget, "master", None)
        self._update_delete_status()

    def _finish_drag(self, _event: tk.Event) -> None:
        folder = self.drag_start
        was_drag = self.drag_active
        self.drag_start = None
        self.drag_active = False
        if folder is not None and not was_drag:
            self.select_slice(folder)

    def _toggle_delete(self, folder: Path) -> str:
        variable = self.delete_vars[folder]
        variable.set(not variable.get())
        self._update_delete_status()
        self.drag_start = None
        return "break"

    def _clear_delete(self, folder: Path) -> str:
        self.delete_vars[folder].set(False)
        self._update_delete_status()
        self.drag_start = None
        return "break"

    def select_slice(self, folder: Path) -> None:
        self.selected_slice = folder
        self._update_delete_status()
        self._clear_detail()
        frames = self.preview_frames.get(folder)
        if frames is not None:
            for column, photo in enumerate(frames):
                self.detail_refs.append(photo)
                cell = ttk.Frame(self.detail_frame)
                cell.grid(row=0, column=column, padx=3)
                ttk.Label(cell, image=photo).pack()
                ttk.Label(cell, text=f"{column}.png", anchor=tk.CENTER).pack(fill=tk.X)
        else:
            for column, path in enumerate(image_files(folder)):
                photo = load_photo(path)
                if photo is None:
                    continue
                self.detail_refs.append(photo)
                cell = ttk.Frame(self.detail_frame)
                cell.grid(row=0, column=column, padx=3)
                ttk.Label(cell, image=photo).pack()
                ttk.Label(cell, text=path.name, anchor=tk.CENTER).pack(fill=tk.X)
        self.detail_canvas.xview_moveto(0)

    def _update_delete_status(self) -> None:
        count = sum(variable.get() for variable in self.delete_vars.values())
        for row in self.preview_rows:
            for label in row:
                folder = getattr(label, "folder", None)
                selected = folder is not None and folder in self.delete_vars and self.delete_vars[folder].get()
                if self._cell_state.get(folder) != selected:
                    label.master.configure(highlightbackground="#d32f2f" if selected else "#b8bcc2")
                    self._cell_state[folder] = selected
        for folder, cell in self.overview_cells.items():
            selected = folder in self.delete_vars and self.delete_vars[folder].get()
            cell.configure(highlightbackground="#d32f2f" if selected else "#b8bcc2")
            self._cell_state[folder] = selected
        viewed = f"查看：{self.selected_slice.name} · " if self.selected_slice else ""
        self.selected_label.configure(text=f"{viewed}已勾选 {count} 个")
        self.delete_button.configure(text=f"删除所选 ({count}) [D/Ctrl+D]", state=tk.NORMAL if count else tk.DISABLED)

    def _load_overview(self) -> None:
        patient_name = self.patients[self.patient_index].name
        self.overview_canvas.configure(height=100)

        def step(column: int) -> None:
            self._overview_after_id = None
            if self.patient_index < 0 or self.patients[self.patient_index].name != patient_name:
                return
            if column >= len(self.current_slices):
                return
            folder = self.current_slices[column]
            files = self._image_files(folder)
            if not files:
                self._overview_after_id = self.root.after(1, lambda: step(column + 1))
                return
            cached_frames = self.frame_cache.get(patient_name, {}).get(folder)
            source = cached_frames[0] if cached_frames else load_photo(files[0])
            if source is not None:
                photo = source
                if not self.overview_original_size.get():
                    factor = max(1, (max(source.width(), source.height()) + 79) // 80)
                    photo = source.subsample(factor)
                self.overview_refs.append(photo)
                self.overview_canvas.configure(height=max(100, photo.height() + 34))
                cell = tk.Frame(self.overview_frame, highlightthickness=2, highlightbackground="#b8bcc2")
                cell.grid(row=0, column=column, padx=3)
                image = ttk.Label(cell, image=photo, text=folder.name, compound=tk.TOP, cursor="hand2")
                image.pack()
                check = ttk.Checkbutton(cell, takefocus=False, variable=self.delete_vars[folder], command=self._update_delete_status)
                if self.overview_original_size.get():
                    check.place(relx=1, x=-4, y=4, anchor=tk.NE)
                cell.configure(highlightbackground="#d32f2f" if self.delete_vars[folder].get() else "#b8bcc2")
                image.folder = folder  # type: ignore[attr-defined]
                cell.folder = folder  # type: ignore[attr-defined]
                image.bind("<ButtonPress-1>", lambda event, selected=folder: self._start_drag(event, selected))
                image.bind("<Control-ButtonPress-1>", lambda _event, selected=folder: self._toggle_delete(selected))
                image.bind("<Button-3>", lambda _event, selected=folder: self._clear_delete(selected))
                image.bind("<B1-Motion>", self._drag_select)
                image.bind("<ButtonRelease-1>", self._finish_drag)
                self._install_navigation_bindtag(cell)
                self.overview_cells[folder] = cell
            self._overview_after_id = self.root.after(1, lambda: step(column + 1))

        step(0)
        self.overview_canvas.xview_moveto(0)

    def _clear_overview(self) -> None:
        for folder in self.overview_cells:
            self._cell_state.pop(folder, None)
        for child in self.overview_frame.winfo_children():
            child.destroy()
        self.overview_refs.clear()
        self.overview_cells.clear()

    def _toggle_overview_size(self) -> None:
        if self._overview_after_id:
            self.root.after_cancel(self._overview_after_id)
            self._overview_after_id = None
        self._clear_overview()
        if self.patients and self.patient_index >= 0:
            self._load_overview()

    def _clear_detail(self) -> None:
        for child in self.detail_frame.winfo_children():
            child.destroy()
        self.detail_refs.clear()

    def _update_detail_scroll(self, _event: tk.Event) -> None:
        self.detail_canvas.configure(scrollregion=self.detail_canvas.bbox("all"))

    def _resize_detail_height(self, event: tk.Event) -> None:
        self.detail_canvas.itemconfigure(self.detail_window, height=max(1, event.height - 2))

    def delete_selected(self) -> None:
        folders = [folder for folder in self.current_slices if self.delete_vars[folder].get()]
        if not folders:
            return
        failures: list[str] = []
        batch: list[tuple[Path, Path]] = []
        batch_name = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        for folder in folders:
            try:
                target = self.trash_root / folder.parent.name / batch_name / folder.name
                target.parent.mkdir(parents=True, exist_ok=True)
                if target.exists():
                    raise FileExistsError(target)
                shutil.move(str(folder), str(target))
                batch.append((target, folder))
            except Exception as exc:
                failures.append(f"{folder.name}: {exc}")
        if batch:
            self.undo_stack.append(batch)
            self._update_undo_button()
        self.show_patient(self.patient_index)
        if failures:
            messagebox.showerror("部分删除失败", "\n".join(failures))

    def undo_delete(self) -> None:
        if not self.undo_stack:
            return
        batch = self.undo_stack.pop()
        failures: list[str] = []
        retry: list[tuple[Path, Path]] = []
        for deleted, original in reversed(batch):
            try:
                if original.exists():
                    raise FileExistsError(f"原位置已存在: {original}")
                original.parent.mkdir(parents=True, exist_ok=True)
                shutil.move(str(deleted), str(original))
            except Exception as exc:
                failures.append(f"{original.name}: {exc}")
                retry.append((deleted, original))
        if retry:
            self.undo_stack.append(list(reversed(retry)))
        self._update_undo_button()
        self.show_patient(self.patient_index)
        if failures:
            messagebox.showerror("部分撤销失败", "\n".join(failures))

    def _update_undo_button(self) -> None:
        self.undo_button.configure(state=tk.NORMAL if self.undo_stack else tk.DISABLED)

    def _delete_shortcut(self, event: tk.Event) -> str | None:
        if isinstance(event.widget, tk.Text):
            return None
        self.delete_selected()
        return "break"

    def _undo_shortcut(self, event: tk.Event) -> str | None:
        if isinstance(event.widget, tk.Text):
            return None
        self.undo_delete()
        return "break"

    def change_patient(self, offset: int) -> None:
        if self.patients:
            self.show_patient(self.patient_index + offset)

    def _load_record(self, patient_id: str) -> None:
        record = self.records.get(patient_id, {})
        for field, variable in self.flags.items():
            variable.set(record.get(field, "") == "1")
        self._set_custom_text(record.get("custom_issue", ""))

    def _clear_current_record(self) -> None:
        if self.patient_index < 0:
            return
        for variable in self.flags.values():
            variable.set(False)
        self._set_custom_text("")
        self.save_current()

    def _on_review_wheel(self, event: tk.Event) -> None:
        self.review_canvas.yview_scroll(int(-event.delta / 120), "units")

    def open_records(self) -> None:
        path = self.records_path
        if path.exists():
            os.startfile(str(path))
        else:
            messagebox.showinfo("提示", f"记录文件尚不存在：\n{path}")

    def open_current_json(self) -> None:
        if self.patient_index < 0 or not self.patients:
            return
        patient = self.patients[self.patient_index]
        files = sorted(
            (path for path in patient.rglob("*") if path.is_file() and path.suffix.lower() == ".json"),
            key=natural_key,
        )
        if files:
            os.startfile(str(files[0]))
        else:
            messagebox.showinfo("提示", f"当前病人文件夹内没有 JSON 文件：\n{patient}")

    def save_current(self) -> bool:
        if self.patient_index < 0 or not self.patients:
            return True
        patient_id = self.patients[self.patient_index].name
        record = {"patient_id": patient_id}
        record.update({field: "1" if variable.get() else "0" for field, variable in self.flags.items()})
        record["custom_issue"] = self._custom_issue_value()
        if record_has_issue(record):
            record["updated_at"] = datetime.now().isoformat(timespec="seconds")
            self.records[patient_id] = record
            self.records_dirty = True
        elif patient_id in self.records:
            self.records.pop(patient_id, None)
            self.records_dirty = True
        if self.records_dirty:
            try:
                write_records(self.records_path, self.records)
            except OSError as exc:
                if not self.record_warning_shown:
                    messagebox.showwarning(
                        "记录暂未保存",
                        f"无法写入：{self.records_path}\n\n请关闭正在打开该 CSV 的 Excel 或其他程序后，再点击保存。\n\n{exc}",
                    )
                    self.record_warning_shown = True
                self._refresh_table()
                return False
            self.records_dirty = False
            self.record_warning_shown = False
        self._refresh_table()
        return True

    def _refresh_table(self) -> None:
        selected = self.patients[self.patient_index].name if 0 <= self.patient_index < len(self.patients) else None
        self.table.delete(*self.table.get_children())
        total = len(self.patients)
        for number, patient in enumerate(self.patients, 1):
            record = self.records.get(patient.name, {})
            self.table.insert(
                "",
                tk.END,
                iid=patient.name,
                text=f"{number}/{total} {patient.name}",
                values=("!" if record_has_issue(record) else "",),
            )
        if selected and self.table.exists(selected):
            self.table.selection_set(selected)

    def _sort_by_issues(self) -> None:
        if not self.patients or self.patient_index < 0:
            return
        current_patient = self.patients[self.patient_index].name
        if not self.save_current():
            return
        self.issue_sort_issues_first = (
            True if self.issue_sort_issues_first is None else not self.issue_sort_issues_first
        )
        self.patients = sort_patients_by_issue(
            self.patients, self.records, self.issue_sort_issues_first
        )
        index = next(i for i, patient in enumerate(self.patients) if patient.name == current_patient)
        self.table.heading("issues", text="问题 ↓" if self.issue_sort_issues_first else "问题 ↑")
        self.patient_index = -1
        self._refresh_table()
        self.show_patient(index)

    def _sort_by_patient_id(self) -> None:
        if not self.patients or self.patient_index < 0:
            return
        current_patient = self.patients[self.patient_index].name
        if not self.save_current():
            return
        self.patients = sorted(self.patients, key=natural_key)
        self.issue_sort_issues_first = None
        self.table.heading("issues", text="问题")
        index = next(i for i, patient in enumerate(self.patients) if patient.name == current_patient)
        self.patient_index = -1
        self._refresh_table()
        self.show_patient(index)

    def _table_select(self, _event: tk.Event) -> None:
        selection = self.table.selection()
        if not selection:
            return
        patient_id = selection[0]
        index = next((i for i, patient in enumerate(self.patients) if patient.name == patient_id), -1)
        if index >= 0 and index != self.patient_index:
            self.show_patient(index)

    def _table_double_click(self, _event: tk.Event) -> None:
        selection = self.table.selection()
        if not selection:
            return
        patient_id = selection[0]
        folder = self.data_root / patient_id
        if folder.is_dir():
            os.startfile(str(folder))

    def _cleanup_trash(self) -> None:
        if self.trash_root.exists():
            shutil.rmtree(self.trash_root)

    def close(self) -> None:
        if not self.save_current():
            return
        if self.after_id:
            self.root.after_cancel(self.after_id)
        if self._preload_after_id:
            self.root.after_cancel(self._preload_after_id)
        if self._load_remaining_after_id:
            self.root.after_cancel(self._load_remaining_after_id)
        if self._overview_after_id:
            self.root.after_cancel(self._overview_after_id)
        self.root.unbind_class(self._navigation_bindtag, "<space>")
        self.root.unbind_class(self._navigation_bindtag, "<Tab>")
        for sequence in (
            "<KeyPress-1>",
            "<KeyPress-2>",
            "<KeyPress-3>",
            "<KeyPress-4>",
            "<q>",
            "<w>",
            "<e>",
            "<Control-s>",
            "<Control-a>",
            "<Control-f>",
            "<Control-x>",
        ):
            self.root.unbind_class(self._navigation_bindtag, sequence)
        self.root.unbind_all("<ButtonRelease-1>")
        self.root.destroy()


def main() -> None:
    parser = argparse.ArgumentParser(description="人工检查并清理病人 PNG slice 文件夹")
    parser.add_argument("root", nargs="?", type=Path, default=DEFAULT_ROOT, help="病人数据根目录")
    parser.add_argument("--records", type=Path, help="清洗记录 CSV 路径")
    args = parser.parse_args()
    if not args.root.is_dir():
        raise SystemExit(f"数据目录不存在: {args.root}")
    window = tk.Tk()
    app = SliceCleaner(window, args.root, args.records or args.root / "清洗记录.csv")
    window.mainloop()
    app._cleanup_trash()


if __name__ == "__main__":
    main()
