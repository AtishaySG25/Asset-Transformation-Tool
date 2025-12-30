"""
Asset Generator
---------------------
Transforms a single PSD master asset into multiple secondary assets
using layout-aware rendering and computer vision techniques.

Supported outputs:
- 160x600 (skyscraper, layer-based)
- 200x200 (square, saliency-based crop)
- 300x250 (rectangle, saliency-based crop)
- 970x90, 728x90, 468x60 (banner, layer-based)

Author: Production-ready version
"""

import os
import cv2
import numpy as np
from psd_tools import PSDImage
from PIL import Image


# ====================================================
# CONFIGURATION
# ====================================================
INPUT_PSD = "D:/Asset-Transformation-Tool/new_aspect_ratios/input/Axis_Multicap_fund.psd"
OUTPUT_DIR = "D:/Asset-Transformation-Tool/new_aspect_ratios/output"

TARGET_SIZES = {
    "160x600": (160, 600),
    "300x250": (300, 250),
    "970x90": (970, 90),
    "728x90": (728, 90),
    "468x60": (468, 60),
}

os.makedirs(OUTPUT_DIR, exist_ok=True)


# ====================================================
# PSD LAYER EXTRACTION (USED BY LAYOUT-BASED RENDERS)
# ====================================================
def extract_layers(psd):
    """
    Extract visible PSD layers and classify them semantically.
    """
    objects = []

    for layer in psd:
        if not layer.visible:
            continue

        img = layer.composite()
        if img is None:
            continue

        if layer.kind == "type":
            obj_type, priority = "text", 3
        elif layer.kind == "smartobject":
            obj_type, priority = "logo", 4
        elif layer.size == psd.size:
            obj_type, priority = "background", 0
        else:
            obj_type, priority = "other", 1

        objects.append({
            "priority": priority,
            "type": obj_type,
            "image": img.convert("RGBA")
        })

    objects.sort(key=lambda x: x["priority"], reverse=True)
    return objects


# ====================================================
# 160x600 — SKYSCRAPER (LAYER-BASED)
# ====================================================
def render_160x600(objects, output_path):
    W, H = 160, 600
    canvas = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    padding = int(0.04 * W)

    for obj in objects:
        if obj["type"] == "background":
            canvas.paste(obj["image"].resize((W, H), Image.LANCZOS), (0, 0))
            break

    fg = [o for o in objects if o["type"] != "background"]
    y = padding

    for obj in fg:
        img = obj["image"]
        scale = min(W / img.width * 0.9, 1.5)
        img = img.resize((int(img.width * scale), int(img.height * scale)))
        x = (W - img.width) // 2

        if y + img.height > H:
            break

        canvas.paste(img, (x, y), img)
        y += img.height + padding

    canvas.save(output_path)


# ====================================================
# SALIENCY UTILITIES (USED BY 200x200, 300x250)
# ====================================================
def load_psd_flat(psd_path):
    psd = PSDImage.open(psd_path, ignore_errors=True)
    return psd.topil().convert("RGB")


def compute_saliency(pil_img):
    img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

    if hasattr(cv2, "saliency"):
        sal = cv2.saliency.StaticSaliencyFineGrained_create()
        _, sal_map = sal.computeSaliency(img)
        return sal_map

    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 100, 200)
    edges = cv2.GaussianBlur(edges, (5, 5), 0)

    return cv2.normalize(edges.astype("float32"), None, 0, 1, cv2.NORM_MINMAX)


def detect_objects(saliency_map, min_area):
    thresh = (saliency_map * 255).astype("uint8")
    _, binary = cv2.threshold(thresh, 120, 255, cv2.THRESH_BINARY)

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return [cv2.boundingRect(c) for c in contours if cv2.contourArea(c) > min_area]


