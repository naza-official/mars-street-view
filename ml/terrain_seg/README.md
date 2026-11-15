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
