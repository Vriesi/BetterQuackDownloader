"""
夸克网盘多线程下载器
"""

from __future__ import annotations

import argparse
import concurrent.futures
import ctypes
import json
import shutil
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from urllib.parse import parse_qs, urlparse
import tkinter.font as tkFont

import requests
import customtkinter as ctk

# ──────────────────────────────────────────────
# DPI + 任务栏图标
# ──────────────────────────────────────────────

try:
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except Exception:
    pass

try:
    ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("quark.downloader.gui")
except Exception:
    pass

# ──────────────────────────────────────────────
# 常量 & 路径
# ──────────────────────────────────────────────

def _exe_dir() -> Path:
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent

def _resource_dir() -> Path:
    """PyInstaller --onefile 解压目录（sys._MEIPASS），源码运行时同 _exe_dir。"""
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent

COOKIE_FILE = _exe_dir() / ".quarkdl_cookies.json"

# 字体配置：优先 Mi Sans，fallback微软雅黑
_FONT_FAMILIES = set()
def _get_font_families():
    if not _FONT_FAMILIES:
        _root = tk.Tk()
        _root.withdraw()
        _FONT_FAMILIES.update(tkFont.families())
        _root.destroy()
    return _FONT_FAMILIES

def _pick_font(preferred, fallback="Microsoft YaHei UI"):
    families = _get_font_families()
    return preferred if preferred in families else fallback

FONT = _pick_font("Mi Sans")
FONT_MONO = _pick_font("Mi Sans Mono", "Cascadia Code")

PC_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) quark-cloud-drive/2.5.56 Chrome/100.0.4896.160 "
    "Electron/18.3.5.12-a038f7b798 Safari/537.36 Channel/pckk_other_ch"
)

WEB_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/143.0.0.0 Safari/537.36 Edg/143.0.0.0"
)

BASE_URL = "https://drive-pc.quark.cn"


# ──────────────────────────────────────────────
# 工具函数
# ──────────────────────────────────────────────


def human_bytes(n: int | float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1000
    return f"{n:.1f} PB"


def plan_ranges(size: int, chunk_size: int) -> list[tuple[int, int]]:
    ranges = []
    start = 0
    while start < size:
        end = min(start + chunk_size - 1, size - 1)
        ranges.append((start, end))
        start = end + 1
    return ranges


def parse_share_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url.strip())
    parts = [p for p in parsed.path.split("/") if p]
    if len(parts) < 2 or parts[0] != "s" or not parts[1]:
        raise ValueError(f"无效的夸克分享链接: {url}")
    pwd_id = parts[1]
    passcode = ""
    for source in (parsed.query, parsed.fragment.split("?", 1)[1] if "?" in parsed.fragment else ""):
        if not source:
            continue
        qs = parse_qs(source)
        passcode = (qs.get("pwd") or qs.get("passcode") or [""])[0]
        if passcode:
            break
    return pwd_id, passcode


def _cdn_headers() -> dict[str, str]:
    return {
        "User-Agent": PC_UA,
        "Origin": "https://pan.quark.cn",
        "Referer": "https://pan.quark.cn/",
    }


def _copy_session(source: requests.Session) -> requests.Session:
    s = requests.Session()
    s.cookies.update(source.cookies)
    return s


# ──────────────────────────────────────────────
# 夸克 API 客户端
# ──────────────────────────────────────────────


