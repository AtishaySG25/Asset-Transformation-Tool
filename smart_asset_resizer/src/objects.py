class DesignObject:
    def __init__(self, image, bbox, obj_type, importance):
        self.image = image          # PIL Image
        self.bbox = bbox            # (x1, y1, x2, y2)
        self.type = obj_type        # text | logo | background | other
        self.importance = importance
