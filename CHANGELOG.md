# Changelog

All notable changes to Trainbar. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [1.3.1] - 2026-11-16

### Changed
- The Countdown slide is slower and smoother: 900 ms, eased in and out, with sub-pixel motion instead of whole-pixel steps.
- Spacing in every theme follows a 4 px grid: 12 px at the edges, 8 px between a bullet and its text, 16 px between separate pieces.
- The README shows a rendered image of each theme on a dark and a light taskbar, made by `tools/render_theme_images.py`.

## [1.3.0] - 2026-11-16

### Added
- `Trainbar.exe`, a standalone build attached to each release. No Python needed.
- **Lock position** in the right-click menu and in Settings.
- A new station search: real line bullets, the borough and line under each name, hover and keyboard selection, and an empty state.
- Search by groups of lines: `jmz` finds every stop on the J, M or Z. Street words match either spelling (`junction` or `jct`).
- Version, **Report a problem** and **Website** links at the bottom of Settings.
- An app icon for the exe and for the Settings and search windows.
- Unit tests, lint and CI on Windows with Python 3.10 and 3.13.

### Changed
- Minimal shows only the line bullet and the minutes, with no capsules.
- Countdown and Clock size themselves to their content, so there is no wide gap between the name and the minutes.
- Bullet letters are drawn with the circle at 4x and scaled down, so they sit exactly in the center.
- Feeds are fetched gzip-compressed (about 5 KB instead of 24 KB each).

### Fixed
- `bedford l` no longer matches Bedford Park Blvd. A single letter now means a line.
- `14 st` finds 14 St stations instead of every stop on the 1 and 4.

### Performance and reliability
- The widget redraws only when something visible changes.
- Offline checks back off from 30 seconds to 5 minutes. Behind a full-screen app, checks slow to every 2 minutes and drawing stops.
- After the PC wakes from sleep, arrivals refresh at once.
- Settings are saved atomically. `error.log` is capped at 256 KB.
- If Trainbar cannot start (for example, no network on the very first run), it says so in a dialog instead of exiting silently.
- A clear error if the MTA station dataset changes format.

## [1.2.0] - 2026-11-16

### Added
- Countdown theme, the new default: one train at a time at full size, sliding to the next.
- A Settings window with every option.

### Changed
- Line letters centered in their bullets. More space in Minimal.

## [1.1.0] - 2026-11-16

### Added
- Clock, LED and Minimal themes, with a transparent background for TranslucentTB and similar tools.
- Follows the Windows light or dark setting.
- Diamond bullets for express trains.

## [1.0.0] - 2026-11-16

- First release: a dot-matrix display of live NYC subway arrivals on the Windows taskbar.

[1.3.1]: https://github.com/thedevmark/trainbar/releases/tag/v1.3.1
[1.3.0]: https://github.com/thedevmark/trainbar/releases/tag/v1.3.0
[1.2.0]: https://github.com/thedevmark/trainbar/releases/tag/v1.2.0
[1.1.0]: https://github.com/thedevmark/trainbar/releases/tag/v1.1.0
[1.0.0]: https://github.com/thedevmark/trainbar/releases/tag/v1.0.0
