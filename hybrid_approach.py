"""
Combined Asset Generator
------------------------
Transforms a single 1080x1080 PSD master asset into multiple secondary assets
using computer vision techniques and layout-aware rendering.

Supported outputs:
- 160x600 (skyscraper, layer-based vertical stacking)
- 200x200 (square, saliency-based crop)
- 300x250 (rectangle, saliency-based crop)
- 970x90, 728x90, 468x60 (banners, fixed-layout positioning)

Author: Combined production-ready version
"""

import os
import cv2
import numpy as np
from psd_tools import PSDImage
from PIL import Image, ImageDraw, ImageFont, ImageOps
from enum import Enum
import logging

# Configure Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


# ====================================================
# CONFIGURATION
# ====================================================
INPUT_PSD = "D:/Datanodes_Assignment/input/Axis_Multicap_fund.psd"
OUTPUT_DIR = "D:/Datanodes_Assignment/general_approach/hybrid/output_hybrid"
OUTPUT_FORMAT = "PNG"  # Change to "JPG" if needed

TARGET_SIZES = {
    "160x600": (160, 600),
    "200x200": (200, 200),
    "300x250": (300, 250),
    "970x90": (970, 90),
    "728x90": (728, 90),
    "468x60": (468, 60),
}

os.makedirs(OUTPUT_DIR, exist_ok=True)


# ====================================================
# ELEMENT TYPE CLASSIFICATION (FOR BANNERS)
# ====================================================
class ElementType(Enum):
    LOGO = "logo"
    TEXT = "text"
    CTA = "cta"
    BACKGROUND = "background"
    GRAPH = "graph"
    UNKNOWN = "unknown"


class GraphicElement:
    """Wrapper for PSD layers with semantic classification"""
    def __init__(self, image: Image.Image, name: str, bbox: tuple):
        self.original_image = image
        self.image = image
        self.name = name
        self.original_bbox = bbox
        self.type = ElementType.UNKNOWN


def classify_element_generic(element: GraphicElement, psd_size: tuple) -> ElementType:
    """
    Generic element classification using computer vision techniques.
    No hardcoded layer names - works with any PSD.
    """
    img = element.original_image
    w, h = img.size
    psd_w, psd_h = psd_size
    x, y, x2, y2 = element.original_bbox
    
    # Convert to numpy for analysis
    img_array = np.array(img.convert('RGB'))
    
    # ========================================
    # 1. BACKGROUND DETECTION (Size-based)
    # ========================================
    area_ratio = (w * h) / (psd_w * psd_h)
    if area_ratio > 0.85:  # Covers >85% of canvas
        return ElementType.BACKGROUND
    
    # ========================================
    # 2. LOGO DETECTION
    # ========================================
    aspect_ratio = w / h if h > 0 else 1
    
    # Logos are typically:
    # - Square-ish (aspect ratio 0.7 to 1.5)
    # - Small to medium size (5-25% of canvas)
    # - High contrast / defined edges
    if 0.7 <= aspect_ratio <= 1.5 and 0.05 <= area_ratio <= 0.25:
        # Check edge density (logos have defined borders)
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        edge_density = np.sum(edges > 0) / (w * h)
        
        if edge_density > 0.02:  # Has defined edges
            return ElementType.LOGO
    
    # ========================================
    # 3. GRAPH/CHART DETECTION
    # ========================================
    # Graphs typically have:
    # - High color variance (multiple colors)
    # - Complex shapes
    # - Medium size
    if 0.1 <= area_ratio <= 0.5:
        # Color variance check
        color_std = np.std(img_array, axis=(0, 1)).mean()
        
        # Check for circular/arc patterns (common in pie charts, risk meters)
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        circles = cv2.HoughCircles(
            gray, cv2.HOUGH_GRADIENT, dp=1, minDist=20,
            param1=50, param2=30, minRadius=10, maxRadius=min(w, h)//2
        )
        
        if color_std > 40 or circles is not None:
            return ElementType.GRAPH
    
    # ========================================
    # 4. TEXT DETECTION
    # ========================================
    # Text typically has:
    # - Wide aspect ratio (width >> height)
    # - High horizontal edge density
    # - Located in upper portion
    if aspect_ratio > 2.0:  # Wide element
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 100, 200)
        edge_density = np.sum(edges > 0) / (w * h)
        
        # Text has very high edge density
        if edge_density > 0.05:
            return ElementType.TEXT
    
    # ========================================
    # 5. POSITIONAL HINTS (Fallback)
    # ========================================
    # Top-left elements often logos
    if x < psd_w * 0.3 and y < psd_h * 0.3 and area_ratio < 0.2:
        return ElementType.LOGO
    
    # Bottom-right elements often graphs/charts
    if x > psd_w * 0.5 and area_ratio > 0.1:
        return ElementType.GRAPH
    
    # Top elements often text
    if y < psd_h * 0.4 and aspect_ratio > 2.5:
        return ElementType.TEXT
    
    # ========================================
    # DEFAULT
    # ========================================
    return ElementType.UNKNOWN