class QuarkClient:
    def __init__(self, cookie_str: str) -> None:
        self.session = requests.Session()
        self._parse_cookie(cookie_str)

    def _parse_cookie(self, cookie_str: str) -> None:
        for item in cookie_str.split(";"):
            item = item.strip()
            if "=" in item:
                name, value = item.split("=", 1)
                self.session.cookies.set(name.strip(), value.strip(), domain=".quark.cn")

    @staticmethod
    def _headers(ua: str = WEB_UA) -> dict[str, str]:
        return {
            "User-Agent": ua,
            "Accept": "application/json, text/plain, */*",
            "Content-Type": "application/json",
            "Origin": "https://pan.quark.cn",
            "Referer": "https://pan.quark.cn/",
            "accept-language": "zh-CN,zh;q=0.9",
        }

    @staticmethod
    def _params(extra: dict[str, str] | None = None) -> dict[str, str]:
        p = {"pr": "ucpro", "fr": "pc", "uc_param_str": ""}
        if extra:
            p.update(extra)
        return p

    def _check(self, resp: requests.Response, ctx: str) -> dict:
        try:
            data = resp.json()
        except ValueError:
            raise RuntimeError(f"{ctx}: 非 JSON 响应 HTTP {resp.status_code}")
        if resp.status_code >= 400:
            raise RuntimeError(f"{ctx}: HTTP {resp.status_code} code={data.get('code')} msg={data.get('message')}")
        return data

    def account_info(self) -> dict:
        resp = self.session.get(
            "https://pan.quark.cn/account/info",
            params={"fr": "pc", "platform": "pc"},
            headers=self._headers(),
            timeout=30,
        )
        return self._check(resp, "account_info").get("data", {})

    def get_stoken(self, pwd_id: str, passcode: str = "") -> str:
        resp = self.session.post(
            f"{BASE_URL}/1/clouddrive/share/sharepage/token",
            params=self._params(),
            json={"pwd_id": pwd_id, "passcode": passcode},
            headers=self._headers(),
            timeout=60,
        )
        data = self._check(resp, "share_token")
        stoken = (data.get("data") or {}).get("stoken")
        if not stoken:
            raise RuntimeError(f"获取 stoken 失败: {data.get('message')}")
        return stoken

    def list_share_files(self, pwd_id: str, stoken: str, pdir_fid: str = "0") -> list[dict]:
        all_files = []
        queue = [("", pdir_fid)]
        while queue:
            path_prefix, dir_fid = queue.pop(0)
            page = 1
            while True:
                resp = self.session.get(
                    f"{BASE_URL}/1/clouddrive/share/sharepage/detail",
                    params=self._params({
                        "pwd_id": pwd_id, "stoken": stoken, "pdir_fid": dir_fid,
                        "_page": str(page), "_size": "100", "_fetch_total": "1",
                        "_sort": "file_type:asc,file_name:asc", "ver": "2",
                    }),
                    headers=self._headers(),
                    timeout=60,
                )
                data = self._check(resp, "share_list")
                rows = (data.get("data") or {}).get("list") or []
                for row in rows:
                    name = row.get("file_name", "")
                    fpath = f"{path_prefix}/{name}" if path_prefix else name
                    entry = {
                        "fid": row["fid"],
                        "file_name": name,
                        "path": fpath,
                        "size": int(row.get("size") or 0),
                        "is_dir": bool(row.get("dir")),
                        "share_fid_token": row.get("share_fid_token", ""),
                    }
                    all_files.append(entry)
                    if entry["is_dir"]:
                        queue.append((fpath, row["fid"]))
                if len(rows) < 100:
                    break
                page += 1
        return all_files

    def save_share(self, pwd_id: str, stoken: str, fid_list: list[str],
                   fid_token_list: list[str], to_pdir_fid: str = "0") -> str:
        resp = self.session.post(
            "https://drive.quark.cn/1/clouddrive/share/sharepage/save",
            params=self._params(),
            json={
                "fid_list": fid_list,
                "fid_token_list": fid_token_list,
                "to_pdir_fid": to_pdir_fid,
                "pwd_id": pwd_id,
                "stoken": stoken,
                "pdir_fid": "0",
                "scene": "link",
            },
            headers=self._headers(),
            timeout=120,
        )
        data = self._check(resp, "save_share")
        task_id = (data.get("data") or {}).get("task_id")
        if not task_id:
            raise RuntimeError(f"转存失败: {data.get('message')}")
        return task_id

    def poll_task(self, task_id: str, retries: int = 100, interval: float = 0.9) -> dict:
        for i in range(retries):
            time.sleep(interval)
            resp = self.session.get(
                f"{BASE_URL}/1/clouddrive/task",
                params=self._params({"task_id": task_id, "retry_index": str(i)}),
                headers=self._headers(),
                timeout=60,
            )
            data = self._check(resp, "poll_task")
            td = data.get("data") or {}
            if td.get("status") == 2:
                return td
            # 如果任务失败，提前终止
            if td.get("status") == 3:
                raise RuntimeError(f"转存任务失败: {td.get('message', '未知错误')}")
        raise RuntimeError("转存任务超时")

    def list_files(self, pdir_fid: str = "0", log_func=None) -> list[dict]:
        files = []
        page = 1
        while True:
            if log_func:
                log_func(f"扫描网盘第 {page} 页...")
            resp = self.session.get(
                f"{BASE_URL}/1/clouddrive/file/sort",
                params=self._params({
                    "pdir_fid": pdir_fid, "_page": str(page), "_size": "100",
                    "_fetch_total": "1", "_sort": "file_type:asc,file_name:asc",
                }),
                headers=self._headers(),
                timeout=60,
            )
            data = self._check(resp, "list_files")
            rows = (data.get("data") or {}).get("list") or []
            for row in rows:
                files.append({
                    "fid": row["fid"],
                    "file_name": row.get("file_name", ""),
                    "size": int(row.get("size") or 0),
                    "is_dir": bool(row.get("dir")),
                })
            if len(rows) < 100:
                break
            page += 1
            # 防止无限循环
            if page > 100:
                if log_func:
                    log_func("警告：扫描页数超过 100 页，停止扫描")
                break
        return files

    def get_download_url(self, fid: str) -> str:
        resp = self.session.post(
            f"{BASE_URL}/1/clouddrive/file/download",
            params=self._params({"sys": "win32", "ve": "2.5.56", "ut": "", "guid": ""}),
            json={"fids": [fid]},
            headers=self._headers(PC_UA),
            timeout=60,
        )
        data = self._check(resp, "download_url")
        rows = data.get("data") or []
        if data.get("status") != 200 or not rows or not rows[0].get("download_url"):
            raise RuntimeError(f"获取下载链接失败: code={data.get('code')} msg={data.get('message')}")
        return rows[0]["download_url"]


