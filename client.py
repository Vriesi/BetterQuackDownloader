"""
夸克 API 客户端
"""

from __future__ import annotations

import time

import requests

from utils import BASE_URL, PC_UA, WEB_UA


class QuarkClient:
    """夸克网盘 API 客户端"""

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
        """获取账号信息"""
        resp = self.session.get(
            "https://pan.quark.cn/account/info",
            params={"fr": "pc", "platform": "pc"},
            headers=self._headers(),
            timeout=30,
        )
        return self._check(resp, "account_info").get("data", {})

    def get_stoken(self, pwd_id: str, passcode: str = "") -> str:
        """获取分享 token"""
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
        """列出分享文件"""
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
        """转存分享文件到自己网盘"""
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
        """轮询任务状态"""
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
            if td.get("status") == 3:
                raise RuntimeError(f"转存任务失败: {td.get('message', '未知错误')}")
        raise RuntimeError("转存任务超时")

    def list_files(self, pdir_fid: str = "0", log_func=None) -> list[dict]:
        """列出网盘文件"""
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
            if page > 100:
                if log_func:
                    log_func("警告：扫描页数超过 100 页，停止扫描")
                break
        return files

    def get_download_url(self, fid: str) -> str:
        """获取文件下载链接"""
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
