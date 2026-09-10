"""Buffered HTTP-range file-like object for reading a remote ZIP archive
without downloading it in full.

The Eclipse dataset's full archive (Eclipse_dataset.zip, ~17.2GB) stores
each Eclipse project as an independent ZIP entry (each individually
DEFLATE-compressed). Zenodo's file server supports HTTP range requests
(verified: a ranged GET returns 206 Partial Content), so we can read just
the ZIP central directory and a single member's compressed stream instead
of downloading all nine projects to get the one (Platform) this project
needs. This roughly halves the network transfer for Phase 1 (~8.1GB
compressed for Platform vs. 17.2GB for the whole archive) and never writes
the other eight projects to disk at all.
"""

from __future__ import annotations

import time

import requests

DEFAULT_BUFFER_SIZE = 16 * 1024 * 1024  # 16MB per HTTP range request
MAX_RETRIES = 6
RETRY_BACKOFF_BASE_SECONDS = 2.0


class HTTPRangeFile:
    """A read()/seek()-capable file-like object backed by ranged GETs.

    Buffers forward reads so that ZIP's typically-sequential internal read
    pattern (small chunks during decompression) doesn't turn into one HTTP
    request per chunk.
    """

    def __init__(self, url: str, buffer_size: int = DEFAULT_BUFFER_SIZE, timeout: int = 120):
        self.url = url
        self.buffer_size = buffer_size
        self.timeout = timeout
        self.session = requests.Session()
        head = self.session.head(url, timeout=timeout, allow_redirects=True)
        head.raise_for_status()
        self.size = int(head.headers["content-length"])
        self.pos = 0
        self._buf_start = 0
        self._buf = b""
        self.request_count = 0
        self.bytes_fetched = 0

    def seekable(self) -> bool:
        return True

    def seek(self, offset: int, whence: int = 0) -> int:
        if whence == 0:
            self.pos = offset
        elif whence == 1:
            self.pos += offset
        elif whence == 2:
            self.pos = self.size + offset
        else:
            raise ValueError(f"invalid whence: {whence}")
        return self.pos

    def tell(self) -> int:
        return self.pos

    def _ensure_buffer(self, nbytes: int) -> None:
        buf_end = self._buf_start + len(self._buf)
        if self._buf_start <= self.pos and self.pos + nbytes <= buf_end:
            return  # fully served by existing buffer
        fetch_len = max(nbytes, self.buffer_size)
        end = min(self.pos + fetch_len, self.size) - 1
        if end < self.pos:
            self._buf_start, self._buf = self.pos, b""
            return

        last_exc: Exception | None = None
        for attempt in range(MAX_RETRIES):
            try:
                resp = self.session.get(
                    self.url, headers={"Range": f"bytes={self.pos}-{end}"}, timeout=self.timeout
                )
                resp.raise_for_status()
                self.request_count += 1
                self.bytes_fetched += len(resp.content)
                self._buf_start = self.pos
                self._buf = resp.content
                return
            except (requests.exceptions.RequestException,) as exc:
                last_exc = exc
                wait = RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt)
                print(f"  [retry {attempt + 1}/{MAX_RETRIES}] range GET failed ({exc}); retrying in {wait:.0f}s")
                time.sleep(wait)
        raise last_exc  # type: ignore[misc]

    def read(self, n: int = -1) -> bytes:
        if n is None or n < 0:
            n = self.size - self.pos
        if n <= 0 or self.pos >= self.size:
            return b""
        self._ensure_buffer(n)
        start = self.pos - self._buf_start
        data = self._buf[start:start + n]
        self.pos += len(data)
        return data
