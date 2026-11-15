# Terrain segmentation (B lane, experimental)

Goal: per-pixel terrain classes on rover images (soil, bedrock, sand, big rock) as ground truth for
a later traversability-cost layer. Not part of the published layers yet.

## Data
[AI4Mars](https://huggingface.co/datasets/hassanjbara/AI4MARS) (JPL, Swan et al. 2021, CC0 mirror of
the Kaggle release): 18,130 Curiosity Navcam training images with crowd labels; test splits
`test_min1/2/3` labelled by experts (min3 = all three agree). Labels: 0 soil, 1 bedrock, 2 sand,
3 big rock, 255 none. `rover_mask == 1` marks the rover body (set to 255 here).

No trustworthy pretrained AI4Mars weights were found (checked Nov 2026), so we train our own.

## Pipeline
```bash
python3 -m venv ~/.venvs/msv-ml && ~/.venvs/msv-ml/bin/pip install -r ml/terrain_seg/requirements.txt
# data -> ~/mars-data/raw/ai4mars/*.parquet (6.5 GB)
~/.venvs/msv-ml/bin/python ml/terrain_seg/prepare.py           # -> ~/mars-data/work/ai4mars512
~/.venvs/msv-ml/bin/python ml/terrain_seg/train.py --epochs 8  # -> ~/mars-data/models/ai4mars_segformer_b2
~/.venvs/msv-ml/bin/python ml/terrain_seg/infer_m2020.py --stop m2020_site1_drive52
```
Model: SegFormer with the ImageNet `nvidia/mit-b2` encoder, class-weighted cross-entropy, strong
photometric augmentation (the target camera differs). Evaluation on `test_min3`.

## Using it on Perseverance
The Navcam CYL panorama (~1.35 mrad/px) is resampled to the training scale (~1.6 mrad/px), converted
to grey and segmented in overlapping tiles. The CYL RNG product gives range per pixel, so class
shares are reported in 0-5 / 5-15 / 15-30 m bands without any map projection.

Known limits: domain shift (camera, colour, illumination), crowd labels are coarse polygons, and the
rare `big_rock` class is the weakest.

## Orbital propagation prototype (status Nov 2026)

Rover evidence (Navcam frames -> AI4Mars model -> ray cast on the PLACES DEM) labels map regions in
five 400 x 400 m windows of the PLACES orbital map: landing (sols 1-44), seitah (200-260),
delta (440-470), floor (130-160), deltaflat (600-640). A per-class vector database of MOMO
embeddings classifies every region/patch: mean cosine distance to the k=3 nearest representatives
of each class, smallest wins.

```bash
python ml/terrain_seg/fetch_navcam_meta.py --name NAME --sol-min A --sol-max B
python ml/terrain_seg/evidence_grid.py --name NAME --sol-min A --sol-max B
python ml/terrain_seg/fetch_map_window.py --name NAME
python ml/terrain_seg/vector_db.py --areas landing seitah delta floor deltaflat        # regions
python ml/terrain_seg/vector_db_patch.py --areas landing seitah delta floor deltaflat  # 4 m patches
```

Leave-one-area-out (each area classified with a database built only from the other four):

| area | region DB acc / majority | patch DB acc / majority | bedrock recall region -> patch |
|---|---|---|---|
| landing | 0.67 / 0.76 | 0.62 / 0.74 | - / 0.38 |
| seitah | 0.73 / 0.83 | 0.63 / 0.65 | 0.33 / 0.33 |
| delta | 0.82 / 0.48 | 0.75 / 0.51 | 0.00 / 0.14 |
| floor | 0.55 / 0.54 | 0.57 / 0.54 | 0.25 / 0.55 |
| deltaflat | 0.60 / 0.49 | 0.48 / 0.47 | 0.00 / 0.35 |

Reading: sand transfers well between areas (recall 0.7-0.9 on delta and Seitah); patch-level
representatives are the only way bedrock and big rocks get recalled at all; neither variant beats
the majority class everywhere. Bottlenecks: the rover model calls Perseverance light-toned
bedrock "soil" (domain shift from Curiosity), and orbital texture at 0.25 m does not resolve the
rover-scale classes. Next: orbital-scale classes, fine-tuning a head on MOMO, more areas on AWS.
MOMO patch tokens are position-debiased (MAE tokens encode position-in-crop).