# ──────────────────────────────────────────────
# 多线程下载器
# ──────────────────────────────────────────────


class MultiThreadDownloader:
    def __init__(
        self,
        client: QuarkClient,
        output_dir: str = "./downloads",
        workers: int = 32,
        chunk_size: int = 1024 * 1024,
        range_threshold: int = 2 * 1024 * 1024,
    ) -> None:
        self.client = client
        self.output_dir = Path(output_dir)
        self.workers = workers
        self.chunk_size = chunk_size
        self.range_threshold = range_threshold

    def download_file(self, fid: str, filename: str | None = None, size: int | None = None) -> Path:
        info = self.client.get_download_url(fid)
        url = info if isinstance(info, str) else info

        if not filename:
            resp = self.client.session.post(
                f"{BASE_URL}/1/clouddrive/file/download",
                params=self.client._params({"sys": "win32", "ve": "2.5.56"}),
                json={"fids": [fid]},
                headers=self.client._headers(PC_UA),
                timeout=60,
            )
            data = resp.json()
            rows = data.get("data") or []
            if rows and rows[0].get("file_name"):
                filename = rows[0]["file_name"]
            else:
                parsed = urlparse(url)
                fname = Path(parsed.path).name
                filename = fname if fname and "." in fname else f"{fid}.bin"

        dest = self.output_dir / filename
        part = dest.with_name(dest.name + ".part")
        chunks_dir = dest.with_name(dest.name + ".chunks")
        self.output_dir.mkdir(parents=True, exist_ok=True)

        if dest.exists() and size and dest.stat().st_size == size:
            return dest

        if not size:
            try:
                head = requests.head(url, headers=_cdn_headers(), timeout=15, allow_redirects=True)
                size = int(head.headers.get("Content-Length", 0))
            except Exception:
                size = 0

        if size and size >= self.range_threshold:
            self._download_ranged(url, dest, part, chunks_dir, size)
        else:
            self._download_stream(url, dest, part, size)

        return dest

    def _download_stream(self, url: str, dest: Path, part: Path, expected_size: int | None) -> None:
        existing = part.stat().st_size if part.exists() else 0
        headers = _cdn_headers()
        mode = "wb"

        if 0 < existing and expected_size and existing < expected_size:
            headers["Range"] = f"bytes={existing}-"
            mode = "ab"
        elif existing and expected_size and existing >= expected_size:
            part.rename(dest)
            return

        with self.client.session.get(url, headers=headers, stream=True, timeout=(30, 180)) as resp:
            if existing > 0 and resp.status_code == 200:
                existing = 0
                mode = "wb"
            if resp.status_code not in (200, 206):
                raise RuntimeError(f"HTTP {resp.status_code}")

            written = existing
            with part.open(mode) as f:
                for block in resp.iter_content(chunk_size=256 * 1024):
                    if not block:
                        continue
                    f.write(block)
                    written += len(block)

        if expected_size and part.stat().st_size != expected_size:
            raise RuntimeError(f"大小不匹配: 期望 {expected_size}, 实际 {part.stat().st_size}")
        part.rename(dest)

    def _download_ranged(self, url: str, dest: Path, part: Path, chunks_dir: Path, expected_size: int) -> None:
        if part.exists():
            part.unlink()
        chunks_dir.mkdir(parents=True, exist_ok=True)

        ranges = plan_ranges(expected_size, self.chunk_size)
        pending = []
        for idx, (start, end) in enumerate(ranges):
            chunk_path = chunks_dir / f"{idx:06d}.part"
            chunk_len = end - start + 1
            if not (chunk_path.exists() and chunk_path.stat().st_size == chunk_len):
                pending.append((idx, start, end, chunk_path))

        if not pending:
            self._assemble(chunks_dir, part, ranges)
            part.rename(dest)
            shutil.rmtree(chunks_dir, ignore_errors=True)
            return

        with concurrent.futures.ThreadPoolExecutor(max_workers=min(self.workers, len(pending))) as executor:
            futures = {
                executor.submit(_download_chunk, self.client.session, url, start, end, chunk_path): (idx, start, end)
                for idx, start, end, chunk_path in pending
            }
            for future in concurrent.futures.as_completed(futures):
                idx, start, end = futures[future]
                try:
                    future.result()
                except Exception as e:
                    for f in futures:
                        f.cancel()
                    raise RuntimeError(f"分片 {idx} 失败: {e}") from e

        self._assemble(chunks_dir, part, ranges)
        if part.stat().st_size != expected_size:
            raise RuntimeError(f"大小不匹配: {expected_size} vs {part.stat().st_size}")
        part.rename(dest)
        shutil.rmtree(chunks_dir, ignore_errors=True)

    def _assemble(self, chunks_dir: Path, part: Path, ranges: list[tuple[int, int]]) -> None:
        tmp = part.with_suffix(".assembling")
        with tmp.open("wb") as out:
            for idx, (start, end) in enumerate(ranges):
                chunk_path = chunks_dir / f"{idx:06d}.part"
                expected = end - start + 1
                if not chunk_path.exists() or chunk_path.stat().st_size != expected:
                    raise RuntimeError(f"分片 {idx} 缺失")
                with chunk_path.open("rb") as fh:
                    shutil.copyfileobj(fh, out, length=1024 * 1024)
        tmp.replace(part)


