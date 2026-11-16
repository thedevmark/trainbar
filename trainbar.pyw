"""Trainbar: live NYC subway arrivals on the Windows taskbar.

Live data comes from the MTA's public GTFS-realtime feeds (no API key).
Right-click it for theme, station, direction and startup options.
Drag it sideways to move it. Click it to refresh.
"""

import csv
import ctypes
import json
import os
import shutil
import sys
import threading
import time
import tkinter as tk
import traceback
import urllib.request
import winreg
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageFont

APP = "Trainbar"
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
THEMES = [("clock", "Clock"), ("led", "LED"), ("minimal", "Minimal")]

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


def light_theme():
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
        self.text((cx, cy + d / 32), label, font("Bold", round(d * 0.66)), ink, opacity, "mm", shadow=False)

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
    content = draw(rows, message, height, k, ink, light)
    w = content.width
    out = Image.new("RGBA", (w, height), (0, 0, 0, 1))  # alpha 1 keeps the whole area clickable
    if hover or background:
        p = Painter(w, height)
        p.shape(lambda d, s: d.rounded_rectangle([2 * k * s, 4 * k * s, (w - 2 * k) * s, (height - 4 * k) * s],
                                                 radius=6 * k * s, fill=255),
                ink, 0.11 if hover else 0.06, shadow=False)
        out.alpha_composite(p.compose(0))
    out.alpha_composite(content)
    return out


def message_only(message, height, k, ink, light, width):
    p = Painter(width, height)
    p.text((width / 2, height / 2), message, font("Regular", round(12 * k)), ink, 0.65, "mm")
    return p.compose(0 if light else 0.5)


def render_clock(rows, message, height, k, ink, light):
    """Two quiet lines, set like the Windows 11 taskbar clock."""
    w = round(206 * k)
    if message:
        return message_only(message, height, k, ink, light, w)
    p = Painter(w, height)
    body, strong = font("Regular", round(12 * k)), font("Semibold", round(12 * k))
    pad, d, unit = 12 * k, 16 * k, " min"
    right = w - pad
    dest_x = pad + d + 8 * k
    dest_max = right - dest_x - strong.getlength("99") - body.getlength(unit) - 8 * k
    centers = [height / 2 - 8.5 * k, height / 2 + 8.5 * k] if len(rows) > 1 else [height / 2]
    for (route, dest, mins, stale), cy in zip(rows, centers):
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
    """One capsule per train: the line and the minutes, nothing else."""
    if message:
        return message_only(message, height, k, ink, light, round(150 * k))
    strong, body = font("Semibold", round(13 * k)), font("Regular", round(11 * k))
    d, ch, gap, pad = 18 * k, 28 * k, 6 * k, 6 * k
    chips = []
    for route, dest, mins, stale in rows:
        num, unit = ("now", "") if mins == 0 else (str(min(mins, 99)), " min")
        cw = 5 * k + d + 7 * k + strong.getlength(num) + body.getlength(unit) + 11 * k
        chips.append((route, num, unit, stale, cw))
    w = round(pad * 2 + sum(c[4] for c in chips) + gap * (len(chips) - 1))
    p = Painter(w, height)
    x, cy = pad, height / 2
    for route, num, unit, stale, cw in chips:
        fade = 0.5 if stale else 1.0
        x0, top = x, cy - ch / 2
        p.shape(lambda g, s, x0=x0, cw=cw, top=top: g.rounded_rectangle(
            [x0 * s, top * s, (x0 + cw) * s, (top + ch) * s], radius=ch / 2 * s, fill=255),
            ink, 0.13 * fade, shadow=False)
        p.bullet(route, x0 + 5 * k + d / 2, cy, d, fade)
        tx = x0 + 5 * k + d + 7 * k
        p.text((tx, cy), num, strong, ink, fade)
        if unit:
            p.text((tx + strong.getlength(num), cy + 0.5 * k), unit, body, ink, 0.6 * fade)
        x += cw + gap
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
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(os.path.join(DATA_DIR, "error.log"), "a", encoding="utf-8") as f:
            f.write(time.strftime("%Y-%m-%d %H:%M:%S ") + "".join(traceback.format_exception(exc_type, exc, tb)))
    except OSError:
        pass


