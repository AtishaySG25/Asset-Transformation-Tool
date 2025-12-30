from psd_tools import PSDImage
from src.objects import DesignObject
# from src.saliency import extract_salient_objects

def classify_layer(layer):
    if layer.kind == "type":
        return "text", 1.0
    if layer.kind == "smartobject":
        return "logo", 0.9
    if layer.size == layer._psd.size:
        return "background", 0.1
    return "other", 0.4


def extract_design_objects(psd_path):
    psd = PSDImage.open(psd_path)
    objects = []

    for layer in psd:
        if not layer.visible:
            continue

        image = layer.composite()
        if image is None:
            continue

        bbox = layer.bbox
        obj_type, importance = classify_layer(layer)

        objects.append(
            DesignObject(
                image=image,
                bbox=bbox,
                obj_type=obj_type,
                importance=importance
            )
        )

    # Sort by importance (descending)
    return sorted(objects, key=lambda x: x.importance, reverse=True)

def merge_objects(psd_objects, cv_objects):
    """
    Prefer PSD objects; use CV objects only if overlap is low
    """
    merged = psd_objects.copy()

    for cv_obj in cv_objects:
        overlap = False
        for psd_obj in psd_objects:
            if iou(cv_obj.bbox, psd_obj.bbox) > 0.4:
                overlap = True
                break
        if not overlap:
            merged.append(cv_obj)

    return sorted(merged, key=lambda x: x.importance, reverse=True)


def iou(a, b):
    x1 = max(a[0], b[0])
    y1 = max(a[1], b[1])
    x2 = min(a[2], b[2])
    y2 = min(a[3], b[3])

    inter = max(0, x2 - x1) * max(0, y2 - y1)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])

    return inter / float(area_a + area_b - inter + 1e-6)

