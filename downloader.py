"""
多线程下载器
"""

from __future__ import annotations

import concurrent.futures
import shutil
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

from client import QuarkClient
from utils import PC_UA, BASE_URL, plan_ranges


def _cdn_headers() -> dict[str, str]:
    return {
        "User-Agent": PC_UA,
        "Origin": "https://pan.quark.cn",
        "Referer": "https://pan.quark.cn/",
    }


def _copy_session(source: requests.Session) -> requests.Session:
    """复制 session（保留 cookies）"""
    s = requests.Session()
    s.cookies.update(source.cookies)
    return s


class MultiThreadDownloader:
    """多线程下载器"""

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
        """下载单个文件"""
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
        """流式下载"""
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
        """分片下载"""
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
        """合并分片"""
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
    """下载单个分片"""
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
