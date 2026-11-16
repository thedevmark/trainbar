"""Render the README theme images from Trainbar's own drawing code.

Usage: python tools/render_theme_images.py trainbar.pyw docs
"""
import importlib.machinery
import importlib.util
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

SRC = Path(sys.argv[1])            # path to trainbar.pyw
OUT = Path(sys.argv[2])
OUT.mkdir(parents=True, exist_ok=True)

loader = importlib.machinery.SourceFileLoader("trainbar", str(SRC))
spec = importlib.util.spec_from_loader("trainbar", loader)
tb = importlib.util.module_from_spec(spec)
loader.exec_module(tb)

S = 3                     # zoom: one real taskbar pixel = 3 image pixels
BAR = 48 * S
W = 1600
BW = W - 120   # visible bar width
ROWS = [("Q", "96 St", 3, False), ("N", "Astoria-Ditmars Blvd", 6, False),
        ("R", "Forest Hills-71 Av", 9, False), ("W", "Astoria-Ditmars Blvd", 14, False)]
ICONS = ImageFont.truetype("C:/Windows/Fonts/SegoeIcons.ttf", 16 * S)
TITLE = tb.font("Semibold", 44)
CAPTION = tb.font("Regular", 24)


def wallpaper(w, h):
    """One flat neutral: both the dark and the light taskbar read against it."""
    return Image.new("RGB", (w, h), (92, 98, 106))


def widget_for(theme):
    if theme == "countdown":
        width = tb.countdown_width(ROWS, BAR)
        return tb.finish(tb.countdown_card(ROWS[0], BAR, 0, len(ROWS), width))
    return tb.render(theme, ROWS, None, BAR)


TRAY = 640  # room the tray icons and clock need
PADL = 16 * S  # bar edge to the widget, like a pinned item's inset


def taskbar(theme, light, BW):
    tb._theme_cache[:] = [1e18, light]        # pin the Windows light/dark setting
    ink = (0, 0, 0) if light else (255, 255, 255)
    bar = Image.new("RGBA", (BW, BAR), (243, 243, 243, 235) if light else (28, 28, 30, 235))
    d = ImageDraw.Draw(bar)
    bar.alpha_composite(widget_for(theme), (PADL, 0))
    # tray: chevron, wifi, volume, battery, then the clock
    x = BW - 12 * S
    clock_f = tb.font("Regular", 12 * S)
    for line, y in (("2:52 PM", BAR * 0.30), ("11/16/2026", BAR * 0.70)):
        d.text((x, y), line, font=clock_f, fill=ink, anchor="rm")
    x -= clock_f.getlength("11/16/2026") + 26 * S
    for glyph in ("\uE83F", "\uE767", "\uE701", "\uE70E"):
        d.text((x, BAR / 2), glyph, font=ICONS, fill=ink, anchor="rm")
        x -= 30 * S
    return bar


def card(theme, label, caption):
    tb._theme_cache[:] = [1e18, False]
    # 8 px grid. Proximity: title and caption are one unit (16 apart); the two bars are
    # one unit (16 apart); the units are separated by 48; one 64 margin on every side.
    M, U = 64, 8
    BW = max(W - 2 * M, PADL + widget_for(theme).width + TRAY)
    W_ = BW + 2 * M
    title_top = M
    cap_top = title_top + TITLE.size + 2 * U
    top = cap_top + CAPTION.size + 6 * U
    h = top + BAR * 2 + 2 * U + M
    img = wallpaper(W_, h).convert("RGBA")
    d = ImageDraw.Draw(img)
    d.text((M, title_top), label, font=TITLE, fill=(255, 255, 255), anchor="lt")
    d.text((M, cap_top), caption, font=CAPTION, fill=(255, 255, 255, 200), anchor="lt")
    for i, light in enumerate((False, True)):
        y = top + i * (BAR + 2 * U)
        bar = taskbar(theme, light, BW)
        mask = Image.new("L", bar.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, BW - 1, BAR - 1], radius=2 * U, fill=255)
        shadow = Image.new("RGBA", (W_, h), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).rounded_rectangle([M, y + U, W_ - M, y + BAR + U], radius=2 * U, fill=(0, 0, 0, 40))
        img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(2 * U)))
        img.paste(bar, (M, y), mask)
    img = img.convert("RGB")
    return img if W_ == W else img.resize((W, round(h * W / W_)), Image.LANCZOS)


THEMES = {
    "countdown": ("Countdown", "One train at a time, full size, sliding to the next. The default."),
    "clock": ("Clock", "Two quiet lines, set like the taskbar clock."),
    "led": ("LED", "A dot-matrix sign with glowing dots and no box."),
    "minimal": ("Minimal", "Only the bullet and the minutes."),
}
for code, (label, caption) in THEMES.items():
    card(code, label, caption).save(OUT / f"theme-{code}.png", optimize=True)
print("RESULT", ", ".join(f"theme-{c}.png" for c in THEMES))
