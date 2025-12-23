"""
Process Visualization Tool
Creates annotated images showing detected regions and cropping decisions
Perfect for demonstrating your CV approach in the interview
"""

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont
import os
from asset_transformer import AssetTransformer

class ProcessVisualizer:
    def __init__(self, master_path: str, output_dir: str = "screenshots/process"):
        self.transformer = AssetTransformer(master_path, output_dir)
        self.output_dir = output_dir
        os.makedirs(output_dir, exist_ok=True)
        # IMPORTANT: load image before visualization
        self.transformer.load_master_asset()
        self.transformer.compute_saliency_mask()

        
    def visualize_saliency(self) -> None:
        """Create visualization of saliency detection."""
        print("🎨 Generating saliency map visualization...")
        
        # Get saliency map
        saliency = self.transformer.detect_salient_regions()
        
        # Create side-by-side comparison
        h, w = self.transformer.master_array.shape[:2]
        combined = np.zeros((h, w * 2, 3), dtype=np.uint8)
        
        # Original image
        combined[:, :w] = self.transformer.master_array
        
        # Saliency map (colorized)
        saliency_colored = cv2.applyColorMap(saliency, cv2.COLORMAP_JET)
        saliency_colored = cv2.cvtColor(saliency_colored, cv2.COLOR_BGR2RGB)
        combined[:, w:] = saliency_colored
        
        # Add labels
        combined_img = Image.fromarray(combined)
        draw = ImageDraw.Draw(combined_img)
        
        # Try to use a nice font, fallback to default
        try:
            font = ImageFont.truetype("Arial.ttf", 40)
        except:
            font = ImageFont.load_default()
        
        draw.text((20, 20), "Original", fill=(255, 255, 255), font=font)
        draw.text((w + 20, 20), "Saliency Map", fill=(255, 255, 255), font=font)
        
        # Save
        output_path = os.path.join(self.output_dir, "01_saliency_detection.png")
        combined_img.save(output_path)
        print(f"  ✓ Saved: {output_path}")
    
    def visualize_text_detection(self) -> None:
        """Create visualization of text region detection."""
        print("🎨 Generating text detection visualization...")
        
        # Detect text regions
        text_regions = self.transformer.detect_text_regions()
        
        # Create annotated image
        annotated = self.transformer.master_array.copy()
        
        # Draw rectangles around text regions
        for i, (x, y, w, h) in enumerate(text_regions):
            cv2.rectangle(annotated, (x, y), (x + w, y + h), (255, 0, 0), 3)
            # Add label
            cv2.putText(annotated, f"T{i+1}", (x, y - 10),
                       cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 0, 0), 3)
        
        # Add legend
        legend_y = 50
        cv2.putText(annotated, f"Text Regions Detected: {len(text_regions)}",
                   (20, legend_y), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 3)
        
        # Save
        output_path = os.path.join(self.output_dir, "02_text_detection.png")
        Image.fromarray(annotated).save(output_path)
        print(f"  ✓ Saved: {output_path}")
    
    def visualize_logo_detection(self) -> None:
        """Create visualization of logo detection."""
        print("🎨 Generating logo detection visualization...")
        
        # Detect logo regions
        logo_regions = self.transformer.detect_logo_regions()
        
        # Create annotated image
        annotated = self.transformer.master_array.copy()
        
        # Draw rectangles around logo regions
        for i, (x, y, w, h) in enumerate(logo_regions):
            cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 255, 0), 3)
            cv2.putText(annotated, f"L{i+1}", (x, y - 10),
                       cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 0), 3)
        
        # Add legend
        cv2.putText(annotated, f"Logo Regions Detected: {len(logo_regions)}",
                   (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (255, 255, 255), 3)
        
        # Save
        output_path = os.path.join(self.output_dir, "03_logo_detection.png")
        Image.fromarray(annotated).save(output_path)
        print(f"  ✓ Saved: {output_path}")
    
    def visualize_all_detections(self) -> None:
        """Create comprehensive visualization with all detections."""
        print("🎨 Generating combined detection visualization...")
        
        # Get all regions
        regions = self.transformer.get_all_important_regions()
        
        # Create annotated image
        annotated = self.transformer.master_array.copy()
        
        # Draw text regions (blue)
        for x, y, w, h in regions['text']:
            cv2.rectangle(annotated, (x, y), (x + w, y + h), (255, 0, 0), 3)
        
        # Draw logo regions (green)
        for x, y, w, h in regions['logos']:
            cv2.rectangle(annotated, (x, y), (x + w, y + h), (0, 255, 0), 3)
        
        # Draw object regions (red, semi-transparent)
        overlay = annotated.copy()
        for x, y, w, h in regions['objects']:
            cv2.rectangle(overlay, (x, y), (x + w, y + h), (0, 0, 255), -1)
        annotated = cv2.addWeighted(annotated, 0.7, overlay, 0.3, 0)
        
        # Add legend
        legend_y = 50
        cv2.putText(annotated, f"Text: {len(regions['text'])} | Logos: {len(regions['logos'])} | Objects: {len(regions['objects'])}",
                   (20, legend_y), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (255, 255, 255), 3)
        
        # Color legend
        cv2.rectangle(annotated, (20, 90), (60, 120), (255, 0, 0), -1)
        cv2.putText(annotated, "Text", (70, 115), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        
        cv2.rectangle(annotated, (200, 90), (240, 120), (0, 255, 0), -1)
        cv2.putText(annotated, "Logos", (250, 115), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        
        cv2.rectangle(annotated, (400, 90), (440, 120), (0, 0, 255), -1)
        cv2.putText(annotated, "Objects", (450, 115), cv2.FONT_HERSHEY_SIMPLEX, 1, (255, 255, 255), 2)
        
        # Save
        output_path = os.path.join(self.output_dir, "04_all_detections.png")
        Image.fromarray(annotated).save(output_path)
        print(f"  ✓ Saved: {output_path}")
    
    def visualize_crop_regions(self) -> None:
        """Show crop regions for each output format."""
        print("🎨 Generating crop region visualizations...")
        
        regions = self.transformer.get_all_important_regions()
        
        # Create grid showing all crop regions
        formats = [
            ('Image_Landscape', (1200, 628)),
            ('Image_Portrait', (960, 1200)),
            ('Logo_Landscape', (1200, 300))
        ]
        
        for format_name, (target_w, target_h) in formats:
            # Get crop region
            crop_x, crop_y, crop_w, crop_h = self.transformer.smart_crop_coordinates(target_w, target_h)
            
            # Create visualization
            annotated = self.transformer.master_array.copy()
            
            # Draw crop region
            cv2.rectangle(annotated, (crop_x, crop_y), 
                         (crop_x + crop_w, crop_y + crop_h), (0, 255, 255), 5)
            
            # Dim outside region
            mask = np.zeros_like(annotated)
            mask[crop_y:crop_y+crop_h, crop_x:crop_x+crop_w] = annotated[crop_y:crop_y+crop_h, crop_x:crop_x+crop_w]
            annotated = cv2.addWeighted(annotated, 0.3, mask, 0.7, 0)
            
            # Add label
            cv2.putText(annotated, f"{format_name} Crop Region", 
                       (20, 50), cv2.FONT_HERSHEY_SIMPLEX, 1.5, (0, 255, 255), 3)
            cv2.putText(annotated, f"Crop: {crop_w}x{crop_h} → Resize: {target_w}x{target_h}", 
                       (20, 100), cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 255, 255), 3)
            
            # Save
            output_path = os.path.join(self.output_dir, f"05_crop_{format_name}.png")
            Image.fromarray(annotated).save(output_path)
            print(f"  ✓ Saved: {output_path}")
    
    def visualize_importance_map(self) -> None:
        """Visualize the importance scoring map."""
        print("🎨 Generating importance map visualization...")
        
        regions = self.transformer.get_all_important_regions()
        importance_map = self.transformer.calculate_region_importance(regions)
        
        # Normalize to 0-255
        importance_viz = (importance_map * 255).astype(np.uint8)
        importance_colored = cv2.applyColorMap(importance_viz, cv2.COLORMAP_HOT)
        importance_colored = cv2.cvtColor(importance_colored, cv2.COLOR_BGR2RGB)
        
        # Create side-by-side with extra space for legend
        h, w = self.transformer.master_array.shape[:2]
        legend_height = 100
        combined = np.zeros((h + legend_height, w * 2, 3), dtype=np.uint8)
        combined[:h, :w] = self.transformer.master_array
        combined[:h, w:] = importance_colored
        
        # Add labels
        combined_img = Image.fromarray(combined)
        draw = ImageDraw.Draw(combined_img)
        
        try:
            font_title = ImageFont.truetype("Arial.ttf", 40)
            font_legend = ImageFont.truetype("Arial.ttf", 24)
        except:
            font_title = ImageFont.load_default()
            font_legend = ImageFont.load_default()
        
        draw.text((20, 20), "Original", fill=(255, 255, 255), font=font_title)
        draw.text((w + 20, 20), "Importance Map", fill=(255, 255, 255), font=font_title)
        
        # Create color legend with gradient bar
        legend_y_start = h + 20
        legend_x_start = w + 20
        gradient_width = 400
        gradient_height = 30
        
        # Draw gradient bar showing importance scale
        gradient = np.zeros((gradient_height, gradient_width, 3), dtype=np.uint8)
        for i in range(gradient_width):
            value = int((i / gradient_width) * 255)
            color = cv2.applyColorMap(np.array([[value]], dtype=np.uint8), cv2.COLORMAP_HOT)
            gradient[:, i] = cv2.cvtColor(color, cv2.COLOR_BGR2RGB)[0, 0]
        
        # Paste gradient onto combined image
        gradient_img = Image.fromarray(gradient)
        combined_img.paste(gradient_img, (legend_x_start, legend_y_start))
        
        # Draw border around gradient
        draw.rectangle(
            [(legend_x_start, legend_y_start), 
             (legend_x_start + gradient_width, legend_y_start + gradient_height)],
            outline=(255, 255, 255),
            width=2
        )
        
        # Add labels for importance levels
        label_y = legend_y_start + gradient_height + 10
        draw.text((legend_x_start, label_y), "Low", fill=(255, 255, 255), font=font_legend)
        draw.text((legend_x_start + gradient_width//2 - 30, label_y), "Medium", fill=(255, 255, 255), font=font_legend)
        draw.text((legend_x_start + gradient_width - 60, label_y), "High", fill=(255, 255, 255), font=font_legend)
        
        # Add importance score explanation
        info_y = label_y + 35
        draw.text((legend_x_start, info_y), 
                 "Importance Score: Saliency + 3×Text + 2×Logos + 1.5×Objects",
                 fill=(200, 200, 200), 
                 font=font_legend)
        
        # Save
        output_path = os.path.join(self.output_dir, "06_importance_map.png")
        combined_img.save(output_path)
        print(f"  ✓ Saved: {output_path}")
    
    def generate_all_visualizations(self) -> None:
        """Generate all process visualizations."""
        print("\n" + "="*60)
        print("📊 GENERATING PROCESS VISUALIZATIONS")
        print("="*60 + "\n")
        
        self.visualize_saliency()
        self.visualize_text_detection()
        self.visualize_logo_detection()
        self.visualize_all_detections()
        self.visualize_importance_map()
        self.visualize_crop_regions()
        
        print("\n" + "="*60)
        print("✅ ALL VISUALIZATIONS COMPLETE")
        print(f"📁 Saved to: {self.output_dir}/")
        print("="*60 + "\n")


def main():
    """Main execution function."""
    INPUT_PATH = "D:/Datanodes_Assignment/claude/claude_outputs/lady/4548556.psd"
    
    # Try JPEG fallback
    if not os.path.exists(INPUT_PATH):
        INPUT_PATH = INPUT_PATH.replace('.psd', '.jpg')
        if not os.path.exists(INPUT_PATH):
            INPUT_PATH = INPUT_PATH.replace('.jpg', '.jpeg')
    
    if not os.path.exists(INPUT_PATH):
        print("❌ Error: Master asset not found")
        return
    
    visualizer = ProcessVisualizer(INPUT_PATH)
    visualizer.generate_all_visualizations()
    
    print("\n💡 Use these visualizations in your README to show your CV approach!")
    print("   Perfect for demonstrating methodology during the interview.")


if __name__ == "__main__":
    main()