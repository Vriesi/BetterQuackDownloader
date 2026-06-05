"""
夸克网盘多线程下载器 - 入口文件
"""

from __future__ import annotations

import argparse
import ctypes
import sys
import time

import customtkinter as ctk

from client import QuarkClient
from downloader import MultiThreadDownloader
from gui import QuarkGUI
from utils import _exe_dir, human_bytes, parse_share_url

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
# 主题
# ──────────────────────────────────────────────

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")


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
