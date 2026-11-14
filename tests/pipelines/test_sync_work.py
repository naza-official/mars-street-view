from pathlib import Path

import pytest

from common.storage import parse_uri
from pipelines import sync_work


def make_tree(root: Path) -> None:
    (root / "a").mkdir(parents=True)
    (root / "a" / "x.tif").write_bytes(b"x")
    (root / "DONE").write_text("")
    (root / ".snakemake" / "log").mkdir(parents=True)
    (root / ".snakemake" / "log" / "run.log").write_text("log")


def test_plan_maps_relative_paths_and_skips_snakemake(tmp_path: Path) -> None:
    make_tree(tmp_path)
    pairs = sync_work.plan(tmp_path, parse_uri("s3://b/work/r1/"))
    assert [uri.uri for _, uri in pairs] == ["s3://b/work/r1/DONE", "s3://b/work/r1/a/x.tif"]


def test_plan_refuses_outside_work(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        sync_work.plan(tmp_path, parse_uri("s3://b/orbital/l1_global/"))


def test_sync_uploads_each_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    make_tree(tmp_path)
    calls: list[str] = []
    monkeypatch.setattr(sync_work, "upload_file", lambda path, uri: calls.append(uri.key))
    assert sync_work.main([str(tmp_path), "s3://b/work/r1"]) == 0
    assert calls == ["work/r1/DONE", "work/r1/a/x.tif"]
