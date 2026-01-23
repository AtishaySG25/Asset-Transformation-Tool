"""
Combined Asset Generator - FIXED VERSION
------------------------
Transforms a single 1080x1080 PSD master asset into multiple secondary assets
using computer vision techniques and layout-aware rendering.

Author: Fixed production-ready version
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
INPUT_PSD = "D:/Asset-Transformation-Tool/new_aspect_ratios/input/Axis.psd"
OUTPUT_DIR = "D:/Asset-Transformation-Tool/new_aspect_ratios/output"
OUTPUT_FORMAT = "PNG"

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
# ELEMENT TYPE CLASSIFICATION
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
    """Generic element classification using computer vision techniques."""
    img = element.original_image
    w, h = img.size
    psd_w, psd_h = psd_size
    x, y, x2, y2 = element.original_bbox
    
    img_array = np.array(img.convert('RGB'))
    area_ratio = (w * h) / (psd_w * psd_h)
    
    # Background detection
    if area_ratio > 0.85:
        return ElementType.BACKGROUND
    
    # Logo detection
    aspect_ratio = w / h if h > 0 else 1
    if 0.7 <= aspect_ratio <= 1.5 and 0.05 <= area_ratio <= 0.25:
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 50, 150)
        edge_density = np.sum(edges > 0) / (w * h)
        if edge_density > 0.02:
            return ElementType.LOGO
    
    # Graph/Chart detection
    if 0.1 <= area_ratio <= 0.5:
        color_std = np.std(img_array, axis=(0, 1)).mean()
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        circles = cv2.HoughCircles(
            gray, cv2.HOUGH_GRADIENT, dp=1, minDist=20,
            param1=50, param2=30, minRadius=10, maxRadius=min(w, h)//2
        )
        if color_std > 40 or circles is not None:
            return ElementType.GRAPH
    
    # Text detection
    if aspect_ratio > 2.0:
        gray = cv2.cvtColor(img_array, cv2.COLOR_RGB2GRAY)
        edges = cv2.Canny(gray, 100, 200)
        edge_density = np.sum(edges > 0) / (w * h)
        if edge_density > 0.05:
            return ElementType.TEXT
    
    # Positional hints
    if x < psd_w * 0.3 and y < psd_h * 0.3 and area_ratio < 0.2:
        return ElementType.LOGO
    if x > psd_w * 0.5 and area_ratio > 0.1:
        return ElementType.GRAPH
    if y < psd_h * 0.4 and aspect_ratio > 2.5:
        return ElementType.TEXT
    
    return ElementType.UNKNOWN


def classify_element(element: GraphicElement, psd_size: tuple = None) -> ElementType:
    """HYBRID APPROACH: Try name-based first, fallback to CV-based."""
    name_lower = element.name.lower()
    
    # Name-based classification with generic keywords
    # Background detection
    if any(x in name_lower for x in ['bg', 'background', 'backdrop']):
        logger.debug(f"✓ Name-based: {element.name} → BACKGROUND")
        return ElementType.BACKGROUND
    
    # Logo detection
    if 'logo' in name_lower or 'brand' in name_lower:
        logger.debug(f"✓ Name-based: {element.name} → LOGO")
        return ElementType.LOGO
    
    # CTA/Button detection
    if any(x in name_lower for x in ['cta', 'button', 'btn', 'action', 'click']):
        logger.debug(f"✓ Name-based: {element.name} → CTA")
        return ElementType.CTA
    
    # Graph/Chart detection
    if any(x in name_lower for x in ['graph', 'chart', 'meter', 'gauge', 'diagram', 'plot']):
        logger.debug(f"✓ Name-based: {element.name} → GRAPH")
        return ElementType.GRAPH
    
    # Text detection (generic keywords only)
    if any(x in name_lower for x in ['text', 'headline', 'title', 'heading', 'copy', 'label']):
        logger.debug(f"✓ Name-based: {element.name} → TEXT")
        return ElementType.TEXT
    
    # Disclaimer/footer text detection
    if any(x in name_lower for x in ['disclaimer', 'footer', 'legal', 'note', 'warning']):
        logger.debug(f"✓ Name-based: {element.name} → TEXT (disclaimer)")
        return ElementType.TEXT
    
    # CV-based fallback - only use if name-based fails
    if psd_size is not None:
        cv_result = classify_element_generic(element, psd_size)
        logger.info(f"⚡ CV-based: {element.name} → {cv_result.value.upper()}")
        return cv_result
    
    logger.warning(f"⚠ Unclassified: {element.name} → UNKNOWN")
    return ElementType.UNKNOWN


# ====================================================
# PSD LAYER EXTRACTION
# ====================================================
def extract_layers(psd):
    """Extract visible PSD layers and classify them semantically."""
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
            "name": layer.name,
            "image": img.convert("RGBA")
        })

    objects.sort(key=lambda x: x["priority"], reverse=True)
    return objects


# ====================================================
# 160x600 – SKYSCRAPER (FIXED VERSION)
# ====================================================
def render_160x600(objects, output_path):
    """Render 160x600 skyscraper with semantic vertical ordering"""
    W, H = 160, 600
    canvas = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    padding = 6
    
    # 1. Background - full canvas
    bg_obj = next((o for o in objects if o["type"] == "background"), None)
    if bg_obj:
        bg_img = bg_obj["image"].resize((W, H), Image.LANCZOS)
        canvas.paste(bg_img, (0, 0))
    
    # 2. Categorize elements by semantic type
    text_objs = [o for o in objects if o["type"] == "text"]
    logo_objs = [o for o in objects if o["type"] == "logo"]
    other_objs = [o for o in objects if o["type"] == "other"]
    
    # 3. Define semantic ordering (top to bottom)
    ordered_elements = []
    
    # Top section: Text elements (headlines)
    for txt in text_objs:
        ordered_elements.append({
            'obj': txt,
            'section': 'top',
            'width_percent': 0.92
        })
    
    # Middle section: Other elements (CTAs, graphs, app badges, ratings)
    # Sort other elements by vertical position in original PSD
    other_objs_sorted = sorted(other_objs, key=lambda x: x.get('bbox', (0, 0, 0, 0))[1] if 'bbox' in x else 0)
    
    for other in other_objs_sorted:
        name_lower = other.get('name', '').lower()
        # Detect app badges and ratings (should go in middle)
        if any(x in name_lower for x in ['app', 'store', 'rating', 'star', 'download']):
            ordered_elements.append({
                'obj': other,
                'section': 'middle',
                'width_percent': 0.85
            })
        # CTAs and buttons
        elif any(x in name_lower for x in ['button', 'cta', 'invest', 'click']):
            ordered_elements.append({
                'obj': other,
                'section': 'middle',
                'width_percent': 0.88
            })
        # Graphs and charts
        else:
            ordered_elements.append({
                'obj': other,
                'section': 'middle',
                'width_percent': 0.85
            })
    
    # Bottom section: Logo (anchor to bottom)
    logo_elem = None
    if logo_objs:
        logo_elem = logo_objs[0]
    
    # 4. Calculate scaling for non-logo elements
    scaled_elements = []
    total_natural_height = 0
    
    for elem_data in ordered_elements:
        img = elem_data['obj']['image']
        target_width = int(W * elem_data['width_percent'])
        scale = target_width / img.width
        scaled_h = int(img.height * scale)
        scaled_w = int(img.width * scale)
        
        scaled_elements.append({
            'obj': elem_data['obj'],
            'width': scaled_w,
            'height': scaled_h,
            'original_img': img,
            'section': elem_data['section']
        })
        total_natural_height += scaled_h
    
    # 5. Reserve space for logo at bottom
    logo_height = 0
    logo_data = None
    if logo_elem:
        logo_h = int(H * 0.08)  # 8% of height for logo
        logo_w = int(logo_elem['image'].width * (logo_h / logo_elem['image'].height))
        if logo_w > W * 0.5:
            logo_w = int(W * 0.5)
            logo_h = int(logo_elem['image'].height * (logo_w / logo_elem['image'].width))
        logo_height = logo_h + padding
        logo_data = {
            'img': logo_elem['image'],
            'width': logo_w,
            'height': logo_h
        }
    
    # 6. Calculate available space and spacing
    available_height = H - (padding * 2) - logo_height
    total_padding_needed = padding * len(scaled_elements)
    space_needed = total_natural_height + total_padding_needed
    
    if space_needed > available_height:
        # Compress elements proportionally
        compression_ratio = (available_height - total_padding_needed) / total_natural_height
        for elem in scaled_elements:
            elem['height'] = int(elem['height'] * compression_ratio)
            elem['width'] = int(elem['width'] * compression_ratio)
        dynamic_padding = padding
    else:
        # Distribute extra space
        extra_space = available_height - space_needed
        num_gaps = len(scaled_elements)
        dynamic_padding = padding + (extra_space // max(num_gaps, 1))
    
    # 7. Place elements top to bottom
    y = padding
    
    for elem in scaled_elements:
        if elem['height'] <= 0 or elem['width'] <= 0:
            continue
        
        # Resize element
        img_resized = elem['original_img'].resize(
            (elem['width'], elem['height']), 
            Image.LANCZOS
        )
        
        # Center horizontally
        x = (W - elem['width']) // 2
        
        # Check if it fits
        if y + elem['height'] > H - logo_height - padding:
            logger.warning(f"Skipping element - out of vertical space")
            break
        
        # Paste element
        canvas.paste(img_resized, (x, y), img_resized)
        y += elem['height'] + dynamic_padding
    
    # 8. Place logo at bottom right
    if logo_data:
        logo_img_resized = logo_data['img'].resize(
            (logo_data['width'], logo_data['height']), 
            Image.LANCZOS
        )
        logo_x = W - logo_data['width'] - padding
        logo_y = H - logo_data['height'] - padding
        canvas.paste(logo_img_resized, (logo_x, logo_y), logo_img_resized)
    
    save_image(canvas, output_path)
    logger.info(f"✓ Generated 160x600: {output_path}")


# ====================================================
# SALIENCY UTILITIES
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
# SMART CROP FUNCTIONS
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
    
    half_w = crop_w // 2
    half_h = crop_h // 2

    cx = max(half_w, min(cx, img_w - half_w))
    cy = max(half_h, min(cy, img_h - half_h))

    cropped = img.crop((cx - half_w, cy - half_h, cx + half_w, cy + half_h))
    return cropped.resize(target_size, Image.LANCZOS)


# ====================================================
# BANNER RENDERING (FIXED VERSION)
# ====================================================
def extract_dominant_colors(img, n_colors=2):
    """Extract dominant colors from an image using K-means clustering."""
    # Convert to RGB and reshape
    img_array = np.array(img.convert('RGB'))
    pixels = img_array.reshape(-1, 3)
    
    # Sample pixels for efficiency (use max 10000 pixels)
    if len(pixels) > 10000:
        indices = np.random.choice(len(pixels), 10000, replace=False)
        pixels = pixels[indices]
    
    # Remove very dark and very light pixels (likely shadows/highlights)
    mask = (pixels.sum(axis=1) > 30) & (pixels.sum(axis=1) < 700)
    pixels = pixels[mask]
    
    if len(pixels) == 0:
        return ['#333333', '#ffffff']  # Fallback
    
    # K-means clustering
    from sklearn.cluster import KMeans
    kmeans = KMeans(n_clusters=min(n_colors, len(pixels)), random_state=42, n_init=10)
    kmeans.fit(pixels)
    
    # Get colors sorted by frequency
    colors = kmeans.cluster_centers_.astype(int)
    
    # Convert to hex
    hex_colors = ['#%02x%02x%02x' % tuple(color) for color in colors]
    return hex_colors


def get_contrasting_color(bg_color):
    """Get contrasting text color (black or white) based on background."""
    # Remove # if present
    bg_color = bg_color.lstrip('#')
    
    # Convert to RGB
    r, g, b = int(bg_color[0:2], 16), int(bg_color[2:4], 16), int(bg_color[4:6], 16)
    
    # Calculate luminance
    luminance = (0.299 * r + 0.587 * g + 0.114 * b) / 255
    
    # Return black for light backgrounds, white for dark
    return '#000000' if luminance > 0.5 else '#ffffff'


def reposition_objects_fixed(elements: list[GraphicElement], target_size: tuple, psd_composite=None):
    """
    Fixed-layout positioning for banners - uses ALL visible layers as images.
    No zone-based logic, just smart scaling and positioning.
    """
    tw, th = target_size
    padding = max(6, int(tw * 0.008))
    
    # Separate elements by type
    bg = next((e for e in elements if e.type == ElementType.BACKGROUND), None)
    logo = next((e for e in elements if e.type == ElementType.LOGO), None)
    graphs = [e for e in elements if e.type == ElementType.GRAPH]
    text_elements = [e for e in elements if e.type == ElementType.TEXT]
    cta_elements = [e for e in elements if e.type == ElementType.CTA]
    
    # Separate disclaimer from main text
    disclaimer_elem = None
    main_text = []
    for txt in text_elements:
        name_lower = txt.name.lower()
        if any(x in name_lower for x in ['disclaimer', 'footer', 'legal', 'warning', 'note']):
            disclaimer_elem = txt
        else:
            main_text.append(txt)
    
    processed = []
    
    # 1. Background - full canvas
    if bg:
        bg_img = bg.original_image.resize((tw, th), Image.Resampling.LANCZOS)
        processed.append((bg_img, 0, 0))
    
    # 2. Determine available horizontal space and create a flow layout
    x_cursor = padding
    y_cursor = padding
    
    # Left side: Text elements (takes ~40-50% of width)
    max_text_width = int(tw * 0.45)
    
    for i, txt in enumerate(main_text[:3]):  # Max 3 text elements
        # Scale text to be readable
        if i == 0:  # Main headline
            target_h = int(th * 0.5)  # Bigger
        else:
            target_h = int(th * 0.3)  # Smaller secondary text
        
        target_w = int(txt.image.width * (target_h / txt.image.height))
        
        # Constrain to max width
        if target_w > max_text_width:
            target_w = max_text_width
            target_h = int(txt.image.height * (target_w / txt.image.width))
        
        if target_h > 0 and target_w > 0:
            txt_img = txt.image.resize((target_w, target_h), Image.Resampling.LANCZOS)
            txt_x = padding
            txt_y = y_cursor
            
            # Ensure it fits vertically
            if txt_y + target_h <= th - padding:
                processed.append((txt_img, txt_x, txt_y))
                y_cursor += target_h + padding
    
    # Update x_cursor for next column
    x_cursor = max_text_width + padding * 2
    
    # Middle: CTA elements
    if cta_elements:
        cta_y = padding
        for cta in cta_elements[:2]:  # Max 2 CTAs
            # Scale CTA to fit
            cta_h = int(th * 0.4)
            cta_w = int(cta.image.width * (cta_h / cta.image.height))
            
            # Limit width
            max_cta_w = int(tw * 0.2)
            if cta_w > max_cta_w:
                cta_w = max_cta_w
                cta_h = int(cta.image.height * (cta_w / cta.image.width))
            
            if cta_h > 0 and cta_w > 0 and x_cursor + cta_w < tw - padding:
                cta_img = cta.image.resize((cta_w, cta_h), Image.Resampling.LANCZOS)
                processed.append((cta_img, x_cursor, cta_y))
                cta_y += cta_h + padding
    
    # Update x_cursor for graphs
    x_cursor = int(tw * 0.65)
    
    # Right side: Graphs
    if graphs:
        graph_y = int(th * 0.15)  # Start a bit below top
        graph_h = int(th * 0.7)
        
        for graph in graphs[:3]:  # Max 3 graphs
            graph_w = int(graph.image.width * (graph_h / graph.image.height))
            
            # Ensure fit
            if x_cursor + graph_w > tw - padding:
                # Scale down to fit
                graph_w = tw - x_cursor - padding
                graph_h = int(graph.image.height * (graph_w / graph.image.width))
            
            if graph_h > 0 and graph_w > 0:
                graph_img = graph.image.resize((graph_w, graph_h), Image.Resampling.LANCZOS)
                processed.append((graph_img, x_cursor, graph_y))
                x_cursor += graph_w + padding
    
    # Logo: Bottom right corner
    if logo:
        logo_h = int(th * 0.25)
        logo_w = int(logo.image.width * (logo_h / logo.image.height))
        
        # Limit size
        max_logo_w = int(tw * 0.12)
        if logo_w > max_logo_w:
            logo_w = max_logo_w
            logo_h = int(logo.image.height * (logo_w / logo.image.width))
        
        if logo_h > 0 and logo_w > 0:
            logo_img = logo.image.resize((logo_w, logo_h), Image.Resampling.LANCZOS)
            logo_x = tw - logo_w - padding
            logo_y = th - logo_h - padding
            processed.append((logo_img, logo_x, logo_y))
    
    # Prepare disclaimer
    disclaimer_layer = None
    if disclaimer_elem:
        disclaimer_layer = {'image': disclaimer_elem.image}
    
    return processed, [], disclaimer_layer  # Empty CTA data since we're using actual images


def render_banner(layers, cta_list, size, path, disclaimer_layer=None):
    """Render banner with fixed layout and multiple CTAs"""
    canvas = Image.new("RGBA", size, (255, 255, 255, 255))
    draw = ImageDraw.Draw(canvas)
    tw, th = size

    # 1. Paste foreground layers
    for img, x, y in layers:
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        canvas.paste(img, (x, y), img)

    # 2. Draw CTA buttons
    if cta_list:
        for cta in cta_list:
            r = cta['rect']
            btn_w = r[2] - r[0]
            btn_h = r[3] - r[1]
            
            bg_color = cta.get('bg_color', '#9f1c55')
            text_color = cta.get('text_color', 'white')
            
            # Draw button body
            draw.rounded_rectangle([r[0], r[1], r[2], r[3]], radius=6, fill=bg_color)
            
            # Dynamic font scaling
            text_str = cta['text']
            current_font_size = int(btn_h * 0.4)
            
            while current_font_size > 6:
                try:
                    font = ImageFont.truetype("arialbd.ttf", current_font_size)
                except:
                    font = ImageFont.load_default()
                
                bbox = draw.textbbox((0, 0), text_str, font=font)
                txt_w = bbox[2] - bbox[0]
                txt_h = bbox[3] - bbox[1]
                
                if txt_w < (btn_w - 10):
                    break
                current_font_size -= 1
            
            # Center and draw text
            text_x = r[0] + (btn_w - txt_w) // 2
            text_y = r[1] + (btn_h - txt_h) // 2
            
            draw.text((text_x, text_y), text_str, fill=text_color, font=font)
    
    # 3. Add disclaimer layer if exists (for wide banners)
    if disclaimer_layer and tw >= 728:
        disc_img = disclaimer_layer['image']
        # Scale to fit width while maintaining aspect ratio
        disc_h = int(th * 0.12)  # 12% of banner height
        disc_w = int(disc_img.width * (disc_h / disc_img.height))
        
        # If too wide, scale down
        if disc_w > tw - 20:
            disc_w = tw - 20
            disc_h = int(disc_img.height * (disc_w / disc_img.width))
        
        disc_resized = disc_img.resize((disc_w, disc_h), Image.Resampling.LANCZOS)
        disc_x = (tw - disc_w) // 2
        disc_y = th - disc_h - 2
        
        canvas.paste(disc_resized, (disc_x, disc_y), disc_resized)

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
    logger.info("Combined Asset Generator - FIXED VERSION")
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
            # Fixed vertical stacking
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
            layers, cta_list, disclaimer_layer = reposition_objects_fixed(elements_banner, size, flat_img)
            render_banner(layers, cta_list, size, out_path, disclaimer_layer)

    logger.info("="*60)
    logger.info("✓ All assets generated successfully!")
    logger.info("="*60)


if __name__ == "__main__":
    main()