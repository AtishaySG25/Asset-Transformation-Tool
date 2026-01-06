import os
import cv2
import numpy as np
from psd_tools import PSDImage
from PIL import Image, ImageDraw, ImageFont
from enum import Enum

# ================= CONFIG =================
INPUT_PSD = "D:/Asset-Transformation-Tool/new_aspect_ratios/input/Axis_Multicap_fund.psd"
OUTPUT_DIR = "D:/Asset-Transformation-Tool/new_aspect_ratios/visualizations"
os.makedirs(OUTPUT_DIR, exist_ok=True)

FONT_SIZE = 16

# ================= TYPES =================
class ElementType(Enum):
    BACKGROUND = "BACKGROUND"
    LOGO = "LOGO"
    TEXT = "TEXT"
    GRAPH = "GRAPH"
    CTA = "CTA"
    UNKNOWN = "UNKNOWN"


# ================= HELPERS =================
def load_font(size):
    try:
        return ImageFont.truetype("arial.ttf", size)
    except:
        return ImageFont.load_default()


def draw_label(img, text, color):
    draw = ImageDraw.Draw(img)
    font = load_font(FONT_SIZE)
    draw.rectangle([0, 0, img.width, 24], fill=(0, 0, 0, 160))
    draw.text((6, 4), text, fill=color, font=font)


# ================= STRUCTURAL CLASSIFIER =================
def classify_structural(layer, psd_size):
    name = layer.name.lower()
    if "bg" in name or layer.size == psd_size:
        return ElementType.BACKGROUND
    if "logo" in name:
        return ElementType.LOGO
    if "cta" in name or "button" in name:
        return ElementType.CTA
    if "graph" in name or "chart" in name:
        return ElementType.GRAPH
    if layer.kind == "type":
        return ElementType.TEXT
    return ElementType.UNKNOWN


# ================= CV CLASSIFIER =================
def classify_cv(img, bbox, psd_size):
    w, h = img.size
    psd_w, psd_h = psd_size

    img_np = np.array(img.convert("RGB"))
    gray = cv2.cvtColor(img_np, cv2.COLOR_RGB2GRAY)

    area_ratio = (w * h) / (psd_w * psd_h)
    aspect_ratio = w / h if h else 1

    if area_ratio > 0.85:
        return ElementType.BACKGROUND

    if 0.7 <= aspect_ratio <= 1.5:
        edges = cv2.Canny(gray, 50, 150)
        if np.mean(edges > 0) > 0.02:
            return ElementType.LOGO

    if aspect_ratio > 2.0:
        edges = cv2.Canny(gray, 100, 200)
        if np.mean(edges > 0) > 0.05:
            return ElementType.TEXT

    color_std = np.std(img_np, axis=(0, 1)).mean()
    if color_std > 40:
        return ElementType.GRAPH

    return ElementType.UNKNOWN


# ================= HYBRID DECISION =================
def hybrid_decision(structural, cv):
    if structural != ElementType.UNKNOWN:
        return structural
    return cv


# ================= MAIN VIS =================
def visualize():
    psd = PSDImage.open(INPUT_PSD, ignore_errors=True)
    psd_size = psd.size

    index = 0
    tiles = []

    for layer in psd:
        if not layer.is_visible() or not layer.composite():
            continue

        img = layer.composite().convert("RGBA")
        bbox = layer.bbox

        structural = classify_structural(layer, psd_size)
        cv_type = classify_cv(img, bbox, psd_size)
        final = hybrid_decision(structural, cv_type)

        vis = img.copy()
        draw_label(
            vis,
            f"STRUCT: {structural.value} | CV: {cv_type.value} | FINAL: {final.value}",
            (255, 255, 255)
        )

        out_path = os.path.join(
            OUTPUT_DIR,
            f"layer_{index:02d}_{final.value.lower()}.png"
        )
        vis.save(out_path)
        tiles.append(vis.resize((300, 300)))

        index += 1

    # ===== summary grid =====
    cols = 3
    rows = (len(tiles) + cols - 1) // cols
    grid = Image.new("RGBA", (cols * 300, rows * 300), (255, 255, 255, 255))

    for i, tile in enumerate(tiles):
        x = (i % cols) * 300
        y = (i // cols) * 300
        grid.paste(tile, (x, y))

    grid.save(os.path.join(OUTPUT_DIR, "summary_grid.png"))
    print("✓ Hybrid visualization generated")


if __name__ == "__main__":
    visualize()
