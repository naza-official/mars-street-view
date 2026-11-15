"""Fine-tune SegFormer (nvidia/mit-b2, ImageNet encoder) on AI4Mars 4-class terrain segmentation.

Input  : PNG pairs from prepare.py ($MSV_DATA/work/ai4mars512/{train,test_min3})
Output : $MSV_DATA/models/ai4mars_segformer_b2/ (best checkpoint by val mIoU, metrics.json)

Loss is computed at the decoder's native 1/4 resolution (labels downsampled with nearest): the
crowd labels are coarse polygons, and full-resolution logits overflow 8 GB of VRAM.

Augmentation leans on photometric changes (gain, gamma, blur, noise) because the target domain is a
different camera (Perseverance Navcam, colour -> grey) from the training domain (Curiosity Navcam).

Usage: python ml/terrain_seg/train.py --epochs 8 --batch 8
"""

from __future__ import annotations

import argparse
import json
import math
import random
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image, ImageFilter
from torch.utils.data import DataLoader, Dataset
from transformers import SegformerForSemanticSegmentation

CLASSES = ["soil", "bedrock", "sand", "big_rock"]
IGNORE = 255
MEAN = np.array([0.485, 0.456, 0.406], np.float32)
STD = np.array([0.229, 0.224, 0.225], np.float32)


def to_tensor(gray: np.ndarray) -> torch.Tensor:
    x = np.repeat(gray[..., None].astype(np.float32) / 255.0, 3, axis=2)
    return torch.from_numpy(((x - MEAN) / STD).transpose(2, 0, 1).copy())


class PairDir(Dataset):
    def __init__(self, root: Path, stems: list[str], train: bool, size: int = 512):
        self.root, self.stems, self.train, self.size = root, stems, train, size

    def __len__(self) -> int:
        return len(self.stems)

    def augment(self, img: Image.Image, lab: Image.Image) -> tuple[Image.Image, Image.Image]:
        s = random.uniform(0.75, 1.5)
        n = int(self.size * s)
        img, lab = img.resize((n, n), Image.BILINEAR), lab.resize((n, n), Image.NEAREST)
        if n > self.size:
            x, y = random.randint(0, n - self.size), random.randint(0, n - self.size)
            box = (x, y, x + self.size, y + self.size)
            img, lab = img.crop(box), lab.crop(box)
        elif n < self.size:
            ci, cl = Image.new("L", (self.size,) * 2, 0), Image.new("L", (self.size,) * 2, IGNORE)
            ci.paste(img), cl.paste(lab)
            img, lab = ci, cl
        if random.random() < 0.5:
            img, lab = img.transpose(Image.FLIP_LEFT_RIGHT), lab.transpose(Image.FLIP_LEFT_RIGHT)
        if random.random() < 0.3:
            img = img.filter(ImageFilter.GaussianBlur(random.uniform(0.3, 1.2)))
        a = np.asarray(img).astype(np.float32) / 255.0
        a = np.clip(a * random.uniform(0.7, 1.3) + random.uniform(-0.1, 0.1), 0, 1)
        a = a ** random.uniform(0.7, 1.4)
        if random.random() < 0.3:
            a = np.clip(a + np.random.normal(0, 0.02, a.shape), 0, 1)
        return Image.fromarray((a * 255).astype(np.uint8)), lab

    def __getitem__(self, i: int):
        stem = self.stems[i]
        img = Image.open(self.root / f"{stem}.jpg").convert("L")
        lab = Image.open(self.root / f"{stem}.png")
        if self.train:
            img, lab = self.augment(img, lab)
        return to_tensor(np.asarray(img)), torch.from_numpy(np.asarray(lab).astype(np.int64))


def stems_in(d: Path) -> list[str]:
    return sorted(p.stem for p in d.glob("*.png"))


def class_weights(root: Path, stems: list[str], n: int = 800) -> torch.Tensor:
    counts = np.zeros(len(CLASSES))
    for s in random.Random(0).sample(stems, min(n, len(stems))):
        lab = np.asarray(Image.open(root / f"{s}.png"))
        counts += np.bincount(lab[lab != IGNORE].ravel(), minlength=len(CLASSES))[: len(CLASSES)]
    freq = counts / counts.sum()
    w = 1.0 / np.sqrt(freq + 1e-6)
    print("class pixel share:", dict(zip(CLASSES, freq.round(4), strict=True)))
    return torch.tensor(w / w.mean(), dtype=torch.float32)


def logits_full(model, x: torch.Tensor) -> torch.Tensor:
    out = model(pixel_values=x).logits
    return F.interpolate(out, size=x.shape[-2:], mode="bilinear", align_corners=False)


