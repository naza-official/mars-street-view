import datetime as dt
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

JOB_PATH = Path(__file__).resolve().parents[2] / "infra" / "build_vm" / "job.py"
spec = importlib.util.spec_from_file_location("build_vm_job", JOB_PATH)
job = importlib.util.module_from_spec(spec)
sys.modules["build_vm_job"] = job
spec.loader.exec_module(job)

EMPTY = {"target": None, "site": None, "run_id": None, "image_tag": None,
         "instance_types": None, "volume_gb": None}


def test_defaults() -> None:
    resolved = job.resolve(EMPTY, env={"MSV_RUN_ID": "smoke-global-1"})
    assert (resolved.target, resolved.site) == ("smoke", "global")
    assert resolved.instance_types == ("m7i.4xlarge", "m6i.4xlarge", "r6i.2xlarge")
    assert resolved.volume_gb == 500
    assert resolved.image == f"ghcr.io/naza-official/mars-sv:{job.DEFAULT_IMAGE_TAG}"


def test_env_overrides_config() -> None:
    config = {**EMPTY, "target": "l1_global", "volume_gb": "100"}
    env = {"MSV_TARGET": "smoke", "MSV_INSTANCE_TYPES": "m7i.xlarge, m6i.xlarge",
           "MSV_RUN_ID": "r-123"}
    resolved = job.resolve(config, env=env)
    assert resolved.target == "smoke"
    assert resolved.volume_gb == 100
    assert resolved.instance_types == ("m7i.xlarge", "m6i.xlarge")


def test_default_run_id_is_valid() -> None:
    run_id = job.default_run_id("l1_global", "global", dt.datetime(2026, 11, 14, 9, 5, 0))
    assert run_id == "l1-global-global-20261114t090500z"
    assert job.RUN_ID.match(run_id)


@pytest.mark.parametrize("override", [
    {"MSV_TARGET": "smoke; rm -rf /"}, {"MSV_SITE": "Jezero"}, {"MSV_RUN_ID": "a b"},
    {"MSV_VOLUME_GB": "5"}, {"MSV_INSTANCE_TYPES": " , "}, {"MSV_IMAGE_TAG": "v1;x"},
])
def test_rejects_bad_values(override: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        job.resolve(EMPTY, env={"MSV_RUN_ID": "r-1", **override})


def test_user_data_renders_all_placeholders(tmp_path: Path) -> None:
    resolved = job.resolve(EMPTY, env={"MSV_RUN_ID": "smoke-global-1"})
    script = job.user_data(resolved, "mars-sv-123412369922", "us-west-2", "/msv/build")
    assert "@@" not in script
    assert 'WORK_URI="s3://${BUCKET}/work/${RUN_ID}"' in script
    assert 'RUN_ID="smoke-global-1"' in script
    assert f'HARD_STOP_MIN="{72 * 60}"' in script
    assert "trap finish EXIT" in script
    path = tmp_path / "ud.sh"
    path.write_text(script)
    assert subprocess.run(["bash", "-n", str(path)]).returncode == 0
