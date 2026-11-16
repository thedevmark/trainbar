"""Tests for Trainbar's pure logic. Run with: python -m unittest discover tests

They need Windows (the module talks to user32 at import) but no network and no window.
"""

import importlib.machinery
import importlib.util
import pathlib
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
_loader = importlib.machinery.SourceFileLoader("trainbar", str(ROOT / "trainbar.pyw"))
_spec = importlib.util.spec_from_loader("trainbar", _loader)
tb = importlib.util.module_from_spec(_spec)
_loader.exec_module(tb)


# --- a tiny protobuf writer, enough to build GTFS-realtime test feeds

def varint(n):
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def field(num, value):
    if isinstance(value, int):
        return varint(num << 3) + varint(value)
    data = value.encode() if isinstance(value, str) else value
    return varint(num << 3 | 2) + varint(len(data)) + data


def trip_update(route, stops):
    """stops: [(stop_id, arrival_epoch)]"""
    trip = field(1, field(1, f"trip-{route}") + field(5, route))
    updates = b"".join(field(2, field(4, sid) + field(2, field(2, t))) for sid, t in stops)
    return field(2, field(1, "entity") + field(3, trip + updates))


def feed(*entities):
    header = field(1, field(1, "2.0") + field(3, 1_700_000_000))
    return header + b"".join(entities)


STATION = {"id": "1", "name": "Bedford Av", "stops": ["L08"], "routes": ["L"], "line": "Canarsie",
           "boro": "Bk", "north": "Manhattan", "south": "Canarsie"}
NAMES = {"L01": "8 Av", "L08": "Bedford Av", "L29": "Canarsie-Rockaway Pkwy"}


class ProtobufTest(unittest.TestCase):
    def test_varint_round_trip(self):
        for n in (0, 1, 127, 128, 300, 1_700_000_000, 2**40):
            self.assertEqual(tb._varint(varint(n), 0), (n, len(varint(n))))

    def test_parse_feed_finds_arrivals_and_destination(self):
        data = feed(
            trip_update("L", [("L06S", 100), ("L08S", 200), ("L29S", 900)]),
            trip_update("L", [("L10N", 150), ("L08N", 300), ("L01N", 800)]),
            trip_update("G", [("G29N", 120)]),
        )
        got = tb.parse_feed(data, {"L08"})
        self.assertEqual(sorted(got), [("L08N", "L", 300, "L01N"), ("L08S", "L", 200, "L29S")])

    def test_unknown_wire_type_raises(self):
        with self.assertRaises(ValueError):
            list(tb.pb_fields(bytes([0x0B])))  # field 1, wire type 3 (groups)


class PickRowsTest(unittest.TestCase):
    now = 1_000_000

    def arrivals(self):
        n = self.now
        return [("L08N", "L", n + 60, "L01N"), ("L08S", "L", n + 120, "L29S"),
                ("L08N", "L", n + 400, "L01N"), ("L08S", "L", n + 700, "L29S")]

    def test_both_directions_shows_next_each_way(self):
        rows = tb.pick_rows(self.arrivals(), STATION, NAMES, self.now, self.now, "B")
        self.assertEqual(rows, [("L", "8 Av", 1, False), ("L", "Canarsie", 2, False)])

    def test_one_direction_shows_next_two(self):
        rows = tb.pick_rows(self.arrivals(), STATION, NAMES, self.now, self.now, "S")
        self.assertEqual([r[2] for r in rows], [2, 11])

    def test_rotation_is_soonest_first(self):
        rows = tb.pick_rows(self.arrivals(), STATION, NAMES, self.now, self.now, "B", rotate=True, limit=3)
        self.assertEqual([r[2] for r in rows], [1, 2, 6])

    def test_departed_trains_drop_out_and_old_data_is_stale(self):
        arrivals = [("L08N", "L", self.now - 60, "L01N"), ("L08N", "L", self.now + 30, "L01N")]
        rows = tb.pick_rows(arrivals, STATION, NAMES, self.now, self.now - 600, "N")
        self.assertEqual(rows, [("L", "8 Av", 0, True)])

    def test_terminating_train_says_last_stop(self):
        rows = tb.pick_rows([("L08N", "L", self.now + 90, "L08N")], STATION, NAMES, self.now, self.now, "N")
        self.assertEqual(rows[0][1], "Last stop")

    def test_shuttle_ids_are_normalised(self):
        rows = tb.pick_rows([("L08N", "GS", self.now + 90, "L01N")], STATION, NAMES, self.now, self.now, "N")
        self.assertEqual(rows[0][0], "S")


