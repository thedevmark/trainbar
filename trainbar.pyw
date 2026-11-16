"""Trainbar: live NYC subway arrivals on the Windows taskbar.

Live data comes from the MTA's public GTFS-realtime feeds (no API key).
Right-click it for theme, station, direction and startup options.
Drag it sideways to move it. Click it to refresh.
"""

import csv
import ctypes
import gzip
import json
import math
import os
import shutil
import sys
import threading
import time
import tkinter as tk
import tkinter.font as tkfont
import traceback
import urllib.request
import webbrowser
import winreg
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont, ImageTk

__version__ = "1.3.0"
APP = "Trainbar"
HOMEPAGE = "https://github.com/thedevmark/trainbar"
DATA_DIR = os.path.join(os.environ["APPDATA"], APP)
CONFIG_PATH = os.path.join(DATA_DIR, "config.json")
STATIONS_PATH = os.path.join(DATA_DIR, "stations.csv")
STATIONS_URL = "https://data.ny.gov/api/views/39hk-dx4f/rows.csv?accessType=DOWNLOAD"
FEED_BASE = "https://api-endpoint.mta.info/Dataservice/mtagtfsfeeds/nyct%2F"
FEEDS = ["gtfs", "gtfs-ace", "gtfs-bdfm", "gtfs-g", "gtfs-jz", "gtfs-nqrw", "gtfs-l", "gtfs-si"]
ROUTE_FEED = {
    **{r: "gtfs" for r in "1234567"},
    **{r: "gtfs-ace" for r in "ACEH"},
    **{r: "gtfs-bdfm" for r in "BDFM"},
    "G": "gtfs-g", "J": "gtfs-jz", "Z": "gtfs-jz",
    **{r: "gtfs-nqrw" for r in "NQRW"},
    "L": "gtfs-l", "SIR": "gtfs-si",
}
POLL_SECONDS = 30
DISCOVER_SECONDS = 300  # full sweep of every feed, to catch rerouted trains

# ---------------------------------------------------------------- look

ROUTE_COLORS = {
    **{r: (238, 53, 46) for r in "123"},
    **{r: (0, 147, 60) for r in "456"},
    "7": (185, 51, 173),
    **{r: (0, 57, 166) for r in "ACE"},
    **{r: (255, 99, 25) for r in "BDFM"},
    "G": (108, 190, 69),
    **{r: (153, 102, 51) for r in "JZ"},
    "L": (167, 169, 172),
    **{r: (252, 204, 10) for r in "NQRW"},
    "S": (128, 129, 131), "H": (128, 129, 131), "SIR": (0, 57, 166),
}
# LEDs need more light than print colours: lift the dark blue and brown.
LED_COLORS = {**ROUTE_COLORS, **{r: (40, 110, 255) for r in ("A", "C", "E", "SIR")},
              **{r: (196, 132, 70) for r in "JZ"}}
DARK_LETTER = set("NQRW")
THEMES = [("countdown", "Countdown"), ("clock", "Clock"), ("led", "LED"), ("minimal", "Minimal")]
DEFAULT_THEME = "countdown"
SLIDE_MS = 900

FONT_FILE = "C:/Windows/Fonts/SegUIVar.ttf"
FALLBACK_FONTS = {"Regular": "segoeui.ttf", "Semibold": "seguisb.ttf", "Bold": "segoeuib.ttf"}
_fonts = {}


def font(weight, px):
    """Segoe UI Variable (the Windows 11 UI font) at a pixel size, cached."""
    key = (weight, px)
    if key not in _fonts:
        try:
            f = ImageFont.truetype(FONT_FILE, px)
            f.set_variation_by_name("Regular" if weight == "Regular" else f"{weight} Text")
        except (OSError, ValueError):
            f = ImageFont.truetype(FALLBACK_FONTS[weight], px)
        _fonts[key] = f
    return _fonts[key]


_theme_cache = [0.0, False]


def light_theme():
    """Windows light/dark setting, cached briefly because every frame asks."""
    now = time.monotonic()
    if now - _theme_cache[0] > 3:
        _theme_cache[:] = [now, _read_light_theme()]
    return _theme_cache[1]


def _read_light_theme():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize") as k:
            return bool(winreg.QueryValueEx(k, "SystemUsesLightTheme")[0])
    except OSError:
        return False


class Painter:
    """Collects alpha masks and colours, then composites them with clean edges."""

    def __init__(self, w, h):
        self.w, self.h = w, h
        self.layers = []

    def mask(self):
        return Image.new("L", (self.w, self.h), 0)

    def add(self, mask, color, opacity=1.0, shadow=True):
        self.layers.append((mask, color, opacity, shadow))

    def text(self, xy, s, fnt, color, opacity=1.0, anchor="lm", shadow=True):
        m = self.mask()
        ImageDraw.Draw(m).text(xy, s, font=fnt, fill=255, anchor=anchor)
        self.add(m, color, opacity, shadow)

    def shape(self, draw_fn, color, opacity=1.0, shadow=True, ss=4):
        """Draw a shape at 4x and scale it down for smooth edges."""
        big = Image.new("L", (self.w * ss, self.h * ss), 0)
        draw_fn(ImageDraw.Draw(big), ss)
        self.add(big.resize((self.w, self.h), Image.LANCZOS), color, opacity, shadow)

    def bullet(self, route, cx, cy, d, opacity=1.0, colors=ROUTE_COLORS):
        color = colors.get(route) or colors.get(route[0], colors["S"])
        r = d / 2
        if route.endswith("X") and len(route) == 2:  # express trains get a diamond
            e = r * 1.12
            self.shape(lambda g, s: g.polygon([(cx * s, (cy - e) * s), ((cx + e) * s, cy * s),
                                               (cx * s, (cy + e) * s), ((cx - e) * s, cy * s)], fill=255),
                       color, opacity)
        else:
            self.shape(lambda g, s: g.ellipse([(cx - r) * s, (cy - r) * s, (cx + r) * s, (cy + r) * s],
                                              fill=255), color, opacity)
        label = "S" if route in ("SIR", "H") else route[0]
        ink = (0, 0, 0) if route[0] in DARK_LETTER else (255, 255, 255)
        ss = 4
        f = font("Bold", round(d * 0.6 * ss))
        # Centre the letter on its actual outline, not on the font's line box.
        l, t, r_, b = f.getbbox(label, anchor="ls")
        big = Image.new("L", (self.w * ss, self.h * ss), 0)
        ImageDraw.Draw(big).text((cx * ss - (l + r_) / 2, cy * ss - (t + b) / 2), label, font=f, fill=255,
                                 anchor="ls")
        self.add(big.resize((self.w, self.h), Image.LANCZOS), ink, opacity, False)

    def compose(self, shadow_strength):
        out = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
        if shadow_strength:
            union = self.mask()
            for m, _, op, sh in self.layers:
                if sh:
                    union = ImageChops.lighter(union, m.point(lambda v, op=op: int(v * op)))
            blur = union.filter(ImageFilter.GaussianBlur(max(1.0, self.h / 36)))
            shadow = Image.new("RGBA", (self.w, self.h), (0, 0, 0, 0))
            shadow.putalpha(blur.point(lambda v: int(v * shadow_strength)))
            out.alpha_composite(shadow, (0, 1))
        for m, color, op, _ in self.layers:
            layer = Image.new("RGBA", (self.w, self.h), color + (0,))
            layer.putalpha(m if op >= 1 else m.point(lambda v, op=op: int(v * op)))
            out.alpha_composite(layer)
        return out


def app_icon(size):
    """The Trainbar icon: a subway-style bullet with a T."""
    p = Painter(size, size)
    blue = (0, 57, 166)
    p.bullet("T", size / 2, size / 2, size * 0.94, colors={"T": blue, "S": blue})
    return p.compose(0)


