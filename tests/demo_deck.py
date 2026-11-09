"""A pitch deck for the demo company, Fernlea Robotics, drawn with Pillow.

Used for the documentation's screenshots (tests/e2e/test_screenshots.py), so
the reader shows a slide that looks like a real deck. Fernlea Robotics, its
people, customers and numbers are all made up.
"""

import io
from collections.abc import Callable
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

FONT = Path(__file__).resolve().parent.parent / "snug/static/snug/fonts/Geist-Variable.woff2"
W, H = 1920, 1080
MARGIN = 120

# Fernlea's own colours: field green, oat, and a harvest gold.
GREEN = (31, 77, 58)
LEAF = (88, 140, 92)
OAT = (244, 239, 230)
GOLD = (232, 163, 61)
INK = (28, 34, 30)
MUTED = (96, 108, 100)
PALE = (221, 230, 220)

Slide = Callable[[ImageDraw.ImageDraw], None]


def font(size: int, weight: int = 400) -> ImageFont.FreeTypeFont:
    face = ImageFont.truetype(str(FONT), size)
    face.set_variation_by_axes([weight])
    return face


def _chrome(draw: ImageDraw.ImageDraw, number: int, title: str, kicker: str = "") -> None:
    draw.rectangle((0, 0, W, H), fill=OAT)
    draw.rectangle((0, 0, 24, H), fill=GREEN)
    if kicker:
        draw.text((MARGIN, 110), kicker.upper(), font=font(30, 600), fill=LEAF)
    draw.text((MARGIN, 160), title, font=font(78, 650), fill=INK)
    draw.text((MARGIN, H - 90), "Fernlea Robotics", font=font(26, 600), fill=GREEN)
    draw.text((W - MARGIN, H - 90), str(number), font=font(26, 500), fill=MUTED, anchor="ra")


def _bullets(
    draw: ImageDraw.ImageDraw,
    items: list[str],
    top: int = 340,
    size: int = 44,
    left: int = MARGIN,
) -> None:
    for n, item in enumerate(items):
        y = top + n * int(size * 2.1)
        draw.ellipse((left, y + 16, left + 18, y + 34), fill=GOLD)
        draw.text((left + 50, y), item, font=font(size), fill=INK)


def _bars(
    draw: ImageDraw.ImageDraw,
    values: list[float],
    labels: list[str],
    box: tuple[int, int, int, int],
    colour: tuple[int, int, int] = GREEN,
    unit: str = "",
) -> None:
    left, top, right, bottom = box
    gap = 24
    width = (right - left - gap * (len(values) - 1)) / len(values)
    peak = max(values)
    for n, (value, label) in enumerate(zip(values, labels, strict=True)):
        x = left + n * (width + gap)
        height = (bottom - top - 60) * value / peak
        draw.rounded_rectangle((x, bottom - height, x + width, bottom), radius=8, fill=colour)
        draw.text(
            (x + width / 2, bottom - height - 14),
            f"{value:g}{unit}",
            font=font(28, 600),
            fill=INK,
            anchor="ms",
        )
        draw.text((x + width / 2, bottom + 40), label, font=font(26), fill=MUTED, anchor="ms")


def title(draw: ImageDraw.ImageDraw) -> None:
    draw.rectangle((0, 0, W, H), fill=GREEN)
    for n in range(9):  # rows of a field
        y = 640 + n * 52
        draw.line((0, y, W, y - 120), fill=LEAF, width=10)
    draw.text((MARGIN, 260), "Fernlea Robotics", font=font(132, 700), fill=OAT)
    draw.text((MARGIN, 430), "Weeding robots for vegetable farms", font=font(58, 400), fill=PALE)
    draw.text((MARGIN, 540), "Seed round, November 2026", font=font(36, 600), fill=GOLD)


def problem(draw: ImageDraw.ImageDraw) -> None:
    _chrome(draw, 2, "Weeds cost UK growers £300m a year", "The problem")
    _bullets(
        draw,
        [
            "Hand-weeding crews are harder to find every season",
            "Herbicide approvals keep being withdrawn",
            "Organic growers lose up to a third of their yield",
            "Tractor-mounted weeders damage young crops",
        ],
    )