def _download_chunk(
    source_session: requests.Session,
    url: str,
    start: int,
    end: int,
    chunk_path: Path,
    retries: int = 5,
) -> int:
    expected = end - start + 1
    if chunk_path.exists() and chunk_path.stat().st_size == expected:
        return expected

    tmp = chunk_path.with_suffix(".tmp")
    headers = _cdn_headers()
    headers["Range"] = f"bytes={start}-{end}"

    last_error = "unknown"
    for attempt in range(1, retries + 1):
        try:
            session = _copy_session(source_session)
            with session.get(url, headers=headers, stream=True, timeout=(15, 60)) as resp:
                if resp.status_code != 206:
                    last_error = f"HTTP {resp.status_code}"
                    time.sleep(min(2 ** attempt, 12))
                    continue
                written = 0
                with tmp.open("wb") as f:
                    for block in resp.iter_content(chunk_size=256 * 1024):
                        if not block:
                            continue
                        f.write(block)
                        written += len(block)
                if written != expected:
                    last_error = f"大小不匹配: {written} vs {expected}"
                    tmp.unlink(missing_ok=True)
                    time.sleep(min(2 ** attempt, 12))
                    continue
                tmp.replace(chunk_path)
                return written
        except Exception as exc:
            last_error = str(exc)
            tmp.unlink(missing_ok=True)
            time.sleep(min(2 ** attempt, 12))

    raise RuntimeError(f"分片 bytes={start}-{end} 失败 ({retries} 次重试): {last_error}")