def classify_element(element: GraphicElement, psd_size: tuple = None) -> ElementType:
    """
    HYBRID APPROACH: Try name-based first, fallback to CV-based.
    This ensures backward compatibility while adding generic support.
    """
    name_lower = element.name.lower()
    
    # Try name-based classification first (your working code)
    if any(x in name_lower for x in ['bg', 'background', 'grid']):
        logger.debug(f"✓ Name-based: {element.name} → BACKGROUND")
        return ElementType.BACKGROUND
    if 'logo' in name_lower:
        logger.debug(f"✓ Name-based: {element.name} → LOGO")
        return ElementType.LOGO
    if any(x in name_lower for x in ['cta', 'button', 'invest']):
        logger.debug(f"✓ Name-based: {element.name} → CTA")
        return ElementType.CTA
    if any(x in name_lower for x in ['graph', 'meter', 'chart', 'riskometer']):
        logger.debug(f"✓ Name-based: {element.name} → GRAPH")
        return ElementType.GRAPH
    if any(x in name_lower for x in ['text', 'headline', 'copy', 'multicap']):
        logger.debug(f"✓ Name-based: {element.name} → TEXT")
        return ElementType.TEXT
    
    # If name-based fails and psd_size provided, use CV-based detection
    if psd_size is not None:
        cv_result = classify_element_generic(element, psd_size)
        logger.info(f"⚡ CV-based: {element.name} → {cv_result.value.upper()}")
        return cv_result
    
    logger.warning(f"⚠ Unclassified: {element.name} → UNKNOWN")
    return ElementType.UNKNOWN