def http_get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "Trainbar/1.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


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
            self.wake.wait(POLL_SECONDS)
            self.wake.clear()

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
            else:
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
            # Copy the script somewhere permanent so startup never points at a moved file.
            os.makedirs(INSTALL_DIR, exist_ok=True)
            target = os.path.join(INSTALL_DIR, "trainbar.pyw")
            if os.path.abspath(__file__) != os.path.abspath(target):
                shutil.copyfile(__file__, target)
            exe = sys.executable
            if exe.lower().endswith("python.exe"):
                exe = exe[:-10] + "pythonw.exe"
            winreg.SetValueEx(k, APP, 0, winreg.REG_SZ, f'"{exe}" "{target}"')
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
        self.surface = tk.Frame(self.root, bd=0, highlightthickness=0, bg="black", cursor="hand2")
        self.surface.pack(fill="both", expand=True)
        self.size = (1, 1)
        self.hover = False
        self.picker = None
        self.drag = None
        self.hidden = False

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

    # -- config
    def _load_cfg(self):
        try:
            with open(CONFIG_PATH, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return {"dir": "B"}

    def _save_cfg(self):
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(self.cfg, f, indent=2)

    @property
    def station(self):
        return self.complexes.get(self.cfg.get("station"))

    # -- drawing
    def rows(self):
        station = self.station
        if not station:
            return None, "Choose a station"
        arrivals, last_ok, error = self.feed.snapshot()
        if not last_ok:
            return None, "Offline" if error else "Loading…"
        now = time.time()
        live = [a for a in arrivals if a[2] >= now - 20]
        mode = self.cfg.get("dir", "B")
        if mode == "B":
            picks = [next((a for a in live if a[0].endswith(d)), None) for d in "NS"]
            picks = [p for p in picks if p] or live[:2]
        else:
            picks = [a for a in live if a[0].endswith(mode)][:2]
        if not picks:
            return None, "No trains"
        stale = now - last_ok > 180
        rows = []
        for sid, route, t, dest in picks:
            route = {"GS": "S", "FS": "S", "SI": "SIR"}.get(route, route)
            name = "Last stop" if dest[:-1] in station["stops"] else short_name(self.names.get(dest[:-1], dest))
            rows.append((route, name, max(0, int((t - now) // 60)), stale))
        return rows, None

    def draw(self):
        rows, message = self.rows()
        img = render(self.cfg.get("theme", "clock"), rows, message, self._bar_height(),
                     hover=self.hover or bool(self.drag), background=self.cfg.get("background", False))
        if img.size != self.size:
            self.size = img.size
            self._place()
        present_layered(self._hwnd(), img)

    def _set_hover(self, on):
        self.hover = on
        self.draw()

    def _bar_height(self):
        g = taskbar_geometry()
        return (g[3] - g[1]) if g else 48

    def _tick(self):
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
        if fullscreen_app_in_front():
            if not self.hidden:
                self.root.withdraw()
                self.hidden = True
        else:
            if self.hidden:
                self.root.deiconify()
                self.hidden = False
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
        if abs(dx) > 3:
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
            for code, label in (("N", st["north"] or "Uptown"), ("S", st["south"] or "Downtown"),
                                ("B", "Both directions")):
                m.add_radiobutton(label=label, value=code, variable=var,
                                  command=lambda c=code: self._set("dir", c))
            m.add_separator()
        themes = tk.Menu(m, tearoff=0)
        tvar = tk.StringVar(value=self.cfg.get("theme", "clock"))
        for code, label in THEMES:
            themes.add_radiobutton(label=label, value=code, variable=tvar,
                                   command=lambda c=code: self._set("theme", c))
        themes.add_separator()
        bg = tk.BooleanVar(value=self.cfg.get("background", False))
        themes.add_checkbutton(label="Show background", variable=bg,
                               command=lambda: self._set("background", bg.get()))
        m.add_cascade(label="Theme", menu=themes)
        m.add_command(label="Change station…", command=self.open_picker)
        m.add_command(label="Refresh now", command=self.feed.refresh)
        if "offset" in self.cfg:
            m.add_command(label="Reset position", command=self._reset_pos)
        auto = tk.BooleanVar(value=autostart_enabled())
        m.add_checkbutton(label="Start with Windows", variable=auto,
                          command=lambda: set_autostart(auto.get()))
        m.add_separator()
        m.add_command(label="Quit", command=self.root.destroy)
        m.tk_popup(e.x_root, e.y_root)

    def _set(self, key, value):
        self.cfg[key] = value
        self._save_cfg()
        self.draw()

    def _reset_pos(self):
        self.cfg.pop("offset", None)
        self._save_cfg()
        self._place()

    # -- station picker
    def open_picker(self):
        if self.picker and self.picker.winfo_exists():
            self.picker.lift()
            return
        light = light_theme()
        bg, field, fg, sub = (("#f3f3f3", "#ffffff", "#1a1a1a", "#5f5f5f") if light
                              else ("#202020", "#2d2d2d", "#ffffff", "#a0a0a0"))
        accent = "#005fb8" if light else "#60cdff"
        p = self.picker = tk.Toplevel(self.root)
        p.title("Choose a station")
        p.configure(bg=bg, padx=16, pady=16)
        p.attributes("-topmost", True)
        p.resizable(False, False)
        ui = ("Segoe UI Variable Text", 11)
        q = tk.StringVar()
        entry = tk.Entry(p, textvariable=q, font=ui, bg=field, fg=fg, insertbackground=fg, relief="flat",
                         highlightthickness=1, highlightbackground=field, highlightcolor=accent)
        entry.pack(fill="x", ipady=7)
        tk.Label(p, text="Search by name or line, like “bedford l”", font=("Segoe UI Variable Text", 9),
                 bg=bg, fg=sub, anchor="w").pack(fill="x", pady=(6, 8))
        lb = tk.Listbox(p, font=ui, width=42, height=11, bg=bg, fg=fg, selectbackground=field,
                        selectforeground=fg, relief="flat", highlightthickness=0, activestyle="none", bd=0)
        lb.pack(fill="both")
        ordered = sorted(self.complexes.values(), key=lambda c: (c["name"], c["routes"]))
        shown = []

        def update(*_):
            tokens = q.get().lower().split()
            shown.clear()
            lb.delete(0, "end")
            for c in ordered:
                hay = c["name"].lower()
                if all(tok in hay or tok.upper() in c["routes"] for tok in tokens):
                    shown.append(c)
                    lb.insert("end", f"  {c['name']}     {'  '.join(c['routes'])}")
            if shown:
                lb.selection_set(0)

        def choose(*_):
            sel = lb.curselection()
            if not sel:
                return
            c = shown[sel[0]]
            self.cfg["station"] = c["id"]
            self.cfg["dir"] = "B"
            self._save_cfg()
            self.feed.set_station(c)
            self.draw()
            p.destroy()

        def move(delta):
            sel = lb.curselection()
            i = max(0, min((sel[0] if sel else 0) + delta, len(shown) - 1))
            lb.selection_clear(0, "end")
            lb.selection_set(i)
            lb.see(i)
            return "break"

        q.trace_add("write", update)
        entry.bind("<Return>", choose)
        entry.bind("<Down>", lambda e: move(1))
        entry.bind("<Up>", lambda e: move(-1))
        lb.bind("<Double-Button-1>", choose)
        lb.bind("<Return>", choose)
        p.bind("<Escape>", lambda e: p.destroy())
        update()
        p.update_idletasks()
        dark = ctypes.c_int(0 if light else 1)  # title bar follows the system theme
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(user32.GetParent(p.winfo_id()), 20,
                                                       ctypes.byref(dark), ctypes.sizeof(dark))
        except OSError:
            pass
        x = min(self.root.winfo_x(), p.winfo_screenwidth() - p.winfo_width() - 12)
        y = self.root.winfo_y() - p.winfo_height() - 48
        p.geometry(f"+{max(0, x)}+{max(0, y)}")
        entry.focus_force()

    def run(self):
        self.root.mainloop()


def main():
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except OSError:
        pass
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\TrainbarTaskbar")
    if ctypes.windll.kernel32.GetLastError() == 183:  # already running
        return
    App().run()
    del mutex


if __name__ == "__main__":
    main()