# ──────────────────────────────────────────────
# 内嵌浏览器（生成临时脚本运行）
# ──────────────────────────────────────────────

_LOGIN_SCRIPT = '''
import sys, time
from pathlib import Path
import webview

output_file = sys.argv[1] if len(sys.argv) > 1 else ".quarkdl_login_cookie.tmp"

def on_loaded(window):
    while True:
        time.sleep(1)
        try:
            cookies = window.get_cookies()
            cookie_dict = {}
            for sc in cookies:
                for name, morsel in sc.items():
                    cookie_dict[name] = morsel.value
            if "__puus" in cookie_dict:
                url = window.evaluate_js("window.location.href")
                if "pan.quark.cn" in url and "/login" not in url:
                    parts = [f"{k}={v}" for k, v in cookie_dict.items()]
                    Path(output_file).write_text("; ".join(parts), encoding="utf-8")
                    window.destroy()
                    return
        except Exception:
            return

w = webview.create_window("夸克网盘登录", "https://pan.quark.cn",
                          width=900, height=700, on_top=True)
webview.start(on_loaded, w, private_mode=False)
'''

_MANAGE_SCRIPT = '''
import webview
w = webview.create_window("夸克网盘 - 管理文件", "https://pan.quark.cn",
                          width=960, height=720, on_top=True)
webview.start(private_mode=False)
'''


def _write_temp_script(name: str, code: str) -> Path:
    """Write a temp .py file next to the exe (so subprocess can find it)."""
    path = _exe_dir() / name
    path.write_text(code.strip(), encoding="utf-8")
    return path


def _find_python() -> str:
    """Find a Python interpreter to run .py scripts."""
    # If running from source, use the current Python
    if not getattr(sys, 'frozen', False):
        return sys.executable

    # Try to find python in PATH
    import shutil
    python_path = shutil.which("python3") or shutil.which("python")
    if python_path:
        return python_path

    # Try common Python installation paths
    import os
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

    # Fallback to sys.executable (won't work for frozen, but worth trying)
    return sys.executable


# ──────────────────────────────────────────────
# 主题
# ──────────────────────────────────────────────

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


class Cancelled(Exception):
    pass


# ──────────────────────────────────────────────
# GUI
# ──────────────────────────────────────────────

class QuarkGUI:
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

    def _open_account_manager(self) -> None:
        script = _write_temp_script("_quarkdl_manage_tmp.py", _MANAGE_SCRIPT)
        python = _find_python()
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

        script = _write_temp_script("_quarkdl_login_tmp.py", _LOGIN_SCRIPT)
        python = _find_python()
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
            # 夸克 API 可能在 save_as 中返回文件信息
            save_as = task_result.get("save_as", {})
            if not save_as:
                return []

            # 尝试从 save_as 中获取文件列表
            files = save_as.get("save_as_top_fids", [])
            if not files:
                return []

            # 构建文件信息列表
            result = []
            for fid_info in files:
                fid = fid_info.get("fid", "")
                if not fid:
                    continue
                # 尝试匹配原始文件信息
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

                    # 尝试从任务结果获取文件信息
                    real_files = self._extract_files_from_task(task_result, fid_list)

                    if not real_files:
                        # 如果无法从任务结果获取，再扫描网盘
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
                        if elapsed >= 0.5 or done_count == total:
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