def robot(draw: ImageDraw.ImageDraw) -> None:
    _chrome(draw, 3, "Meet Fern", "Our robot")
    _bullets(
        draw,
        [
            "Wheelbarrow-sized, light enough for wet soil",
            "Solar charged, works through the night",
            "Pulls weeds by hand, no chemicals",
        ],
        size=40,
    )
    # A friendly robot: body, wheels, arm, camera.
    draw.rounded_rectangle((1180, 420, 1640, 700), radius=40, fill=GREEN)
    draw.rounded_rectangle((1240, 360, 1580, 430), radius=20, fill=GOLD)
    for x in (1240, 1580):
        draw.ellipse((x - 70, 640, x + 70, 780), fill=INK)
        draw.ellipse((x - 30, 680, x + 30, 740), fill=MUTED)
    draw.line((1640, 520, 1760, 640, 1760, 760), fill=INK, width=18, joint="curve")
    draw.ellipse((1300, 470, 1380, 550), fill=OAT)
    draw.ellipse((1322, 492, 1358, 528), fill=INK)
    draw.line((1060, 800, 1840, 800), fill=LEAF, width=8)


def how(draw: ImageDraw.ImageDraw) -> None:
    _chrome(draw, 4, "How Fern works", "Technology")
    steps = [
        ("Sees", "A camera under the robot finds every plant in the row"),
        ("Decides", "A model trained on 2 million photos tells crop from weed"),
        ("Weeds", "A small arm pulls the weed out, root and all"),
    ]
    for n, (head, body) in enumerate(steps):
        x = MARGIN + n * 570
        draw.rounded_rectangle((x, 360, x + 520, 820), radius=24, fill=(255, 255, 255))
        draw.ellipse((x + 40, 400, x + 120, 480), fill=GOLD)
        draw.text((x + 80, 440), str(n + 1), font=font(40, 700), fill=INK, anchor="mm")
        draw.text((x + 40, 520), head, font=font(52, 650), fill=GREEN)
        words, line, y = body.split(), "", 610
        for word in words:
            if font(34).getlength(f"{line} {word}") > 440:
                draw.text((x + 40, y), line, font=font(34), fill=MUTED)
                line, y = word, y + 50
            else:
                line = f"{line} {word}".strip()
        draw.text((x + 40, y), line, font=font(34), fill=MUTED)


def trials(draw: ImageDraw.ImageDraw) -> None:
    _chrome(draw, 5, "Field trials, summer 2026", "Evidence")
    _bars(
        draw,
        [61, 72, 80, 86, 90, 92, 94],
        [f"Week {n}" for n in range(1, 8)],
        (MARGIN + 20, 360, W - MARGIN, 900),
        unit="%",
    )
    draw.text(
        (W - MARGIN, 300), "Weeds removed, per hectare", font=font(30), fill=MUTED, anchor="ra"
    )


def results(draw: ImageDraw.ImageDraw) -> None:
    _chrome(draw, 6, "What six farms saw", "Results")
    figures = [
        ("94%", "of weeds removed"),
        ("0 litres", "of herbicide"),
        ("£410", "saved per hectare"),
    ]
    for n, (big, small) in enumerate(figures):
        x = MARGIN + n * 570
        draw.text((x, 420), big, font=font(130, 700), fill=GREEN)
        draw.text((x, 590), small, font=font(40), fill=MUTED)
    draw.line((MARGIN, 760, W - MARGIN, 760), fill=PALE, width=4)
    draw.text(
        (MARGIN, 800),
        "Trials on carrots, onions and lettuce in Lincolnshire and Kent",
        font=font(34),
        fill=MUTED,
    )


def market(draw: ImageDraw.ImageDraw) -> None:
    _chrome(draw, 7, "A £2.1bn market in Europe", "Market")
    centre = (1400, 600)
    for radius, colour in ((380, PALE), (260, LEAF), (140, GREEN)):
        x, y = centre
        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=colour)
    _bullets(
        draw,
        [
            "£2.1bn spent on weeding in Europe",
            "£640m on field vegetables",
            "£90m in the UK, where we start",
        ],
        size=40,
    )


