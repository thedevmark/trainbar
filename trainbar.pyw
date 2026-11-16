"""Trainbar: a dot-matrix NYC subway countdown that sits on the Windows taskbar.

Live data comes from the MTA's public GTFS-realtime feeds (no API key).
Right-click the display for station, direction and startup options.
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
import urllib.request
import winreg
from concurrent.futures import ThreadPoolExecutor
from ctypes import wintypes

from PIL import Image, ImageChops, ImageDraw, ImageFilter, ImageTk

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

BG = (5, 5, 5)
OFF = (22, 22, 22)
DEST = (92, 232, 120)
MINS = (226, 234, 255)
DIM = (110, 110, 110)
ROUTE_COLORS = {
    **{r: (238, 53, 46) for r in "123"},
    **{r: (0, 147, 60) for r in "456"},
    "7": (185, 51, 173),
    **{r: (40, 110, 255) for r in "ACE"},
    **{r: (255, 99, 25) for r in "BDFM"},
    "G": (108, 190, 69),
    **{r: (196, 132, 70) for r in "JZ"},
    "L": (167, 169, 172),
    **{r: (252, 204, 10) for r in "NQRW"},
    "S": (128, 129, 131), "H": (128, 129, 131), "SIR": (40, 110, 255),
}

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
        color = ROUTE_COLORS.get(route, ROUTE_COLORS["S"])
        letter = mini(route[0] if route != "SIR" else "S")
        for r, row in enumerate(BULLET):
            for c, b in enumerate(row):
                cut = letter and 1 <= r <= 5 and 2 <= c <= 4 and letter[r - 1][c - 2] == "#"
                if b == "#" and not cut:
                    self.set(x + c, y + r, color)


def render(dots, height):
    pitch = max(3, height // 16)
    size = max(2, round(pitch * 0.68))
    rows = ROW_H * 2 + 1
    pad = (height - rows * pitch) // 2
    w = COLS * pitch + pad * 2 + pitch
    base = Image.new("RGB", (w, height), BG)
    glow = Image.new("RGB", (w, height), (0, 0, 0))
    d, g = ImageDraw.Draw(base), ImageDraw.Draw(glow)
    round_dots = size >= 4
    for y in range(rows):
        for x in range(COLS):
            px = pad + pitch // 2 + x * pitch + (pitch - size) // 2
            py = pad + y * pitch + (pitch - size) // 2
            box = [px, py, px + size - 1, py + size - 1]
            color = dots.lit.get((x, y), OFF)
            (d.ellipse if round_dots else d.rectangle)(box, fill=color)
            if (x, y) in dots.lit:
                g.rectangle(box, fill=color)
    # Soft bloom: add a blurred, dimmed copy of the lit dots.
    glow = glow.filter(ImageFilter.GaussianBlur(pitch * 0.9)).point(lambda v: int(v * 0.55))
    return ImageChops.add(base, glow)


# ---------------------------------------------------------------- data

def http_get(url, timeout=12):
    req = urllib.request.Request(url, headers={"User-Agent": "Trainbar/1.0"})
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
    mv = memoryview(data)
    for f, entity in pb_fields(mv):
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
    "Coney Island-Stillwell Av": "CONEY IS", "Van Cortlandt Park-242 St": "242 ST",
    "Flatbush Av-Brooklyn College": "FLATBUSH", "Inwood-207 St": "207 ST",
    "Jamaica Center-Parsons/Archer": "JAMAICA", "Jamaica-179 St": "179 ST",
    "Harlem-148 St": "148 ST", "Pelham Bay Park": "PELHAM BAY", "Eastchester-Dyre Av": "DYRE AV",
    "Crown Hts-Utica Av": "UTICA AV", "Brooklyn Bridge-City Hall": "BKLYN BRG",
    "Flushing-Main St": "FLUSHING", "34 St-Hudson Yards": "HUDSON YD", "South Ferry": "S FERRY",
    "Astoria-Ditmars Blvd": "DITMARS", "Forest Hills-71 Av": "FOREST HLS",
    "Bay Ridge-95 St": "BAY RIDGE", "Whitehall St-South Ferry": "WHITEHALL",
    "Canarsie-Rockaway Pkwy": "CANARSIE", "Middle Village-Metropolitan Av": "MIDDLE VLG",
    "Norwood-205 St": "205 ST", "Far Rockaway-Mott Av": "FAR ROCK",
    "Rockaway Park-Beach 116 St": "ROCKAWAY", "Ozone Park-Lefferts Blvd": "LEFFERTS",
    "World Trade Center": "WTC", "Wakefield-241 St": "WAKEFIELD", "Woodlawn": "WOODLAWN",
    "Court Sq-23 St": "COURT SQ", "8 Av": "8 AV", "Times Sq-42 St": "TIMES SQ",
    "Grand Central-42 St": "GRAND CTRL", "Broad Channel": "BROAD CHNL",
    "Jamaica-Van Wyck": "VAN WYCK", "Bedford Park Blvd": "BEDFORD PK",
    "Lexington Av/59 St": "LEX AV", "Church Av": "CHURCH AV", "Kings Hwy": "KINGS HWY",
}
ABBR = [("AVENUE", "AV"), ("STREET", "ST"), ("ISLAND", "IS"), ("PARKWAY", "PKWY"),
        ("BOULEVARD", "BLVD"), ("SQUARE", "SQ"), ("CENTER", "CTR"), ("JUNCTION", "JCT"),
        ("HEIGHTS", "HTS"), ("PARK", "PK"), ("ROAD", "RD"), ("PLAZA", "PLZ")]


def short_name(name, max_dots):
    s = SHORT.get(name)
    if s is None:
        s = name.split("-")[0].split("/")[0].upper()
        for a, b in ABBR:
            s = s.replace(a, b)
    s = "".join(ch for ch in s if ch == " " or ch in FONT5x7)
    while s and text_width(s) > max_dots:
        s = s[:-1].rstrip()
    return s


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
            self._poll()
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

        results, errors = [], 0
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
                    key = (a[0], a[1], a[2])
                    if key not in seen:
                        seen.add(key)
                        uniq.append(a)
                self.arrivals = uniq
        self.on_update()

    def snapshot(self):
        with self.lock:
            return list(self.arrivals), self.last_ok, self.error


# ---------------------------------------------------------------- windows plumbing

user32 = ctypes.windll.user32
HWND_TOPMOST = -1
SWP_NOSIZE, SWP_NOMOVE, SWP_NOACTIVATE, SWP_SHOWWINDOW = 0x1, 0x2, 0x10, 0x40
GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW, WS_EX_NOACTIVATE, WS_EX_APPWINDOW = 0x80, 0x08000000, 0x40000
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
INSTALL_DIR = os.path.join(os.environ["LOCALAPPDATA"], APP)


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
    if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
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
        self.root.configure(bg="#050505")
        self.label = tk.Label(self.root, bd=0, highlightthickness=0, bg="#050505", cursor="hand2")
        self.label.pack()
        self.photo = None
        self.blink = False
        self.picker = None
        self.drag = None
        self.hidden = False

        self.label.bind("<ButtonPress-1>", self._press)
        self.label.bind("<B1-Motion>", self._motion)
        self.label.bind("<ButtonRelease-1>", self._release)
        self.label.bind("<Button-3>", self._menu)

        self.feed = Feed(lambda: self.root.after(0, self.draw))
        station = self.complexes.get(self.cfg.get("station"))
        self.draw()
        self._place()
        self.root.deiconify()
        self.root.update_idletasks()
        self._style_window()
        if station:
            self.feed.set_station(station)
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
            return None, "PICK A STOP"
        arrivals, last_ok, error = self.feed.snapshot()
        if not last_ok:
            return None, "OFFLINE" if error else "LOADING"
        now = time.time()
        live = [a for a in arrivals if a[2] >= now - 20]
        mode = self.cfg.get("dir", "B")
        if mode == "B":
            picks = [next((a for a in live if a[0].endswith(d)), None) for d in "NS"]
            picks = [p for p in picks if p] or live[:2]
        else:
            picks = [a for a in live if a[0].endswith(mode)][:2]
        if not picks:
            return None, "NO TRAINS"
        stale = now - last_ok > 180
        return [(a, stale) for a in picks], None

    def draw(self):
        dots = Dots()
        rows, message = self.rows()
        right = COLS - 2
        if message:
            dots.text(message, (COLS - text_width(message)) // 2, 4, DIM)
        else:
            now = time.time()
            for i, (a, stale) in enumerate(rows):
                sid, route, t, dest = a
                y = i * (ROW_H + 1)
                route = route.rstrip("X") if route not in ("SIR",) else route
                route = {"GS": "S", "FS": "S", "SI": "SIR"}.get(route, route)
                dots.bullet(route, 1, y)
                mins = max(0, int((t - now) // 60))
                dest_name = short_name(self.names.get(dest[:-1], dest), COLS - 10 - 21)
                if dest[:-1] in self.station["stops"]:
                    dest_name = "LAST STOP"
                dim = (lambda c: tuple(v // 2 for v in c)) if stale else (lambda c: c)
                dots.text(dest_name, 10, y, dim(DEST))
                if mins == 0:
                    if not self.blink:
                        w = text_width("NOW")
                        dots.text("NOW", right - w + 1, y, dim(MINS))
                else:
                    dots.digits(str(min(mins, 99)), right, y, dim(MINS))
        h = self._bar_height()
        img = render(dots, h)
        self.photo = ImageTk.PhotoImage(img)
        self.label.configure(image=self.photo)
        self.size = img.size

    def _bar_height(self):
        g = taskbar_geometry()
        return (g[3] - g[1]) if g else 48

    def _tick(self):
        self.blink = not self.blink
        self.draw()
        self.root.after(500, self._tick)

    # -- placement
    def _place(self):
        g = taskbar_geometry()
        w, h = self.size
        if not g:
            return
        l, t, r, b, tray_left = g
        offset = self.cfg.get("offset")
        x = r - offset if offset is not None else tray_left - w - 12
        x = max(l, min(x, r - w))
        y = t + (b - t - h) // 2
        self.root.geometry(f"{w}x{h}+{x}+{y}")

    def _hwnd(self):
        return user32.GetParent(self.label.winfo_toplevel().winfo_id())

    def _style_window(self):
        hwnd = self._hwnd()
        ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, (ex | WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE) & ~WS_EX_APPWINDOW)
        pref = ctypes.c_int(3)  # DWMWCP_ROUNDSMALL
        try:
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 33, ctypes.byref(pref), ctypes.sizeof(pref))
        except OSError:
            pass

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
                                  command=lambda c=code: self._set_dir(c))
            m.add_separator()
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

    def _set_dir(self, code):
        self.cfg["dir"] = code
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
        p = self.picker = tk.Toplevel(self.root)
        p.title("Trainbar")
        p.configure(bg="#111111", padx=14, pady=14)
        p.attributes("-topmost", True)
        p.resizable(False, False)
        font = ("Segoe UI Variable Text", 11)
        q = tk.StringVar()
        entry = tk.Entry(p, textvariable=q, font=font, bg="#1c1c1c", fg="#f2f2f2",
                         insertbackground="#f2f2f2", relief="flat", highlightthickness=1,
                         highlightbackground="#2a2a2a", highlightcolor="#5ce878")
        entry.pack(fill="x", ipady=6)
        lb = tk.Listbox(p, font=font, width=40, height=12, bg="#111111", fg="#e6e6e6",
                        selectbackground="#5ce878", selectforeground="#050505",
                        relief="flat", highlightthickness=0, activestyle="none", bd=0)
        lb.pack(fill="both", pady=(10, 0))
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
                    lb.insert("end", f"{c['name']}    {' '.join(c['routes'])}")
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
        dark = ctypes.c_int(1)  # dark title bar to match
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