@torch.no_grad()
def evaluate(model, loader, device) -> dict:
    model.eval()
    inter = np.zeros(len(CLASSES))
    union = np.zeros(len(CLASSES))
    correct = total = 0
    for x, y in loader:
        x, y = x.to(device), y.to(device)
        with torch.autocast("cuda", dtype=torch.float16, enabled=device.type == "cuda"):
            p = logits_full(model, x).argmax(1)
        valid = y != IGNORE
        correct += int((p[valid] == y[valid]).sum())
        total += int(valid.sum())
        for c in range(len(CLASSES)):
            pc, yc = (p == c) & valid, (y == c) & valid
            inter[c] += int((pc & yc).sum())
            union[c] += int((pc | yc).sum())
    iou = inter / np.maximum(union, 1)
    model.train()
    return {"pixel_acc": correct / max(total, 1), "miou": float(iou.mean()),
            "iou": dict(zip(CLASSES, iou.round(4).tolist(), strict=True))}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--data", type=Path, default=Path.home() / "mars-data/work/ai4mars512")
    ap.add_argument("--out", type=Path,
                    default=Path.home() / "mars-data/models/ai4mars_segformer_b2")
    ap.add_argument("--backbone", default="nvidia/mit-b2")
    ap.add_argument("--epochs", type=int, default=8)
    ap.add_argument("--batch", type=int, default=8)
    ap.add_argument("--lr", type=float, default=6e-5)
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="use only N training images (smoke test)")
    a = ap.parse_args()
    random.seed(0)
    torch.manual_seed(0)
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    a.out.mkdir(parents=True, exist_ok=True)

    train_dir = a.data / "train"
    stems = stems_in(train_dir)
    random.Random(1).shuffle(stems)
    n_val = max(50, len(stems) // 20)
    val_stems, tr_stems = stems[:n_val], stems[n_val:]
    if a.limit:
        tr_stems, val_stems = tr_stems[: a.limit], val_stems[: max(20, a.limit // 10)]
    test_dir = a.data / "test_min3"
    n_test = len(stems_in(test_dir))
    print(f"train {len(tr_stems)}  val {len(val_stems)}  test {n_test}  on {device}")

    tr = DataLoader(PairDir(train_dir, tr_stems, True), a.batch, shuffle=True,
                    num_workers=a.workers, pin_memory=True, drop_last=True, persistent_workers=True)
    va = DataLoader(PairDir(train_dir, val_stems, False), a.batch, num_workers=a.workers)
    te = DataLoader(PairDir(test_dir, stems_in(test_dir), False), a.batch, num_workers=a.workers)

    model = SegformerForSemanticSegmentation.from_pretrained(
        a.backbone, num_labels=len(CLASSES), id2label=dict(enumerate(CLASSES)),
        label2id={c: i for i, c in enumerate(CLASSES)}, ignore_mismatched_sizes=True).to(device)
    w = class_weights(train_dir, tr_stems).to(device)
    opt = torch.optim.AdamW(model.parameters(), lr=a.lr, weight_decay=0.01)
    steps = a.epochs * len(tr)
    sched = torch.optim.lr_scheduler.LambdaLR(
        opt, lambda s: min(1.0, (s + 1) / 300) * (1 - s / steps) ** 0.9)
    scaler = torch.amp.GradScaler(enabled=device.type == "cuda")

    best, history, step, t0 = -1.0, [], 0, time.time()
    for ep in range(a.epochs):
        for x, y in tr:
            x, y = x.to(device, non_blocking=True), y.to(device, non_blocking=True)
            with torch.autocast("cuda", dtype=torch.float16, enabled=device.type == "cuda"):
                lg = model(pixel_values=x).logits
                ys = F.interpolate(y[:, None].float(), size=lg.shape[-2:], mode="nearest")
                loss = F.cross_entropy(lg.float(), ys[:, 0].long(), weight=w, ignore_index=IGNORE)
            opt.zero_grad(set_to_none=True)
            scaler.scale(loss).backward()
            scaler.step(opt)
            scaler.update()
            sched.step()
            step += 1
            if step % 100 == 0:
                rate = step / (time.time() - t0)
                eta = (steps - step) / rate / 60
                print(f"ep {ep} step {step}/{steps} loss {loss.item():.3f} "
                      f"{rate:.1f} it/s eta {eta:.0f} min", flush=True)
            if math.isnan(loss.item()):
                raise RuntimeError("loss is NaN")
        m = evaluate(model, va, device)
        history.append({"epoch": ep, "val": m})
        print(f"== epoch {ep}: val mIoU {m['miou']:.4f} acc {m['pixel_acc']:.4f} {m['iou']}",
              flush=True)
        if m["miou"] > best:
            best = m["miou"]
            model.save_pretrained(a.out)
    model = SegformerForSemanticSegmentation.from_pretrained(a.out).to(device)
    test = evaluate(model, te, device)
    print(f"== test_min3 (expert labels): mIoU {test['miou']:.4f} acc {test['pixel_acc']:.4f} "
          f"{test['iou']}")
    (a.out / "metrics.json").write_text(json.dumps(
        {"backbone": a.backbone, "epochs": a.epochs, "train_images": len(tr_stems),
         "best_val_miou": best, "history": history, "test_min3": test,
         "classes": CLASSES}, indent=2))


if __name__ == "__main__":
    main()
