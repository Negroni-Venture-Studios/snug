"""A browser window drawn around a screenshot, for the documentation.

The window has a toolbar with three traffic lights, back and forward, and an
address bar holding the page's domain, with rounded corners and a soft
shadow on a transparent background, so the picture sits well on GitHub's
light and dark pages alike. Drawn with Pillow; nothing is copied from a
real browser.
"""

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

FONT = Path(__file__).resolve().parent.parent / "snug/static/snug/fonts/Geist-Variable.woff2"


@dataclass(frozen=True)
class Theme:
    toolbar: tuple[int, int, int]
    rule: tuple[int, int, int]
    pill: tuple[int, int, int]
    text: tuple[int, int, int]
    icon: tuple[int, int, int]
    edge: tuple[int, int, int, int]
    shadow: int


THEMES = {
    "light": Theme(
        toolbar=(246, 245, 244),
        rule=(222, 220, 218),
        pill=(234, 232, 230),
        text=(60, 58, 56),
        icon=(138, 134, 130),
        edge=(0, 0, 0, 40),
        shadow=70,
    ),
    "dark": Theme(
        toolbar=(44, 42, 41),
        rule=(24, 22, 21),
        pill=(62, 59, 57),
        text=(226, 222, 218),
        icon=(150, 145, 140),
        edge=(255, 255, 255, 34),
        shadow=150,
    ),
}
LIGHTS = ((255, 95, 87), (254, 188, 46), (40, 200, 64))


def _font(size: int, weight: int) -> ImageFont.FreeTypeFont:
    face = ImageFont.truetype(str(FONT), size)
    face.set_variation_by_axes([weight])
    return face


def window(shot: Image.Image, domain: str, theme: str, scale: float = 2) -> Image.Image:
    """The screenshot in a browser window, without the shadow."""
    colours = THEMES[theme]
    toolbar = round(52 * scale)
    radius = round(12 * scale)
    width, height = shot.width, shot.height + toolbar
    body = Image.new("RGBA", (width, height), colours.toolbar)
    body.paste(shot.convert("RGBA"), (0, toolbar))
    draw = ImageDraw.Draw(body)
    draw.line((0, toolbar - 1, width, toolbar - 1), fill=colours.rule, width=max(1, round(scale)))

    middle = toolbar / 2
    for n, colour in enumerate(LIGHTS):
        x = (20 + n * 20) * scale
        r = 6 * scale
        draw.ellipse((x - r, middle - r, x + r, middle + r), fill=colour)

    # Back and forward.
    stroke = max(2, round(1.6 * scale))
    for n, sign in enumerate((-1, 1)):
        x = (104 + n * 30) * scale
        arm = 5 * scale
        colour = colours.icon if n == 0 else colours.rule
        draw.line(
            (
                x - sign * arm / 2,
                middle - arm,
                x + sign * arm / 2,
                middle,
                x - sign * arm / 2,
                middle + arm,
            ),
            fill=colour,
            width=stroke,
            joint="curve",
        )

    # The address bar, with the domain in the middle.
    pill_width = min(width * 0.46, 560 * scale)
    pill = (
        (width - pill_width) / 2,
        middle - 15 * scale,
        (width + pill_width) / 2,
        middle + 15 * scale,
    )
    draw.rounded_rectangle(pill, radius=9 * scale, fill=colours.pill)
    draw.text(
        (width / 2, middle),
        domain,
        font=_font(round(13 * scale), 450),
        fill=colours.text,
        anchor="mm",
    )

    # Tabs and share, as plain marks on the right.
    x = width - 30 * scale
    box = 7 * scale
    draw.rounded_rectangle(
        (x - box, middle - box, x + box, middle + box),
        radius=2 * scale,
        outline=colours.icon,
        width=stroke,
    )
    x -= 34 * scale
    draw.line((x - box, middle, x + box, middle), fill=colours.icon, width=stroke)
    draw.line((x, middle - box, x, middle + box), fill=colours.icon, width=stroke)

    # Round the corners, and draw a hairline edge.
    mask = Image.new("L", body.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width - 1, height - 1), radius=radius, fill=255)
    body.putalpha(mask)
    edge = Image.new("RGBA", body.size, (0, 0, 0, 0))
    ImageDraw.Draw(edge).rounded_rectangle(
        (0, 0, width - 1, height - 1),
        radius=radius,
        outline=colours.edge,
        width=max(1, round(scale / 2)),
    )
    return Image.alpha_composite(body, edge)


