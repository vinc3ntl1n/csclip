"""Download and unpack demos (.dem, .dem.gz, .dem.zst) and find ones dropped into the inbox folder."""

from __future__ import annotations

import gzip
import re
import shutil
from pathlib import Path

import requests
import zstandard

DEMO_MAGIC = b"PBDEMS2\0"
GZIP_MAGIC = b"\x1f\x8b"
ZSTD_MAGIC = b"\x28\xb5\x2f\xfd"
MATCH_ID_RE = re.compile(r"1-[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
DEMO_SUFFIXES = (".dem", ".dem.gz", ".dem.zst")


def download(url: str, dest: Path) -> Path:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".part")
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        with partial.open("wb") as out:
            for chunk in resp.iter_content(chunk_size=1 << 20):
                out.write(chunk)
    partial.replace(dest)
    return dest


def unpack(src: Path, dest: Path) -> Path:
    """Write the raw .dem inside `src` (gzip, zstd or already raw) to `dest`."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as f:
        magic = f.read(8)
    partial = dest.with_name(dest.name + ".part")
    with src.open("rb") as raw, partial.open("wb") as out:
        if magic.startswith(GZIP_MAGIC):
            with gzip.open(raw) as inner:
                shutil.copyfileobj(inner, out, 1 << 20)
        elif magic.startswith(ZSTD_MAGIC):
            zstandard.ZstdDecompressor(max_window_size=2**31).copy_stream(raw, out)
        elif magic == DEMO_MAGIC:
            shutil.copyfileobj(raw, out, 1 << 20)
        else:
            partial.unlink(missing_ok=True)
            raise ValueError(f"{src.name} is not a CS2 demo (file starts with {magic!r})")
    with partial.open("rb") as f:
        if f.read(8) != DEMO_MAGIC:
            partial.unlink()
            raise ValueError(f"{src.name} did not unpack to a CS2 demo")
    partial.replace(dest)
    return dest


def demo_key(path: Path) -> str:
    """FACEIT match id if the file name has one, otherwise the file name without its demo suffix."""
    found = MATCH_ID_RE.search(path.name)
    if found:
        return found.group(0)
    name = path.name
    for suffix in DEMO_SUFFIXES:
        if name.endswith(suffix):
            return name[: -len(suffix)]
    return path.stem


def inbox_demos(inbox: Path) -> dict[str, Path]:
    if not inbox.is_dir():
        return {}
    return {
        demo_key(p): p
        for p in sorted(inbox.iterdir(), key=lambda p: p.stat().st_mtime)
        if p.is_file() and p.name.endswith(DEMO_SUFFIXES)
    }
