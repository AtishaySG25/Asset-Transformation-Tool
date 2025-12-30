def plan_layout(objects, target_size):
    width, height = target_size
    layout = []

    # Background first
    bg = next((o for o in objects if o.type == "background"), None)
    if bg:
        layout.append(("background", bg))

    # Foreground objects
    foreground = [o for o in objects if o.type != "background"]

    if width > height:
        # Horizontal banner (970x90, 728x90)
        x_cursor = 10
        for obj in foreground:
            layout.append(("place", obj, (x_cursor, height // 2)))
            x_cursor += obj.image.width + 20

    else:
        # Vertical banner (160x600)
        y_cursor = 10
        for obj in foreground:
            layout.append(("place", obj, (width // 2, y_cursor)))
            y_cursor += obj.image.height + 20

    return layout