def _shadow(size: tuple[int, int], radius: int, strength: int, blur: float) -> Image.Image:
    """A blurred shadow, BLUR_ROOM times the blur wider on every side than the window."""
    room = round(blur * BLUR_ROOM)
    shadow = Image.new("RGBA", (size[0] + 2 * room, size[1] + 2 * room), (0, 0, 0, 0))
    ImageDraw.Draw(shadow).rounded_rectangle(
        (room, room, room + size[0], room + size[1]), radius=radius, fill=(0, 0, 0, strength)
    )
    return shadow.filter(ImageFilter.GaussianBlur(blur))


BLUR_ROOM = 3
# Shadow fainter than this is cleared before the colours are reduced, or it would
# become one flat shade and show the edge of the blur as a pale box.
FAINTEST = 12


def _place(canvas: Image.Image, shadow: Image.Image, at: tuple[int, int], blur: float) -> None:
    room = round(blur * BLUR_ROOM)
    canvas.alpha_composite(shadow, (at[0] - room, at[1] - room))


def framed(shot: Image.Image, domain: str, theme: str, scale: float = 2) -> Image.Image:
    """The screenshot in a browser window with its shadow, on a transparent canvas."""
    win = window(shot, domain, theme, scale)
    blur = 14 * scale
    pad = round(blur * BLUR_ROOM)
    canvas = Image.new("RGBA", (win.width + 2 * pad, win.height + 2 * pad), (0, 0, 0, 0))
    shadow = _shadow(win.size, round(12 * scale), THEMES[theme].shadow, blur)
    _place(canvas, shadow, (pad, pad + round(10 * scale)), blur)
    canvas.alpha_composite(win, (pad, pad))
    return canvas


def hero(
    back: Image.Image, front: Image.Image, domain: str, theme: str, scale: float = 2
) -> Image.Image:
    """Two windows: the founder's, and the investor's smaller and in front, lower right."""
    big = window(back, domain, theme, scale)
    small_shot = front.resize(
        (round(front.width * 0.62), round(front.height * 0.62)), Image.Resampling.LANCZOS
    )
    small = window(small_shot, domain, theme, scale * 0.62)
    blur = 14 * scale
    pad = round(blur * BLUR_ROOM)
    offset = (round(big.width * 0.66), round(big.height * 0.36))
    width = max(big.width, offset[0] + small.width) + 2 * pad
    height = max(big.height, offset[1] + small.height) + 2 * pad
    canvas = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    strength = THEMES[theme].shadow
    _place(
        canvas,
        _shadow(big.size, round(12 * scale), strength, blur),
        (pad, pad + round(10 * scale)),
        blur,
    )
    canvas.alpha_composite(big, (pad, pad))
    at = (pad + offset[0], pad + offset[1])
    deeper = min(255, strength + 40)
    _place(
        canvas,
        _shadow(small.size, round(8 * scale), deeper, blur),
        (at[0], at[1] + round(12 * scale)),
        blur,
    )
    canvas.alpha_composite(small, at)
    return canvas


def save(image: Image.Image, path: Path) -> None:
    """A PNG, made smaller by reducing to 256 colours, which UI screenshots barely need.

    The quantiser would merge the clear background with the faintest shadow and
    leave a faint box around the window, so clear pixels get a palette entry of
    their own.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    rgba = image.convert("RGBA")
    reduced = rgba.quantize(colors=255, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
    palette = reduced.getpalette(rawmode="RGBA") or []
    palette = [*(palette + [0, 0, 0, 0] * 256)[: 255 * 4], 0, 0, 0, 0]
    reduced.putpalette(palette, rawmode="RGBA")
    clear = rgba.getchannel("A").point(lambda a: 255 if a < FAINTEST else 0)
    reduced.paste(255, mask=clear)
    reduced.save(path, optimize=True)
