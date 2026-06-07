"""
GUI 界面
"""

from __future__ import annotations

import json
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

import customtkinter as ctk

from client import QuarkClient
from downloader import MultiThreadDownloader
from scripts import open_login_window, open_manage_window
from utils import (
    _exe_dir, _resource_dir, COOKIE_FILE, FONT, FONT_MONO,
    human_bytes, parse_share_url
)


class FileSelectDialog(ctk.CTkToplevel):
    """文件选择弹窗：勾选要下载的文件"""

    def __init__(self, parent, file_list: list[dict]):
        super().__init__(parent)
        self.title("选择要下载的文件")
        self.geometry("560x500")
        self.minsize(400, 300)
        self.resizable(True, True)
        self.grab_set()  # 模态

        self.result: list[dict] | None = None
        self._checks: dict[str, ctk.CTkCheckBox] = {}  # fid -> checkbox
        self._dir_children: dict[str, list[str]] = {}   # dir_fid -> [child_fid]
        self._file_map: dict[str, dict] = {}            # fid -> file_info
        self._size_map: dict[str, int] = {}             # fid -> size

        c = {
            "card": "#ffffff", "border": "#e5e7eb",
            "accent": "#6366f1", "text": "#1f2937",
            "text_secondary": "#6b7280", "input_bg": "#f9fafb",
        }

        # ── 顶部按钮栏 ──
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 4))
        ctk.CTkLabel(top, text=f"共 {len(file_list)} 项",
                     font=ctk.CTkFont(family=FONT, size=11),
                     text_color=c["text_secondary"]).pack(side="left")
        ctk.CTkButton(top, text="全不选", width=60, height=28,
                      fg_color=c["input_bg"], hover_color=c["border"],
                      text_color=c["text"],
                      font=ctk.CTkFont(family=FONT, size=10),
                      command=self._deselect_all).pack(side="right", padx=(4, 0))
        ctk.CTkButton(top, text="全选", width=60, height=28,
                      fg_color=c["input_bg"], hover_color=c["border"],
                      text_color=c["text"],
                      font=ctk.CTkFont(family=FONT, size=10),
                      command=self._select_all).pack(side="right")

        # ── 文件列表（可滚动）──
        self._scroll = ctk.CTkScrollableFrame(self, fg_color=c["card"],
                                               border_color=c["border"], border_width=1,
                                               corner_radius=8)
        self._scroll.pack(fill="both", expand=True, padx=14, pady=6)

        # 构建树结构
        tree: dict[str, list[dict]] = {}  # parent_path -> [file_info]
        root_items: list[dict] = []
        for f in file_list:
            self._file_map[f["fid"]] = f
            self._size_map[f["fid"]] = f.get("size", 0)
            path = f.get("path", f["file_name"])
            parts = path.strip("/").split("/")
            if len(parts) <= 1:
                root_items.append(f)
            else:
                parent = "/".join(parts[:-1])
                tree.setdefault(parent, []).append(f)

        # 递归添加行
        def add_items(items: list[dict], depth: int):
            for f in items:
                fid = f["fid"]
                path = f.get("path", f["file_name"])
                indent = "  " * depth
                prefix = "📁 " if f["is_dir"] else "📄 "
                name = f['file_name']
                size_str = human_bytes(f["size"]) if not f["is_dir"] and f.get("size") else ""
                label = f"{indent}{prefix}{name}    {size_str}" if size_str else f"{indent}{prefix}{name}"

                cb = ctk.CTkCheckBox(
                    self._scroll, text=label,
                    font=ctk.CTkFont(family=FONT, size=11),
                    text_color=c["text"],
                    fg_color=c["accent"], hover_color=c["accent"],
                    corner_radius=3, border_width=1,
                    border_color=c["border"],
                    checkbox_width=16, checkbox_height=16,
                    command=lambda fid=fid: self._on_toggle(fid),
                )
                cb.select()
                cb.pack(anchor="w", padx=(12 + depth * 16, 12), pady=1)
                self._checks[fid] = cb

                # 子文件
                children = tree.get(path, [])
                if children:
                    self._dir_children[fid] = [c["fid"] for c in children]
                    add_items(children, depth + 1)

        add_items(root_items, 0)

        # ── 底部状态栏 ──
        bot = ctk.CTkFrame(self, fg_color="transparent")
        bot.pack(fill="x", padx=14, pady=(4, 12))
        self._lbl_stats = ctk.CTkLabel(bot, text="",
                                        font=ctk.CTkFont(family=FONT, size=11),
                                        text_color=c["text_secondary"])
        self._lbl_stats.pack(side="left")
        ctk.CTkButton(bot, text="取消", width=70, height=30,
                      fg_color=c["input_bg"], hover_color=c["border"],
                      text_color=c["text"],
                      font=ctk.CTkFont(family=FONT, size=11),
                      command=self._on_cancel).pack(side="right", padx=(6, 0))
        ctk.CTkButton(bot, text="确认下载", width=90, height=30,
                      fg_color=c["accent"], hover_color="#4f46e5",
                      text_color="white",
                      font=ctk.CTkFont(family=FONT, size=11, weight="bold"),
                      command=self._on_confirm).pack(side="right")

        self._update_stats()
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _on_toggle(self, fid: str):
        """文件夹勾选联动子文件"""
        if fid in self._dir_children:
            checked = self._checks[fid].get()
            for child_fid in self._dir_children[fid]:
                if child_fid in self._checks:
                    if checked:
                        self._checks[child_fid].select()
                    else:
                        self._checks[child_fid].deselect()
                # 递归子文件夹
                if child_fid in self._dir_children:
                    self._on_toggle(child_fid)
        self._update_stats()

    def _select_all(self):
        for cb in self._checks.values():
            cb.select()
        self._update_stats()

    def _deselect_all(self):
        for cb in self._checks.values():
            cb.deselect()
        self._update_stats()

    def _update_stats(self):
        selected = [fid for fid, cb in self._checks.items()
                    if cb.get() and fid in self._file_map
                    and not self._file_map[fid]["is_dir"]]
        total_size = sum(self._size_map.get(fid, 0) for fid in selected)
        self._lbl_stats.configure(
            text=f"已选 {len(selected)} 个文件，共 {human_bytes(total_size)}")

    def _on_confirm(self):
        self.result = [
            self._file_map[fid] for fid, cb in self._checks.items()
            if cb.get() and fid in self._file_map and not self._file_map[fid]["is_dir"]
        ]
        self.grab_release()
        self.destroy()

    def _on_cancel(self):
        self.result = []
        self.grab_release()
        self.destroy()