class SearchTest(unittest.TestCase):
    complexes = {
        "1": {"name": "Myrtle Av", "line": "Jamaica", "boro": "Bk", "routes": ["M", "J", "Z"]},
        "2": {"name": "Bedford Av", "line": "Canarsie", "boro": "Bk", "routes": ["L"]},
        "3": {"name": "Bedford Park Blvd", "line": "Concourse", "boro": "Bx", "routes": ["B", "D"]},
        "4": {"name": "Broadway Junction", "line": "Jamaica", "boro": "Bk", "routes": ["A", "C", "J", "Z", "L"]},
        "5": {"name": "14 St / 8 Av", "line": "8th Av / Canarsie", "boro": "M", "routes": ["A", "C", "E", "L"]},
        "6": {"name": "145 St", "line": "Broadway-7th Av", "boro": "M", "routes": ["1"]},
    }

    def names(self, query):
        return [c["name"] for c in tb.search_stations(self.complexes, query)]

    def test_line_letters_find_every_stop_on_those_lines(self):
        found = self.names("jmz")
        self.assertEqual(found[0], "Myrtle Av")  # serves all three
        self.assertIn("Broadway Junction", found)

    def test_single_letter_means_a_line(self):
        self.assertEqual(self.names("bedford l"), ["Bedford Av"])

    def test_street_words_match_either_spelling(self):
        self.assertEqual(self.names("broadway junction"), ["Broadway Junction"])
        self.assertEqual(self.names("broadway jct"), ["Broadway Junction"])

    def test_numbers_are_street_numbers(self):
        self.assertEqual(self.names("14 st"), ["14 St / 8 Av"])
        self.assertEqual(self.names("8 av"), ["14 St / 8 Av"])

    def test_no_match(self):
        self.assertEqual(self.names("zzz"), [])


class NamesTest(unittest.TestCase):
    def test_short_names(self):
        self.assertEqual(tb.short_name("Coney Island-Stillwell Av"), "Coney Island")
        self.assertEqual(tb.short_name("Marcy Av"), "Marcy Av")
        self.assertEqual(tb.short_name("Hoyt-Schermerhorn Sts"), "Hoyt")

    def test_fit_adds_ellipsis(self):
        f = tb.font("Regular", 12)
        self.assertEqual(tb.fit("Bay Ridge", f, 500), "Bay Ridge")
        cut = tb.fit("Van Cortlandt Park", f, 50)
        self.assertTrue(cut.endswith("…") and f.getlength(cut) <= 50)


class RenderTest(unittest.TestCase):
    rows = [("L", "Canarsie", 3, False), ("6X", "Pelham Bay Park", 0, True)]

    def test_every_theme_renders_at_taskbar_height(self):
        for theme, _ in tb.THEMES:
            if theme == "countdown":
                continue
            for height in (40, 48, 72):
                img = tb.render(theme, self.rows, None, height)
                self.assertEqual((img.mode, img.height), ("RGBA", height), theme)
                self.assertGreater(img.width, 40, theme)

    def test_messages_render(self):
        for theme in ("clock", "led", "minimal"):
            self.assertEqual(tb.render(theme, None, "Offline", 48).height, 48)

    def test_countdown_cards_share_one_width(self):
        width = tb.countdown_width(self.rows, 48)
        cards = [tb.countdown_card(r, 48, i, 2, width) for i, r in enumerate(self.rows)]
        self.assertEqual({c.size for c in cards}, {(width, 48)})

    def test_background_stays_clickable(self):
        img = tb.render("clock", self.rows, None, 48)
        self.assertGreaterEqual(img.getpixel((0, 0))[3], 1)  # alpha 0 would let clicks fall through


if __name__ == "__main__":
    unittest.main()
