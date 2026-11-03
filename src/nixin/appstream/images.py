"""Screenshots and icons for the GUI's details pane: fetched one at a time when the person
opens a package's details (never ahead), size-capped, cached under the nixin cache.

    $XDG_CACHE_HOME/nixin/images/<sha1(url)>.<ext>

An image larger than MAX_IMAGE is refused (the download stops there); the directory is
pruned to MAX_TOTAL, least recently used first. Only http(s) URLs and image content types
are accepted. search.nixos.org's own copies (small webp files under /screenshots/ and
/icons/) are preferred over the upstream URL by the callers when the hint has them.
"""

from __future__ import annotations

import hashlib
import os
import time
from pathlib import Path

import requests

from .. import http
from ..cache import default_root

MAX_IMAGE = 4 * 1024 * 1024
MAX_TOTAL = 64 * 1024 * 1024
TIMEOUT = 15.0
_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
    "image/avif": ".avif",
}


class ImageError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def directory(root: Path | None = None) -> Path:
    return (root or default_root()) / "images"


def cached(url: str, root: Path | None = None) -> Path | None:
    stem = hashlib.sha1(url.encode()).hexdigest()[:32]
    d = directory(root)
    try:
        for f in d.iterdir():
            if f.stem == stem and f.suffix != ".tmp":
                os.utime(f)  # recently used
                return f
    except OSError:
        return None
    return None


def prune(root: Path | None = None, total: int = MAX_TOTAL) -> None:
    d = directory(root)
    try:
        files = [(f.stat().st_mtime, f.stat().st_size, f) for f in d.iterdir() if f.is_file()]
    except OSError:
        return
    size = sum(s for _, s, _ in files)
    for _mtime, s, f in sorted(files):
        if size <= total:
            break
        try:
            f.unlink()
            size -= s
        except OSError:
            pass


def fetch(url: str, root: Path | None = None, cap: int = MAX_IMAGE) -> Path:
    """The local file for `url`: cached, or downloaded now (at most `cap` bytes)."""
    if not url.startswith(("https://", "http://")):
        raise ImageError("usage", f"not an http(s) URL: {url}")
    hit = cached(url, root)
    if hit is not None:
        return hit
    try:
        resp = http.session().get(
            url, stream=True, timeout=TIMEOUT, headers={"Accept": "image/*"}, allow_redirects=True
        )
    except requests.RequestException as exc:
        raise ImageError("network", f"cannot fetch {url}: {exc}") from exc
    try:
        if resp.status_code >= 400:
            raise ImageError("network", f"{url} answered HTTP {resp.status_code}")
        ctype = resp.headers.get("Content-Type", "").split(";")[0].strip().lower()
        if not ctype.startswith("image/"):
            raise ImageError("not-an-image", f"{url} is {ctype or 'of no type'}, not an image")
        length = resp.headers.get("Content-Length")
        if length and length.isdigit() and int(length) > cap:
            raise ImageError("too-large", f"{url} is {int(length)} bytes (the cap is {cap})")
        data = bytearray()
        for chunk in resp.iter_content(64 * 1024):
            data += chunk
            if len(data) > cap:
                raise ImageError("too-large", f"{url} is larger than {cap} bytes")
    finally:
        resp.close()
    ext = _EXT.get(ctype) or os.path.splitext(url.split("?")[0])[1][:6] or ".img"
    d = directory(root)
    path = d / (hashlib.sha1(url.encode()).hexdigest()[:32] + ext)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.monotonic_ns()}.tmp")
    try:
        d.mkdir(parents=True, exist_ok=True)
        tmp.write_bytes(bytes(data))
        os.replace(tmp, path)
    except OSError as exc:
        tmp.unlink(missing_ok=True)
        raise ImageError("cache", f"cannot write {path}: {exc}") from exc
    prune(root)
    return path