def model(draw: ImageDraw.ImageDraw) -> None:
    _chrome(draw, 8, "Robots as a service", "Business model")
    draw.rounded_rectangle((MARGIN, 360, 860, 820), radius=24, fill=GREEN)
    draw.text((MARGIN + 60, 420), "£1,800", font=font(120, 700), fill=OAT)
    draw.text((MARGIN + 60, 580), "per robot, per season", font=font(40), fill=PALE)
    draw.text((MARGIN + 60, 660), "repairs and updates included", font=font(34), fill=GOLD)
    _bullets(
        draw,
        [
            "One robot covers 4 hectares",
            "Growers pay from the first week",
            "68% gross margin by 2028",
        ],
        top=400,
        size=40,
        left=980,
    )


def traction(draw: ImageDraw.ImageDraw) -> None:
    _chrome(draw, 9, "Hectares under contract", "Traction")
    _bars(
        draw,
        [12, 18, 31, 44, 63, 85],
        ["Jun", "Jul", "Aug", "Sep", "Oct", "Nov"],
        (MARGIN + 20, 360, W - MARGIN, 900),
        colour=LEAF,
    )


def customers(draw: ImageDraw.ImageDraw) -> None:
    _chrome(draw, 10, "Growers already signed", "Customers")
    farms = [
        "Holbeach Greens",
        "Marsh Lane Organics",
        "Wolds Veg Co",
        "Romney Growers",
        "Fenside Farms",
        "Old Rectory Produce",
    ]
    for n, farm in enumerate(farms):
        x = MARGIN + (n % 3) * 570
        y = 380 + (n // 3) * 230
        draw.rounded_rectangle((x, y, x + 520, y + 180), radius=20, fill=(255, 255, 255))
        draw.text((x + 40, y + 62), farm, font=font(40, 600), fill=GREEN)


def competition(draw: ImageDraw.ImageDraw) -> None:
    _chrome(draw, 11, "Where Fern sits", "Competition")
    left, top, size = 560, 330, 620
    draw.line((left, top + size / 2, left + size, top + size / 2), fill=MUTED, width=4)
    draw.line((left + size / 2, top, left + size / 2, top + size), fill=MUTED, width=4)
    draw.text(
        (left + size + 30, top + size / 2), "Small farms", font=font(30), fill=MUTED, anchor="lm"
    )
    draw.text((left - 30, top + size / 2), "Large farms", font=font(30), fill=MUTED, anchor="rm")
    draw.text((left + size / 2, top - 30), "No chemicals", font=font(30), fill=MUTED, anchor="ms")
    draw.text((left + size / 2, top + size + 50), "Sprays", font=font(30), fill=MUTED, anchor="ms")
    for x, y, name, colour in (
        (0.78, 0.18, "Fern", GOLD),
        (0.34, 0.8, "Tractor sprayers", PALE),
        (0.34, 0.3, "Field robots", PALE),
        (0.66, 0.78, "Hand crews", PALE),
    ):
        cx, cy = left + size * x, top + size * y
        draw.ellipse((cx - 34, cy - 34, cx + 34, cy + 34), fill=colour)
        if x < 0.5:
            draw.text((cx - 50, cy), name, font=font(32, 600), fill=INK, anchor="rm")
        else:
            draw.text((cx + 50, cy), name, font=font(32, 600), fill=INK, anchor="lm")


def team(draw: ImageDraw.ImageDraw) -> None:
    _chrome(draw, 12, "The team", "People")
    people = [
        ("RM", "Ruth Marsh", "Chief executive", "Grew up on a Fenland farm"),
        ("DO", "Dev Okafor", "Chief technology", "Robotics, Oxford"),
        ("AL", "Anna Lind", "Operations", "Ran harvest crews for 8 years"),
        ("TK", "Tomas Kral", "Machine learning", "Crop imaging, Wageningen"),
    ]
    for n, (initials, name, role, line) in enumerate(people):
        x = MARGIN + n * 430
        draw.ellipse((x, 360, x + 220, 580), fill=GREEN if n % 2 == 0 else LEAF)
        draw.text((x + 110, 470), initials, font=font(70, 650), fill=OAT, anchor="mm")
        draw.text((x, 630), name, font=font(42, 650), fill=INK)
        draw.text((x, 690), role, font=font(32, 500), fill=GREEN)
        draw.text((x, 740), line, font=font(28), fill=MUTED)


def roadmap(draw: ImageDraw.ImageDraw) -> None:
    _chrome(draw, 13, "Roadmap", "Plan")
    stages = [
        ("Spring 2027", "40 robots on 160 hectares"),
        ("Autumn 2027", "Brassicas and leeks"),
        ("2028", "The Netherlands and Denmark"),
        ("2029", "500 robots in the field"),
    ]
    y = 560
    draw.line((MARGIN, y, W - MARGIN, y), fill=GREEN, width=8)
    for n, (when, what) in enumerate(stages):
        x = MARGIN + 80 + n * 440
        draw.ellipse((x - 26, y - 26, x + 26, y + 26), fill=GOLD)
        draw.text((x - 26, y - 110), when, font=font(40, 650), fill=GREEN)
        words, line, ty = what.split(), "", y + 70
        for word in words:
            if font(32).getlength(f"{line} {word}") > 360:
                draw.text((x - 26, ty), line, font=font(32), fill=MUTED)
                line, ty = word, ty + 46
            else:
                line = f"{line} {word}".strip()
        draw.text((x - 26, ty), line, font=font(32), fill=MUTED)


def financials(draw: ImageDraw.ImageDraw) -> None:
    _chrome(draw, 14, "Revenue, £ thousands", "Financials")
    _bars(
        draw,
        [48, 290, 1150, 3400],
        ["2026", "2027", "2028", "2029"],
        (MARGIN + 20, 360, W - MARGIN, 900),
    )


def unit(draw: ImageDraw.ImageDraw) -> None:
    _chrome(draw, 15, "Each robot pays back in two seasons", "Unit economics")
    rows = [
        ("Cost to build", "£2,300"),
        ("Revenue per season", "£1,800"),
        ("Running cost per season", "£570"),
        ("Payback", "1.9 seasons"),
    ]
    for n, (label, value) in enumerate(rows):
        y = 380 + n * 120
        draw.text((MARGIN, y), label, font=font(44), fill=INK)
        draw.text((1400, y), value, font=font(44, 650), fill=GREEN, anchor="ra")
        draw.line((MARGIN, y + 86, 1400, y + 86), fill=PALE, width=3)


def risks(draw: ImageDraw.ImageDraw) -> None:
    _chrome(draw, 16, "What could go wrong", "Risks")
    _bullets(
        draw,
        [
            "Wet springs: Fern's tracks are tested to 30% soil moisture",
            "Crop types: each new crop needs a season of training",
            "Parts: two suppliers for every component",
        ],
        size=40,
    )


def ask(draw: ImageDraw.ImageDraw) -> None:
    _chrome(draw, 17, "Raising £2.5m", "The ask")
    uses = [("Robots", 45), ("Engineering", 30), ("Sales", 15), ("Reserve", 10)]
    left: float = MARGIN
    for name, share in uses:
        width = (W - 2 * MARGIN) * share / 100
        colour = {"Robots": GREEN, "Engineering": LEAF, "Sales": GOLD}.get(name, PALE)
        draw.rectangle((left, 420, left + width - 8, 560), fill=colour)
        draw.text((left, 600), name, font=font(38, 650), fill=INK)
        draw.text((left, 656), f"{share}%", font=font(34), fill=MUTED)
        left += width
    draw.text(
        (MARGIN, 800), "18 months of runway, to 40 robots in the field", font=font(40), fill=INK
    )


def thanks(draw: ImageDraw.ImageDraw) -> None:
    draw.rectangle((0, 0, W, H), fill=GREEN)
    draw.text((MARGIN, 380), "Thank you", font=font(120, 700), fill=OAT)
    draw.text((MARGIN, 540), "ruth@fernlea.example", font=font(48), fill=PALE)
    draw.text((MARGIN, 620), "fernlea.example", font=font(48), fill=GOLD)


SLIDES: list[Slide] = [
    title,
    problem,
    robot,
    how,
    trials,
    results,
    market,
    model,
    traction,
    customers,
    competition,
    team,
    roadmap,
    financials,
    unit,
    risks,
    ask,
    thanks,
]


def slides() -> list[Image.Image]:
    pages = []
    for draw_slide in SLIDES:
        image = Image.new("RGB", (W, H), OAT)
        draw_slide(ImageDraw.Draw(image))
        pages.append(image)
    return pages


def deck_pdf() -> bytes:
    """The 18 slides as a PDF of 960 by 540 points."""
    pages = slides()
    out = io.BytesIO()
    pages[0].save(out, "PDF", save_all=True, append_images=pages[1:], resolution=144, quality=88)
    return out.getvalue()
