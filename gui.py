"""
GUI 界面
"""

from __future__ import annotations

import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path

import customtkinter as ctk

from client import QuarkClient
from downloader import MultiThreadDownloader, _download_chunk
from scripts import LOGIN_SCRIPT, MANAGE_SCRIPT, write_temp_script
from utils import (
    _exe_dir, _resource_dir, COOKIE_FILE, FONT, FONT_MONO,
    human_bytes, plan_ranges, parse_share_url
)


class Cancelled(Exception):
    pass


class QuarkGUI:
    """夸克网盘下载器 GUI"""

    def __init__(self, root: ctk.CTk) -> None:
        self.root = root
        self.root.title("夸克网盘多线程下载器")
        self.root.geometry("860x860")
        self.root.minsize(700, 600)
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

    # ─── helpers ───

    def _card(self, parent, **pack_kw) -> ctk.CTkFrame:
        card = ctk.CTkFrame(parent, fg_color=self.c["card"],
                            border_color=self.c["border"],
                            border_width=1, corner_radius=10)
        defaults = {"fill": "x", "padx": 12, "pady": 5}
        defaults.update(pack_kw)
        card.pack(**defaults)
        return card

    def _section(self, parent: ctk.CTkFrame, title: str) -> ctk.CTkFrame:
        card = self._card(parent)
        ctk.CTkLabel(card, text=title,
                     font=ctk.CTkFont(family=FONT, size=14, weight="bold"),
                     text_color=self.c["text"],
                     anchor="w").pack(anchor="w", padx=18, pady=(14, 2))
        body = ctk.CTkFrame(card, fg_color="transparent")
        body.pack(fill="x", padx=18, pady=(6, 14))
        return body

    # ─── UI construction ───

    def _build_ui(self) -> None:
        c = self.c

        # ── Header bar ──
        bar = ctk.CTkFrame(self.root, fg_color=c["card"], corner_radius=0,
                           border_color=c["border"], border_width=1)
        bar.pack(fill="x")
        inner = ctk.CTkFrame(bar, fg_color="transparent")
        inner.pack(fill="x", padx=22, pady=(16, 14))
        ctk.CTkLabel(inner, text="夸克网盘下载器",
                     font=ctk.CTkFont(family=FONT, size=16, weight="bold"),
                     text_color=c["text"]).pack(side="left")
        ctk.CTkLabel(inner, text="获取 cookie 需要 python3.8+ 环境",
                     font=ctk.CTkFont(family=FONT, size=10),
                     text_color=c["text_dim"]).pack(side="right")

        # ── 1. Cookie ──
        body1 = self._section(self.root, "🔑  Cookie")

        btn_row = ctk.CTkFrame(body1, fg_color="transparent")
        btn_row.pack(fill="x", pady=(0, 8))
        self.btn_login = ctk.CTkButton(
            btn_row, text="🌐 获取 Cookie",
            command=self._open_login_browser,
            fg_color=c["accent"], hover_color=c["accent_hover"],
            text_color="white", font=ctk.CTkFont(family=FONT, size=11),
            corner_radius=8, width=40, height=32,
        )
        self.btn_login.pack(side="right")
        self.btn_manage = ctk.CTkButton(
            btn_row, text="👤 管理账号",
            command=self._open_account_manager,
            fg_color=c["card"], hover_color=c["input_bg"],
            text_color=c["text"], font=ctk.CTkFont(family=FONT, size=11),
            corner_radius=8, width=40, height=32,
            border_color=c["border"], border_width=1,
        )
        self.btn_manage.pack(side="right", padx=(0, 8))

        self.ent_cookie = ctk.CTkTextbox(
            body1, height=68,
            fg_color=c["input_bg"], text_color=c["text"],
            border_color=c["border"], border_width=1,
            corner_radius=6, font=ctk.CTkFont(family=FONT_MONO, size=10),
            wrap="word", activate_scrollbars=False,
        )
        self.ent_cookie.pack(fill="x")

        # ── 2. 参数 / 日志  Tabview ──
        card_tab = self._card(self.root, fill="both", expand=True)
        ctk.CTkLabel(card_tab, text="⬇  下载参数",
                     font=ctk.CTkFont(family=FONT, size=14, weight="bold"),
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
            font=ctk.CTkFont(family=FONT, size=13, weight="bold"),
            corner_radius=20,
        )

        tab_params = self.tabview.add("  参数  ")
        tab_log = self.tabview.add("  日志  ")

        # ── 参数 tab ──
        body2 = ctk.CTkFrame(tab_params, fg_color="transparent")
        body2.pack(fill="both", expand=True, padx=4, pady=(8, 4))

        ctk.CTkLabel(body2, text="分享链接",
                     font=ctk.CTkFont(family=FONT, size=10),
                     text_color=c["text_secondary"],
                     anchor="w").pack(anchor="w", pady=(0, 2))
        self.ent_url = ctk.CTkEntry(
            body2, height=36,
            fg_color=c["input_bg"], text_color=c["text"],
            border_color=c["border"], corner_radius=6,
            font=ctk.CTkFont(family=FONT_MONO, size=11),
        )
        self.ent_url.pack(fill="x", pady=(0, 10))

        ctk.CTkLabel(body2, text="文件 FID（可选）",
                     font=ctk.CTkFont(family=FONT, size=10),
                     text_color=c["text_secondary"],
                     anchor="w").pack(anchor="w", pady=(0, 2))
        self.ent_fid = ctk.CTkEntry(
            body2, height=36,
            fg_color=c["input_bg"], text_color=c["text"],
            border_color=c["border"], corner_radius=6,
            font=ctk.CTkFont(family=FONT_MONO, size=11),
        )
        self.ent_fid.pack(fill="x", pady=(0, 12))

        # ── 线程数 ──
        row1 = ctk.CTkFrame(body2, fg_color="transparent")
        row1.pack(fill="x", pady=(0, 8))
        ctk.CTkLabel(row1, text="线程数",
                     font=ctk.CTkFont(family=FONT, size=12),
                     text_color=c["text_secondary"]).pack(side="left")
        self.var_workers = tk.IntVar(value=32)
        self.ent_workers = ctk.CTkEntry(row1, width=56, height=28,
                                        text_color=c["text"],
                                        fg_color=c["input_bg"],
                                        border_color=c["border"], corner_radius=6,
                                        font=ctk.CTkFont(family=FONT_MONO, size=12),
                                        justify="center")
        self.ent_workers.pack(side="right", padx=(0, 4))
        self.ent_workers.insert(0, "32")
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
                     font=ctk.CTkFont(family=FONT, size=12),
                     text_color=c["text_secondary"]).pack(side="left")
        self.var_chunk = tk.IntVar(value=1)
        self.ent_chunk = ctk.CTkEntry(row2, width=56, height=28,
                                      text_color=c["text"],
                                      fg_color=c["input_bg"],
                                      border_color=c["border"], corner_radius=6,
                                      font=ctk.CTkFont(family=FONT_MONO, size=12),
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
        ctk.CTkLabel(row3, text="输出目录",
                     font=ctk.CTkFont(family=FONT, size=12),
                     text_color=c["text_secondary"]).pack(side="left")
        self.var_output = tk.StringVar(value=str(_exe_dir() / "downloads"))
        ctk.CTkEntry(row3, textvariable=self.var_output,
                     height=32,
                     fg_color=c["input_bg"], text_color=c["text"],
                     border_color=c["border"], corner_radius=6,
                     font=ctk.CTkFont(family=FONT_MONO, size=10),
                     ).pack(side="left", fill="x", expand=True, padx=(6, 6))
        ctk.CTkButton(row3, text="...", width=36, height=32,
                      command=self._browse_output,
                      fg_color=c["input_bg"], hover_color=c["border"],
                      text_color=c["text"], corner_radius=6,
                      font=ctk.CTkFont(family=FONT, size=10, weight="bold"),
                      border_color=c["border"], border_width=1,
                      ).pack(side="left")

        # ── 日志 tab ──
        self.txt_log = ctk.CTkTextbox(
            tab_log,
            fg_color=c["card"], text_color=c["text"],
            border_color=c["border"], border_width=1,
            corner_radius=6, font=ctk.CTkFont(family=FONT_MONO, size=10),
            wrap="word", activate_scrollbars=False,
        )
        self.txt_log.pack(fill="both", expand=True, padx=4, pady=(8, 4))
        self.txt_log.configure(state="disabled")

        # ── 3. Actions ──
        frm_actions = ctk.CTkFrame(self.root, fg_color="transparent")
        frm_actions.pack(fill="x", padx=12, pady=(10, 5))
        self.btn_parse = ctk.CTkButton(
            frm_actions, text="🔍 解析链接",
            command=self._parse_link,
            fg_color=c["card"], hover_color=c["input_bg"],
            text_color=c["text"], corner_radius=8,
            font=ctk.CTkFont(family=FONT, size=11),
            border_color=c["border"], border_width=1,
            width=120, height=36,
        )
        self.btn_parse.pack(side="left", padx=(0, 10))
        self.btn_download = ctk.CTkButton(
            frm_actions, text="⬇ 开始下载",
            command=self._start_download,
            fg_color=c["accent"], hover_color=c["accent_hover"],
            text_color="white", corner_radius=8,
            font=ctk.CTkFont(family=FONT, size=11, weight="bold"),
            width=130, height=36,
        )
        self.btn_download.pack(side="left", padx=(0, 10))
        self.btn_cancel = ctk.CTkButton(
            frm_actions, text="✕ 取消",
            command=self._cancel_download,
            fg_color=c["card"], hover_color="#fee2e2",
            text_color=c["text_dim"], corner_radius=8,
            font=ctk.CTkFont(family=FONT, size=11),
            border_color=c["border"], border_width=1,
            width=100, height=36,
            state="disabled",
        )
        self.btn_cancel.pack(side="left")

        # ── 4. File list ──
        body3 = self._section(self.root, "📂  文件列表")
        frm_tree = tk.Frame(body3, bg=c["border"])
        frm_tree.pack(fill="x", padx=0, pady=0)
        self.tree = ttk.Treeview(frm_tree, columns=("name", "size", "status"),
                                 show="headings", height=5)
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
        self.tree.pack(fill="x")

        # progress bar
        frm_prog = ctk.CTkFrame(body3, fg_color="transparent")
        frm_prog.pack(fill="x", padx=16, pady=(0, 14))
        self.lbl_progress = ctk.CTkLabel(frm_prog, text="",
                                         font=ctk.CTkFont(family=FONT, size=10),
                                         text_color=c["text_secondary"])
        self.lbl_progress.pack(side="left", padx=(0, 10))
        self.bar_progress = ctk.CTkProgressBar(
            frm_prog, height=10,
            fg_color=c["input_bg"], progress_color=c["accent"],
            corner_radius=5,
        )
        self.bar_progress.pack(side="left", fill="x", expand=True)
        self.bar_progress.set(0)

    # ═══════════════════════════════════════════
    #  Cookie 持久化
    # ═══════════════════════════════════════════

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

    # ═══════════════════════════════════════════
    #  获取 Cookie / 管理账号
    # ═══════════════════════════════════════════

    def _find_python(self) -> str:
        """查找 Python 解释器"""
        if not getattr(sys, 'frozen', False):
            return sys.executable

        python_path = shutil.which("python3") or shutil.which("python")
        if python_path:
            return python_path

        possible_paths = [
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python312\python.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python311\python.exe"),
            os.path.expandvars(r"%LOCALAPPDATA%\Programs\Python\Python310\python.exe"),
            os.path.expandvars(r"%PROGRAMFILES%\Python312\python.exe"),
            os.path.expandvars(r"%PROGRAMFILES%\Python311\python.exe"),
            os.path.expandvars(r"%PROGRAMFILES%\Python310\python.exe"),
        ]
        for path in possible_paths:
            if os.path.exists(path):
                return path

        return sys.executable

    def _open_account_manager(self) -> None:
        script = write_temp_script("_quarkdl_manage_tmp.py", MANAGE_SCRIPT, _exe_dir())
        python = self._find_python()
        subprocess.Popen(
            [python, str(script)],
            cwd=str(_exe_dir()),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

    def _open_login_browser(self) -> None:
        self.btn_login.configure(state="disabled", text="登录中...")
        self.log("正在打开夸克登录页面，请在弹出的窗口中登录...")

        cookie_file = _exe_dir() / ".quarkdl_login_cookie.tmp"
        if cookie_file.exists():
            cookie_file.unlink()

        script = write_temp_script("_quarkdl_login_tmp.py", LOGIN_SCRIPT, _exe_dir())
        python = self._find_python()
        self._login_proc = subprocess.Popen(
            [python, str(script), str(cookie_file)],
            cwd=str(_exe_dir()),
            creationflags=subprocess.CREATE_NO_WINDOW,
        )

        def wait_for_result():
            if cookie_file.exists():
                cookie = cookie_file.read_text(encoding="utf-8").strip()
                cookie_file.unlink(missing_ok=True)
                if cookie:
                    self.ent_cookie.delete("0.0", "end")
                    self.ent_cookie.insert("0.0", cookie)
                    self._save_cookie(cookie)
                    self.log("Cookie 获取成功！", "ok")
                else:
                    self.log("未检测到登录状态，请重试", "warn")
                self.btn_login.configure(state="normal", text="🌐 获取 Cookie")
            elif self._login_proc.poll() is None:
                self.root.after(500, wait_for_result)
            else:
                self.log("浏览器已关闭，未获取到 Cookie", "warn")
                self.btn_login.configure(state="normal", text="🌐 获取 Cookie")

        self.root.after(1000, wait_for_result)

    # ═══════════════════════════════════════════
    #  日志
    # ═══════════════════════════════════════════

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

    # ═══════════════════════════════════════════
    #  辅助
    # ═══════════════════════════════════════════

    def _browse_output(self) -> None:
        d = filedialog.askdirectory(title="选择输出目录")
        if d:
            self.var_output.set(d)

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

    # ═══════════════════════════════════════════
    #  从转存任务结果提取文件信息
    # ═══════════════════════════════════════════

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

    # ═══════════════════════════════════════════
    #  解析分享链接
    # ═══════════════════════════════════════════

    def _parse_link(self) -> None:
        cookie = self._get_cookie()
        url = self.ent_url.get().strip()
        if not cookie:
            messagebox.showwarning("提示", "请先获取或输入 Cookie")
            return
        if not url:
            messagebox.showwarning("提示", "请输入分享链接")
            return

        self.btn_parse.configure(state="disabled", text="解析中...")
        self.tree.delete(*self.tree.get_children())
        self.log("正在解析分享链接...")

        def worker():
            try:
                client = QuarkClient(cookie)
                pwd_id, passcode = parse_share_url(url)
                stoken = client.get_stoken(pwd_id, passcode)
                files = client.list_share_files(pwd_id, stoken)
                file_list = [f for f in files if not f["is_dir"]]

                self._parsed_files = file_list
                self._parsed_pwd_id = pwd_id
                self._parsed_stoken = stoken

                self.root.after(0, lambda: self._populate_tree(file_list))
                self.root.after(0, lambda: self.log(f"解析完成，共 {len(file_list)} 个文件", "ok"))
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
                             values=(f["file_name"], human_bytes(f["size"]), "等待下载"))

    # ═══════════════════════════════════════════
    #  下载
    # ═══════════════════════════════════════════

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
        self.btn_cancel.configure(state="normal", text="✕ 取消")
        self.bar_progress.set(0)
        self.lbl_progress.configure(text="")
        workers = self._get_int(self.var_workers, 32)
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
                    my_files = client.list_files("0")
                    fid_map = {f["fid"]: f for f in my_files}

                    for i, fid in enumerate(fids, 1):
                        if self.cancel_event.is_set():
                            break
                        info = fid_map.get(fid)
                        name = info["file_name"] if info else fid
                        size = info["size"] if info else 0
                        self.root.after(0, lambda n=name, idx=i, total=len(fids):
                                        self.log(f"[{idx}/{total}] {n}"))
                        try:
                            self._download_with_progress(downloader, client, fid, name, size)
                            self.root.after(0, lambda n=name: self.log(f"  ✓ {n}", "ok"))
                            self.root.after(0, self._update_progress, 100, "✓ 完成")
                        except Cancelled:
                            break
                        except Exception as e:
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
                        except Cancelled:
                            self.root.after(0, self._update_status, fid, "已取消", "warn")
                            break
                        except Exception as e:
                            self.root.after(0, self._update_status, fid, "✗ 失败", "err")
                            self.root.after(0, lambda n=name, err=str(e): self.log(f"  ✗ {n}: {err}", "err"))

                if not self.cancel_event.is_set():
                    self.root.after(0, lambda: self.log("下载任务结束", "ok"))
            except Cancelled:
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
        dest = downloader.output_dir / filename
        chunks_dir = dest.with_name(dest.name + ".chunks")
        downloader.output_dir.mkdir(parents=True, exist_ok=True)

        if dest.exists() and size and dest.stat().st_size == size:
            self.root.after(0, lambda: self.log(f"  文件已存在，跳过"))
            return

        if size and size >= downloader.range_threshold:
            part_file = dest.with_name(dest.name + ".part")
            if part_file.exists():
                part_file.unlink(missing_ok=True)
            chunks_dir.mkdir(parents=True, exist_ok=True)

            ranges = plan_ranges(size, downloader.chunk_size)
            total = len(ranges)
            pending = []
            done_bytes = 0
            for idx, (s, e) in enumerate(ranges):
                cp = chunks_dir / f"{idx:06d}.part"
                if cp.exists() and cp.stat().st_size == e - s + 1:
                    done_bytes += e - s + 1
                else:
                    pending.append((idx, s, e, cp))

            done_count = total - len(pending)
            last_print = time.time()
            last_bytes = done_bytes
            smooth_speed = 0.0

            with concurrent.futures.ThreadPoolExecutor(max_workers=min(downloader.workers, max(1, len(pending)))) as executor:
                futures = {
                    executor.submit(_download_chunk, client.session, url, s, e, cp): (idx, s, e)
                    for idx, s, e, cp in pending
                }
                try:
                    for future in concurrent.futures.as_completed(futures):
                        if self.cancel_event.is_set():
                            for f in futures:
                                f.cancel()
                            raise Cancelled()
                        idx, s, e = futures[future]
                        got = future.result()
                        done_bytes += got
                        done_count += 1

                        now = time.time()
                        elapsed = now - last_print
                        if elapsed >= 1.0 or done_count == total:
                            instant = (done_bytes - last_bytes) / elapsed if elapsed > 0 else 0
                            smooth_speed = instant if smooth_speed == 0 else smooth_speed * 0.6 + instant * 0.4
                            pct = done_bytes * 100 // size if size else 0
                            self.root.after(0, self._update_status, fid, f"{pct}% | {human_bytes(smooth_speed)}/s")
                            self.root.after(0, self._update_progress, pct, f"{pct}% | {human_bytes(smooth_speed)}/s")
                            last_print = now
                            last_bytes = done_bytes
                except Cancelled:
                    executor.shutdown(wait=False, cancel_futures=True)
                    raise

            downloader._assemble(chunks_dir, part_file, ranges)
            part_file.rename(dest)
            shutil.rmtree(chunks_dir, ignore_errors=True)
        else:
            part = dest.with_name(dest.name + ".part")
            downloader._download_stream(url, dest, part, size)

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
        self.btn_cancel.configure(state="disabled", text="✕ 取消")