# ──────────────────────────────────────────────
# CLI 入口（保留命令行功能）
# ──────────────────────────────────────────────


def cmd_download(args: argparse.Namespace) -> None:
    client = QuarkClient(args.cookie)
    try:
        info = client.account_info()
        print(f"[登录] {info.get('nickname', '未知')}")
    except Exception as e:
        print(f"[警告] {e}")

    downloader = MultiThreadDownloader(
        client=client, output_dir=args.output,
        workers=args.workers, chunk_size=args.chunk_size,
    )

    if args.fid:
        downloader.download_file(args.fid, filename=args.filename, size=args.size)
    elif args.url:
        pwd_id, passcode = parse_share_url(args.url)
        stoken = client.get_stoken(pwd_id, passcode)
        files = client.list_share_files(pwd_id, stoken)
        file_list = [f for f in files if not f["is_dir"]]
        if not file_list:
            print("[错误] 分享中没有文件")
            return
        print(f"[分享] {len(file_list)} 个文件:")
        for i, f in enumerate(file_list, 1):
            print(f"  {i}. {f['path']} ({human_bytes(f['size'])})")
        print(f"\n[转存] 转存到自己网盘...")
        fid_list = [f["fid"] for f in file_list]
        token_list = [f["share_fid_token"] for f in file_list]
        task_id = client.save_share(pwd_id, stoken, fid_list, token_list)
        client.poll_task(task_id)
        print(f"[转存] 完成")
        time.sleep(1)
        print("[扫描] 获取真实文件信息...")
        my_files = client.list_files("0")
        name_map = {f["file_name"]: f for f in my_files if not f["is_dir"]}
        matched = [name_map[f["file_name"]] for f in file_list if f["file_name"] in name_map]
        print(f"[扫描] 匹配 {len(matched)}/{len(file_list)}")
        for i, finfo in enumerate(matched, 1):
            print(f"\n{'='*60}")
            print(f"[{i}/{len(matched)}] {finfo['file_name']}")
            print(f"{'='*60}")
            downloader.download_file(finfo["fid"], filename=finfo["file_name"], size=finfo["size"])
    else:
        print("[错误] 请指定 --url 或 --fid")
        sys.exit(1)


def cmd_list(args: argparse.Namespace) -> None:
    client = QuarkClient(args.cookie)
    try:
        info = client.account_info()
        print(f"账号: {info.get('nickname', '未知')}")
    except Exception as e:
        print(f"[警告] {e}")
    files = client.list_files(args.dir_fid)
    if not files:
        print("(空)")
        return
    for f in files:
        p = "📁" if f["is_dir"] else "📄"
        s = human_bytes(f["size"]) if not f["is_dir"] else ""
        print(f"  {p} {f['file_name']}  {s}  [fid={f['fid']}]")


# ──────────────────────────────────────────────
# 入口
# ──────────────────────────────────────────────


def main():
    # 如果有命令行参数走 CLI，否则启动 GUI
    if len(sys.argv) > 1 and sys.argv[1].startswith("--"):
        parser = argparse.ArgumentParser(description="夸克网盘多线程下载器")
        parser.add_argument("--cookie", required=True, help="Cookie 字符串")
        parser.add_argument("--url", help="分享链接")
        parser.add_argument("--fid", help="文件 fid")
        parser.add_argument("--filename", help="文件名")
        parser.add_argument("--size", type=int, help="文件大小")
        parser.add_argument("-o", "--output", default=str(_exe_dir() / "downloads"))
        parser.add_argument("-w", "--workers", type=int, default=32)
        parser.add_argument("--chunk-size", type=int, default=1024 * 1024)
        sub = parser.add_subparsers(dest="command")
        sub.add_parser("list", help="列出网盘文件").add_argument("--dir-fid", default="0")
        args = parser.parse_args()
        if args.command == "list":
            cmd_list(args)
        else:
            cmd_download(args)
    else:
        root = ctk.CTk()
        QuarkGUI(root)
        root.mainloop()


if __name__ == "__main__":
    main()
