import datetime as dt
import os
import re
from dataclasses import dataclass
from pathlib import Path

USER_DATA = Path(__file__).with_name("user_data.sh")
PLACEHOLDER = re.compile(r"@@([A-Z_]+)@@")
NAME = re.compile(r"^[a-z0-9_]+$")
RUN_ID = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,80}$")
IMAGE_REPO = "ghcr.io/naza-official/mars-sv"
DEFAULT_INSTANCE_TYPES = ("m7i.4xlarge", "m6i.4xlarge", "r6i.2xlarge")
DEFAULT_VOLUME_GB = 500
DEFAULT_IMAGE_TAG = "v0.3.0"
HARD_STOP_MIN = 72 * 60


@dataclass(frozen=True)
class Job:
    target: str
    site: str
    run_id: str
    image_tag: str
    instance_types: tuple[str, ...]
    volume_gb: int

    @property
    def image(self) -> str:
        return f"{IMAGE_REPO}:{self.image_tag}"


def default_run_id(target: str, site: str, now: dt.datetime | None = None) -> str:
    stamp = (now or dt.datetime.now(dt.UTC)).strftime("%Y%m%dt%H%M%Sz")
    return f"{target}-{site}-{stamp}".replace("_", "-")


def _pick(env: dict[str, str], key: str, configured: str | None, default: str) -> str:
    return env.get(key) or configured or default


def _types(raw: str) -> tuple[str, ...]:
    return tuple(t.strip() for t in raw.split(",") if t.strip())


def resolve(config: dict[str, str | None], env: dict[str, str] | None = None) -> Job:
    env = dict(os.environ if env is None else env)
    target = _pick(env, "MSV_TARGET", config.get("target"), "smoke")
    site = _pick(env, "MSV_SITE", config.get("site"), "global")
    job = Job(
        target=target,
        site=site,
        run_id=_pick(env, "MSV_RUN_ID", config.get("run_id"), default_run_id(target, site)),
        image_tag=_pick(env, "MSV_IMAGE_TAG", config.get("image_tag"), DEFAULT_IMAGE_TAG),
        instance_types=_types(_pick(env, "MSV_INSTANCE_TYPES", config.get("instance_types"),
                                    ",".join(DEFAULT_INSTANCE_TYPES))),
        volume_gb=int(_pick(env, "MSV_VOLUME_GB", config.get("volume_gb"),
                            str(DEFAULT_VOLUME_GB))),
    )
    check(job)
    return job


def check(job: Job) -> None:
    problems = []
    if not NAME.match(job.target):
        problems.append(f"target {job.target!r}")
    if not NAME.match(job.site):
        problems.append(f"site {job.site!r}")
    if not RUN_ID.match(job.run_id):
        problems.append(f"run_id {job.run_id!r}")
    if not re.match(r"^[A-Za-z0-9_.-]+$", job.image_tag):
        problems.append(f"image_tag {job.image_tag!r}")
    if not job.instance_types:
        problems.append("instance_types empty")
    if not 20 <= job.volume_gb <= 2000:
        problems.append(f"volume_gb {job.volume_gb}")
    if problems:
        raise ValueError("invalid job: " + ", ".join(problems))


def user_data(job: Job, bucket: str, region: str, log_group: str) -> str:
    values = {
        "RUN_ID": job.run_id, "TARGET": job.target, "SITE": job.site, "IMAGE": job.image,
        "BUCKET": bucket, "REGION": region, "LOG_GROUP": log_group,
        "HARD_STOP_MIN": str(HARD_STOP_MIN),
    }
    return PLACEHOLDER.sub(lambda m: values[m.group(1)], USER_DATA.read_text())
