"""
内嵌浏览器功能
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import webview


def open_login_window(cookie_file: Path) -> str | None:
    """打开登录窗口，获取 cookie"""
    result = []

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
                        cookie_str = "; ".join(parts)
                        result.append(cookie_str)
                        window.destroy()
                        return
            except Exception:
                return

    w = webview.create_window("夸克网盘登录", "https://pan.quark.cn",
                              width=900, height=700, on_top=True)
    webview.start(on_loaded, w, private_mode=False)

    return result[0] if result else None


def open_manage_window():
    """打开网盘管理窗口"""
    w = webview.create_window("夸克网盘 - 管理文件", "https://pan.quark.cn",
                              width=960, height=720, on_top=True)
    webview.start(private_mode=False)
