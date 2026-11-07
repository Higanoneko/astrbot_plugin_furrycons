"""Sample a poster's visible left edge without changing the source image."""

from PIL import Image


def edge_colors(image):
    """Return top/middle/bottom colors for extending the left image boundary."""
    with image.convert("RGBA") as rgba:
        bounds = rgba.getchannel("A").getbbox()
        if bounds is None:
            return ("transparent",) * 3
        left = bounds[0]
        right = min(bounds[2], left + max(1, round(image.width * .03)))
        # RGBA resizing averages premultiplied colors, ignoring hidden RGB.
        with (
            rgba.crop((left, 0, right, image.height)) as edge,
            edge.resize((1, 3), Image.Resampling.BOX) as samples,
        ):
            pixels = list(samples.get_flattened_data())
    visible = [(index, pixel) for index, pixel in enumerate(pixels) if pixel[3]]
    if not visible:
        return ("transparent",) * 3
    return tuple(
        "#{:02x}{:02x}{:02x}".format(*(
            pixel if pixel[3] else min(visible, key=lambda item: abs(item[0] - index))[1]
        )[:3])
        for index, pixel in enumerate(pixels)
    )
