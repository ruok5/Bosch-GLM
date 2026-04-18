# Bosch-GLM

Stream measurements from a Bosch GLM laser rangefinder over Bluetooth LE
to your terminal (and optionally your clipboard).

> **Credit where it's due.** This project is a Python rewrite / fork of
> [**ketan/Bosch-GLM50C-Rangefinder**](https://github.com/ketan/Bosch-GLM50C-Rangefinder).
> Ketan did the hard work of reverse-engineering the GLM's BLE protocol
> — the service UUID, the magic byte sequence, and the measurement
> packet format all come from that repo. This fork is a ground-up
> Python rewrite that drops the ESP32 firmware side and focuses on a
> macOS-friendly CLI workflow. Go star the original.

Tested on a Bosch GLM 50 C on macOS. Should work on any GLM that
advertises the same BLE service UUID (prefix `02a6c0…`); YMMV on other
models.

## Features

- Auto-discovers the GLM by BLE service UUID — **no MAC address
  configuration required**
- Reconnect loop with backoff; survives the rangefinder going to sleep
- Big-text terminal display of each reading
- Imperial output rounded to the nearest ½" with zero-feet and
  zero-inches always shown (`0'-3 1/2"`, not `3 1/2"`)
- `--clipboard` to copy each measurement via `pbcopy` in one of several
  formats
- `--offset` to add a static correction (useful when measuring from a
  fixed reference like a tape hook or a jig)
- Quiet by default; `-v` / `-vv` for diagnostics

## Requirements

- macOS (the clipboard integration uses `pbcopy`; everything else is
  cross-platform via [`bleak`](https://github.com/hbldh/bleak))
- Python 3.10+
- A Bosch GLM with Bluetooth (e.g. GLM 50 C)

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

```sh
python main.py                      # quiet; big-text readings only
python main.py -c                   # also copy decimal inches to clipboard
python main.py -c arch              # copy feet-inches string (e.g. 3'-7 1/2")
python main.py -c mm                # copy millimeters
python main.py --offset 2.5         # add 2.5" to every reading
python main.py --offset -0.75 -c in # subtract 3/4", copy inches
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

## License

Dual: original portions © ketan (all rights reserved, used under
GitHub ToS fork conventions); my additions are WTFPL. See
[`LICENSE`](LICENSE).
