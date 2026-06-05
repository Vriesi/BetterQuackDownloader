"""
内嵌浏览器脚本
"""

from __future__ import annotations

from pathlib import Path

# 登录脚本
LOGIN_SCRIPT = '''
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

# 管理脚本
MANAGE_SCRIPT = '''
import webview
w = webview.create_window("夸克网盘 - 管理文件", "https://pan.quark.cn",
                          width=960, height=720, on_top=True)
webview.start(private_mode=False)
'''


def write_temp_script(name: str, code: str, exe_dir: Path) -> Path:
    """写入临时脚本文件"""
    path = exe_dir / name
    path.write_text(code.strip(), encoding="utf-8")
    return path
