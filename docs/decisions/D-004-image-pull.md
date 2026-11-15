# D-004 How the build VM pulls the image

## Context
M2 published `ghcr.io/naza-official/mars-sv:v0.1.0` (digest `sha256:11ef1e28…`, 2.91 GB compressed, 8.97 GB uncompressed, linux/amd64). GHCR created the package **private**: an anonymous pull of the manifest returns HTTP 404, although the repo is public. The M3 build VM (Spot, no SSH, instance role `msv-build`) has to pull it on every run.

The image holds only open-source software (ISIS, ASP, GDAL, Snakemake, conda-forge packages) plus this repo's code, which is already public. It holds no secrets: AWS access comes from the instance role at runtime.

## Options
1. **Make the GHCR package public** (one click in package settings, or `gh api` with `admin:packages`). Anonymous pull, no secret on the VM, no extra AWS resources. Cost: $0. Anyone can pull the image, which only exposes what the public repo already shows.
2. **Keep it private; store a fine-grained GitHub token (read:packages) in SSM `/msv/ghcr/token`** (SecureString). User-data reads it and runs `docker login`. Cost: about $0. Adds a long-lived secret to rotate, plus a failure mode when it expires mid-project.
3. **Mirror to ECR in us-west-2** (CI pushes to both, or the VM pulls from ECR only). The instance role pulls with IAM, no token, and transfer stays in-region, so it may pull faster. Cost: about 2.9 GB x $0.10/GB-month = ~$0.30/month, plus a repo, a lifecycle policy, and AWS credentials in CI (OIDC role `msv-ci`).

## Recommendation
Option 1. The repo is public and the image holds no secrets, so being private only adds credentials to manage. Pick option 3 later if GHCR pull speed or rate limits hurt Spot start-up time.

## Decision
2026-11-14 (human): option 1, make the GHCR package public. GitHub has no API for package visibility, so the human changes it in the package settings. Human set it public the same day. Verified: `DOCKER_CONFIG=<empty> docker manifest inspect ghcr.io/naza-official/mars-sv:v0.2.0` exits 0, and an anonymous registry request with `Accept: application/vnd.oci.image.manifest.v1+json` returns HTTP 200 for `v0.2.0` and `v0.1.0`.

Correction: the "private" finding in Context came from a flawed check. The request sent only OCI-index and Docker-v2 Accept types, the image is an OCI manifest, and GHCR answers that with `MANIFEST_UNKNOWN` (HTTP 404). Whether the package was private before the change is unknown. The option-1 outcome stands either way.

## Date
2026-11-14 (opened)
