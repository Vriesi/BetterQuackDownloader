"""
工具函数
"""

from __future__ import annotations

import sys
from pathlib import Path
from urllib.parse import parse_qs, urlparse


def _exe_dir() -> Path:
    """获取 exe 所在目录"""
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _resource_dir() -> Path:
    """PyInstaller --onefile 解压目录（sys._MEIPASS），源码运行时同 _exe_dir。"""
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)
    return Path(__file__).resolve().parent


def human_bytes(n: int | float) -> str:
    """将字节数转换为人类可读的格式"""
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if abs(n) < 1000:
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1000
    return f"{n:.1f} PB"


def plan_ranges(size: int, chunk_size: int) -> list[tuple[int, int]]:
    """规划分片下载的范围"""
    ranges = []
    start = 0
    while start < size:
        end = min(start + chunk_size - 1, size - 1)
        ranges.append((start, end))
        start = end + 1
    return ranges


def parse_share_url(url: str) -> tuple[str, str]:
    """解析夸克分享链接，返回 (pwd_id, passcode)"""
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


# 常量
COOKIE_FILE = _exe_dir() / ".quarkdl_cookies.json"
FONT = "Microsoft YaHei UI"
FONT_MONO = "Cascadia Code"

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