# ====================================================
# PSD LAYER EXTRACTION (FOR 160x600)
# ====================================================
def extract_layers(psd):
    """
    Extract visible PSD layers and classify them semantically.
    Used by 160x600 rendering.
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
# 160x600 – SKYSCRAPER (LAYER-BASED VERTICAL STACKING)
# ====================================================
def render_160x600(objects, output_path):
    """Render 160x600 skyscraper format with vertical stacking"""
    W, H = 160, 600
    canvas = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    padding = int(0.04 * W)

    # Background first
    for obj in objects:
        if obj["type"] == "background":
            canvas.paste(obj["image"].resize((W, H), Image.LANCZOS), (0, 0))
            break

    # Stack foreground elements vertically
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

    # Save with format
    save_image(canvas, output_path)
    logger.info(f"✓ Generated 160x600: {output_path}")


# ====================================================
# SALIENCY UTILITIES (FOR 200x200, 300x250)
# ====================================================
def load_psd_flat(psd_path):
    """Load PSD as flattened RGB image"""
    psd = PSDImage.open(psd_path, ignore_errors=True)
    return psd.topil().convert("RGB")


def compute_saliency(pil_img):
    """Compute saliency map using OpenCV"""
    img = cv2.cvtColor(np.array(pil_img), cv2.COLOR_RGB2BGR)

    if hasattr(cv2, "saliency"):
        sal = cv2.saliency.StaticSaliencyFineGrained_create()
        _, sal_map = sal.computeSaliency(img)
        return sal_map

    # Fallback: edge detection
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, 100, 200)
    edges = cv2.GaussianBlur(edges, (5, 5), 0)

    return cv2.normalize(edges.astype("float32"), None, 0, 1, cv2.NORM_MINMAX)


def detect_objects(saliency_map, min_area):
    """Detect object bounding boxes from saliency map"""
    thresh = (saliency_map * 255).astype("uint8")
    _, binary = cv2.threshold(thresh, 80, 255, cv2.THRESH_BINARY)

    contours, _ = cv2.findContours(binary, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return [cv2.boundingRect(c) for c in contours if cv2.contourArea(c) > min_area]


# ====================================================
# 200x200 – SQUARE CROP (SALIENCY-BASED)
# ====================================================
def smart_square_crop(img, boxes, target_size):
    """Intelligently crop to square based on detected objects"""
    img_w, img_h = img.size
    crop_size = min(img_w, img_h)

    if boxes:
        xs = [x for x, _, w, _ in boxes] + [x + w for x, _, w, _ in boxes]
        ys = [y for _, y, _, h in boxes] + [y + h for _, y, _, h in boxes]
        cx, cy = int(sum(xs) / len(xs)), int(sum(ys) / len(ys))
    else:
        cx, cy = img_w // 2, img_h // 2

    half = crop_size // 2
    cx = max(half, min(cx, img_w - half))
    cy = max(half, min(cy, img_h - half))
    cropped = img.crop((cx - half, cy - half, cx + half, cy + half))
    return cropped.resize(target_size, Image.LANCZOS)


# ====================================================
# 300x250 – RECTANGLE CROP (SALIENCY-BASED)
# ====================================================
def smart_rectangle_crop(img, boxes, target_size):
    """Intelligently crop to rectangle based on detected objects"""
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
    # Ensure crop stays within valid image bounds
    half_w = crop_w // 2
    half_h = crop_h // 2

    cx = max(half_w, min(cx, img_w - half_w))
    cy = max(half_h, min(cy, img_h - half_h))

    cropped = img.crop((
        cx - half_w,
        cy - half_h,
        cx + half_w,
        cy + half_h
    ))
    return cropped.resize(target_size, Image.LANCZOS)


# ====================================================
# BANNER RENDERING (970x90, 728x90, 468x60)
# ====================================================
def reposition_objects_fixed(elements: list[GraphicElement], target_size: tuple):
    """
    Fixed-layout positioning for banners:
    - Logo: left-anchored
    - Graph: right-anchored
    - CTA: center (dynamically scaled)
    """
    tw, th = target_size
    # Aggressive padding reduction for smallest banner
    px = 4 if th <= 60 else int(tw * 0.015)

    bg = next((e for e in elements if e.type == ElementType.BACKGROUND), None)
    logo = next((e for e in elements if e.type == ElementType.LOGO), None)
    graphs = [e for e in elements if e.type == ElementType.GRAPH]

    processed = []

    # 1. Background
    if bg:
        bg_img = ImageOps.fit(bg.original_image, (tw, th), Image.Resampling.LANCZOS)
        processed.append((bg_img, 0, 0))

    # 2. Right Anchor: Graphs
    right_boundary = tw - px
    if graphs:
        gh = int(th * 0.75) if th <= 60 else int(th * 0.85)
        total_graph_width = 0
        
        for g in reversed(graphs):
            gw = int(g.image.width * (gh / g.image.height))
            total_graph_width += gw + px
        
        # Check if graphs fit, if not, reduce their size
        max_graph_space = int(tw * 0.35)  # Graphs can't take more than 35% of width
        if total_graph_width > max_graph_space:
            scale_factor = max_graph_space / total_graph_width
            gh = int(gh * scale_factor)
        
        for g in reversed(graphs):
            gw = int(g.image.width * (gh / g.image.height))
            g_img = g.image.resize((gw, gh), Image.Resampling.LANCZOS)
            gx = max(px, right_boundary - gw)
            gy = (th - gh) // 2
            processed.append((g_img, gx, gy))
            right_boundary = gx - px

    # 3. Left Column: Text (top) + Logo (bottom)
    left_boundary = px
    left_column_width = 0

    # First, position logo at bottom of left column
    if logo:
        # Smaller logo - 30% of height, positioned at bottom
        lh = int(th * 0.30) if th <= 60 else int(th * 0.35)
        lw = int(logo.image.width * (lh / logo.image.height))
        
        # Ensure logo doesn't take too much space
        max_logo_width = int(tw * 0.30)  # Max 15% of width
        if lw > max_logo_width:
            scale_factor = max_logo_width / lw
            lw = max_logo_width
            lh = int(lh * scale_factor)
        
        l_img = logo.image.resize((lw, lh), Image.Resampling.LANCZOS)
        lx = px
        ly = int(th * 0.55)  # Position in lower half
        processed.append((l_img, lx, ly))
        left_column_width = lw

    # Add text elements ABOVE logo
    text_elements = [e for e in elements if e.type == ElementType.TEXT]
    if text_elements and logo:
        # Stack text above logo in same column
        text_y = int(th * 0.10)  # Start near top
        remaining_height = ly - text_y - px  # Space between text and logo
        
        for txt_elem in text_elements[:2]:  # Limit to 2 text elements max
            if remaining_height <= 0:
                break
                
            # Scale text to fit remaining vertical space
            txt_h = min(int(txt_elem.image.height * 0.4), remaining_height)
            txt_w = int(txt_elem.image.width * (txt_h / txt_elem.image.height))
            
            # Ensure text doesn't exceed left column width
            max_txt_width = int(tw * 0.20)  # Text can be slightly wider than logo
            if txt_w > max_txt_width:
                scale_factor = max_txt_width / txt_w
                txt_w = max_txt_width
                txt_h = int(txt_h * scale_factor)
            
            if txt_h > 10:  # Only add if reasonably visible
                txt_img = txt_elem.image.resize((txt_w, txt_h), Image.Resampling.LANCZOS)
                processed.append((txt_img, px, text_y))
                text_y += txt_h + int(px * 0.3)
                remaining_height -= (txt_h + int(px * 0.3))
                left_column_width = max(left_column_width, txt_w)

    left_boundary = px + left_column_width + px

    # 4. Center: CTA
    available_w = right_boundary - left_boundary

    if available_w > 20:
        cta_h = int(th * 0.5)
        desired_cta_w = int(cta_h * 2.5) if th <= 60 else int(cta_h * 3.2)
        cta_w = min(desired_cta_w, int(available_w * 0.98))

        gap_center = left_boundary + (available_w // 2)
        cta_x0 = gap_center - (cta_w // 2)
        cta_x1 = cta_x0 + cta_w

        if cta_x1 <= cta_x0:
            cta_x1 = cta_x0 + 1

        cta_y = (th - cta_h) // 2
        cta_data = {'rect': [cta_x0, cta_y, cta_x1, cta_y + cta_h], 'text': "INVEST NOW"}
    else:
        cta_data = None

    # 4. Center: CTA (only if enough space)
    available_w = right_boundary - left_boundary

    # Minimum space required for CTA to be visible
    min_cta_width = 80 if th <= 60 else 100
    
    if available_w > min_cta_width:
        cta_h = int(th * 0.45) if th <= 60 else int(th * 0.5)
        desired_cta_w = int(cta_h * 2.5) if th <= 60 else int(cta_h * 3.2)
        
        # CTA can't take more than 70% of available space
        max_cta_w = int(available_w * 0.7)
        cta_w = min(desired_cta_w, max_cta_w)
        
        # Ensure minimum width
        cta_w = max(cta_w, min_cta_width)

        gap_center = left_boundary + (available_w // 2)
        cta_x0 = gap_center - (cta_w // 2)
        cta_x1 = cta_x0 + cta_w

        if cta_x1 <= cta_x0:
            cta_x1 = cta_x0 + 1

        cta_y = (th - cta_h) // 2
        cta_data = {'rect': [cta_x0, cta_y, cta_x1, cta_y + cta_h], 'text': "INVEST NOW"}
    else:
        # Not enough space - skip CTA
        cta_data = None

    return processed, cta_data

def render_banner(layers, cta, size, path):
    """Render banner with fixed layout and dynamic CTA text scaling"""
    canvas = Image.new("RGBA", size, (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)

    # 1. Paste foreground layers
    for img, x, y in layers:
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        canvas.paste(img, (x, y), img)

    # 2. Draw CTA with dynamic text scaling
    if cta is not None:
        r = cta['rect']
        btn_w = r[2] - r[0]
        btn_h = r[3] - r[1]

        # Draw button body
        draw.rounded_rectangle([r[0], r[1], r[2], r[3]], radius=6, fill="#9f1c55")

        # Dynamic font scaling
        current_font_size = int(btn_h * 0.5)
        text_str = cta['text']

        while current_font_size > 8:
            try:
                font = ImageFont.truetype("arialbd.ttf", current_font_size)
            except:
                font = ImageFont.load_default()

            bbox = draw.textbbox((0, 0), text_str, font=font)
            txt_w = bbox[2] - bbox[0]
            txt_h = bbox[3] - bbox[1]

            if txt_w < (btn_w - 8):
                break
            current_font_size -= 1

        # Center and draw text
        text_x = r[0] + (btn_w - txt_w) // 2
        text_y = r[1] + (btn_h - txt_h) // 2 - 2

        draw.text((text_x, text_y), text_str, fill="white", font=font)

    # Save with format
    save_image(canvas, path)
    logger.info(f"✓ Generated {size[0]}x{size[1]}: {path}")


# ====================================================
# UNIFIED SAVE FUNCTION
# ====================================================
def save_image(img, path):
    """Save image in standardized format"""
    if OUTPUT_FORMAT.upper() == "JPG":
        img.convert("RGB").save(path.replace(".png", ".jpg"), quality=98, subsampling=0)
    else:
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        img.save(path.replace(".jpg", ".png"))


# ====================================================
# MAIN ENTRY POINT
# ====================================================
def main():
    logger.info("="*60)
    logger.info("Combined Asset Generator - Starting")
    logger.info("="*60)

    # Load PSD
    psd = PSDImage.open(INPUT_PSD, ignore_errors=True)
    flat_img = load_psd_flat(INPUT_PSD)

    # Extract layers for 160x600
    objects_160 = extract_layers(psd)

    # Extract elements for banners
    elements_banner = []
    psd_size = psd.size
    for layer in psd:
        if layer.is_visible() and layer.composite():
            el = GraphicElement(layer.composite(), layer.name, layer.bbox)
            el.type = classify_element(el, psd_size)
            elements_banner.append(el)

    # Process each target size
    for name, size in TARGET_SIZES.items():
        ext = ".jpg" if OUTPUT_FORMAT.upper() == "JPG" else ".png"
        out_path = os.path.join(OUTPUT_DIR, f"{name}{ext}")

        if name == "160x600":
            # Layer-based vertical stacking
            render_160x600(objects_160, out_path)

        elif name == "200x200":
            # Saliency-based square crop
            sal = compute_saliency(flat_img)
            boxes = detect_objects(sal, min_area=1500)
            cropped = smart_square_crop(flat_img, boxes, size)
            save_image(cropped, out_path)
            logger.info(f"✓ Generated 200x200: {out_path}")

        elif name == "300x250":
            # Saliency-based rectangle crop
            sal = compute_saliency(flat_img)
            boxes = detect_objects(sal, min_area=2000)
            cropped = smart_rectangle_crop(flat_img, boxes, size)
            save_image(cropped, out_path)
            logger.info(f"✓ Generated 300x250: {out_path}")

        else:
            # Fixed-layout banners (970x90, 728x90, 468x60)
            layers, cta = reposition_objects_fixed(elements_banner, size)
            render_banner(layers, cta, size, out_path)

    logger.info("="*60)
    logger.info("✓ All assets generated successfully!")
    logger.info("="*60)


if __name__ == "__main__":
    main()