def write_ico(path):
    """Used by the release build: python -c "import ...; write_ico('trainbar.ico')"."""
    app_icon(256).save(path, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def fit(s, fnt, max_w):
    if fnt.getlength(s) <= max_w:
        return s
    while s and fnt.getlength(s + "…") > max_w:
        s = s[:-1]
    return s.rstrip() + "…"


def render(theme, rows, message, height, hover=False, background=False):
    """Render the widget for a theme. rows: [(route, destination, minutes, stale)]."""
    k = height / 48
    light = light_theme()
    ink = (0, 0, 0) if light else (255, 255, 255)
    draw = {"led": render_led, "minimal": render_minimal}.get(theme, render_clock)
    return finish(draw(rows, message, height, k, ink, light), hover, background)


def finish(content, hover=False, background=False):
    """Put content on a clickable, optionally highlighted base."""
    w, height = content.size
    k = height / 48
    ink = (0, 0, 0) if light_theme() else (255, 255, 255)
    out = Image.new("RGBA", (w, height), (0, 0, 0, 1))  # alpha 1 keeps the whole area clickable
    if hover or background:
        p = Painter(w, height)
        p.shape(lambda d, s: d.rounded_rectangle([2 * k * s, 4 * k * s, (w - 2 * k) * s, (height - 4 * k) * s],
                                                 radius=6 * k * s, fill=255),
                ink, 0.11 if hover else 0.06, shadow=False)
        out.alpha_composite(p.compose(0))
    out.alpha_composite(content)
    return out


# Spacing follows a 4 px grid (in taskbar pixels, scaled by k): 4 inside an item,
# 8 between a bullet and its text, 16 between separate pieces, 12 at the outer edges.
PAD = 12
PAGER_R = 1.6
PAGER_W = 8 + 2 * PAGER_R + 8  # gap before the pager dots, the dots, the edge


def countdown_fonts(k):
    return (font("Semibold", round(19 * k)), font("Regular", round(12 * k)), font("Semibold", round(14 * k)),
            font("Semibold", round(16 * k)))


def countdown_width(rows, height):
    """Smallest width that fits every train in the rotation, so cards never jump."""
    k = height / 48
    num_f, unit_f, dest_f, now_f = countdown_fonts(k)
    widths = []
    for _route, dest, mins, _stale in rows:
        time_w = now_f.getlength("now") if mins == 0 else \
            num_f.getlength(str(min(mins, 99))) + unit_f.getlength(" min")
        widths.append(PAD * k + 24 * k + 8 * k + min(dest_f.getlength(dest), 150 * k) + 16 * k + time_w
                      + (PAGER_W if len(rows) > 1 else PAD) * k)
    return round(max(widths + [140 * k]))


def countdown_card(row, height, page=0, pages=1, width=None):
    """One train, full size, in the spirit of the MTA countdown clocks."""
    k = height / 48
    light = light_theme()
    ink = (0, 0, 0) if light else (255, 255, 255)
    w = width or countdown_width([row], height)
    route, dest, mins, stale = row
    fade = 0.5 if stale else 1.0
    p = Painter(w, height)
    pad, d = PAD * k, 24 * k
    cy = height / 2
    base = cy + 5.5 * k  # shared baseline for every word on the line
    p.bullet(route, pad + d / 2, cy, d, fade)
    num_f, unit_f, dest_f, now_f = countdown_fonts(k)
    right = w - (PAGER_W if pages > 1 else PAD) * k
    if mins == 0:
        p.text((right, base), "now", now_f, ink, fade, "rs")
        time_w = now_f.getlength("now")
    else:
        unit = " min"
        p.text((right, base), unit, unit_f, ink, 0.6 * fade, "rs")
        p.text((right - unit_f.getlength(unit), base), str(min(mins, 99)), num_f, ink, fade, "rs")
        time_w = unit_f.getlength(unit) + num_f.getlength(str(min(mins, 99)))
    dest_x = pad + d + 8 * k
    dest_max = right - time_w - 16 * k - dest_x
    p.text((dest_x, base), fit(dest, dest_f, dest_max), dest_f, ink, 0.95 * fade, "ls")
    if pages > 1:  # a quiet pager: which of the upcoming trains this is
        gap, r = 6 * k, PAGER_R * k
        x = w - (8 + PAGER_R) * k
        top = cy - gap * (pages - 1) / 2
        for i in range(pages):
            y = top + i * gap
            p.shape(lambda g, s, y=y: g.ellipse([(x - r) * s, (y - r) * s, (x + r) * s, (y + r) * s], fill=255),
                    ink, 0.9 if i == page else 0.28, shadow=False)
    return p.compose(0 if light else 0.5)


def countdown_message(message, height):
    k = height / 48
    light = light_theme()
    ink = (0, 0, 0) if light else (255, 255, 255)
    return message_only(message, height, k, ink, light, round(150 * k))


def shifted(img, dy, opacity):
    """img moved vertically by dy pixels (sub-pixel) and faded, on a same-size transparent canvas."""
    w, h = img.size
    if abs(dy) >= h or opacity <= 0:
        return Image.new("RGBA", (w, h), (0, 0, 0, 0))
    if opacity < 1:
        img = img.copy()
        img.putalpha(img.getchannel("A").point(lambda v: int(v * opacity)))
    # Premultiplied so the bilinear blend between rows never darkens the edges of the text.
    return img.convert("RGBa").transform((w, h), Image.AFFINE, (1, 0, 0, 0, 1, -dy),
                                         resample=Image.BILINEAR).convert("RGBA")


def message_only(message, height, k, ink, light, width):
    p = Painter(width, height)
    p.text((width / 2, height / 2), message, font("Regular", round(12 * k)), ink, 0.65, "mm")
    return p.compose(0 if light else 0.5)


def render_clock(rows, message, height, k, ink, light):
    """Two quiet lines, set like the Windows 11 taskbar clock."""
    body, strong = font("Regular", round(12 * k)), font("Semibold", round(12 * k))
    pad, d, unit = PAD * k, 16 * k, " min"
    if message:
        return message_only(message, height, k, ink, light, round(150 * k))
    dest_w = max(min(body.getlength(r[1]), 130 * k) for r in rows)
    w = round(pad + d + 8 * k + dest_w + 16 * k + strong.getlength("99") + body.getlength(unit) + pad)
    p = Painter(w, height)
    right = w - pad
    dest_x = pad + d + 8 * k
    dest_max = right - dest_x - strong.getlength("99") - body.getlength(unit) - 16 * k
    centers = [height / 2 - 8 * k, height / 2 + 8 * k] if len(rows) > 1 else [height / 2]
    for (route, dest, mins, stale), cy in zip(rows, centers, strict=False):
        fade = 0.5 if stale else 1.0
        p.bullet(route, pad + d / 2, cy, d, fade)
        p.text((dest_x, cy), fit(dest, body, dest_max), body, ink, 0.92 * fade)
        if mins == 0:
            p.text((right, cy), "now", strong, ink, fade, "rm")
        else:
            p.text((right, cy), unit, body, ink, 0.6 * fade, "rm")
            p.text((right - body.getlength(unit), cy), str(min(mins, 99)), strong, ink, fade, "rm")
    return p.compose(0 if light else 0.5)


def render_minimal(rows, message, height, k, ink, light):
    """Only the line and the time, small and close together."""
    if message:
        return message_only(message, height, k, ink, light, round(110 * k))
    strong, body = font("Semibold", round(12 * k)), font("Regular", round(11 * k))
    d, inner, gap, pad = 16 * k, 4 * k, 16 * k, PAD * k
    items = []
    for route, _dest, mins, stale in rows:
        num, unit = ("now", "") if mins == 0 else (str(min(mins, 99)), "m")
        items.append((route, num, unit, stale, d + inner + strong.getlength(num) + body.getlength(unit)))
    w = round(pad * 2 + sum(i[4] for i in items) + gap * (len(items) - 1))
    p = Painter(w, height)
    x, cy = pad, height / 2
    for route, num, unit, stale, iw in items:
        fade = 0.5 if stale else 1.0
        p.bullet(route, x + d / 2, cy, d, fade)
        tx = x + d + inner
        p.text((tx, cy + 4 * k), num, strong, ink, fade, "ls")
        if unit:
            p.text((tx + strong.getlength(num), cy + 4 * k), unit, body, ink, 0.6 * fade, "ls")
        x += iw + gap
    return p.compose(0 if light else 0.45)


# -- LED theme: a dot-matrix sign without the box.

LED_ABBR = [(" ISLAND ", " IS "), (" AVENUE ", " AV "), (" STREET ", " ST "), (" PARK ", " PK "),
            (" CENTER ", " CTR "), (" BOULEVARD ", " BLVD "), (" HEIGHTS ", " HTS "), (" SQUARE ", " SQ "),
            (" VILLAGE ", " VLG "), (" LAST STOP ", " LAST STOP ")]
DEST = (92, 232, 120)
MINS = (226, 234, 255)
DIM = (150, 150, 150)

FONT5x7 = {
    "A": ".###." "#...#" "#...#" "#####" "#...#" "#...#" "#...#", "B": "####.#...##...#####.#...##...#####.",
    "C": ".###.#...##....#....#....#...#.###.", "D": "####.#...##...##...##...##...#####.",
    "E": "######....#....####.#....#....#####", "F": "######....#....####.#....#....#....",
    "G": ".###.#...##....#.####...##...#.####", "H": "#...##...##...#######...##...##...#",
    "I": ".###...#....#....#....#....#...###.", "J": "..###...#....#....#....#.#..#..##..",
    "K": "#...##..#.#.#..##...#.#..#..#.#...#", "L": "#....#....#....#....#....#....#####",
    "M": "#...###.###.#.##.#.##...##...##...#", "N": "#...##...###..##.#.##..###...##...#",
    "O": ".###.#...##...##...##...##...#.###.", "P": "####.#...##...#####.#....#....#....",
    "Q": ".###.#...##...##...##.#.##..#..##.#", "R": "####.#...##...#####.#.#..#..#.#...#",
    "S": ".#####....#.....###.....#....#####.", "T": "#####..#....#....#....#....#....#..",
    "U": "#...##...##...##...##...##...#.###.", "V": "#...##...##...##...##...#.#.#...#..",
    "W": "#...##...##...##.#.##.#.##.#.#.#.#.", "X": "#...##...#.#.#...#...#.#.#...##...#",
    "Y": "#...##...#.#.#...#....#....#....#..", "Z": "#####....#...#...#...#...#....#####",
    "0": ".###.#...##..###.#.###..##...#.###.", "1": "..#...##....#....#....#....#...###.",
    "2": ".###.#...#....#...#...#...#...#####", "3": "#####" "...#." "..#.." "...#." "....#" "#...#" ".###.",
    "4": "...#...##..#.#.#..#.#####...#....#.", "5": "######....####.....#....##...#.###.",
    "6": "..##..#...#....####.#...##...#.###.", "7": "#####....#...#...#...#....#....#...",
    "8": ".###.#...##...#.###.#...##...#.###.", "9": ".###.#...##...#.####....#...#..##..",
    ".": "..............................#....", "-": "....." "....." "....." ".###." "....." "....." ".....",
    "/": "....#....#...#...#...#...#....#....", "'": "..#....#...#.......................",
    "&": ".##..#..#.#.#...#...#.#.##..#..##.#",
}
FONT3x5 = {
    "1": ".#.##..#..#.###", "2": "##...#.#.#..###", "3": "##...#.#...###.",
    "4": "#.##.####..#..#", "5": "####..##...###.", "6": ".###..####.####",
    "7": "###..#.#..#..#.", "A": ".#." "#.#" "###" "#.#" "#.#", "B": "##.#.###.#.###.",
    "C": ".###..#..#...##", "D": "##.#.##.##.###.", "E": "####..##.#..###",
    "F": "####..##.#..#..", "G": ".###..#.##.#.##", "H": "#.#" "#.#" "###" "#.#" "#.#",
    "J": "..#..#..##.#.#.", "L": "#..#..#..#..###", "M": "#.#" "###" "###" "#.#" "#.#",
    "N": "##.#.##.##.##.#", "Q": ".#.#.##.###..##", "R": "##.#.###.#.##.#",
    "S": ".###...#...###.", "W": "#.##.##.#####.#", "Z": "###..#.#.#..###",
}
BULLET = ["..###..", ".#####.", "#######", "#######", "#######", ".#####.", "..###.."]

COLS = 90  # display width in dots
ROW_H = 7


def glyph(ch):
    bits = FONT5x7.get(ch)
    if bits is None:
        return []
    rows = [bits[i * 5:i * 5 + 5] for i in range(7)]
    cols = [c for c in range(5) if any(r[c] == "#" for r in rows)]
    lo, hi = cols[0], cols[-1]
    return [r[lo:hi + 1] for r in rows]


def text_width(s):
    w = 0
    for ch in s:
        w += 3 if ch == " " else len(glyph(ch)[0]) + 1 if glyph(ch) else 0
    return max(0, w - 1)


def mini(ch):
    bits = FONT3x5.get(ch, "")
    return [bits[i * 3:i * 3 + 3] for i in range(5)] if len(bits) == 15 else None


class Dots:
    """A grid of LED dots. Lit dots get a colour; everything else stays dark."""

    def __init__(self):
        self.lit = {}

    def set(self, x, y, color):
        if 0 <= x < COLS and 0 <= y < ROW_H * 2 + 1:
            self.lit[(x, y)] = color

    def text(self, s, x, y, color):
        for ch in s:
            if ch == " ":
                x += 3
                continue
            g = glyph(ch)
            if not g:
                continue
            for r, row in enumerate(g):
                for c, b in enumerate(row):
                    if b == "#":
                        self.set(x + c, y + r, color)
            x += len(g[0]) + 1
        return x

    def digits(self, s, right, y, color):
        """Tabular digits, right-aligned so the number never jitters."""
        x = right - len(s) * 6 + 1
        for ch in s:
            g = [FONT5x7[ch][i * 5:i * 5 + 5] for i in range(7)]
            for r, row in enumerate(g):
                for c, b in enumerate(row):
                    if b == "#":
                        self.set(x + c, y + r, color)
            x += 6

    def bullet(self, route, x, y):
        color = LED_COLORS.get(route) or LED_COLORS.get(route[0], LED_COLORS["S"])
        letter = mini(route[0] if route != "SIR" else "S")
        for r, row in enumerate(BULLET):
            for c, b in enumerate(row):
                cut = letter and 1 <= r <= 5 and 2 <= c <= 4 and letter[r - 1][c - 2] == "#"
                if b == "#" and not cut:
                    self.set(x + c, y + r, color)



def render_led(rows, message, height, k, ink, light):
    dots = Dots()
    right = COLS - 2
    if message:
        msg = "".join(ch for ch in message.upper() if ch == " " or ch in FONT5x7)
        dots.text(msg, (COLS - text_width(msg)) // 2, 4, DIM)
    else:
        for i, (route, dest, mins, stale) in enumerate(rows):
            y = i * (ROW_H + 1)
            dim = (lambda c: tuple(v // 2 for v in c)) if stale else (lambda c: c)
            dots.bullet(route, 1, y)
            name = " " + dest.upper() + " "
            for long, short in LED_ABBR:
                name = name.replace(long, short)
            name = "".join(ch for ch in name.strip() if ch == " " or ch in FONT5x7)
            while name and text_width(name) > COLS - 31:
                name = name[:-1].rstrip()
            dots.text(name, 10, y, dim(DEST))
            if mins == 0:
                dots.text("NOW", right - text_width("NOW") + 1, y, dim(MINS))
            else:
                dots.digits(str(min(mins, 99)), right, y, dim(MINS))
    pitch = max(3, height // 16)
    size = max(2, round(pitch * 0.68))
    n_rows = ROW_H * 2 + 1
    pad = (height - n_rows * pitch) // 2
    w = COLS * pitch + pad * 2 + pitch
    lit = Image.new("RGB", (w, height), (0, 0, 0))
    mask = Image.new("L", (w, height), 0)
    dl, ml = ImageDraw.Draw(lit), ImageDraw.Draw(mask)
    shape = "ellipse" if size >= 4 else "rectangle"
    for (x, y), color in dots.lit.items():
        px = pad + pitch // 2 + x * pitch + (pitch - size) // 2
        py = pad + y * pitch + (pitch - size) // 2
        box = [px, py, px + size - 1, py + size - 1]
        getattr(dl, shape)(box, fill=color)
        getattr(ml, shape)(box, fill=255)
    glow = lit.filter(ImageFilter.GaussianBlur(pitch * 0.9)).point(lambda v: int(v * 0.6))
    out = Image.new("RGBA", (w, height), (0, 0, 0, 0))
    if not light:  # a soft dark halo keeps the dots readable on any wallpaper
        halo = mask.filter(ImageFilter.GaussianBlur(pitch * 1.1)).point(lambda v: min(150, v * 2))
        out.alpha_composite(Image.merge("RGBA", (*Image.new("RGB", (w, height)).split(), halo)))
        out.alpha_composite(Image.merge("RGBA", (*glow.split(), glow.convert("L"))))
    out.alpha_composite(Image.merge("RGBA", (*lit.split(), mask)))
    return out



# ---------------------------------------------------------------- data

def log_error(exc_type, exc, tb):
    """Append to %APPDATA%\\Trainbar\\error.log, keeping one 256 KB backup at most."""
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        path = os.path.join(DATA_DIR, "error.log")
        if os.path.exists(path) and os.path.getsize(path) > 256 * 1024:
            os.replace(path, path + ".1")
        with open(path, "a", encoding="utf-8") as f:
            f.write(f"Trainbar {__version__} ")
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + "".join(traceback.format_exception(exc_type, exc, tb)))
    except OSError:
        pass


def http_get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": f"Trainbar/{__version__} (+{HOMEPAGE})",
                                               "Accept-Encoding": "gzip"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read()
        return gzip.decompress(data) if r.headers.get("Content-Encoding") == "gzip" else data


def _varint(b, i):
    out = shift = 0
    while True:
        c = b[i]
        i += 1
        out |= (c & 0x7F) << shift
        if c < 0x80:
            return out, i
        shift += 7


def pb_fields(b):
    """Minimal protobuf wire-format reader: yields (field_number, value)."""
    i, n = 0, len(b)
    while i < n:
        key, i = _varint(b, i)
        field, wt = key >> 3, key & 7
        if wt == 0:
            v, i = _varint(b, i)
        elif wt == 2:
            ln, i = _varint(b, i)
            v = b[i:i + ln]
            i += ln
        elif wt == 1:
            v, i = b[i:i + 8], i + 8
        elif wt == 5:
            v, i = b[i:i + 4], i + 4
        else:
            raise ValueError("unsupported wire type")
        yield field, v


def parse_feed(data, stop_ids):
    """Return arrivals at the given stop ids: (stop_id, route, epoch, dest_stop)."""
    out = []
    for f, entity in pb_fields(memoryview(data)):
        if f != 2:
            continue
        for f2, tu in pb_fields(entity):
            if f2 != 3:
                continue
            route, stops = "", []
            for f3, v3 in pb_fields(tu):
                if f3 == 1:
                    for f4, v4 in pb_fields(v3):
                        if f4 == 5:
                            route = bytes(v4).decode()
                elif f3 == 2:
                    sid, t = "", 0
                    for f4, v4 in pb_fields(v3):
                        if f4 == 4:
                            sid = bytes(v4).decode()
                        elif f4 in (2, 3) and not t:
                            for f5, v5 in pb_fields(v4):
                                if f5 == 2:
                                    t = v5
                    stops.append((sid, t))
            if not stops:
                continue
            dest = stops[-1][0]
            for sid, t in stops:
                if t and sid[:-1] in stop_ids:
                    out.append((sid, route, t, dest))
    return out


STATION_COLUMNS = {"GTFS Stop ID", "Complex ID", "Stop Name", "Line", "Borough", "Daytime Routes",
                   "North Direction Label", "South Direction Label"}


def load_stations():
    os.makedirs(DATA_DIR, exist_ok=True)
    fresh = os.path.exists(STATIONS_PATH) and time.time() - os.path.getmtime(STATIONS_PATH) < 30 * 86400
    if not fresh:
        try:
            raw = http_get(STATIONS_URL, timeout=20)
            with open(STATIONS_PATH, "wb") as f:
                f.write(raw)
        except OSError:
            if not os.path.exists(STATIONS_PATH):
                raise
    with open(STATIONS_PATH, encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    missing = STATION_COLUMNS - set(rows[0] if rows else ())
    if missing:
        os.remove(STATIONS_PATH)  # fetch a fresh copy next time
        raise RuntimeError(f"The MTA station list changed format (missing {', '.join(sorted(missing))}).")
    names, complexes = {}, {}
    for r in rows:
        sid = r["GTFS Stop ID"]
        names[sid] = r["Stop Name"]
        cx = complexes.setdefault(r["Complex ID"], {
            "id": r["Complex ID"], "name": r["Stop Name"], "stops": [], "routes": [],
            "line": r["Line"], "boro": r["Borough"],
            "north": r["North Direction Label"], "south": r["South Direction Label"],
        })
        cx["stops"].append(sid)
        if r["Line"] not in cx["line"].split(" / "):
            cx["line"] += " / " + r["Line"]
        if r["Stop Name"] not in cx["name"].split(" / "):
            cx["name"] += " / " + r["Stop Name"]
        for route in r["Daytime Routes"].split():
            if route not in cx["routes"]:
                cx["routes"].append(route)
    return names, complexes


SHORT = {
    "Coney Island-Stillwell Av": "Coney Island", "Van Cortlandt Park-242 St": "242 St",
    "Flatbush Av-Brooklyn College": "Flatbush Av", "Inwood-207 St": "Inwood",
    "Jamaica Center-Parsons/Archer": "Jamaica Center", "Jamaica-179 St": "Jamaica",
    "Harlem-148 St": "Harlem", "Eastchester-Dyre Av": "Dyre Av", "Crown Hts-Utica Av": "Utica Av",
    "Brooklyn Bridge-City Hall": "City Hall", "Flushing-Main St": "Flushing",
    "34 St-Hudson Yards": "Hudson Yards", "Astoria-Ditmars Blvd": "Astoria",
    "Forest Hills-71 Av": "Forest Hills", "Bay Ridge-95 St": "Bay Ridge",
    "Whitehall St-South Ferry": "Whitehall", "Canarsie-Rockaway Pkwy": "Canarsie",
    "Middle Village-Metropolitan Av": "Middle Village", "Norwood-205 St": "Norwood",
    "Far Rockaway-Mott Av": "Far Rockaway", "Rockaway Park-Beach 116 St": "Rockaway Park",
    "Ozone Park-Lefferts Blvd": "Lefferts Blvd", "Wakefield-241 St": "Wakefield",
    "Court Sq-23 St": "Court Sq", "Times Sq-42 St": "Times Sq", "Grand Central-42 St": "Grand Central",
    "Jamaica-Van Wyck": "Van Wyck", "Lexington Av/59 St": "Lexington Av",
}


def short_name(name):
    return SHORT.get(name) or name.split("-")[0]


def pick_rows(arrivals, station, names, now, last_ok, mode, rotate=False, limit=2):
    """Choose which trains to show. Returns [(route, destination, minutes, stale)].

    rotate: the next `limit` trains, soonest first (Countdown).
    Otherwise in mode "B" the next train each way, else the next two in direction `mode`.
    """
    live = [a for a in arrivals if a[2] >= now - 20]
    if rotate:
        picks = [a for a in live if mode == "B" or a[0].endswith(mode)][:limit]
    elif mode == "B":
        picks = [next((a for a in live if a[0].endswith(d)), None) for d in "NS"]
        picks = [p for p in picks if p] or live[:2]
    else:
        picks = [a for a in live if a[0].endswith(mode)][:2]
    stale = now - last_ok > 180
    rows = []
    for _sid, route, t, dest in picks:
        route = {"GS": "S", "FS": "S", "SI": "SIR"}.get(route, route)
        name = "Last stop" if dest[:-1] in station["stops"] else short_name(names.get(dest[:-1], dest))
        rows.append((route, name, max(0, int((t - now) // 60)), stale))
    return rows


class Feed:
    """Background poller. Keeps the latest arrivals for one station."""

    def __init__(self, on_update):
        self.on_update = on_update
        self.lock = threading.Lock()
        self.station = None
        self.arrivals = []
        self.last_ok = 0.0
        self.error = None
        self.extra_feeds = set()
        self.last_sweep = 0.0
        self.wake = threading.Event()
        self.idle = False  # set while the widget is hidden behind a fullscreen app
        self.failures = 0
        self.pool = ThreadPoolExecutor(max_workers=8)
        threading.Thread(target=self._loop, daemon=True).start()

    def set_station(self, station):
        with self.lock:
            self.station = station
            self.arrivals = []
            self.extra_feeds = set()
            self.last_sweep = 0.0
            self.last_ok = 0.0
        self.refresh()

    def refresh(self):
        self.wake.set()

    def _loop(self):
        while True:
            try:
                self._poll()
            except Exception:
                log_error(*sys.exc_info())
            self.wake.wait(self.next_wait())
            self.wake.clear()

    def next_wait(self):
        """30 s normally; doubling up to 5 min while offline; 2 min while hidden."""
        if self.failures:
            return min(POLL_SECONDS * 2 ** (self.failures - 1), 300)
        return 120 if self.idle else POLL_SECONDS

    def _poll(self):
        with self.lock:
            station = self.station
        if not station:
            return
        stop_ids = set(station["stops"])
        sweep = time.time() - self.last_sweep > DISCOVER_SECONDS
        if sweep:
            feeds = set(FEEDS)
        else:
            feeds = {ROUTE_FEED[r] for r in station["routes"] if r in ROUTE_FEED} | self.extra_feeds
            if "S" in station["routes"]:
                feeds |= {"gtfs", "gtfs-ace", "gtfs-bdfm"}

        def one(name):
            return name, parse_feed(http_get(FEED_BASE + name), stop_ids)

        results, errors, err = [], 0, None
        for fut in [self.pool.submit(one, f) for f in feeds]:
            try:
                name, arr = fut.result()
                results.extend(arr)
                if sweep and arr:
                    self.extra_feeds.add(name)
            except Exception as e:  # network or parse failure on one feed
                errors += 1
                err = e
        with self.lock:
            if self.station is not station:
                return  # station changed mid-poll
            if errors == len(feeds):
                self.error = str(err)
                self.failures += 1
            else:
                self.failures = 0
                if sweep:
                    self.last_sweep = time.time()
                self.error = None
                self.last_ok = time.time()
                seen, uniq = set(), []
                for a in sorted(results, key=lambda a: a[2]):
                    if a[:3] not in seen:
                        seen.add(a[:3])
                        uniq.append(a)
                self.arrivals = uniq
        self.on_update()

    def snapshot(self):
        with self.lock:
            return list(self.arrivals), self.last_ok, self.error


# ---------------------------------------------------------------- windows plumbing

user32 = ctypes.windll.user32
gdi32 = ctypes.windll.gdi32
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE = 0x1, 0x2, 0x10
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_APPWINDOW, WS_EX_LAYERED = 0x80, 0x08000000, 0x40000, 0x80000
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
INSTALL_DIR = os.path.join(os.environ["LOCALAPPDATA"], APP)

for fn, res, args in (
    (user32.GetDC, wintypes.HDC, [wintypes.HWND]),
    (user32.ReleaseDC, ctypes.c_int, [wintypes.HWND, wintypes.HDC]),
    (user32.GetParent, wintypes.HWND, [wintypes.HWND]),
    (user32.SetWindowPos, wintypes.BOOL, [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                                          ctypes.c_int, ctypes.c_int, wintypes.UINT]),
    (user32.UpdateLayeredWindow, wintypes.BOOL, [wintypes.HWND, wintypes.HDC, ctypes.c_void_p, ctypes.c_void_p,
                                                 wintypes.HDC, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
                                                 wintypes.DWORD]),
    (gdi32.CreateCompatibleDC, wintypes.HDC, [wintypes.HDC]),
    (gdi32.CreateDIBSection, wintypes.HBITMAP, [wintypes.HDC, ctypes.c_void_p, wintypes.UINT,
                                                ctypes.POINTER(ctypes.c_void_p), wintypes.HANDLE, wintypes.DWORD]),
    (gdi32.SelectObject, wintypes.HGDIOBJ, [wintypes.HDC, wintypes.HGDIOBJ]),
    (gdi32.DeleteObject, wintypes.BOOL, [wintypes.HGDIOBJ]),
    (gdi32.DeleteDC, wintypes.BOOL, [wintypes.HDC]),
):
    fn.restype, fn.argtypes = res, args


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long), ("biHeight", ctypes.c_long),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
                ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD)]


class BLENDFUNCTION(ctypes.Structure):
    _fields_ = [("BlendOp", ctypes.c_ubyte), ("BlendFlags", ctypes.c_ubyte),
                ("SourceConstantAlpha", ctypes.c_ubyte), ("AlphaFormat", ctypes.c_ubyte)]


def present_layered(hwnd, img):
    """Show an RGBA image on a layered window with per-pixel alpha."""
    w, h = img.size
    r, g, b, a = img.split()
    r, g, b = (ImageChops.multiply(c, a) for c in (r, g, b))  # premultiplied alpha
    data = Image.merge("RGBA", (b, g, r, a)).tobytes()
    bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
    screen = user32.GetDC(None)
    mem = gdi32.CreateCompatibleDC(screen)
    bits = ctypes.c_void_p()
    bmp = gdi32.CreateDIBSection(mem, ctypes.byref(bmi), 0, ctypes.byref(bits), None, 0)
    ctypes.memmove(bits, data, len(data))
    old = gdi32.SelectObject(mem, bmp)
    size, src = wintypes.SIZE(w, h), wintypes.POINT(0, 0)
    blend = BLENDFUNCTION(0, 0, 255, 1)  # AC_SRC_OVER, AC_SRC_ALPHA
    user32.UpdateLayeredWindow(hwnd, screen, None, ctypes.byref(size), mem, ctypes.byref(src), 0,
                               ctypes.byref(blend), 2)  # ULW_ALPHA
    gdi32.SelectObject(mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem)
    user32.ReleaseDC(None, screen)


def animations_enabled():
    """Respect Settings > Accessibility > Visual effects > Animation effects."""
    on = wintypes.BOOL(True)
    user32.SystemParametersInfoW(0x1042, 0, ctypes.byref(on), 0)  # SPI_GETCLIENTAREAANIMATION
    return bool(on.value)


def rect_of(hwnd):
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def taskbar_geometry():
    tb = user32.FindWindowW("Shell_TrayWnd", None)
    if not tb:
        return None
    l, t, r, b = rect_of(tb)
    tray = user32.FindWindowExW(tb, None, "TrayNotifyWnd", None)
    tray_left = rect_of(tray)[0] if tray else r - 300
    return l, t, r, b, tray_left


def fullscreen_app_in_front():
    fg = user32.GetForegroundWindow()
    if not fg:
        return False
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(fg, cls, 64)
    if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd", "TkTopLevel"):
        return False
    l, t, r, b = rect_of(fg)
    return l <= 0 and t <= 0 and r >= user32.GetSystemMetrics(0) and b >= user32.GetSystemMetrics(1)


def autostart_enabled():
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as k:
            winreg.QueryValueEx(k, APP)
            return True
    except OSError:
        return False


def set_autostart(on):
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as k:
        if on:
            # Copy the program somewhere permanent so startup never points at a moved file.
            os.makedirs(INSTALL_DIR, exist_ok=True)
            if getattr(sys, "frozen", False):  # the standalone Trainbar.exe
                target = os.path.join(INSTALL_DIR, "Trainbar.exe")
                command = f'"{target}"'
                source = sys.executable
            else:
                target = os.path.join(INSTALL_DIR, "trainbar.pyw")
                exe = sys.executable
                if exe.lower().endswith("python.exe"):
                    exe = exe[:-10] + "pythonw.exe"
                command = f'"{exe}" "{target}"'
                source = __file__
            if os.path.abspath(source) != os.path.abspath(target):
                shutil.copyfile(source, target)
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, command)
        else:
            try:
                winreg.DeleteValue(k, APP)
            except OSError:
                pass


# ---------------------------------------------------------------- app

class App:
    def __init__(self):
        self.cfg = self._load_cfg()
        self.names, self.complexes = load_stations()
        self.root = tk.Tk()
        self.root.withdraw()
        self.root.overrideredirect(True)
        self.root.report_callback_exception = log_error
        self.icon = ImageTk.PhotoImage(app_icon(64))
        self.root.iconphoto(True, self.icon)  # title bars of Settings and the station search
        self.surface = tk.Frame(self.root, bd=0, highlightthickness=0, bg="black", cursor="hand2")
        self.surface.pack(fill="both", expand=True)
        self.size = (1, 1)
        self.hover = False
        self.picker = None
        self.settings = None
        self.drag = None
        self.hidden = False
        self.page = 0
        self.anim = None  # (old_card, start_time) while a slide is running
        self.cards = {}
        self.drawn = None  # what is on screen now, to skip identical redraws
        self.last_guard = time.monotonic()

        s = self.surface
        s.bind("<ButtonPress-1>", self._press)
        s.bind("<B1-Motion>", self._motion)
        s.bind("<ButtonRelease-1>", self._release)
        s.bind("<Button-3>", self._menu)
        s.bind("<Enter>", lambda e: self._set_hover(True))
        s.bind("<Leave>", lambda e: self._set_hover(False))

        self.feed = Feed(lambda: self.root.after(0, self.draw))
        self.root.geometry("1x1+0+0")
        self.root.deiconify()
        self.root.update_idletasks()
        hwnd = self._hwnd()
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        ex = (ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED) & ~WS_EX_APPWINDOW
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, ex)
        self.draw()
        if self.station:
            self.feed.set_station(self.station)
        else:
            self.root.after(300, self.open_picker)
        self._tick()
        self._guard()
        self.root.after(self._cycle_ms(), self._cycle)

    # -- config
    def _load_cfg(self):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                cfg = json.load(f)
            return cfg if isinstance(cfg, dict) else {"dir": "B"}
        except (OSError, ValueError):
            return {"dir": "B"}

    def _save_cfg(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        tmp = CONFIG_PATH + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, indent=2)
        os.replace(tmp, CONFIG_PATH)

    @property
    def station(self):
        return self.complexes.get(self.cfg.get("station"))

    # -- drawing
    @property
    def theme(self):
        return self.cfg.get("theme", DEFAULT_THEME)

    def rows(self, limit=2):
        station = self.station
        if not station:
            return None, "Choose a station"
        arrivals, last_ok, error = self.feed.snapshot()
        if not last_ok:
            return None, "Offline" if error else "Loading…"
        rows = pick_rows(arrivals, station, self.names, time.time(), last_ok, self.cfg.get("dir", "B"),
                         rotate=self.theme == "countdown", limit=limit)
        return (rows, None) if rows else (None, "No trains")

    def draw(self):
        if self.hidden:
            return
        hover, background = self.hover or bool(self.drag), self.cfg.get("background", False)
        height, light = self._bar_height(), light_theme()
        if self.theme == "countdown":
            rows, message = self.rows(limit=self.cfg.get("cycle_count", 4))
            key = ("countdown", rows, message, self.page, hover, background, height, light)
        else:
            rows, message = self.rows()
            key = (self.theme, rows, message, hover, background, height, light)
        if key == self.drawn and not self.anim:
            return  # nothing visible changed: skip the render and the window update
        self.drawn = None if self.anim else key
        if self.theme == "countdown":
            img = finish(self._countdown_frame(), hover, background)
        else:
            img = render(self.theme, rows, message, height, hover=hover, background=background)
        if img.size != self.size:
            self.size = img.size
            self._place()
        present_layered(self._hwnd(), img)

    # -- countdown: one train at a time, sliding to the next
    def _card(self, row, page, pages, width):
        key = (row, page, pages, width, self._bar_height(), light_theme())
        if key not in self.cards:
            if len(self.cards) > 64:
                self.cards.clear()
            self.cards[key] = countdown_card(row, self._bar_height(), page, pages, width)
        return self.cards[key]

    def _current_card(self):
        rows, message = self.rows(limit=self.cfg.get("cycle_count", 4))
        if message:
            self.page = 0
            return countdown_message(message, self._bar_height())
        self.page %= len(rows)
        width = countdown_width(rows, self._bar_height())
        return self._card(rows[self.page], self.page, len(rows), width)

    def _countdown_frame(self):
        card = self._current_card()
        if not self.anim:
            return card
        old, start = self.anim
        t = min(1.0, (time.perf_counter() - start) * 1000 / SLIDE_MS)
        if t >= 1 or old.size != card.size:
            self.anim = None
            return card
        e = (1 - math.cos(math.pi * t)) / 2  # ease in and out
        travel = card.height * 0.45
        frame = shifted(old, -e * travel, 1 - e)
        frame.alpha_composite(shifted(card, (1 - e) * travel, e))
        return frame

    def _advance(self):
        rows, message = self.rows(limit=self.cfg.get("cycle_count", 4))
        if message or len(rows) < 2:
            return
        old = self._current_card()
        self.page = (self.page + 1) % len(rows)
        if self.cfg.get("animate", True) and animations_enabled():
            self.anim = (old, time.perf_counter())
            self._animate()
        else:
            self.draw()

    def _animate(self):
        self.draw()
        if self.anim:
            self.root.after(15, self._animate)

    def _cycle_ms(self):
        return int(self.cfg.get("cycle_seconds", 5) * 1000)

    def _cycle(self):
        if self.theme == "countdown" and not self.hover and not self.drag and not self.hidden:
            self._advance()
        self.root.after(self._cycle_ms(), self._cycle)

    def _set_hover(self, on):
        self.hover = on
        self.draw()

    def _bar_height(self):
        g = taskbar_geometry()
        return (g[3] - g[1]) if g else 48

    def _tick(self):
        if not self.anim:
            self.draw()
        self.root.after(2000, self._tick)

    # -- placement
    def _place(self):
        g = taskbar_geometry()
        if not g:
            return
        w, h = self.size
        l, t, r, b, tray_left = g
        offset = self.cfg.get("offset")
        x = r - offset if offset is not None else tray_left - w - 4
        x = max(l, min(x, r - w))
        y = t + (b - t - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _hwnd(self):
        return user32.GetParent(self.surface.winfo_toplevel().winfo_id())

    def _guard(self):
        """Stay above the taskbar, follow it if it moves, and hide for fullscreen apps."""
        now = time.monotonic()
        if now - self.last_guard > 60:  # the PC was asleep: the data is old
            self.feed.refresh()
        self.last_guard = now
        if fullscreen_app_in_front():
            if not self.hidden:
                self.root.withdraw()
                self.hidden = True
                self.feed.idle = True
        else:
            if self.hidden:
                self.root.deiconify()
                self.hidden = False
                self.feed.idle = False
                self.feed.refresh()
                self.drawn = None
                self.draw()
            if not self.drag:
                self._place()
            user32.SetWindowPos(self._hwnd(), HWND_TOPMOST, 0, 0, 0, 0,
                                SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE)
        self.root.after(1000, self._guard)

    # -- input
    def _press(self, e):
        self.drag = {"x0": e.x_root, "wx": self.root.winfo_x(), "moved": False}

    def _motion(self, e):
        if not self.drag:
            return
        dx = e.x_root - self.drag["x0"]
        if abs(dx) > 3 and not self.cfg.get("locked", False):
            self.drag["moved"] = True
        if self.drag["moved"]:
            g = taskbar_geometry()
            x = max(g[0], min(self.drag["wx"] + dx, g[2] - self.size[0]))
            self.root.geometry(f"+{x}+{self.root.winfo_y()}")

    def _release(self, e):
        if self.drag and self.drag["moved"]:
            g = taskbar_geometry()
            self.cfg["offset"] = g[2] - self.root.winfo_x()
            self._save_cfg()
        elif self.drag:
            self.drag = None
            if self.theme == "countdown":
                self._advance()  # click shows the next train
            else:
                self.feed.refresh()
        self.drag = None
        self.draw()

    def _menu(self, e):
        m = tk.Menu(self.root, tearoff=0)
        st = self.station
        if st:
            m.add_command(label=f"{st['name']}  ·  {' '.join(st['routes'])}", state="disabled")
            m.add_separator()
            var = tk.StringVar(value=self.cfg.get("dir", "B"))
            for code, label in self._directions():
                m.add_radiobutton(label=label, value=code, variable=var,
                                  command=lambda c=code: self._set("dir", c))
            m.add_separator()
        themes = tk.Menu(m, tearoff=0)
        tvar = tk.StringVar(value=self.theme)
        for code, label in THEMES:
            themes.add_radiobutton(label=label, value=code, variable=tvar,
                                   command=lambda c=code: self._set("theme", c))
        m.add_cascade(label="Theme", menu=themes)
        m.add_command(label="Change station…", command=self.open_picker)
        lock = tk.BooleanVar(value=self.cfg.get("locked", False))
        m.add_checkbutton(label="Lock position", variable=lock, command=lambda: self._set("locked", lock.get()))
        m.add_command(label="Settings…", command=self.open_settings)
        m.add_command(label="Refresh now", command=self.feed.refresh)
        m.add_separator()
        m.add_command(label="Quit", command=self.root.destroy)
        m.tk_popup(e.x_root, e.y_root)

    def _directions(self):
        st = self.station or {}
        return (("N", st.get("north") or "Uptown"), ("S", st.get("south") or "Downtown"),
                ("B", "Both"))

    def _set(self, key, value):
        self.cfg[key] = value
        self._save_cfg()
        self.anim = None
        self.drawn = None
        self.draw()
        if self.settings and self.settings.winfo_exists():
            self.settings.refresh()

    def _reset_pos(self):
        self.cfg.pop("offset", None)
        self._save_cfg()
        self._place()

    # -- settings
    def open_settings(self):
        if self.settings and self.settings.winfo_exists():
            self.settings.lift()
            return
        self.settings = SettingsWindow(self)

    # -- station picker
    def open_picker(self):
        if self.picker and self.picker.winfo_exists():
            self.picker.lift()
            return
        self.picker = StationPicker(self)

    def choose_station(self, c):
        self.cfg["station"] = c["id"]
        self.cfg["dir"] = "B"
        self._save_cfg()
        self.feed.set_station(c)
        self.page, self.anim = 0, None
        self.draw()
        if self.settings and self.settings.winfo_exists():
            self.settings.refresh()

    def run(self):
        self.root.mainloop()


BOROUGHS = {"M": "Manhattan", "Bk": "Brooklyn", "Bx": "Bronx", "Q": "Queens", "SI": "Staten Island"}
ROUTE_LETTERS = set("1234567ABCDEFGJLMNQRSWZ")


SYNONYMS = {"junction": "jct", "avenue": "av", "ave": "av", "avs": "av", "street": "st", "sts": "st",
            "square": "sq", "boulevard": "blvd", "parkway": "pkwy", "heights": "hts", "center": "ctr",
            "road": "rd", "place": "pl", "highway": "hwy", "hwy": "hwy"}


def normalize(text):
    """Lower case, hyphens and slashes as spaces, and one spelling for each street word."""
    words = text.lower().replace("-", " ").replace("/", " ").replace(",", " ").split()
    return " ".join(SYNONYMS.get(w, w) for w in words)


def search_stations(complexes, query):
    """Match names, line names and lines. "jmz" finds every J, M or Z stop; "bedford l" narrows."""
    tokens = normalize(query).split()
    results = []
    for c in complexes.values():
        hay = normalize(c["name"] + " " + c["line"] + " " + BOROUGHS.get(c["boro"], ""))
        routes = set(c["routes"])
        served = 0
        words = hay.split()
        for tok in tokens:
            up = tok.upper()
            if up in routes:
                served += 1
                continue
            if tok.isdigit() and tok in words:  # "8" means 8 Av, not 80 St
                continue
            if len(tok) > 1 and not tok.isdigit() and any(w.startswith(tok) for w in words):
                continue
            letters = set(up)
            # "jmz" means any of the J, M or Z. Numbers stay street numbers ("14" is 14 St).
            is_route_set = not tok.isdigit() and 1 < len(tok) == len(letters) and letters <= ROUTE_LETTERS
            if is_route_set and letters & routes:
                served += len(letters & routes)
                continue
            break
        else:
            name = normalize(c["name"])
            query_text = " ".join(tokens)
            exact = any(normalize(part) == query_text for part in c["name"].split(" / "))
            starts = bool(tokens) and name.startswith(tokens[0])
            results.append(((-served, not exact, not starts, c["name"], c["routes"]), c))
    return [c for _, c in sorted(results, key=lambda r: r[0])]


class StationPicker(tk.Toplevel):
    """A search box with real line bullets, styled after Windows 11."""

    ROW, W, VISIBLE, MAX = 54, 460, 8, 150

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.c = c = palette()
        self.hover_bg = "#eaeaea" if not c["dark"] else "#272727"
        self.title("Choose a station")
        self.configure(bg=c["bg"], padx=14, pady=14)
        self.attributes("-topmost", True)
        self.resizable(False, False)
        self.name_font = tkfont.Font(family="Segoe UI Variable Text", size=11)
        self.sub_font = tkfont.Font(family="Segoe UI Variable Text", size=9)
        self.bullets = {}
        self.results, self.sel, self.hover = [], 0, None

        border = tk.Frame(self, bg=c["line"], padx=1, pady=1)
        border.pack(fill="x")
        box = tk.Frame(border, bg=c["card"], padx=10, pady=8)
        box.pack(fill="x")
        tk.Label(box, text="\ue721", font=("Segoe Fluent Icons", 11), bg=c["card"], fg=c["sub"]).pack(side="left")
        self.q = tk.StringVar()
        self.entry = tk.Entry(box, textvariable=self.q, font=("Segoe UI Variable Text", 12), bg=c["card"],
                              fg=c["fg"], insertbackground=c["fg"], relief="flat", bd=0, highlightthickness=0)
        self.entry.pack(side="left", fill="x", expand=True, padx=(10, 0))
        self.placeholder = tk.Label(box, text="Search stations or lines", font=("Segoe UI Variable Text", 12),
                                    bg=c["card"], fg=c["sub"])
        self.placeholder.place(in_=self.entry, x=0, rely=0.5, anchor="w")
        self.placeholder.bind("<Button-1>", lambda e: self.entry.focus_set())
        self.entry.bind("<FocusIn>", lambda e: border.configure(bg=c["accent"]))
        self.entry.bind("<FocusOut>", lambda e: border.configure(bg=c["line"]))

        self.canvas = tk.Canvas(self, width=self.W, height=self.ROW * self.VISIBLE, bg=c["bg"],
                                highlightthickness=0, yscrollincrement=self.ROW // 2)
        self.canvas.pack(pady=(10, 0))

        self.q.trace_add("write", lambda *_: self.update_results())
        self.entry.bind("<Return>", lambda e: self.choose(self.sel))
        self.entry.bind("<Down>", lambda e: self.move(1))
        self.entry.bind("<Up>", lambda e: self.move(-1))
        self.entry.bind("<Next>", lambda e: self.move(self.VISIBLE))
        self.entry.bind("<Prior>", lambda e: self.move(-self.VISIBLE))
        self.bind("<Escape>", lambda e: self.destroy())
        self.canvas.bind("<Motion>", self._motion)
        self.canvas.bind("<Leave>", lambda e: self._set_hover(None))
        self.canvas.bind("<Button-1>", lambda e: self.choose(self._index_at(e.y)))
        self.canvas.bind("<MouseWheel>", lambda e: self.canvas.yview_scroll(-2 * (e.delta // 120), "units"))

        self.update_results()
        self.update_idletasks()
        dark = ctypes.c_int(1 if c["dark"] else 0)
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(user32.GetParent(self.winfo_id()), 20,
                                                       ctypes.byref(dark), ctypes.sizeof(dark))
        except OSError:
            pass
        x = min(app.root.winfo_x(), self.winfo_screenwidth() - self.winfo_width() - 12)
        y = app.root.winfo_y() - self.winfo_height() - 48
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.entry.focus_force()

    # data
    def update_results(self):
        query = self.q.get()
        if query:
            self.placeholder.place_forget()
        else:
            self.placeholder.place(in_=self.entry, x=0, rely=0.5, anchor="w")
        self.results = search_stations(self.app.complexes, query)[:self.MAX]
        self.sel, self.hover = 0, None
        self.canvas.yview_moveto(0)
        self.redraw()

    def bullet_strip(self, routes):
        key = tuple(routes)
        if key not in self.bullets:
            d, gap, shown = 20, 4, routes[:6]
            w = max(1, len(shown) * (d + gap) - gap + 2)
            pt = Painter(w, d + 2)
            for i, r in enumerate(shown):
                pt.bullet(r, 1 + d / 2 + i * (d + gap), (d + 2) / 2, d)
            self.bullets[key] = ImageTk.PhotoImage(pt.compose(0))
        return self.bullets[key]

    # drawing
    def round_rect(self, x1, y1, x2, y2, r, **kw):
        pts = [x1 + r, y1, x2 - r, y1, x2, y1, x2, y1 + r, x2, y2 - r, x2, y2, x2 - r, y2, x1 + r, y2,
               x1, y2, x1, y2 - r, x1, y1 + r, x1, y1]
        return self.canvas.create_polygon(pts, smooth=True, **kw)

    def redraw(self):
        cv, c, R, W = self.canvas, self.c, self.ROW, self.W
        cv.delete("all")
        if not self.results:
            query = self.q.get().strip()
            cv.create_text(W / 2, R * 3.2, text=f"No stations match “{query}”", font=self.name_font,
                           fill=c["fg"])
            cv.create_text(W / 2, R * 3.2 + 26, text="Try part of a name, like “bedford”, or lines, like “jmz”",
                           font=self.sub_font, fill=c["sub"])
            cv.configure(scrollregion=(0, 0, W, R * self.VISIBLE))
            return
        for i, st in enumerate(self.results):
            y = i * R
            if i == self.sel:
                self.round_rect(2, y + 3, W - 2, y + R - 3, 8, fill=c["card"], outline="")
                self.round_rect(4, y + R / 2 - 8, 7, y + R / 2 + 8, 2, fill=c["accent"], outline="")
            elif i == self.hover:
                self.round_rect(2, y + 3, W - 2, y + R - 3, 8, fill=self.hover_bg, outline="")
            strip = self.bullet_strip(st["routes"])
            more = len(st["routes"]) - 6
            right = W - 14
            if more > 0:
                cv.create_text(right, y + R / 2, text=f"+{more}", font=self.sub_font, fill=c["sub"], anchor="e")
                right -= self.sub_font.measure(f"+{more}") + 6
            cv.create_image(right, y + R / 2, image=strip, anchor="e")
            max_w = right - strip.width() - 18 - 20
            cv.create_text(20, y + R / 2 - 9, text=self._fit(st["name"], self.name_font, max_w),
                           font=self.name_font, fill=c["fg"], anchor="w")
            sub = " · ".join(x for x in (BOROUGHS.get(st["boro"], ""), st["line"].split(" / ")[0]) if x)
            cv.create_text(20, y + R / 2 + 11, text=self._fit(sub, self.sub_font, max_w),
                           font=self.sub_font, fill=c["sub"], anchor="w")
        cv.configure(scrollregion=(0, 0, W, max(R * self.VISIBLE, R * len(self.results))))

    @staticmethod
    def _fit(text, fnt, max_w):
        if fnt.measure(text) <= max_w:
            return text
        while text and fnt.measure(text + "…") > max_w:
            text = text[:-1]
        return text.rstrip() + "…"

    # input
    def _index_at(self, y):
        i = int(self.canvas.canvasy(y) // self.ROW)
        return i if 0 <= i < len(self.results) else None

    def _motion(self, e):
        self._set_hover(self._index_at(e.y))

    def _set_hover(self, i):
        if i != self.hover:
            self.hover = i
            self.redraw()

    def move(self, delta):
        if not self.results:
            return "break"
        self.sel = max(0, min(self.sel + delta, len(self.results) - 1))
        top = self.canvas.canvasy(0) / self.ROW
        if self.sel < top:
            self.canvas.yview_moveto(self.sel / len(self.results))
        elif self.sel >= top + self.VISIBLE:
            self.canvas.yview_moveto((self.sel - self.VISIBLE + 1) / len(self.results))
        self.redraw()
        return "break"

    def choose(self, i):
        if i is None or not self.results:
            return
        self.app.choose_station(self.results[i])
        self.destroy()


def palette():
    if light_theme():
        return dict(bg="#f3f3f3", card="#ffffff", line="#e5e5e5", fg="#1a1a1a", sub="#5f5f5f",
                    accent="#005fb8", on_accent="#ffffff", off="#8a8a8a", dark=False)
    return dict(bg="#202020", card="#2b2b2b", line="#1d1d1d", fg="#ffffff", sub="#a6a6a6",
                accent="#60cdff", on_accent="#000000", off="#9a9a9a", dark=True)


class SettingsWindow(tk.Toplevel):
    """Every option in one place, styled after Windows 11 Settings. Changes apply at once."""

    def __init__(self, app):
        super().__init__(app.root)
        self.app = app
        self.c = c = palette()
        self.title("Trainbar settings")
        self.configure(bg=c["bg"], padx=20, pady=18)
        self.attributes("-topmost", True)
        self.resizable(False, False)
        self.f = {
            "title": ("Segoe UI Variable Display", 15, "bold"),
            "head": ("Segoe UI Variable Text", 10, "bold"),
            "body": ("Segoe UI Variable Text", 10),
            "small": ("Segoe UI Variable Text", 9),
        }
        self.bind("<Escape>", lambda e: self.destroy())
        self.body = tk.Frame(self, bg=c["bg"])
        self.body.pack(fill="both")
        self.refresh()
        self.update_idletasks()
        dark = ctypes.c_int(1 if c["dark"] else 0)
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(user32.GetParent(self.winfo_id()), 20,
                                                       ctypes.byref(dark), ctypes.sizeof(dark))
        except OSError:
            pass
        x = min(app.root.winfo_x(), self.winfo_screenwidth() - self.winfo_width() - 12)
        y = app.root.winfo_y() - self.winfo_height() - 48
        self.geometry(f"+{max(0, x)}+{max(0, y)}")
        self.focus_force()

    # building blocks
    def section(self, title):
        tk.Label(self.body, text=title, font=self.f["head"], bg=self.c["bg"], fg=self.c["fg"],
                 anchor="w").pack(fill="x", pady=(14, 6))
        card = tk.Frame(self.body, bg=self.c["card"])
        card.pack(fill="x")
        return card

    def row(self, card, label, hint=None):
        if card.winfo_children():
            tk.Frame(card, bg=self.c["line"], height=1).pack(fill="x")
        r = tk.Frame(card, bg=self.c["card"], padx=14, pady=10)
        r.pack(fill="x")
        text = tk.Frame(r, bg=self.c["card"])
        text.pack(side="left", fill="x", expand=True)
        tk.Label(text, text=label, font=self.f["body"], bg=self.c["card"], fg=self.c["fg"],
                 anchor="w").pack(fill="x")
        if hint:
            tk.Label(text, text=hint, font=self.f["small"], bg=self.c["card"], fg=self.c["sub"],
                     anchor="w", justify="left", wraplength=230).pack(fill="x")
        right = tk.Frame(r, bg=self.c["card"])
        right.pack(side="right")
        return right

    def button(self, parent, text, command, selected=False):
        c = self.c
        bg, fg = (c["accent"], c["on_accent"]) if selected else (c["bg"], c["fg"])
        b = tk.Label(parent, text=text, font=self.f["body"], bg=bg, fg=fg, padx=10, pady=4, cursor="hand2")
        b.bind("<Button-1>", lambda e: command())
        return b

    def segmented(self, parent, options, current, key):
        for code, label in options:
            self.button(parent, label, lambda v=code: self.app._set(key, v),
                        selected=code == current).pack(side="left", padx=(4, 0))

    def stepper(self, parent, key, value, lo, hi, unit):
        def change(delta):
            self.app._set(key, max(lo, min(hi, value + delta)))
        self.button(parent, "−", lambda: change(-1)).pack(side="left")
        tk.Label(parent, text=f"{value} {unit}", font=self.f["body"], bg=self.c["card"], fg=self.c["fg"],
                 width=6).pack(side="left")
        self.button(parent, "+", lambda: change(1)).pack(side="left")

    def toggle(self, parent, value, command):
        c = self.c
        cv = tk.Canvas(parent, width=40, height=20, bg=c["card"], highlightthickness=0, cursor="hand2")
        fill = c["accent"] if value else c["card"]
        outline = c["accent"] if value else c["off"]
        cv.create_oval(1, 1, 19, 19, fill=fill, outline=outline)
        cv.create_oval(21, 1, 39, 19, fill=fill, outline=outline)
        cv.create_rectangle(10, 1, 30, 19, fill=fill, outline=fill)
        cv.create_line(10, 1, 30, 1, fill=outline)
        cv.create_line(10, 19, 30, 19, fill=outline)
        knob = (24, 5, 34, 15) if value else (6, 5, 16, 15)
        cv.create_oval(*knob, fill=c["on_accent"] if value else c["off"], outline="")
        cv.bind("<Button-1>", lambda e: command(not value))
        cv.pack()

    def refresh(self):
        """Rebuild from the current config so the window always shows the truth."""
        for w in self.body.winfo_children():
            w.destroy()
        app, cfg, c = self.app, self.app.cfg, self.c
        tk.Label(self.body, text="Trainbar", font=self.f["title"], bg=c["bg"], fg=c["fg"],
                 anchor="w").pack(fill="x")

        card = self.section("Station")
        st = app.station
        right = self.row(card, st["name"] if st else "No station yet",
                         "  ".join(st["routes"]) if st else "Pick the stop you leave from")
        self.button(right, "Change…", app.open_picker).pack()
        right = self.row(card, "Direction")
        self.segmented(right, app._directions(), cfg.get("dir", "B"), "dir")

        card = self.section("Look")
        right = self.row(card, "Theme")
        self.segmented(right, THEMES, app.theme, "theme")
        right = self.row(card, "Show background", "Otherwise it appears only when you point at it")
        self.toggle(right, cfg.get("background", False), lambda v: app._set("background", v))

        if app.theme == "countdown":
            card = self.section("Countdown")
            right = self.row(card, "Trains to show", "One at a time, soonest first")
            self.stepper(right, "cycle_count", cfg.get("cycle_count", 4), 1, 8, "")
            right = self.row(card, "Time on each train")
            self.stepper(right, "cycle_seconds", cfg.get("cycle_seconds", 5), 2, 30, "s")
            right = self.row(card, "Slide animation", "Also follows the Windows animation setting")
            self.toggle(right, cfg.get("animate", True), lambda v: app._set("animate", v))

        card = self.section("System")
        right = self.row(card, "Start with Windows", "Copies Trainbar to %LOCALAPPDATA%\\Trainbar")
        self.toggle(right, autostart_enabled(), lambda v: (set_autostart(v), self.refresh()))
        locked = cfg.get("locked", False)
        right = self.row(card, "Lock position", "Stops dragging from moving it" if locked
                         else "Drag the widget sideways to move it")
        self.toggle(right, locked, lambda v: app._set("locked", v))
        right = self.row(card, "Position", "Put it back next to the tray icons")
        self.button(right, "Reset", lambda: (app._reset_pos(), self.refresh())).pack()
        right = self.row(card, "Data", "MTA real-time feeds, checked every 30 seconds")
        self.button(right, "Refresh now", app.feed.refresh).pack()

        foot = tk.Frame(self.body, bg=c["bg"])
        foot.pack(fill="x", pady=(14, 0))
        tk.Label(foot, text=f"Trainbar {__version__}", font=self.f["small"], bg=c["bg"], fg=c["sub"]).pack(
            side="left")
        for text, url in (("Report a problem", HOMEPAGE + "/issues"), ("Website", HOMEPAGE)):
            link = tk.Label(foot, text=text, font=self.f["small"], bg=c["bg"], fg=c["accent"], cursor="hand2")
            link.bind("<Button-1>", lambda e, u=url: webbrowser.open(u))
            link.pack(side="right", padx=(12, 0))


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:
        pass
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\TrainbarTaskbar")
    if ctypes.windll.kernel32.GetLastError() == 183:  # already running
        return
    try:
        App().run()
    except Exception as e:
        log_error(*sys.exc_info())
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("Trainbar", f"Trainbar could not start.\n\n{e}\n\nDetails are in {DATA_DIR}\\error.log")
    del mutex


if __name__ == "__main__":
    main()
