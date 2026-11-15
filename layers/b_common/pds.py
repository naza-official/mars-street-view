"""Small helpers shared by Person B's layers (L3, L4, L6): data paths,
PDS directory listings and resumable downloads.

Kept outside `common/` on purpose: `common/` is the L0 contract library that
both people review. These helpers are lane-internal and can change freely.
"""

from __future__ import annotations

import hashlib
import html
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests

USER_AGENT = "mars-street-view-fetch/0.1 (research; python-requests)"
_session = requests.Session()
_session.headers["User-Agent"] = USER_AGENT


def data_root() -> Path:
    """Root of local data: raw/<layer>/ downloads and rover/<layer>/<site>/vN/ outputs."""
    return Path(os.environ.get("MSV_DATA", Path.home() / "mars-data")).expanduser()


def raw_dir(layer: str) -> Path:
    p = data_root() / "raw" / layer
    p.mkdir(parents=True, exist_ok=True)
    return p


@dataclass
class Entry:
    name: str
    url: str
    is_dir: bool
    size: int | None  # bytes; approximate when the server prints "12M"


_UNITS = {"": 1, "K": 1024, "M": 1024**2, "G": 1024**3, "T": 1024**4}


def _parse_size(s: str) -> int | None:
    s = s.strip()
    m = re.fullmatch(r"([\d.]+)\s*([KMGT]?)", s)
    if not m:
        return None
    return int(float(m[1]) * _UNITS[m[2]])


def list_dir(url: str, retries: int = 3) -> list[Entry]:
    """Parse an Apache (pds-imaging) or IIS (pds-geosciences) autoindex page."""
    if not url.endswith("/"):
        url += "/"
    for attempt in range(retries):
        try:
            r = _session.get(url, timeout=120)
            r.raise_for_status()
            break
        except requests.RequestException:
            if attempt == retries - 1:
                raise
            time.sleep(5 * (attempt + 1))
    text = r.text
    base_path = urlparse(url).path
    out: dict[str, Entry] = {}

    # Apache table rows: <a href="x">x</a></td><td>date</td><td>size</td>
    for m in re.finditer(
        r'<a href="([^"?#]+)">[^<]*</a>\s*</td>\s*<td[^>]*>[^<]*</td>\s*<td[^>]*>\s*([^<]*)</td>',
        text,
    ):
        href = html.unescape(m[1])
        full = urljoin(url, href)
        if not urlparse(full).path.startswith(base_path) or full == url:
            continue
        name = href.rstrip("/").split("/")[-1]
        is_dir = href.endswith("/")
        out[name] = Entry(name, full, is_dir, None if is_dir else _parse_size(m[2]))

    if not out:
        # IIS: "7/30/2026  5:55 PM      8684070 <A HREF="...">name</A>" or "&lt;dir&gt; <A ...>"
        for m in re.finditer(r"(&lt;dir&gt;|\d+)\s*<a href=\"([^\"]+)\">([^<]+)</a>", text, re.I):
            href = html.unescape(m[2])
            full = urljoin(url, href)
            if not urlparse(full).path.startswith(base_path) or full.rstrip("/") == url.rstrip("/"):
                continue
            is_dir = m[1].lower() == "&lt;dir&gt;"
            name = m[3].strip()
            if is_dir and not full.endswith("/"):
                full += "/"
            out[name] = Entry(name, full, is_dir, None if is_dir else int(m[1]))

    if not out:
        # Plain Apache <pre> listing: sizes are not reliably parseable; HEAD gives them on download.
        for m in re.finditer(r'href="([^"?#]+)"', text, re.I):
            href = html.unescape(m[1])
            full = urljoin(url, href)
            if not urlparse(full).path.startswith(base_path) or full == url:
                continue
            name = href.rstrip("/").split("/")[-1]
            out[name] = Entry(name, full, href.endswith("/"), None)
    return list(out.values())


def download(url: str, dest: Path, retries: int = 5, quiet: bool = False) -> Path:
    """Resumable download. Skips files whose size already matches the server's Content-Length."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(dest.name + ".part")
    for attempt in range(retries):
        try:
            head = _session.head(url, allow_redirects=True, timeout=60)
            total = int(head.headers.get("Content-Length", 0)) or None
            if dest.exists() and (total is None or dest.stat().st_size == total):
                return dest
            have = part.stat().st_size if part.exists() else 0
            headers = {"Range": f"bytes={have}-"} if have and total and have < total else {}
            if not headers:
                have = 0
            with _session.get(url, stream=True, headers=headers, timeout=120) as r:
                r.raise_for_status()
                if headers and r.status_code != 206:
                    have = 0
                mode = "ab" if have else "wb"
                done, t0, last = have, time.time(), 0.0
                with open(part, mode) as f:
                    for chunk in r.iter_content(1 << 20):
                        f.write(chunk)
                        done += len(chunk)
                        if not quiet and total and time.time() - last > 5:
                            last = time.time()
                            rate = (done - have) / max(last - t0, 1e-6) / 1e6
                            print(
                                f"    {dest.name}: {done / 1e6:,.0f}/{total / 1e6:,.0f} MB "
                                f"({rate:.1f} MB/s)",
                                file=sys.stderr,
                            )
            if total and part.stat().st_size != total:
                raise OSError(f"size mismatch {part.stat().st_size} != {total}")
            part.replace(dest)
            return dest
        except (OSError, requests.RequestException) as e:
            if attempt == retries - 1:
                raise
            print(f"  retry {attempt + 1} for {url}: {e}", file=sys.stderr)
            time.sleep(5 * (attempt + 1))
    return dest


def md5sum(path: Path) -> str:
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def write_manifest(folder: Path, sources: list[dict]) -> None:
    """Record what was fetched and when; build.py copies this into layer.json `sources`."""
    path = folder / "fetch_manifest.json"
    old = json.loads(path.read_text()) if path.exists() else {"sources": []}
    seen = {s["url"] for s in sources}
    merged = [s for s in old["sources"] if s["url"] not in seen] + sources
    stamp = datetime.now(UTC).isoformat(timespec="seconds")
    for s in sources:
        s.setdefault("downloaded", stamp)
    path.write_text(json.dumps({"sources": merged}, indent=2))


def human(n: float | None) -> str:
    if n is None:
        return "?"
    for u in ["B", "KB", "MB", "GB", "TB"]:
        if n < 1024:
            return f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} PB"
