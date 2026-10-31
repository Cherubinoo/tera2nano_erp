"""Prepare logo artwork for letterheads: remove a flat background, crop to the artwork, cap the size.

Logos are often exported on a solid white or black background. Printed on a tinted page or used as a
watermark that shows up as a box, so the background is converted to transparency while keeping
anti-aliased edges (colour is un-blended from the background, not just thresholded).
"""
import io

from PIL import Image

MAX_WIDTH = 1400


def _distance(pixel, bg):
    return max(abs(pixel[0] - bg[0]), abs(pixel[1] - bg[1]), abs(pixel[2] - bg[2]))


def prepare_logo(data: bytes, *, max_width: int = MAX_WIDTH, padding: float = 0.03) -> bytes:
    """Return PNG bytes with the flat background made transparent and the artwork cropped.

    Images that already have real transparency are only cropped and resized.
    """
    im = Image.open(io.BytesIO(data))
    im.load()
    has_alpha = im.mode in ("RGBA", "LA") and im.getchannel("A").getextrema()[0] < 250
    im = im.convert("RGBA")
    if im.width > max_width:
        im = im.resize((max_width, round(im.height * max_width / im.width)), Image.LANCZOS)

    if not has_alpha:
        w, h = im.size
        rgb = im.convert("RGB")
        corners = [rgb.getpixel(p) for p in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1))]
        bg = max(set(corners), key=corners.count)  # the flat background colour
        pixels = list(rgb.getdata())
        dist = [_distance(p, bg) for p in pixels]
        # Level at which a pixel counts as fully artwork (distance from the background colour).
        solid = sorted(d for d in dist if d > 12)
        # A low percentile keeps dark-but-solid artwork (e.g. navy on black) opaque instead of translucent.
        full = solid[int(len(solid) * 0.05)] if solid else 255
        full = min(max(full, 40), 120)
        out = []
        for (r, g, b), d in zip(pixels, dist):
            alpha = min(1.0, d / full) if d > 6 else 0.0
            if alpha <= 0:
                out.append((0, 0, 0, 0))
                continue
            if alpha < 1.0:  # un-blend the edge pixel from the background
                r = max(0, min(255, round(bg[0] + (r - bg[0]) / alpha)))
                g = max(0, min(255, round(bg[1] + (g - bg[1]) / alpha)))
                b = max(0, min(255, round(bg[2] + (b - bg[2]) / alpha)))
            out.append((r, g, b, round(alpha * 255)))
        im = Image.new("RGBA", (w, h))
        im.putdata(out)

    box = im.getchannel("A").point(lambda a: 255 if a > 8 else 0).getbbox()
    if box:
        pad_x, pad_y = round(im.width * padding), round(im.height * padding)
        box = (max(0, box[0] - pad_x), max(0, box[1] - pad_y),
               min(im.width, box[2] + pad_x), min(im.height, box[3] + pad_y))
        im = im.crop(box)
    buf = io.BytesIO()
    im.save(buf, "PNG", optimize=True)
    return buf.getvalue()
