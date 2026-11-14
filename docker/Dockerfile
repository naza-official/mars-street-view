FROM condaforge/miniforge3:26.9.4-0

ARG ASP_VERSION=3.7.0
ARG ASP_DATE=2026-06-08
ARG ASP_SHA256=46ac51b282ca1b90e7089f6bb808d7cff548953eeab6c36cb12334089b2706d2

SHELL ["/bin/bash", "-o", "pipefail", "-c"]

RUN apt-get update \
 && apt-get install -y --no-install-recommends bzip2 ca-certificates curl libice6 libsm6 libx11-6 libxau6 libxext6 \
 && rm -rf /var/lib/apt/lists/*

COPY docker/env-isis.yml /tmp/env-isis.yml
RUN mamba env create -f /tmp/env-isis.yml \
 && mamba clean -afy \
 && rm -rf /opt/conda/envs/isis/share/doc /opt/conda/envs/isis/doc /opt/conda/envs/isis/include \
 && find /opt/conda/envs/isis -name '*.a' -delete

ENV ASP_DIR=/opt/asp
RUN name="StereoPipeline-${ASP_VERSION}-${ASP_DATE}-x86_64-Linux" \
 && curl -fsSL -o /tmp/asp.tar.bz2 \
    "https://github.com/NeoGeographyToolkit/StereoPipeline/releases/download/${ASP_VERSION}/${name}.tar.bz2" \
 && echo "${ASP_SHA256}  /tmp/asp.tar.bz2" | sha256sum -c - \
 && mkdir -p "${ASP_DIR}" \
 && tar -xjf /tmp/asp.tar.bz2 -C "${ASP_DIR}" --strip-components=1 \
 && rm /tmp/asp.tar.bz2

COPY docker/env-msv.yml /tmp/env-msv.yml
RUN mamba env create -f /tmp/env-msv.yml && mamba clean -afy

WORKDIR /opt/msv
COPY pyproject.toml ./
COPY common ./common
COPY pipelines ./pipelines
COPY configs ./configs
RUN /opt/conda/envs/msv/bin/pip install --no-cache-dir ".[tiler]"

ENV PATH=/opt/conda/envs/msv/bin:/opt/conda/bin:${ASP_DIR}/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    ISISROOT=/opt/conda/envs/isis \
    ISISDATA=/isisdata \
    MSV_WORK=/work \
    MSV_BUCKET=mars-sv-123412369922 \
    AWS_REGION=us-west-2

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh && mkdir -p /work /isisdata

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
CMD ["--help"]
