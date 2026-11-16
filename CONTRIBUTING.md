# Contributing

Thanks for helping. Trainbar is deliberately one file with one dependency, so anyone can read it end to end and run it without a build step. Please keep it that way.

## Set up

Windows 10 or 11 and Python 3.10 or later.

```
pip install -r requirements.txt ruff
pythonw trainbar.pyw
```

Only one copy runs at a time. Quit the running widget (right-click > Quit) before you start your edited copy.

## Check your change

```
python -m ruff check .
python -m unittest discover -v tests
```

CI runs both on Windows with Python 3.10 and 3.13. The tests need no network and open no windows. They build small GTFS-realtime feeds by hand, so parser changes can be tested exactly.

For visual changes, run the widget and look at it on a dark and a light taskbar, and at 100% and 150% display scaling if you can.

The README theme images come from the app's own drawing code. After a visual change, make them again with `python tools/render_theme_images.py trainbar.pyw docs`.

## How the code is laid out

`trainbar.pyw`, top to bottom:

| Section | What it does |
| --- | --- |
| look | Themes. `Painter` collects alpha masks and composites them, with shapes drawn at 4x for smooth edges. `render()` dispatches to `render_clock`, `render_led` and `render_minimal`. `countdown_card()` draws one Countdown card. |
| data | `pb_fields` is a minimal protobuf reader. `parse_feed` turns a GTFS-realtime feed into `(stop_id, route, epoch, destination)` tuples. `load_stations` reads the MTA station list. `pick_rows` chooses which trains to show. `Feed` polls in a background thread. |
| windows plumbing | ctypes calls: the layered window (`present_layered`), taskbar geometry, full-screen detection, start-with-Windows. |
| app | `App` owns the window, the redraw loop, the Countdown slide, input and the menu. `SettingsWindow` and `StationPicker` are the two dialogs. |

Rules of thumb:

- Keep logic that can be pure out of `App`, and test it.
- The UI thread never touches the network. `Feed` does, and calls back with `root.after`.
- `App.draw()` skips work when nothing visible changed. If you add state that affects the picture, add it to the redraw key.
- Every number in a theme is scaled by `k = taskbar height / 48`, so it works at any display scaling.

## Releasing

1. Update `__version__` in `trainbar.pyw` and add a section to `CHANGELOG.md`.
2. Commit and push to `main`, and wait for CI to pass.
3. Publish a GitHub release named `vX.Y.Z` with `trainbar.pyw` attached. The release workflow builds `Trainbar.exe` and attaches it to the release.
