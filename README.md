# Asset-Transformation-Tool
Technical Assignment - Image Processing & Computer Vision EngineerTechnical Assignment - Image Processing & Computer Vision Engineer

# Asset Transformation Tool

A simple computer vision–based Python tool that converts a single
1080×1080 master creative into 5 secondary marketing assets
while preserving key visual elements.

---

## What This Tool Does

Input:
- One square PSD or JPEG (1080×1080)

Output:
- 3 image assets (landscape, square, portrait)
- 2 logo assets (landscape, square)
- Exact dimensions, no clipping, no padding

---

## Computer Vision Approach (Simple Explanation)

1. Important regions (text, logos, main objects) are detected using
   edge detection, color saliency, and contour analysis.
2. When changing aspect ratios, the crop window is chosen by maximizing
   visual importance instead of center-cropping.
3. For narrow logo formats, the most dominant region is resized and
   repositioned onto a background derived from the original image.

---

## 3-Sentence Strategy Summary

The tool identifies important visual regions using edge detection,
saliency maps, and contour analysis. It selects crop regions by maximizing
the amount of important content retained for each target aspect ratio.
For narrow logo formats, dominant regions are resized and repositioned
to maintain visual hierarchy without clipping or padding.

---

## How to Run

```bash
pip install -r requirements.txt

python main.py \
  --input input/lady.psd \
  --output output/secondary_assets

## Process Visualization (CV Explainability)

To demonstrate the computer vision methodology, a separate visualization
script is included.

It generates annotated images showing:
- Saliency detection
- Text, logo, and object detection
- Importance scoring
- Crop region decisions for different aspect ratios

Run:
```bash
python visualize_process.py

