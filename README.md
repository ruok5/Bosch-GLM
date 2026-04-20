# Bosch-GLM

Stream measurements from a Bosch GLM laser rangefinder over Bluetooth LE
to your terminal (and optionally your clipboard, a SQLite log, or a
Textual TUI).

> **Credit where it's due.** This project began as a Python rewrite of
> [**ketan/Bosch-GLM50C-Rangefinder**](https://github.com/ketan/Bosch-GLM50C-Rangefinder).
> Ketan did the original reverse-engineering of the GLM's BLE protocol —
> the service UUID, the magic byte sequence, and the basic measurement
> packet format all came from that repo. Go star the original.
>
> The protocol layer here was later expanded by decompiling Bosch's own
> Android app (`MeasureOn`) with `jadx` to extract the full
> `MtProtocol` SDK — frame format with CRC8/CRC16 by mode, the EDC
> message catalog, settings read/write, and the on-device history fetch
> path. Reverse engineering for interoperability with a device you own
> is well-trodden ground (see *Sega v. Accolade*, *Sony v. Connectix*,
> and 17 USC § 1201(f)).

Tested on a Bosch GLM165-27C6 on macOS. Should work on any GLM in the
"MIRACULIX" family — anything that advertises service UUID prefix
`02a6c0…`. YMMV on other models.

## Features

- Auto-discovers the GLM by BLE service UUID — **no MAC address
  configuration required**
- Reconnect loop with backoff; survives the rangefinder going to sleep
- Big-text terminal display of each reading; imperial output rounded to a
  user-selectable precision (1", ½", ¼", or ⅛") with zero-feet/inches always
  shown (`0'-3 1/2"`). The TUI `P` key cycles precision and the choice
  persists in `prefs.json`.
- `--clipboard` to copy each measurement via `pbcopy`
- `--offset` for a static correction (tape-hook or jig offsets)
- **Persistent SQLite log** of every measurement, deduped by the
  device's `meas_id`. Default location:
  `~/Library/Application Support/bosch-glm/measurements.sqlite`
- **Catchup** (`--catchup`): on each (re)connect, walk the device's
  on-device history list (last ~63 measurements) and recover any rows
  not yet in the local store. Value-tuple dedup means re-runs don't
  duplicate.
- **Error detection**: GLM error responses (`devMode 63`, returning
  `1.0 m`) are now displayed as errors, not silently rendered as a
  bogus distance.
- **Textual TUI** (`tui.py`): live big-text reading + scrollable
  history table + device settings panel + key bindings for copy,
  offset, refresh, settings sync.
- **Settings CLI** (`settings.py`): read or write device settings —
  measurement units, beep, laser pointer, backlight, spirit level,
  display rotation.
- **Geolocation** (macOS-only today): every captured measurement is
  tagged with lat/lon/accuracy via `CoreLocation`. First use prompts
  the OS for Location Services access; failures degrade silently.
- **Site registry** (`sites.json`): JSON list of named locations. On
  connect we look up the current location and tag matching
  measurements with the nearest site name (within a per-site radius).
- **Export** (`export.py`): dump filtered measurement subsets as CSV,
  JSON, or Markdown — by date range, site, device, or row count.
- **Notes** (`notes.py`): attach free-form text to specific
  measurements after the fact.
- **Stations** (`station.py`, TUI): consecutive vertical-elevation
  shots at one X-Y datum auto-group into a "station" (default 60s
  idle window). The TUI's `l` key opens a review modal where you
  assign labels from a preset palette
  (`bottom-of-beam` → `…-purlin` → `…-subpurlin` → `…-foil` →
  `…-deck`, plus `bottom-of-pipe(<size>)` from a pipe-size picker)
  in Z-order. Stations stay `draft` until you confirm.
- **Error-error gesture**: two GLM measurement errors within 3s
  soft-delete the most recent good measurement. Hands-free fix-the-
  misfire workflow; deleted rows hide by default, toggle with `D`,
  undelete with `U`. The double-beep audio confirms the action.
- **Audio + visual feedback** (`glm/feedback.py`): undocumented
  `0x45/0x46` beeper and `0x47/0x48` display commands give us 30ms
  beep, double-beep, triple-beep, and display-blink primitives —
  no laser, no measurement, no entry in the device's history list.
- **AutoCAD-targeted exports** (`export.py --format mleader|attribs`):
  per-station MLEADER text blocks for paste into AutoCAD multi-leaders,
  or per-station CSV with `BOT_BEAM`/`BOT_PURLIN`/etc. attribute-tag
  columns for block-attribute import.

## Platform support

- **macOS**: full feature set (BLE, clipboard via `pbcopy`,
  geolocation via `CoreLocation`, TUI, etc.). Tested.
- **Linux / Windows**: `bleak` (BLE), `textual` (TUI), `platformdirs`,
  and `sqlite` are already cross-platform — the core streaming and
  storage paths *should* work, but the clipboard helper currently
  shells out to `pbcopy` and geolocation is `CoreLocation`-only. Both
  are isolatable seams; see
  [`docs/CROSS_PLATFORM_PLAN.md`](docs/CROSS_PLATFORM_PLAN.md) for
  the abstraction plan and difficulty estimates per backend.

## Requirements

- Python 3.10+
- A Bosch GLM with Bluetooth (e.g. GLM165-27C6)

## Install

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

## Pairing the GLM

You don't need to pair the device with macOS, and you don't need to
know its MAC address — the script discovers the GLM by its BLE service
UUID.

1. Turn on the rangefinder.
2. Enable Bluetooth on the device: hold the **Function** button, select
   the Bluetooth icon, and confirm. The BT indicator should appear on
   the display.
3. Run the script (below). It will scan for up to ~8 seconds and
   connect automatically.

If you have multiple GLMs in range and want to pin to a specific one,
open an issue — an optional `--address` flag would be a small addition.

## Usage

### Headless streaming

```sh
python main.py                      # quiet; big-text readings only
python main.py -c                   # also copy decimal inches to clipboard
python main.py -c arch              # copy feet-inches string (e.g. 3'-7 1/2")
python main.py -c mm                # copy millimeters
python main.py --offset 2.5         # add 2.5" to every reading
python main.py --offset -0.75 -c in # subtract 3/4", copy inches
python main.py --catchup            # also recover history-on-device on connect
python main.py --no-store           # don't persist to SQLite
python main.py --no-location        # skip CoreLocation lookup (geotagging off)
python main.py --sites my-sites.json     # custom site registry path
python main.py --log-file /tmp/glm.log   # write DEBUG log
python main.py -v                   # info-level logging
python main.py -vv                  # full debug (bleak + raw bytes)
```

Press the green **measure** button on the rangefinder to take a
reading. `Ctrl-C` to quit.

### Clipboard formats

| Flag          | Example output    | Notes                            |
| ------------- | ----------------- | -------------------------------- |
| `-c` / `-c in`| `43.5`            | displayed inches (matches big text) |
| `-c arch`     | `3'-7 1/2"`       | feet-inches string               |
| `-c m`        | `1.1049`          | meters, four decimal places      |
| `-c mm`       | `1105`            | millimeters, integer             |

### Textual TUI

```sh
python tui.py                       # full TUI: live + history + settings
python tui.py --offset 2.5          # with offset
python tui.py --catchup             # with reconnect-recovery
```

Key bindings:

| Key | Action |
| --- | ------ |
| `q` | Quit |
| `c` | Copy last reading to clipboard |
| `o` | Set offset (opens input prompt) |
| `r` | Reload history table from store |
| `s` | Re-fetch device settings |
| `P` | Cycle display precision (1" → ½" → ¼" → ⅛") |
| `T` | Set setup auto-close timeout (seconds) |

The right-hand settings panel collapses automatically when the terminal is
narrower than 100 columns. TUI preferences (precision, setup timeout,
collapse override) live in
`~/Library/Application Support/bosch-glm/prefs.json`.

### Settings get / set

```sh
python settings.py                                 # read current settings, exit
python settings.py --beep off --laser on           # write settings, exit
python settings.py --units ft-in --backlight auto
```

Available flags: `--units {m,mm,yd,ft,in,in-frac,ft-in}`,
`--beep {on,off}`, `--laser {on,off}`,
`--backlight {auto,on,off}`, `--spirit-level {on,off}`,
`--rotate {on,off}`.

### Sites file

A site registry lets you tag measurements with a named location based on
the device's current geofix. Drop a JSON file at
`~/Library/Application Support/bosch-glm/sites.json`:

```json
[
  {"name": "Smith House",  "lat": 37.5001, "lon": -122.5001},
  {"name": "Jones Garage", "lat": 37.6, "lon": -122.4,
   "address": "200 Oak Ave", "radius_m": 50}
]
```

`radius_m` is per-site (default 100m). On each app start we look up the
current location, find the closest site within its own radius, and tag
all subsequent measurements with that site's `name`.

### Export

```sh
python export.py                                # CSV of everything to stdout
python export.py --format json                  # JSON dump
python export.py --format md --site "Smith House"   # Markdown by site
python export.py --since 2026-04-15 --until 2026-04-18
python export.py --since 2026-04-18 -o today.csv
python export.py --device D2F92907-... --limit 50
```

### Notes

```sh
python notes.py set --meas-id 786 "front porch height to soffit"
python notes.py show --meas-id 786
python notes.py list                            # all measurements with notes
echo "long note from a file or pipe" | python notes.py set --meas-id 786 -
```

### Stations

Take 3–6 vertical elevation shots at one location within ~60s of each
other; the tracker groups them as a station automatically. In the TUI:

| Key | Action |
| --- | ------ |
| `l` | Open the station-review modal for the most recent station |
| `1`–`6` | (in modal) pick label from preset; `6` opens pipe-size picker |
| `t` | (in modal) custom label |
| `x` | (in modal) clear label |
| `Enter` | (in modal) confirm + save |
| `s` | (in modal) save as draft |
| `D` | Toggle "show deleted" in history |
| `U` | Undelete the most recent soft-deleted measurement |

Headless: stations are tracked silently; review past stations with
`python station.py list / show <id> / confirm <id>`.

### AutoCAD export

```sh
# Per-station MLEADER text blocks (paste into a multi-leader)
python export.py --format mleader -o /tmp/mleader.txt

# Per-station CSV with BOT_BEAM/BOT_PURLIN/etc columns matching
# AutoCAD block attribute tags
python export.py --format attribs -o /tmp/attribs.csv

# By default both exclude soft-deleted rows AND draft (unconfirmed) stations.
# Widen with --include-deleted / --include-drafts.
python export.py --format attribs --include-drafts
```

### Error-error gesture

While streaming, if you take a measurement you don't want, fire two
errors at empty space within 3 seconds — the last good measurement is
soft-deleted (you'll hear a double beep). Toggle `D` in the TUI to
view deleted rows; press `U` to undelete the most recent.

### Inspecting the SQLite log

```sh
sqlite3 ~/Library/'Application Support'/bosch-glm/measurements.sqlite \
  "SELECT meas_id, ROUND(result_m,4), site_name, notes,
          datetime(captured_at/1000,'unixepoch','localtime') \
   FROM measurements ORDER BY captured_at DESC LIMIT 20"
```

## Project layout

```
glm/
  protocol/        # pure-Python wire layer (CRC, frame, messages)
  ble.py           # bleak transport
  cli.py           # argparse entry points (headless, tui, settings)
  export.py        # CSV/JSON/MD/MLEADER/attribs export logic
  feedback.py      # beep/display primitives via undocumented 0x45-0x48 cmds
  format.py        # display + clipboard helpers
  gestures.py      # ErrorErrorTracker for hands-free soft-delete
  location.py      # CoreLocation lookup + haversine
  notes.py         # set/get/list note CLI logic
  sites.py         # site JSON registry + nearest-match lookup
  station.py       # StationTracker + preset label catalog + station CLI
  store.py         # SQLite persistence (with idempotent migrations)
  tui/app.py       # Textual app
  tui/screens.py   # StationReviewScreen + PipeSizePicker modals
main.py            # shim → glm.cli:headless
tui.py             # shim → glm.cli:tui
settings.py        # shim → glm.cli:settings_main
export.py          # shim → glm.export:export_main
notes.py           # shim → glm.notes:notes_main
station.py         # shim → glm.station:station_main
docs/              # design notes
tests/             # pytest suite (83 tests)
```

The `reverse/` directory (gitignored) is where decompiled Bosch app
sources live during development; nothing in it ships.

## License

Dual: original portions © ketan (all rights reserved, used under
GitHub ToS fork conventions); my additions are WTFPL. See
[`LICENSE`](LICENSE).