# ====================================================
# 200x200 — SQUARE CROP
# ====================================================
def smart_square_crop(img, boxes, target_size):
    img_w, img_h = img.size
    crop_size = min(img_w, img_h)

    if boxes:
        xs = [x for x, _, w, _ in boxes] + [x + w for x, _, w, _ in boxes]
        ys = [y for _, y, _, h in boxes] + [y + h for _, y, _, h in boxes]
        cx, cy = int(sum(xs) / len(xs)), int(sum(ys) / len(ys))
    else:
        cx, cy = img_w // 2, img_h // 2

    half = crop_size // 2
    cropped = img.crop((cx - half, cy - half, cx + half, cy + half))
    return cropped.resize(target_size, Image.LANCZOS)


# ====================================================
# 300x250 — RECTANGLE CROP
# ====================================================
def smart_rectangle_crop(img, boxes, target_size):
    img_w, img_h = img.size
    target_w, target_h = target_size
    target_ratio = target_w / target_h
    img_ratio = img_w / img_h

    if boxes:
        xs = [x for x, _, w, _ in boxes] + [x + w for x, _, w, _ in boxes]
        ys = [y for _, y, _, h in boxes] + [y + h for _, y, _, h in boxes]
        cx, cy = int(sum(xs) / len(xs)), int(sum(ys) / len(ys))
    else:
        cx, cy = img_w // 2, img_h // 2

    if img_ratio > target_ratio:
        crop_h = img_h
        crop_w = int(crop_h * target_ratio)
    else:
        crop_w = img_w
        crop_h = int(crop_w / target_ratio)

    cropped = img.crop((
        cx - crop_w // 2,
        cy - crop_h // 2,
        cx + crop_w // 2,
        cy + crop_h // 2
    ))
    return cropped.resize(target_size, Image.LANCZOS)


# ====================================================
# BANNERS — 970x90, 728x90, 468x60 (LAYER-BASED)
# ====================================================
def render_banner(objects, target_size, output_path):
    W, H = target_size
    canvas = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    padding = int(0.06 * H)

    for obj in objects:
        if obj["type"] == "background":
            canvas.paste(obj["image"].resize((W, H), Image.LANCZOS), (0, 0))
            break

    fg = [o for o in objects if o["type"] != "background"]
    x_cursor = padding

    logo = next((o for o in fg if o["type"] == "logo"), None)
    if logo:
        img = logo["image"]
        scale = min((H - 2 * padding) / img.height * 0.7, 1.0)
        img = img.resize((int(img.width * scale), int(img.height * scale)))
        canvas.paste(img, (x_cursor, (H - img.height) // 2), img)
        x_cursor += img.width + padding

    main = next((o for o in fg if o["type"] in ("text", "other")), None)
    if main:
        img = main["image"]
        scale = min((H - 2 * padding) / img.height,
                    (W - x_cursor - padding) / img.width,
                    1.1)
        img = img.resize((int(img.width * scale), int(img.height * scale)))
        canvas.paste(img, (x_cursor, (H - img.height) // 2), img)

    canvas.save(output_path)


# ====================================================
# MAIN ENTRY POINT
# ====================================================
def main():
    psd = PSDImage.open(INPUT_PSD, ignore_errors=True)
    flat_img = load_psd_flat(INPUT_PSD)
    objects = extract_layers(psd)

    for name, size in TARGET_SIZES.items():
        out_path = os.path.join(OUTPUT_DIR, f"{name}.png")

        if name == "160x600":
            render_160x600(objects, out_path)

        elif name == "200x200":
            sal = compute_saliency(flat_img)
            boxes = detect_objects(sal, min_area=600)
            smart_square_crop(flat_img, boxes, size).save(out_path)

        elif name == "300x250":
            sal = compute_saliency(flat_img)
            boxes = detect_objects(sal, min_area=800)
            smart_rectangle_crop(flat_img, boxes, size).save(out_path)

        else:
            render_banner(objects, size, out_path)

        print(f"Generated: {out_path}")


if __name__ == "__main__":
    main()
