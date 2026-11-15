import json
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

CHUNK = 64 << 20


def size(url: str) -> int:
    req = urllib.request.Request(url, method="HEAD")
    with urllib.request.urlopen(req) as r:
        return int(r.headers["Content-Length"])


def part_once(url: str, out: Path, start: int, end: int) -> int:
    req = urllib.request.Request(url, headers={"Range": f"bytes={start}-{end}"})
    written = 0
    with urllib.request.urlopen(req, timeout=30) as r, out.open("r+b") as fh:
        fh.seek(start)
        while block := r.read(1 << 20):
            written += fh.write(block)
    if written != end - start + 1:
        raise OSError(f"short read {written} of {end - start + 1}")
    return written


def part(url: str, out: Path, start: int, end: int, tries: int = 6) -> int:
    for attempt in range(tries):
        try:
            return part_once(url, out, start, end)
        except OSError as err:
            print(f"retry {start}: {err}", flush=True)
            time.sleep(2 * (attempt + 1))
    raise OSError(f"range {start}-{end} failed {tries} times")


def fetch(url: str, out: Path, workers: int = 12) -> None:
    total = size(url)
    if out.exists() and out.stat().st_size == total and not out.with_suffix(".part").exists():
        print(f"skip {out.name}")
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    marker = out.with_suffix(".part")
    resumable = marker.exists() and marker.stat().st_size
    finished = set(json.loads(marker.read_text())) if resumable else set()
    if not out.exists() or out.stat().st_size != total:
        finished = set()
        with out.open("wb") as fh:
            fh.truncate(total)
    ranges = [(s, min(s + CHUNK, total) - 1) for s in range(0, total, CHUNK) if s not in finished]

    def run(r: tuple[int, int]) -> int:
        n = part(url, out, *r)
        finished.add(r[0])
        marker.write_text(json.dumps(sorted(finished)))
        print(f"{out.name}: {len(finished)}/{-(-total // CHUNK)} chunks", flush=True)
        return n

    with ThreadPoolExecutor(workers) as pool:
        list(pool.map(run, ranges))
    marker.unlink()


if __name__ == "__main__":
    dest = Path(sys.argv[1])
    for url in sys.argv[2:]:
        fetch(url, dest / url.rsplit("/", 1)[1])
