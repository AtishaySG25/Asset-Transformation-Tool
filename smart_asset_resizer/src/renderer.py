from PIL import Image

def render(layout, target_size, output_path):
    canvas = Image.new("RGBA", target_size, (255, 255, 255, 255))

    for item in layout:
        if item[0] == "background":
            bg = item[1]
            bg_img = bg.image.resize(target_size)
            canvas.paste(bg_img, (0, 0))
        else:
            _, obj, (cx, cy) = item
            w, h = obj.image.size
            pos = (int(cx - w / 2), int(cy - h / 2))
            canvas.paste(obj.image, pos, obj.image)

    canvas.save(output_path)