class CloudFileBrowser(ctk.CTkToplevel):
    """夸克网盘文件浏览器"""

    def __init__(self, parent, client: QuarkClient):
        super().__init__(parent)
        self.title("浏览夸克网盘")
        self.geometry("560x500")
        self.minsize(400, 300)
        self.resizable(True, True)
        self.grab_set()

        self.client = client
        self.result: list[str] | None = None  # 选中的 fid 列表
        self._checks: dict[str, ctk.CTkCheckBox] = {}
        self._file_map: dict[str, dict] = {}
        self._path_stack: list[tuple[str, str]] = [("0", "根目录")]  # (fid, name)

        c = {
            "card": "#ffffff", "border": "#e5e7eb",
            "accent": "#6366f1", "text": "#1f2937",
            "text_secondary": "#6b7280", "input_bg": "#f9fafb",
        }

        # ── 顶部路径栏 ──
        top = ctk.CTkFrame(self, fg_color="transparent")
        top.pack(fill="x", padx=14, pady=(12, 4))
        self.btn_back = ctk.CTkButton(
            top, text="← 返回", width=60, height=28,
            fg_color=c["input_bg"], hover_color=c["border"],
            text_color=c["text"], font=self._font(10),
            command=self._go_back,
        )
        self.btn_back.pack(side="left")
        self.lbl_path = ctk.CTkLabel(top, text="根目录",
                                      font=self._font(11),
                                      text_color=c["text_secondary"])
        self.lbl_path.pack(side="left", padx=(10, 0))

        # ── 文件列表 ──
        self._scroll = ctk.CTkScrollableFrame(self, fg_color=c["card"],
                                               border_color=c["border"], border_width=1,
                                               corner_radius=8)
        self._scroll.pack(fill="both", expand=True, padx=14, pady=6)

        # ── 底部 ──
        bot = ctk.CTkFrame(self, fg_color="transparent")
        bot.pack(fill="x", padx=14, pady=(4, 12))
        self._lbl_stats = ctk.CTkLabel(bot, text="",
                                        font=self._font(11),
                                        text_color=c["text_secondary"])
        self._lbl_stats.pack(side="left")
        ctk.CTkButton(bot, text="取消", width=70, height=30,
                      fg_color=c["input_bg"], hover_color=c["border"],
                      text_color=c["text"], font=self._font(11),
                      command=self._on_cancel).pack(side="right", padx=(6, 0))
        ctk.CTkButton(bot, text="确认选择", width=90, height=30,
                      fg_color=c["accent"], hover_color="#4f46e5",
                      text_color="white", font=self._font(11, bold=True),
                      command=self._on_confirm).pack(side="right")

        self._load_dir("0")
        self.protocol("WM_DELETE_WINDOW", self._on_cancel)

    def _font(self, size: int, bold: bool = False) -> ctk.CTkFont:
        return ctk.CTkFont(family=FONT, size=size, weight="bold" if bold else "normal")

    def _load_dir(self, pdir_fid: str):
        """加载目录内容"""
        # 清空旧内容
        for w in self._scroll.winfo_children():
            w.destroy()
        self._checks.clear()
        self._file_map.clear()

        c = {
            "card": "#ffffff", "border": "#e5e7eb",
            "accent": "#6366f1", "text": "#1f2937",
            "text_secondary": "#6b7280", "input_bg": "#f9fafb",
        }

        try:
            files = self.client.list_files(pdir_fid)
        except Exception as e:
            ctk.CTkLabel(self._scroll, text=f"加载失败: {e}",
                         font=self._font(11), text_color="#ef4444").pack(pady=20)
            self._update_back_btn()
            return

        if not files:
            ctk.CTkLabel(self._scroll, text="（空目录）",
                         font=self._font(11), text_color=c["text_secondary"]).pack(pady=20)
            self._update_back_btn()
            return

        for f in files:
            fid = f["fid"]
            self._file_map[fid] = f
            name = f["file_name"]
            is_dir = f["is_dir"]
            prefix = "📁 " if is_dir else "📄 "
            size_str = "" if is_dir else human_bytes(f.get("size", 0))
            label = f"{prefix}{name}    {size_str}" if size_str else f"{prefix}{name}"

            if is_dir:
                # 文件夹：双击进入
                btn = ctk.CTkButton(
                    self._scroll, text=label, anchor="w",
                    fg_color="transparent", hover_color=c["input_bg"],
                    text_color=c["text"], font=self._font(11),
                    height=28, corner_radius=4,
                    command=lambda fid=fid, name=name: self._enter_dir(fid, name),
                )
                btn.pack(fill="x", padx=4, pady=1)
            else:
                cb = ctk.CTkCheckBox(
                    self._scroll, text=label,
                    font=self._font(11), text_color=c["text"],
                    fg_color=c["accent"], hover_color=c["accent"],
                    corner_radius=3, border_width=1, border_color=c["border"],
                    checkbox_width=16, checkbox_height=16,
                    command=self._update_stats,
                )
                cb.pack(anchor="w", padx=8, pady=1)
                self._checks[fid] = cb

        self._update_back_btn()

    def _enter_dir(self, fid: str, name: str):
        self._path_stack.append((fid, name))
        self.lbl_path.configure(text=" > ".join(n for _, n in self._path_stack))
        self._load_dir(fid)

    def _go_back(self):
        if len(self._path_stack) <= 1:
            return
        self._path_stack.pop()
        fid = self._path_stack[-1][0]
        self.lbl_path.configure(text=" > ".join(n for _, n in self._path_stack))
        self._load_dir(fid)

    def _update_back_btn(self):
        self.btn_back.configure(state="normal" if len(self._path_stack) > 1 else "disabled")

    def _update_stats(self):
        selected = [fid for fid, cb in self._checks.items() if cb.get()]
        total_size = sum(self._file_map[fid].get("size", 0) for fid in selected)
        self._lbl_stats.configure(
            text=f"已选 {len(selected)} 个文件，共 {human_bytes(total_size)}")

    def _on_confirm(self):
        self.result = [fid for fid, cb in self._checks.items() if cb.get()]
        self.grab_release()
        self.destroy()

    def _on_cancel(self):
        self.result = None
        self.grab_release()
        self.destroy()


