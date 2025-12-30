import cv2
import numpy as np
from PIL import Image
from src.objects import DesignObject

def extract_salient_objects(psd):
    """
    Fallback object detection using OpenCV saliency + contours
    """
    composite = psd.composite()
    if composite is None:
        return []

    img = np.array(composite.convert("RGB"))
    gray = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)

    # --- Saliency ---
    saliency = cv2.saliency.StaticSaliencySpectralResidual_create()
    success, saliency_map = saliency.computeSaliency(img)

    saliency_map = (saliency_map * 255).astype("uint8")

    # --- Threshold + contours ---
    _, thresh = cv2.threshold(saliency_map, 150, 255, cv2.THRESH_BINARY)
    contours, _ = cv2.findContours(
        thresh, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    objects = []

    for cnt in contours:
        x, y, w, h = cv2.boundingRect(cnt)

        # Ignore tiny regions
        if w * h < 0.01 * img.shape[0] * img.shape[1]:
            continue

        crop = composite.crop((x, y, x + w, y + h))

        objects.append(
            DesignObject(
                image=crop,
                bbox=(x, y, x + w, y + h),
                obj_type="cv_object",
                importance=0.6
            )
        )

    return objects
