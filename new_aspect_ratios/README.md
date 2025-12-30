# Smart Asset Generator

## Overview
This project builds a Python-based tool to transform a single 1080×1080 PSD master asset into multiple secondary advertising assets with exact dimensions, while preserving key visual elements such as logos, text, and product imagery.

The system intelligently adapts layout strategies based on aspect ratio rather than relying on naive resizing.

---

## Supported Output Sizes

| Asset Type | Dimensions | Strategy |
|----------|------------|----------|
| Skyscraper | 160×600 | Layer-based vertical stacking |
| Square | 200×200 | Saliency-based square crop |
| Medium Rectangle | 300×250 | Saliency-based rectangular crop |
| Banner | 970×90 | Layer-based horizontal layout |
| Banner | 728×90 | Layer-based horizontal layout |
| Banner | 468×60 | Layer-based horizontal layout |

---

## Key Design Decisions

### 1. Aspect-Ratio–Aware Rendering
Each output size uses a dedicated strategy:
- Tall formats preserve vertical hierarchy.
- Box formats rely on visual saliency detection.
- Thin banners re-layout PSD layers instead of cropping.

### 2. PSD Layer Semantics
Text layers, logos, backgrounds, and other visual elements are classified and prioritized to preserve brand hierarchy.

### 3. Robustness
- Uses `ignore_errors=True` to handle modern PSD features.
- Avoids reliance on unsupported PSD metadata.
- Saliency has safe fallbacks when OpenCV contrib modules are unavailable.

---


---

## How to Run

1. Install dependencies:
```bash
pip install psd-tools pillow opencv-python opencv-contrib-python
python smart_asset_generator.py


