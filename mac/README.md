# GLM Wedge (macOS)

The wedge as a Mac app. While it's open, every GLM shot is appended to
`~/.glm/shots.log` for AutoCAD's `LASER` command. Close the window to quit and
stop logging.

The window shows the GLM connection, the last shot, the log, and an activity
list. ⌘, opens settings for the doorbell and an optional notify URL.

## Build

```sh
cd mac
xcodegen generate            # project.yml → GLMWedge.xcodeproj (not checked in)
xcodebuild -project GLMWedge.xcodeproj -scheme GLMWedge -configuration Release \
  -destination 'platform=macOS' -derivedDataPath build build
open "build/Build/Products/Release/GLM Wedge.app"
```

Tests (log format, seq, catch-up alignment, hold-during-catch-up, ERR/GAP):

```sh
xcodebuild test -project GLMWedge.xcodeproj -scheme GLMWedge \
  -destination 'platform=macOS' -derivedDataPath build
```

The app is signed with the Apple Development identity (team 43JT6B83AS). A
stable signature keeps the Bluetooth and Accessibility grants across rebuilds.

To try it against a scratch log instead of `~/.glm`:

```sh
open -n "build/Build/Products/Release/GLM Wedge.app" --args -logDir /tmp/glm-test
```

## Permissions

- **Bluetooth**: asked on first launch.
- **Accessibility**: only for the doorbell (the one-pixel mouse nudge that wakes
  LASER's `grread`). Click **Allow…** in the window footer.

## Behaviour

The log contract is shared with `~/development/AutoLISP` (LASER.lsp); change it
there first.

- Single-distance shots and ERR are logged. Every ERR is kept, because two
  adjacent ERR lines are LASER's reset.
- On each connect the GLM's history is lined up against the log tail by
  sequence. Missing shots are appended as `recovered`, oldest first, then any
  shots taken during that read as `live`. If shots fell off the GLM's history,
  a `GAP` line goes first.
- The first-ever connect records a baseline and backfills nothing. It saves
  the GLM's history in `~/.glm/wedge.json`, and later catch-ups line up
  against that plus everything logged since.
- Beeps on the GLM: one per live shot, two when a catch-up recovered shots.

GLM165-27C6 history, measured 2026-11-08:

- listIndex 1…30, **oldest first**. A shot on a full list drops #1. Slots past
  the newest shot read as devMode 1 with zeros, and 36+ return junk, so the
  wedge never reads past 30.
- ERR shots aren't stored, so an ERR taken while the app is closed is lost.
- Other modes are stored too (e.g. devMode 17, indirect, which can have a
  negative result); the wedge skips them.
- meas_id is the device's message counter. A history read stamps fresh ids,
  and an ERR repeats the previous shot's id, so it isn't a shot identity.
