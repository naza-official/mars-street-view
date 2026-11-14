L1_DEST = f"s3://{BUCKET}/orbital/l1_global/global/v1/"


rule l1_fetched:
    output: "l1/fetched.txt"
    shell: "date -u +%F > {output}"


rule l1_fetch_source:
    output: "raw/{src}.tif"
    wildcard_constraints: src="mola|mdim"
    shell: "mkdir -p raw && touch raw/.nosync && python -m layers.l1_global.ingest fetch {wildcards.src} {output}"


rule l1_fetch_areoid:
    output: "raw/mega90n000cb.img"
    shell: "mkdir -p raw && touch raw/.nosync && python -m layers.l1_global.ingest fetch areoid {output}"


rule l1_dtm:
    input: mola="raw/mola.tif", areoid="raw/mega90n000cb.img", fetched="l1/fetched.txt"
    output: dtm="l1/layer/dtm.tif", check="l1/dem_geoid_check.json"
    threads: workflow.cores
    shell:
        "python -m layers.l1_global.ingest dtm {input.mola} {input.areoid} {output.dtm}"
        " --work raw/tmp_dtm --fetched $(cat {input.fetched}) > {output.check}.tmp"
        " && mv {output.check}.tmp {output.check}"


rule l1_ortho:
    input: mdim="raw/mdim.tif", fetched="l1/fetched.txt"
    output: "l1/layer/ortho.tif"
    threads: workflow.cores
    shell:
        "python -m layers.l1_global.ingest ortho {input.mdim} {output}"
        " --work raw/tmp_ortho --fetched $(cat {input.fetched})"


rule l1_registration:
    input: dtm="l1/layer/dtm.tif", ortho="l1/layer/ortho.tif"
    output: "l1/registration.json"
    shell: "python -m layers.l1_global.shift registration {input.dtm} {input.ortho} --out {output}"


rule l1_conversion:
    input: mdim="raw/mdim.tif", ortho="l1/layer/ortho.tif"
    output: "l1/conversion.json"
    shell:
        "python -m layers.l1_global.shift conversion {input.mdim} {input.ortho} --ppd 256"
        " --out {output}"


rule l1_tiles:
    input: dtm="l1/layer/dtm.tif", ortho="l1/layer/ortho.tif"
    output: tileset="l1/layer/tileset.json", stats="l1/layer.stats.json"
    threads: workflow.cores
    shell:
        "python -m layers.l1_global.tiles {input.dtm} l1/layer --ortho {input.ortho}"
        " --workers {threads}"


rule l1_layer:
    input:
        tileset="l1/layer/tileset.json", stats="l1/layer.stats.json",
        conversion="l1/conversion.json", registration="l1/registration.json",
        geoid="l1/dem_geoid_check.json", fetched="l1/fetched.txt"
    output: "l1/layer/layer.json"
    shell:
        "touch l1/layer/.nosync && python -m layers.l1_global.layer l1/layer"
        " --conversion {input.conversion} --registration {input.registration}"
        " --geoid {input.geoid} --stats {input.stats} --fetched $(cat {input.fetched})"
        " --built-with ${{MSV_IMAGE:-local}} && python -m common.validate l1/layer"


rule l1_publish:
    input: "l1/layer/layer.json"
    output: "l1/published.txt"
    params: dest=L1_DEST
    shell: "python -m common.publish l1/layer {params.dest} > {output}.tmp && mv {output}.tmp {output}"


rule l1:
    input: "l1/published.txt"
    output: "l1/catalog.json"
    shell: "python -m pipelines.catalog l1_global/global v1 --bucket {BUCKET} > {output}.tmp && mv {output}.tmp {output}"