class QuarkGUI:
    """夸克网盘下载器 GUI"""

    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.root.title("夸克网盘多线程下载器")
        self.root.geometry("1280x720")
        self.root.minsize(960, 540)
        self.root.configure(fg_color="#f0f2f5")
        self._set_window_icon()
        self._build_colors()
        self._build_ui()
        self.downloading = False
        self.cancel_event = threading.Event()
        self._load_saved_cookie()

    def _build_colors(self) -> None:
        self.c = {
            "page": "#f0f2f5",
            "card": "#ffffff",
            "border": "#e5e7eb",
            "accent": "#6366f1",
            "accent_hover": "#4f46e5",
            "text": "#1f2937",
            "text_secondary": "#6b7280",
            "text_dim": "#9ca3af",
            "input_bg": "#f9fafb",
            "green": "#10b981",
            "red": "#ef4444",
            "yellow": "#f59e0b",
        }

    def _set_window_icon(self) -> None:
        ico = _resource_dir() / "icon.ico"
        if not ico.exists():
            return
        try:
            ico_path = str(ico)
            self.root.iconbitmap(ico_path)
            self.root.after(200, lambda: self.root.iconbitmap(ico_path))
        except Exception:
            pass

    # ─── 辅助方法 ───

    def _font(self, size: int, bold: bool = False) -> ctk.CTkFont:
        """统一字体：普通文本用 normal，标题用 bold"""
        return ctk.CTkFont(family=FONT, size=size, weight="bold" if bold else "normal")

    def _font_mono(self, size: int) -> ctk.CTkFont:
        return ctk.CTkFont(family=FONT_MONO, size=size, weight="normal")

    def _card(self, parent, **pack_kw) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=self.c["card"],
                            border_color=self.c["border"],
                            border_width=1, corner_radius=10)
        defaults = {"fill": "x", "padx": 12, "pady": 5}
        defaults.update(pack_kw)
        card.pack(**defaults)
        return card

    def _section(self, parent: ctk.CTkFrame, title: str, **pack_kw) -> ctk.CTkFrame:
        defaults = {"fill": "x", "padx": 12, "pady": 5}
        defaults.update(pack_kw)
        card = self._card(parent, **defaults)
        ctk.CTkLabel(card, text=title,
                     font=self._font(14, bold=True),
                     text_color=self.c["text"],
                     anchor="w").pack(anchor="w", padx=18, pady=(14, 2))
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="both", expand=True, padx=18, pady=(6, 14))
        return body

    # ─── 界面构建 ───

    def _build_ui(self) -> None:
        c = self.c

        # ── 顶部栏 ──
        bar = ctk.CTkFrame(self.root, fg_color=c["card"], corner_radius=0,
                           border_color=c["border"], border_width=1)
        bar.pack(fill="x")
        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="x", padx=22, pady=(12, 10))
        ctk.CTkLabel(inner, text="夸克网盘下载器",
                     font=self._font(16, bold=True),
                     text_color=c["text"]).pack(side="left")
        ctk.CTkLabel(inner, text="BetterQuackDownloader",
                     font=self._font(10),
                     text_color=c["text_dim"]).pack(side="right")

        # ── 左右分栏 ──
        pane = ctk.CTkFrame(self.root, fg_color="transparent")
        pane.pack(fill="both", expand=True, padx=0, pady=0)
        pane.columnconfigure(0, weight=1, uniform="half")
        pane.columnconfigure(1, weight=1, uniform="half")
        pane.rowconfigure(0, weight=1)

        left = ctk.CTkFrame(pane, fg_color="transparent")
        left.grid(row=0, column=0, sticky="nsew", padx=(12, 3), pady=10)
        right = ctk.CTkFrame(pane, fg_color="transparent")
        right.grid(row=0, column=1, sticky="nsew", padx=(3, 12), pady=10)

        # ── 左侧：Cookie + 参数 + 操作 ──

        # ── Cookie ──
        body_cookie = self._section(left, "🔑  Cookie")

        btn_row = ctk.CTkFrame(body_cookie, fg_color="transparent")
        btn_row.pack(fill="x", pady=(0, 8))
        self.btn_login = ctk.CTkButton(
            btn_row, text="🌐 获取 Cookie",
            command=self._open_login_browser,
            fg_color=c["accent"], hover_color=c["accent_hover"],
            text_color="white", font=self._font(11),
            corner_radius=8, width=40, height=32,
        )
        self.btn_login.pack(side="right")
        self.btn_manage = ctk.CTkButton(
            btn_row, text="👤 管理账号",
            command=self._open_account_manager,
            fg_color=c["card"], hover_color=c["input_bg"],
            text_color=c["text"], font=self._font(11),
            corner_radius=8, width=40, height=32,
            border_color=c["border"], border_width=1,
            state="disabled",
        )
        self.btn_manage.pack(side="right", padx=(0, 8))

        self.ent_cookie = ctk.CTkTextbox(
            body_cookie, height=68,
            fg_color=c["input_bg"], text_color=c["text"],
            border_color=c["border"], border_width=1,
            corner_radius=6, font=self._font_mono(10),
            wrap="word", activate_scrollbars=False,
        )
        self.ent_cookie.pack(fill="x")
        self.ent_cookie.bind("<KeyRelease>", lambda e: self._update_manage_btn())

        # ── 参数 / 日志 Tabview ──
        card_tab = self._card(left, fill="both", expand=True)
        ctk.CTkLabel(card_tab, text="⬇  下载参数",
                     font=self._font(14, bold=True),
                     text_color=c["text"], anchor="w").pack(anchor="w", padx=18, pady=(14, 2))
        self.tabview = ctk.CTkTabview(card_tab, fg_color="transparent",
                                      segmented_button_fg_color=c["input_bg"],
                                      segmented_button_selected_color=c["accent"],
                                      segmented_button_unselected_color=c["input_bg"],
                                      segmented_button_selected_hover_color=c["accent_hover"],
                                      text_color=c["text"],
                                      corner_radius=8)
        self.tabview.pack(fill="both", expand=True, padx=14, pady=(0, 12))
        self.tabview._segmented_button.configure(
            font=self._font(13, bold=True),
            corner_radius=20,
        )

        tab_params = self.tabview.add("  参数  ")
        tab_log = self.tabview.add("  日志  ")

        # 固定内容区域大小，切换 tab 不会跳动
        self.tabview.update_idletasks()
        self.tabview.grid_propagate(False)
        for child in self.tabview.winfo_children():
            if child.winfo_class() == "Frame" and child.grid_info().get("row") == 3:
                child.grid_propagate(False)
                child.configure(width=child.winfo_width(), height=child.winfo_height())
                child.grid_rowconfigure(0, weight=1)
                child.grid_columnconfigure(0, weight=1)
                break

        tab_params.grid_rowconfigure(0, weight=1)
        tab_params.grid_columnconfigure(0, weight=1)
        tab_log.grid_rowconfigure(0, weight=1)
        tab_log.grid_columnconfigure(0, weight=1)

        # ── 参数 tab ──
        body2 = ctk.CTkFrame(tab_params, fg_color="transparent")
        body2.grid(row=0, column=0, sticky="nsew", padx=4, pady=(8, 4))

        ctk.CTkLabel(body2, text="分享链接 / 文件 FID    二选一",
                     font=self._font(11),
                     text_color=c["text_dim"],
                     anchor="w").pack(anchor="w", pady=(0, 2))
        ctk.CTkLabel(body2, text="分享链接",
                     font=self._font(10),
                     text_color=c["text_secondary"],
                     anchor="w").pack(anchor="w", pady=(0, 2))
        self.ent_url = ctk.CTkEntry(
            body2, height=36,
            fg_color=c["input_bg"], text_color=c["text"],
            border_color=c["border"], corner_radius=6,
            font=self._font_mono(11),
        )
        self.ent_url.pack(fill="x", pady=(0, 10))
        self.ent_url.bind("<KeyRelease>", lambda e: self._on_input_change())

        ctk.CTkLabel(body2, text="网盘内文件（FID）",
                     font=self._font(10),
                     text_color=c["text_secondary"],
                     anchor="w").pack(anchor="w", pady=(0, 2))
        fid_row = ctk.CTkFrame(body2, fg_color="transparent")
        fid_row.pack(fill="x", pady=(0, 12))
        self.ent_fid = ctk.CTkEntry(
            fid_row, height=36,
            fg_color=c["input_bg"], text_color=c["text"],
            border_color=c["border"], corner_radius=6,
            font=self._font_mono(11),
        )
        self.ent_fid.pack(side="left", fill="x", expand=True, padx=(0, 6))
        self.ent_fid.bind("<KeyRelease>", lambda e: self._on_input_change())
        self.btn_browse_cloud = ctk.CTkButton(
            fid_row, text="📂", width=36, height=36,
            fg_color=c["input_bg"], hover_color=c["border"],
            text_color=c["text"], corner_radius=6,
            font=self._font(14),
            border_color=c["border"], border_width=1,
            command=self._open_cloud_browser,
        )
        self.btn_browse_cloud.pack(side="left")

        # ── 线程数 ──
        row1 = ctk.CTkFrame(body2, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(row1, text="线程数",
                     font=self._font(12),
                     text_color=c["text_secondary"]).pack(side="left")
        self.var_workers = tk.IntVar(value=64)
        self.ent_workers = ctk.CTkEntry(row1, width=56, height=28,
                                        text_color=c["text"],
                                        fg_color=c["input_bg"],
                                        border_color=c["border"], corner_radius=6,
                                        font=self._font_mono(12),
                                        justify="center")
        self.ent_workers.pack(side="right", padx=(0, 4))
        self.ent_workers.insert(0, "64")
        self.sld_workers = ctk.CTkSlider(
            row1, from_=1, to=1024, number_of_steps=1023,
            variable=self.var_workers, height=16,
            fg_color=c["border"], progress_color=c["accent"],
            button_color=c["accent"], button_hover_color=c["accent_hover"],
            command=lambda v: self._sync_ent_from_sld(self.ent_workers, v, 1, 1024),
        )
        self.sld_workers.pack(side="right", fill="x", expand=True, padx=(0, 6))
        self._bind_entry_to_slider(self.ent_workers, self.sld_workers, self.var_workers, 1, 1024)

        # ── 分片 ──
        row2 = ctk.CTkFrame(body2, fg_color="transparent")
        row2.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(row2, text="分片 (MB)",
                     font=self._font(12),
                     text_color=c["text_secondary"]).pack(side="left")
        self.var_chunk = tk.IntVar(value=1)
        self.ent_chunk = ctk.CTkEntry(row2, width=56, height=28,
                                      text_color=c["text"],
                                      fg_color=c["input_bg"],
                                      border_color=c["border"], corner_radius=6,
                                      font=self._font_mono(12),
                                      justify="center")
        self.ent_chunk.pack(side="right", padx=(0, 4))
        self.ent_chunk.insert(0, "1")
        self.sld_chunk = ctk.CTkSlider(
            row2, from_=1, to=64, number_of_steps=63,
            variable=self.var_chunk, height=16,
            fg_color=c["border"], progress_color=c["accent"],
            button_color=c["accent"], button_hover_color=c["accent_hover"],
            command=lambda v: self._sync_ent_from_sld(self.ent_chunk, v, 1, 64),
        )
        self.sld_chunk.pack(side="right", fill="x", expand=True, padx=(0, 6))
        self._bind_entry_to_slider(self.ent_chunk, self.sld_chunk, self.var_chunk, 1, 64)

        # ── 输出目录 ──
        row3 = ctk.CTkFrame(body2, fg_color="transparent")
        row3.pack(fill="x")
        ctk.CTkLabel(row3, text="下载目录",
                     font=self._font(12),
                     text_color=c["text_secondary"]).pack(side="left")
        self.var_output = tk.StringVar(value=str(_exe_dir() / "downloads"))
        ctk.CTkEntry(row3, textvariable=self.var_output,
                     height=32,
                     fg_color=c["input_bg"], text_color=c["text"],
                     border_color=c["border"], corner_radius=6,
                     font=self._font_mono(10),
                     ).pack(side="left", fill="x", expand=True, padx=(6, 6))
        ctk.CTkButton(row3, text="...", width=36, height=32,
                      command=self._browse_output,
                      fg_color=c["input_bg"], hover_color=c["border"],
                      text_color=c["text"], corner_radius=6,
                      font=self._font(10, bold=True),
                      border_color=c["border"], border_width=1,
                      ).pack(side="left")

        # ── 日志 tab ──
        self.txt_log = ctk.CTkTextbox(
            tab_log,
            fg_color=c["card"], text_color=c["text"],
            border_color=c["border"], border_width=1,
            corner_radius=6, font=self._font_mono(10),
            wrap="word", activate_scrollbars=False,
        )
        self.txt_log.grid(row=0, column=0, sticky="nsew", padx=4, pady=(8, 4))
        self.txt_log.configure(state="disabled")

        # ── 右侧：文件列表 + 操作按钮 + 进度 ──

        card_right = self._card(right, fill="both", expand=True)
        header_right = ctk.CTkFrame(card_right, fg_color="transparent")
        header_right.pack(fill="x", padx=18, pady=(14, 2))
        ctk.CTkLabel(header_right, text="📂  文件列表",
                     font=self._font(14, bold=True),
                     text_color=c["text"], anchor="w").pack(side="left")

        # 操作按钮（右上角）
        self.btn_cancel = ctk.CTkButton(
            header_right, text="✕ 取消",
            command=self._cancel_download,
            fg_color=c["card"], hover_color="#fee2e2",
            text_color=c["text_dim"], corner_radius=8,
            font=self._font(11),
            border_color=c["border"], border_width=1,
            width=40, height=32,
            state="disabled",
        )
        self.btn_cancel.pack(side="right")
        self.btn_download = ctk.CTkButton(
            header_right, text="⬇ 开始下载",
            command=self._start_download,
            fg_color=c["accent"], hover_color=c["accent_hover"],
            text_color="white", corner_radius=8,
            font=self._font(11, bold=True),
            width=40, height=32,
        )
        self.btn_download.pack(side="right", padx=(0, 8))
        self.btn_parse = ctk.CTkButton(
            header_right, text="🔍 解析链接",
            command=self._parse_link,
            fg_color=c["card"], hover_color=c["input_bg"],
            text_color=c["text"], corner_radius=8,
            font=self._font(11),
            border_color=c["border"], border_width=1,
            width=40, height=32,
        )
        self.btn_parse.pack(side="right", padx=(0, 8))

        body_files = ctk.CTkFrame(card_right, fg_color="transparent")
        body_files.pack(fill="both", expand=True, padx=18, pady=(6, 14))

        frm_tree = tk.Frame(body_files, bg=c["border"])
        frm_tree.pack(fill="both", expand=True, padx=0, pady=(0, 8))
        self.tree = ttk.Treeview(frm_tree, columns=("name", "size", "status"),
                                 show="headings")
        self.tree.heading("name", text="文件名")
        self.tree.heading("size", text="大小")
        self.tree.heading("status", text="状态")
        self.tree.column("name", minwidth=200)
        self.tree.column("size", width=90, anchor="e")
        self.tree.column("status", width=120, anchor="center")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("Treeview", background=c["card"],
                        foreground=c["text"],
                        fieldbackground=c["card"], borderwidth=0,
                        rowheight=28, font=(FONT, 10))
        style.configure("Treeview.Heading", background=c["input_bg"],
                        foreground=c["text"], borderwidth=0,
                        font=(FONT, 10, "bold"),
                        padding=(8, 5))
        style.map("Treeview",
                  background=[("selected", c["accent"])],
                  foreground=[("selected", "white")])
        scrollbar = ctk.CTkScrollbar(frm_tree, orientation="vertical",
                                     command=self.tree.yview,
                                     fg_color=c["input_bg"],
                                     button_color=c["border"],
                                     button_hover_color=c["text_dim"],
                                     corner_radius=6, width=10)
        self.tree.configure(yscrollcommand=scrollbar.set)
        self.tree.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y", padx=(2, 0))

        # 进度条（默认隐藏）
        self.frm_prog = ctk.CTkFrame(body_files, fg_color="transparent")
        self.lbl_progress = ctk.CTkLabel(self.frm_prog, text="",
                                         font=self._font(10),
                                         text_color=c["text_secondary"])
        self.lbl_progress.pack(side="left", padx=(0, 10))
        self.bar_progress = ctk.CTkProgressBar(
            self.frm_prog, height=10,
            fg_color=c["input_bg"], progress_color=c["accent"],
            corner_radius=5,
        )
        self.bar_progress.pack(side="left", fill="x", expand=True)
        self.bar_progress.set(0)

    # ─── Cookie 持久化 ───

    def _save_cookie(self, cookie_str: str) -> None:
        try:
            COOKIE_FILE.write_text(json.dumps({"cookie": cookie_str}, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def _load_saved_cookie(self) -> None:
        try:
            if COOKIE_FILE.exists():
                data = json.loads(COOKIE_FILE.read_text(encoding="utf-8"))
                cookie = data.get("cookie", "")
                if cookie:
                    self.ent_cookie.insert("0.0", cookie)
                    self.log("已加载上次保存的 Cookie", "ok")
        except Exception:
            pass
        self._update_manage_btn()

    # ─── 获取 Cookie / 管理账号 ───

    def _open_account_manager(self) -> None:
        """打开网盘管理窗口"""
        self.log("正在打开网盘管理窗口...")
        # pywebview 必须在主线程运行，使用 after 延迟调用
        self.root.after(100, open_manage_window)

    def _open_cloud_browser(self) -> None:
        """打开云盘文件浏览器"""
        cookie = self._get_cookie()
        if not cookie:
            messagebox.showwarning("提示", "请先获取或输入 Cookie")
            return
        client = QuarkClient(cookie)
        try:
            client.account_info()
        except Exception:
            messagebox.showerror("错误", "Cookie 无效，请重新获取")
            return
        dlg = CloudFileBrowser(self.root, client)
        self.root.wait_window(dlg)
        if dlg.result:
            self.ent_fid.configure(state="normal")
            self.ent_fid.delete(0, "end")
            self.ent_fid.insert(0, ",".join(dlg.result))
            self._on_input_change()
            self.log(f"已从网盘选择 {len(dlg.result)} 个文件", "ok")

    def _open_login_browser(self) -> None:
        """打开登录窗口获取 Cookie"""
        self.btn_login.configure(state="disabled", text="登录中...")
        self.log("正在打开夸克登录页面，请在弹出的窗口中登录...")

        def do_login():
            cookie_file = _exe_dir() / ".quarkdl_login_cookie.tmp"
            cookie = open_login_window(cookie_file)
            if cookie:
                self.ent_cookie.delete("0.0", "end")
                self.ent_cookie.insert("0.0", cookie)
                self._save_cookie(cookie)
                self.log("Cookie 获取成功！", "ok")
                self._update_manage_btn()
            else:
                self.log("未获取到 Cookie，请重试", "warn")
            self.btn_login.configure(state="normal", text="🌐 获取 Cookie")

        # pywebview 必须在主线程运行
        self.root.after(100, do_login)

    # ─── 日志 ───

    def log(self, msg: str, tag: str = "normal") -> None:
        c = self.c
        colors = {"normal": c["text"], "ok": c["green"], "err": c["red"], "warn": c["yellow"]}
        self.txt_log.configure(state="normal")
        self.txt_log.insert("end", msg + "\n")
        self.txt_log.see("end")
        try:
            line_start = self.txt_log.index("end-2l linestart")
            line_end = self.txt_log.index("end-1l lineend")
            self.txt_log._textbox.tag_add(tag, line_start, line_end)
            self.txt_log._textbox.tag_configure(tag, foreground=colors.get(tag, c["text"]))
        except Exception:
            pass
        self.txt_log.configure(state="disabled")

    # ─── 辅助 ───

    def _browse_output(self) -> None:
        d = filedialog.askdirectory(title="选择下载目录")
        if d:
            self.var_output.set(d)

    def _update_manage_btn(self) -> None:
        """Cookie 有内容时启用管理账号按钮"""
        has_cookie = bool(self._get_cookie())
        self.btn_manage.configure(state="normal" if has_cookie else "disabled")

    def _on_input_change(self) -> None:
        """分享链接与 FID 互斥：一方有内容时禁用另一方"""
        c = self.c
        url_has = bool(self.ent_url.get().strip())
        fid_has = bool(self.ent_fid.get().strip())

        if url_has:
            self.ent_fid.configure(state="disabled", fg_color=c["border"], text_color=c["text_dim"])
        else:
            self.ent_fid.configure(state="normal", fg_color=c["input_bg"], text_color=c["text"])

        if fid_has:
            self.ent_url.configure(state="disabled", fg_color=c["border"], text_color=c["text_dim"])
            self.btn_parse.configure(state="disabled")
        else:
            self.ent_url.configure(state="normal", fg_color=c["input_bg"], text_color=c["text"])
            self.btn_parse.configure(state="normal")

    def _get_cookie(self) -> str:
        return self.ent_cookie.get("0.0", "end").strip()

    def _get_int(self, var: tk.Variable, default: int) -> int:
        try:
            return int(float(var.get()))
        except (ValueError, TypeError):
            return default

    @staticmethod
    def _clamp(v: int, lo: int, hi: int) -> int:
        return max(lo, min(hi, v))

    def _sync_ent_from_sld(self, entry: ctk.CTkEntry, value: float,
                           lo: int, hi: int) -> None:
        v = self._clamp(int(float(value)), lo, hi)
        entry.delete(0, "end")
        entry.insert(0, str(v))

    def _bind_entry_to_slider(self, entry: ctk.CTkEntry, slider: ctk.CTkSlider,
                               var: tk.IntVar, lo: int, hi: int) -> None:
        def apply(_event=None):
            try:
                v = self._clamp(int(entry.get().strip()), lo, hi)
            except ValueError:
                v = var.get()
            var.set(v)
            slider.set(v)
            entry.delete(0, "end")
            entry.insert(0, str(v))
        entry.bind("<Return>", apply)
        entry.bind("<FocusOut>", apply)

    # ─── 从转存任务结果提取文件信息 ───

    def _extract_files_from_task(self, task_result: dict, fid_list: list[dict]) -> list[dict]:
        """尝试从转存任务结果中提取文件信息，避免全盘扫描"""
        try:
            save_as = task_result.get("save_as", {})
            if not save_as:
                return []
            files = save_as.get("save_as_top_fids", [])
            if not files:
                return []
            result = []
            for fid_info in files:
                fid = fid_info.get("fid", "")
                if not fid:
                    continue
                original = next((f for f in fid_list if f["file_name"] == fid_info.get("file_name")), None)
                if original:
                    result.append({
                        "fid": fid,
                        "file_name": original["file_name"],
                        "size": original.get("size", 0),
                        "is_dir": False,
                    })
            return result
        except Exception:
            return []

    # ─── 解析分享链接 ───

    def _parse_link(self) -> None:
        cookie = self._get_cookie()
        url = self.ent_url.get().strip()
        if not cookie:
            messagebox.showwarning("提示", "请先获取或输入 Cookie")
            return
        if not url:
            messagebox.showwarning("提示", "请输入分享链接")
            return
        if "quark" not in url.lower():
            messagebox.showwarning("提示", "请输入合法的夸克网盘分享链接")
            return

        self.btn_parse.configure(state="disabled", text="解析中...")
        self.tree.delete(*self.tree.get_children())
        self.log("正在解析分享链接...")

        def worker():
            try:
                client = QuarkClient(cookie)
                pwd_id, passcode = parse_share_url(url)
                stoken = client.get_stoken(pwd_id, passcode)
                all_files = client.list_share_files(pwd_id, stoken)
                file_list = [f for f in all_files if not f["is_dir"]]

                self._parsed_pwd_id = pwd_id
                self._parsed_stoken = stoken

                # 弹出文件选择窗口
                def show_dialog():
                    dlg = FileSelectDialog(self.root, all_files)
                    self.root.wait_window(dlg)
                    if dlg.result:
                        self._parsed_files = dlg.result
                        self._populate_tree(dlg.result)
                        self.log(f"已选择 {len(dlg.result)} 个文件", "ok")
                    else:
                        self._parsed_files = []
                        self.log("已取消选择", "warn")

                self.root.after(0, show_dialog)
            except Exception as e:
                self.root.after(0, lambda: self.log(f"解析失败: {e}", "err"))
                self.root.after(0, lambda: messagebox.showerror("错误", str(e)))
            finally:
                self.root.after(0, lambda: self.btn_parse.configure(state="normal", text="🔍 解析链接"))

        threading.Thread(target=worker, daemon=True).start()

    def _populate_tree(self, file_list: list[dict]) -> None:
        self.tree.delete(*self.tree.get_children())
        for f in file_list:
            self.tree.insert("", "end", iid=f["fid"],
                             values=(f["file_name"], human_bytes(f["size"]), "等待点击开始下载"))

    # ─── 下载 ───

    def _cancel_download(self) -> None:
        self.cancel_event.set()
        self.log("正在取消...", "warn")
        self.btn_cancel.configure(state="disabled", text="取消中...")

    def _start_download(self) -> None:
        if self.downloading:
            return

        cookie = self._get_cookie()
        if not cookie:
            messagebox.showwarning("提示", "请先获取或输入 Cookie")
            return

        fid_input = self.ent_fid.get().strip()
        fid_list = getattr(self, "_parsed_files", None)

        if not fid_input and not fid_list:
            messagebox.showwarning("提示", "请输入分享链接并解析，或输入文件 fid")
            return

        self._save_cookie(cookie)

        self.cancel_event.clear()
        self.downloading = True
        self.btn_download.configure(state="disabled", text="下载中...")
        self.btn_cancel.configure(state="normal", text="✕ 取消",
                                  fg_color="#fee2e2", text_color="#ef4444",
                                  hover_color="#fecaca")
        self.frm_prog.pack(fill="x", padx=4, pady=(0, 4))
        self.bar_progress.set(0)
        self.lbl_progress.configure(text="")
        workers = self._get_int(self.var_workers, 64)
        chunk_mb = self._get_int(self.var_chunk, 1)
        output = self.var_output.get()

        def worker():
            try:
                client = QuarkClient(cookie)
                downloader = MultiThreadDownloader(
                    client=client, output_dir=output,
                    workers=workers, chunk_size=chunk_mb * 1024 * 1024,
                )

                if fid_input:
                    fids = [f.strip() for f in fid_input.split(",") if f.strip()]
                    self.root.after(0, lambda: self.log(f"直接下载 {len(fids)} 个文件"))

                    # 递归扫描网盘，查找所有 FID
                    fid_map: dict[str, dict] = {}
                    scan_queue = ["0"]
                    while scan_queue:
                        pdir = scan_queue.pop(0)
                        try:
                            for f in client.list_files(pdir):
                                fid_map[f["fid"]] = f
                                if f["is_dir"]:
                                    scan_queue.append(f["fid"])
                        except Exception:
                            pass

                    # 填充文件列表
                    tree_files = []
                    for fid in fids:
                        info = fid_map.get(fid)
                        if info:
                            tree_files.append(info)
                        else:
                            tree_files.append({"fid": fid, "file_name": fid, "size": 0, "is_dir": False})
                    self.root.after(0, lambda: self._populate_tree(tree_files))

                    for i, fid in enumerate(fids, 1):
                        if self.cancel_event.is_set():
                            self.root.after(0, self._update_status, fid, "已取消", "warn")
                            break
                        info = fid_map.get(fid)
                        name = info["file_name"] if info else fid
                        size = info["size"] if info else 0
                        self.root.after(0, self._update_status, fid, "下载中...")
                        self.root.after(0, lambda n=name, idx=i, total=len(fids):
                                        self.log(f"[{idx}/{total}] {n}"))
                        try:
                            self._download_with_progress(downloader, client, fid, name, size)
                            self.root.after(0, self._update_status, fid, "✓ 完成", "ok")
                            self.root.after(0, self._update_progress, 100, "✓ 完成")
                            self.root.after(0, lambda n=name: self.log(f"  ✓ {n}", "ok"))
                        except InterruptedError:
                            self.root.after(0, self._update_status, fid, "已取消", "warn")
                            break
                        except Exception as e:
                            self.root.after(0, self._update_status, fid, "✗ 失败", "err")
                            self.root.after(0, lambda n=name, err=str(e): self.log(f"  ✗ {n}: {err}", "err"))

                elif fid_list:
                    self.root.after(0, lambda: self.log(f"开始下载 {len(fid_list)} 个文件, {workers} 线程"))
                    self.root.after(0, lambda: self.log("转存中..."))
                    pwd_id = self._parsed_pwd_id
                    stoken = self._parsed_stoken
                    fids = [f["fid"] for f in fid_list]
                    tokens = [f["share_fid_token"] for f in fid_list]
                    task_id = client.save_share(pwd_id, stoken, fids, tokens)
                    self.root.after(0, lambda: self.log(f"等待转存任务完成 (task_id: {task_id[:8]}...)"))
                    task_result = client.poll_task(task_id)
                    self.root.after(0, lambda: self.log("转存完成，正在获取文件信息..."))

                    real_files = self._extract_files_from_task(task_result, fid_list)

                    if not real_files:
                        self.root.after(0, lambda: self.log("任务结果无文件信息，扫描网盘中..."))
                        try:
                            my_files = client.list_files("0", log_func=lambda msg: self.root.after(0, lambda: self.log(msg)))
                            name_map = {f["file_name"]: f for f in my_files if not f["is_dir"]}
                            real_files = [name_map[f["file_name"]] for f in fid_list if f["file_name"] in name_map]
                            self.root.after(0, lambda: self.log(f"扫描完成，匹配 {len(real_files)}/{len(fid_list)}", "ok"))
                        except Exception as e:
                            self.root.after(0, lambda: self.log(f"扫描网盘失败: {e}", "err"))
                            real_files = []
                    else:
                        self.root.after(0, lambda: self.log(f"从任务结果获取 {len(real_files)} 个文件", "ok"))

                    if not real_files:
                        self.root.after(0, lambda: self.log("未找到可下载的文件", "err"))
                        return

                    self.root.after(0, lambda: self._populate_tree(real_files))

                    for i, finfo in enumerate(real_files, 1):
                        if self.cancel_event.is_set():
                            break
                        fid = finfo["fid"]
                        name = finfo["file_name"]
                        size = finfo["size"]
                        self.root.after(0, self._update_status, fid, "下载中...")
                        self.root.after(0, lambda n=name, idx=i, total=len(real_files):
                                        self.log(f"[{idx}/{total}] {n} ({human_bytes(size)})"))
                        try:
                            self._download_with_progress(downloader, client, fid, name, size)
                            self.root.after(0, self._update_status, fid, "✓ 完成", "ok")
                            self.root.after(0, self._update_progress, 100, "✓ 完成")
                            self.root.after(0, lambda n=name: self.log(f"  ✓ {n}", "ok"))
                        except InterruptedError:
                            self.root.after(0, self._update_status, fid, "已取消", "warn")
                            break
                        except Exception as e:
                            self.root.after(0, self._update_status, fid, "✗ 失败", "err")
                            self.root.after(0, lambda n=name, err=str(e): self.log(f"  ✗ {n}: {err}", "err"))

                if not self.cancel_event.is_set():
                    self.root.after(0, lambda: self.log("下载任务结束", "ok"))
            except InterruptedError:
                self.root.after(0, lambda: self.log("已取消", "warn"))
            except Exception as e:
                self.root.after(0, lambda: self.log(f"失败: {e}", "err"))
                self.root.after(0, lambda: messagebox.showerror("错误", str(e)))
            finally:
                self.root.after(0, self._finish_download)

        threading.Thread(target=worker, daemon=True).start()

    def _download_with_progress(self, downloader: MultiThreadDownloader,
                                client: QuarkClient, fid: str, filename: str, size: int) -> None:
        self.root.after(0, lambda: self.log(f"  获取下载链接..."))
        url = client.get_download_url(fid)
        self.root.after(0, lambda: self.log(f"  下载链接获取完成"))

        # 进度追踪状态
        last_print = time.time()
        last_bytes = 0
        smooth_speed = 0.0

        def on_progress(done_bytes: int, total: int) -> None:
            nonlocal last_print, last_bytes, smooth_speed
            now = time.time()
            elapsed = now - last_print
            if elapsed >= 1.0 or done_bytes >= total:
                instant = (done_bytes - last_bytes) / elapsed if elapsed > 0 else 0
                smooth_speed = instant if smooth_speed == 0 else smooth_speed * 0.6 + instant * 0.4
                pct = done_bytes * 100 // total if total else 0
                self.root.after(0, self._update_status, fid, f"{pct}% | {human_bytes(smooth_speed)}/s")
                self.root.after(0, self._update_progress, pct, f"{pct}% | {human_bytes(smooth_speed)}/s")
                last_print = now
                last_bytes = done_bytes

        def is_cancelled() -> bool:
            return self.cancel_event.is_set()

        try:
            downloader.download_file(
                fid, filename=filename, size=size, url=url,
                on_progress=on_progress, is_cancelled=is_cancelled,
            )
        except InterruptedError:
            # 取消时清理流式下载的 .part 文件（分片下载已在 downloader 中清理）
            part = downloader.output_dir / (filename + ".part")
            part.unlink(missing_ok=True)
            raise

    def _update_status(self, fid: str, status: str, tag: str = "normal") -> None:
        try:
            self.tree.set(fid, "status", status)
        except Exception:
            pass

    def _update_progress(self, pct: float, label: str = "") -> None:
        self.bar_progress.set(pct / 100)
        if label:
            self.lbl_progress.configure(text=label)

    def _finish_download(self) -> None:
        self.downloading = False
        self.btn_download.configure(state="normal", text="⬇ 开始下载")
        self.btn_cancel.configure(state="disabled", text="✕ 取消",
                                  fg_color=self.c["card"], text_color=self.c["text_dim"])
        self.frm_prog.pack_forget()
