import cv2
import numpy as np
from PIL import Image
from psd_tools import PSDImage
import os
import logging
from typing import Tuple, List, Dict

class AssetTransformer:
    """Combined asset transformation tool generating all 5 secondary assets."""
    
    def __init__(self, input_path: str, output_dir: str):
        self.input_path = input_path
        self.output_dir = output_dir
        self.master_img = None  # BGR format for OpenCV operations
        self.master_array = None  # RGB format for PIL/numpy operations
        self.integral_saliency = None

    def load_master_asset(self):
        """Load PSD or JPEG and resize to 1080x1080 if needed."""
        if self.input_path.lower().endswith('.psd'):
            psd = PSDImage.open(self.input_path)
            pil_img = psd.composite()
            logger.info(f"Loaded PSD: {pil_img.size}")
        else:
            pil_img = Image.open(self.input_path)
            logger.info(f"Loaded image: {pil_img.size}")
        
        # Store in both formats
        self.master_array = np.array(pil_img.convert('RGB'))
        self.master_img = cv2.cvtColor(self.master_array, cv2.COLOR_RGB2BGR)
        
        if self.master_img.shape[:2] != (1080, 1080):
            logger.warning("Resizing master to 1080x1080")
            self.master_img = cv2.resize(self.master_img, (1080, 1080), interpolation=cv2.INTER_LANCZOS4)
            self.master_array = cv2.cvtColor(self.master_img, cv2.COLOR_BGR2RGB)

    def compute_saliency_mask(self):
        """Custom saliency emphasizing text, high-contrast objects, and color distinctiveness."""
        gray = cv2.cvtColor(self.master_img, cv2.COLOR_BGR2GRAY)
        
        # Strong edge detection for text and logos
        edges = cv2.Canny(gray, 80, 160)
        edges = cv2.dilate(edges, np.ones((7,7), np.uint8), iterations=4)
        edges = cv2.GaussianBlur(edges, (31, 31), 0)
        
        # Color saliency in Lab space
        lab = cv2.cvtColor(self.master_img, cv2.COLOR_BGR2Lab)
        l, a, b = cv2.split(lab)
        color_sal = cv2.GaussianBlur(a**2 + b**2, (31, 31), 0)
        
        # Combine with emphasis on edges
        saliency_map = cv2.normalize(edges, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        color_map = cv2.normalize(color_sal, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
        saliency_map = cv2.addWeighted(saliency_map, 0.8, color_map, 0.2, 0)
        
        # Threshold and clean up
        _, binary_mask = cv2.threshold(saliency_map, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        binary_mask = cv2.morphologyEx(binary_mask, cv2.MORPH_CLOSE, np.ones((20,20), np.uint8))
        binary_mask = cv2.morphologyEx(binary_mask, cv2.MORPH_OPEN, np.ones((10,10), np.uint8))
        
        self.integral_saliency = cv2.integral(binary_mask)
        logger.info("Saliency mask computed")

    def detect_salient_regions(self) -> np.ndarray:
        """Detect salient regions using spectral residual method."""
        saliency = cv2.saliency.StaticSaliencySpectralResidual_create()
        success, saliency_map = saliency.computeSaliency(self.master_array)
        
        saliency_map = (saliency_map * 255).astype(np.uint8)
        saliency_map = cv2.GaussianBlur(saliency_map, (5, 5), 0)
        
        return saliency_map
    
    def calculate_region_importance(self, regions: Dict) -> np.ndarray:
        """Calculate importance score map based on detected regions."""
        h, w = self.master_array.shape[:2]
        importance_map = np.zeros((h, w), dtype=np.float32)
        
        saliency = self.detect_salient_regions()
        importance_map += saliency.astype(np.float32) / 255.0
        
        for x, y, w, h in regions['text']:
            importance_map[y:y+h, x:x+w] += 3.0
        
        for x, y, w, h in regions['logos']:
            importance_map[y:y+h, x:x+w] += 2.0
        
        for x, y, w, h in regions['objects']:
            importance_map[y:y+h, x:x+w] += 1.5
        
        if importance_map.max() > 0:
            importance_map = importance_map / importance_map.max()
        
        return importance_map
    
    def detect_text_regions(self) -> List[Tuple[int, int, int, int]]:
        """Detect text regions using edge detection and morphological operations."""
        gray = cv2.cvtColor(self.master_array, cv2.COLOR_RGB2GRAY)
        filtered = cv2.bilateralFilter(gray, 9, 75, 75)
        edges = cv2.Canny(filtered, 50, 150)
        
        kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (20, 5))
        dilated = cv2.dilate(edges, kernel, iterations=2)
        
        contours, _ = cv2.findContours(dilated, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        text_regions = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            aspect_ratio = w / h if h > 0 else 0
            area = w * h
            if 2 < aspect_ratio < 15 and area > 1000:
                text_regions.append((x, y, w, h))
        
        return text_regions
    
    def detect_logo_regions(self) -> List[Tuple[int, int, int, int]]:
        """Detect logo regions using color segmentation and contour analysis."""
        hsv = cv2.cvtColor(self.master_array, cv2.COLOR_RGB2HSV)
        _, sat_thresh = cv2.threshold(hsv[:,:,1], 100, 255, cv2.THRESH_BINARY)
        
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (5, 5))
        cleaned = cv2.morphologyEx(sat_thresh, cv2.MORPH_CLOSE, kernel)
        
        contours, _ = cv2.findContours(cleaned, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        logo_regions = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = w * h
            aspect_ratio = max(w, h) / min(w, h) if min(w, h) > 0 else 0
            if 500 < area < 50000 and aspect_ratio < 3:
                logo_regions.append((x, y, w, h))
        
        return logo_regions
    
    def detect_prominent_objects(self) -> List[Tuple[int, int, int, int]]:
        """Detect prominent objects using combined saliency and contour analysis."""
        saliency_map = self.detect_salient_regions()
        _, binary = cv2.threshold(saliency_map, 200, 255, cv2.THRESH_BINARY)
        
        kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (11, 11))
        closed = cv2.morphologyEx(binary, cv2.MORPH_CLOSE, kernel)
        
        contours, _ = cv2.findContours(closed, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        objects = []
        for contour in contours:
            x, y, w, h = cv2.boundingRect(contour)
            area = w * h
            if area > 5000:
                objects.append((x, y, w, h))
        
        return objects
    
    def get_all_important_regions(self) -> Dict[str, List[Tuple[int, int, int, int]]]:
        """Detect all important regions in the master asset."""
        regions = {
            'text': self.detect_text_regions(),
            'logos': self.detect_logo_regions(),
            'objects': self.detect_prominent_objects()
        }
        logger.info(f"Found {len(regions['text'])} text, {len(regions['logos'])} logos, {len(regions['objects'])} objects")
        return regions
    
    def smart_crop_coordinates(self, target_w: int, target_h: int, img_w: int = 1080, img_h: int = 1080) -> Tuple[int, int, int, int]:
        """Find the crop window that maximizes salient content using integral image."""
        aspect_target = target_w / target_h
        aspect_src = img_w / img_h
        
        if aspect_target > aspect_src:  # Wider than source → crop height
            crop_h = img_h
            crop_w = int(crop_h * aspect_target)
        else:  # Taller or same → crop width
            crop_w = img_w
            crop_h = int(crop_w / aspect_target)
        
        crop_w = min(crop_w, img_w)
        crop_h = min(crop_h, img_h)
        
        max_saliency = -1
        best_x, best_y = (img_w - crop_w) // 2, (img_h - crop_h) // 2
        
        step = max(10, crop_w // 50)
        for y in range(0, img_h - crop_h + 1, step):
            for x in range(0, img_w - crop_w + 1, step):
                s = (self.integral_saliency[x + crop_w, y + crop_h] -
                     self.integral_saliency[x, y + crop_h] -
                     self.integral_saliency[x + crop_w, y] +
                     self.integral_saliency[x, y])
                if s > max_saliency:
                    max_saliency = s
                    best_x, best_y = x, y
        
        logger.info(f"Best crop for {target_w}x{target_h}: ({best_x}, {best_y}, {crop_w}x{crop_h})")
        return best_x, best_y, crop_w, crop_h
    
    def smart_crop_square_logo(self, regions: Dict) -> Tuple[int, int, int, int]:
        """Intelligently crop to square (1200x1200) preserving important regions."""
        src_h, src_w = self.master_array.shape[:2]
        importance_map = self.calculate_region_importance(regions)
        
        crop_size = min(src_h, src_w)
        
        best_score = -1
        best_x = 0
        best_y = 0
        
        for y in range(0, src_h - crop_size + 1, 10):
            for x in range(0, src_w - crop_size + 1, 10):
                score = importance_map[y:y+crop_size, x:x+crop_size].sum()
                if score > best_score:
                    best_score = score
                    best_x = x
                    best_y = y
        
        return (best_x, best_y, crop_size, crop_size)
    
    def create_landscape(self):
        """Generate Image_Landscape: 1200x628 JPEG"""
        x, y, crop_w, crop_h = self.smart_crop_coordinates(1200, 628)
        cropped = self.master_img[y:y + crop_h, x:x + crop_w]
        result = cv2.resize(cropped, (1200, 628), interpolation=cv2.INTER_LANCZOS4)
        
        os.makedirs(self.output_dir, exist_ok=True)
        output_path = os.path.join(self.output_dir, "sample_master_Image_Landscape.jpeg")
        cv2.imwrite(output_path, result, [cv2.IMWRITE_JPEG_QUALITY, 95])
        logger.info(f"Image_Landscape saved: {output_path}")
        return output_path
    
    def create_square(self):
        """Generate Image_Square: 1200x1200 JPEG"""
        x, y, crop_w, crop_h = self.smart_crop_coordinates(1200, 1200)
        cropped = self.master_img[y:y + crop_h, x:x + crop_w]
        result = cv2.resize(cropped, (1200, 1200), interpolation=cv2.INTER_LANCZOS4)
        
        os.makedirs(self.output_dir, exist_ok=True)
        output_path = os.path.join(self.output_dir, "sample_master_Image_Square.jpeg")
        cv2.imwrite(output_path, result, [cv2.IMWRITE_JPEG_QUALITY, 95])
        logger.info(f"Image_Square saved: {output_path}")
        return output_path
    
    def create_portrait(self):
        """Generate Image_Portrait: 960x1200 JPEG"""
        x, y, crop_w, crop_h = self.smart_crop_coordinates(960, 1200)
        cropped = self.master_img[y:y + crop_h, x:x + crop_w]
        result = cv2.resize(cropped, (960, 1200), interpolation=cv2.INTER_LANCZOS4)
        
        os.makedirs(self.output_dir, exist_ok=True)
        output_path = os.path.join(self.output_dir, "sample_master_Image_Portrait.jpeg")
        cv2.imwrite(output_path, result, [cv2.IMWRITE_JPEG_QUALITY, 95])
        logger.info(f"Image_Portrait saved: {output_path}")
        return output_path
    
    def detect_primary_region(self, image: np.ndarray) -> Tuple[int, int, int, int]:
        """Detect the most visually important region (logo/text/subject cluster)."""
        gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
        edges = cv2.Canny(gray, 80, 160)
        edges = cv2.dilate(edges, None, iterations=2)
        
        contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
        
        img_h, img_w = image.shape[:2]
        best_score = -1
        best_bbox = None
        
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            area = w * h
            
            if area < 0.02 * img_w * img_h:
                continue
            
            # Score: area + centrality
            centrality = 1 - abs((x + w / 2) - img_w / 2) / (img_w / 2)
            score = area * centrality
            
            if score > best_score:
                best_score = score
                best_bbox = (x, y, w, h)
        
        return best_bbox
    
    def content_aware_resize(self, img: np.ndarray, target_width: int, target_height: int) -> np.ndarray:
        """Resize image intelligently to exact target dimensions."""
        src_h, src_w = img.shape[:2]
        
        scale = min(target_width / src_w, target_height / src_h)
        new_w = int(src_w * scale)
        new_h = int(src_h * scale)
        
        resized = cv2.resize(img, (new_w, new_h), interpolation=cv2.INTER_LANCZOS4)
        
        if new_w == target_width and new_h == target_height:
            return resized
        
        canvas = np.zeros((target_height, target_width, 3), dtype=np.uint8)
        
        top_color = np.median(resized[0:5, :], axis=(0, 1)).astype(np.uint8)
        bottom_color = np.median(resized[-5:, :], axis=(0, 1)).astype(np.uint8)
        left_color = np.median(resized[:, 0:5], axis=(0, 1)).astype(np.uint8)
        right_color = np.median(resized[:, -5:], axis=(0, 1)).astype(np.uint8)
        
        canvas[:] = np.mean([top_color, bottom_color, left_color, right_color], axis=0).astype(np.uint8)
        
        y_offset = (target_height - new_h) // 2
        x_offset = (target_width - new_w) // 2
        canvas[y_offset:y_offset+new_h, x_offset:x_offset+new_w] = resized
        
        return canvas
    
    def create_logo_landscape(self):
        """Generate Logo_Landscape: 1200x300 PNG using object repositioning"""
        TARGET_W, TARGET_H = 1200, 300
        canvas = np.zeros((TARGET_H, TARGET_W, 3), dtype=np.uint8)
        
        # Background fill (mean color)
        bg_color = self.master_img.mean(axis=(0, 1)).astype(np.uint8)
        canvas[:] = bg_color
        
        bbox = self.detect_primary_region(self.master_img)
        if bbox is None:
            canvas_rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        else:
            x, y, w, h = bbox
            roi = self.master_img[y:y + h, x:x + w]
            
            # Scale ROI to fit height
            scale = (TARGET_H * 0.85) / h
            new_w = int(w * scale)
            new_h = int(h * scale)
            
            roi_resized = cv2.resize(roi, (new_w, new_h), interpolation=cv2.INTER_AREA)
            
            # Position: left-aligned, vertically centered
            x_offset = 40
            y_offset = (TARGET_H - new_h) // 2
            
            # Clamp width if overflow
            if x_offset + new_w > TARGET_W:
                new_w = TARGET_W - x_offset - 20
                roi_resized = cv2.resize(roi, (new_w, new_h))
            
            canvas[y_offset:y_offset + new_h, x_offset:x_offset + new_w] = roi_resized
            canvas_rgb = cv2.cvtColor(canvas, cv2.COLOR_BGR2RGB)
        
        os.makedirs(self.output_dir, exist_ok=True)
        output_path = os.path.join(self.output_dir, "sample_master_Logo_Landscape.png")
        Image.fromarray(canvas_rgb).save(output_path, 'PNG', optimize=True)
        logger.info(f"Logo_Landscape saved: {output_path}")
        return output_path
    
    def create_logo_square(self):
        """Generate Logo_Square: 1200x1200 PNG"""
        regions = self.get_all_important_regions()
        
        crop_x, crop_y, crop_w, crop_h = self.smart_crop_square_logo(regions)
        cropped = self.master_array[crop_y:crop_y+crop_h, crop_x:crop_x+crop_w]
        
        final = self.content_aware_resize(cropped, 1200, 1200)
        
        os.makedirs(self.output_dir, exist_ok=True)
        output_path = os.path.join(self.output_dir, "sample_master_Logo_Square.png")
        Image.fromarray(final).save(output_path, 'PNG', optimize=True)
        logger.info(f"Logo_Square saved: {output_path}")
        return output_path
    
    def run(self):
        """Full pipeline to generate all 5 secondary assets."""
        logger.info("Starting asset transformation pipeline...")
        self.load_master_asset()
        self.compute_saliency_mask()
        
        # Generate Image assets (JPEG)
        self.create_landscape()
        self.create_square()
        self.create_portrait()
        
        # Generate Logo assets (PNG)
        self.create_logo_landscape()
        self.create_logo_square()
        
        logger.info("All 5 secondary assets generated successfully!")
