# Trainbar

Live NYC subway arrivals on your Windows taskbar, set to look like part of Windows.

![Trainbar on the Windows 11 taskbar, showing the next L trains at Bedford Av](docs/screenshot.png)

Pick your station once. Trainbar shows the next train in each direction, or the next two in one direction, with the line's bullet, where the train is headed, and how many minutes away it is. When a train is pulling in, the minutes read `now`.

The background is transparent, so it works on the standard taskbar and on see-through taskbars from tools such as TranslucentTB. Text follows the Windows light or dark setting.

## Themes

Right-click and open **Theme** to switch.

| Theme | Look |
| --- | --- |
| Clock | Two lines in Segoe UI Variable, sized like the taskbar clock. The default. |
| LED | A dot-matrix sign with glowing dots and no box behind it. |
| Minimal | One small capsule per train with the line and the minutes. |

**Show background** adds a faint rounded backdrop behind any theme. Without it, the backdrop appears only when you point at the widget.

Express trains (such as the 6 and 7 express) get a diamond instead of a circle.

## Install

You need Windows 10 or 11 and Python 3 with Pillow. Tested on Windows 11 with Python 3.14.

```
pip install -r requirements.txt
pythonw trainbar.pyw
```

`pythonw` runs it without a console window. On first launch a search box opens so you can choose your station.

To start it with Windows, right-click the widget and tick **Start with Windows**. This copies `trainbar.pyw` to `%LOCALAPPDATA%\Trainbar` and adds an entry under `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`. Untick it to remove the entry.

## Use

| Action | What it does |
| --- | --- |
| Right-click | Direction, theme, station, refresh, reset position, start with Windows, quit |
| Click | Refresh now |
| Drag sideways | Move it along the taskbar (the position is remembered) |

In the station search, type part of the name, a line, or both: `96 q`, `bedford l`, `jay st`. Complexes such as Times Sq / 42 St–Port Authority are one entry and show every line that stops there.

The direction menu uses the MTA's own labels for that station, such as "Manhattan" and "Canarsie", instead of north and south.

Trainbar hides itself while a full-screen app is in front, and it keeps itself above the taskbar.

## How it works

- Arrival times come from the MTA's public [GTFS-realtime subway feeds](https://api.mta.info/), which need no API key. Trainbar checks the feeds for your station's lines every 30 seconds, and all eight feeds every 5 minutes so that it can catch trains rerouted from other lines.
- Station names, lines and direction labels come from the [MTA Subway Stations](https://data.ny.gov/Transportation/MTA-Subway-Stations/39hk-dx4f) dataset on data.ny.gov. It is downloaded on first run and refreshed every 30 days.
- The protobuf feeds are decoded by a small reader in the script, so the only dependency is Pillow, which draws the widget. A layered window with per-pixel alpha keeps the edges smooth on any background.
- Settings live in `%APPDATA%\Trainbar\config.json`. Unexpected errors are written to `error.log` in the same folder.

If the feeds can't be reached, the widget reads "Offline". If data is more than three minutes old, the text fades to half strength so you know not to trust it.

## Limits

- Subway only, including the Staten Island Railway. No LIRR, Metro-North, PATH, NJ Transit or buses.
- The widget goes on the taskbar of your primary monitor and expects the taskbar at the bottom of the screen.
- Windows 11 no longer lets apps put toolbars inside the taskbar, so Trainbar is a small borderless window placed over it, next to the tray icons.

## Uninstall

1. Right-click the widget, untick **Start with Windows**, then select **Quit**.
2. Delete `%APPDATA%\Trainbar` and `%LOCALAPPDATA%\Trainbar`.

## Not affiliated

Trainbar is an independent project. It is not made, endorsed or supported by the Metropolitan Transportation Authority. Arrival data is provided by the MTA as is.

## License

[MIT](LICENSE)
