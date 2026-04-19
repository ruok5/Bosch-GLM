# Cross-platform support plan

## What's already cross-platform

| Layer | Library | Status |
|-------|---------|--------|
| BLE transport | `bleak` | ✓ — uses CoreBluetooth (mac), BlueZ/D-Bus (Linux), WinRT (Windows) under the hood. No code change needed. |
| Storage path | `platformdirs` | ✓ — `user_data_path("bosch-glm")` resolves to the right place on all three OSes. |
| SQLite | stdlib `sqlite3` | ✓ |
| TUI | `textual` | ✓ |
| Async / event loop | stdlib `asyncio` | ✓ |
| Frame / CRC / message decoding | pure Python | ✓ |

## What's macOS-specific today (and easy to abstract)

There are exactly two mac-only seams:

### 1. Clipboard (`glm/format.py:copy_to_clipboard`)

Currently shells out to `pbcopy`. Trivial fix.

**Plan:**
- Add `glm/clipboard.py` with a `copy(text: str) -> bool` function.
- Detect platform and dispatch:
  - **macOS**: `subprocess.run(["pbcopy"], …)` (current behavior).
  - **Linux**: try `wl-copy` (Wayland) → `xclip -selection clipboard` → `xsel -b` in that order. Most desktops have at least one.
  - **Windows**: `subprocess.run(["clip.exe"], …)` (works in plain CMD and WSL).
  - **Fallback**: `pyperclip` library if installed — it handles all three plus Cygwin / Termux. Add as an optional dep.
- Return `False` (with a warning log) if no copy backend is found rather than raising. The TUI already tolerates clipboard failures.

**Difficulty:** very low. ~30 lines, no external runtime deps if we keep the
subprocess approach. Adding `pyperclip` as an optional fallback would smooth
the experience on uncommon Linux setups.

### 2. Geolocation (`glm/location.py`)

Currently uses `pyobjc-framework-CoreLocation`. Genuinely macOS-only.

**Plan:**

Define a backend protocol:

```python
# glm/location/base.py
class LocationBackend(Protocol):
    async def get_fix(self, timeout_s: float) -> LocationFix | None: ...
```

Then per-platform implementations:

#### macOS — `glm/location/_macos.py`
- Uses `CoreLocation` via `pyobjc-framework-CoreLocation` (already implemented).
- Difficulty: **done**.
- Cost: one extra wheel (~15 KB). Already pulled in transitively by bleak's pyobjc deps, so no actual install delta.

#### Linux — `glm/location/_linux.py`
- Use **GeoClue 2** over D-Bus. It's the freedesktop standard, present on
  most modern desktops (GNOME / KDE / etc), and proxies to GPS / WiFi /
  cell / 3G / IP geolocation.
- Two reasonable Python bindings:
  - `dbus-next` (asyncio-friendly, pure Python, no system bindings) — preferred.
  - `pydbus` (uses GLib mainloop, harder to integrate with asyncio).
- Code sketch:
  ```python
  from dbus_next.aio import MessageBus
  bus = await MessageBus().connect()
  introspection = await bus.introspect("org.freedesktop.GeoClue2", "/org/freedesktop/GeoClue2/Manager")
  # … create client, start, await location signal
  ```
- Permissions: GeoClue prompts via the system policy daemon — usually no
  user prompt, sometimes an org-managed allowlist on locked-down distros.
- Headless / server: GeoClue is **not present** on most server installs.
  Detect at runtime and fall back to IP-based.
- Difficulty: **medium**. ~80–120 lines including the D-Bus signal dance.
  Main complication is that GeoClue's "client" interface is somewhat
  stateful; you create a Client, start it, and listen for a signal.

#### Windows — `glm/location/_windows.py`
- Use **Windows.Devices.Geolocation** via WinRT. Either:
  - `winsdk` (Microsoft's official Python projection of WinRT), or
  - `winrt` (older but still works).
- Skeleton:
  ```python
  from winsdk.windows.devices.geolocation import Geolocator
  loc = Geolocator()
  pos = await loc.get_geoposition_async()
  return LocationFix(pos.coordinate.latitude, pos.coordinate.longitude, pos.coordinate.accuracy)
  ```
- Permissions: Windows 10+ requires the user to grant Location access in
  Settings → Privacy → Location, both for "all apps" and per-app. Console
  apps inherit a system-level grant.
- Difficulty: **low–medium**. The WinRT bridge is well-trodden and the
  geolocator is one of the simpler APIs. ~60 lines.

#### Universal fallback — `glm/location/_ip.py`
- HTTP request to a free geolocation API: ipinfo.io, ip-api.com,
  ipapi.co. Returns coarse city-level location (~5–50 km accuracy).
- Useful when: native API unavailable, user denied OS-level access,
  testing on a server. Useless when: phone-grade precision is needed.
- Privacy: the API provider sees the request IP. Document this and make
  it opt-in via flag.
- Difficulty: **trivial**. ~20 lines.

#### Backend selection — `glm/location/__init__.py`
```python
import sys
def get_backend() -> LocationBackend:
    if sys.platform == "darwin":
        try:
            from ._macos import MacOSBackend
            return MacOSBackend()
        except ImportError:
            pass
    if sys.platform.startswith("linux"):
        try:
            from ._linux import GeoClueBackend
            return GeoClueBackend()
        except ImportError:
            pass
    if sys.platform == "win32":
        try:
            from ._windows import WindowsBackend
            return WindowsBackend()
        except ImportError:
            pass
    from ._ip import IPBackend
    return IPBackend()
```

The async `get_fix()` function in `glm/location.py` becomes a thin
delegate to the selected backend.

**Difficulty (whole geolocation rework):** medium. Maybe a day of work
including writing tests with mock backends. The hard part is that
geolocation tests *can't* use real backends in CI — you have to inject
a mock. The protocol/backend split makes that natural.

## Optional extras for full cross-platform parity

### Settings file location
`platformdirs` already handles this. No work.

### Bluetooth permission prompts
- macOS: bleak triggers the system Bluetooth permission prompt — works fine.
- Linux: usually no prompt (BlueZ is permissive); requires the user to
  be in the `bluetooth` group on some distros.
- Windows: Bluetooth requires the OS Bluetooth radio to be on; bleak
  raises if not. Already handled via the existing reconnect loop.

### Process / window title
TUI sets `self.title` — works everywhere via Textual.

### Notifications (the `notify` calls)
Textual handles in-TUI notifications cross-platform. We don't currently
emit OS-level toasts. If we wanted to, **`plyer`** wraps native
notification APIs on all three OSes.

## Suggested rollout order

If the user wants me to implement this:

1. **Clipboard abstraction** (1 hour). Lowest risk, biggest immediate
   value for any non-mac contributor.
2. **IP fallback for location** (30 min). Gives us a universal default,
   makes the geolocation feature degradable rather than mac-locked.
3. **Backend protocol + restructure** (2 hours). Move the existing
   CoreLocation code behind the new protocol. No behavior change.
4. **Linux GeoClue backend** (3 hours). The most useful net-new
   platform. I can write it but can't smoke-test without a Linux box.
5. **Windows WinRT backend** (2 hours). Similar — write-only without a
   Windows machine to test on.

CI: matrix-test on `macos-latest`, `ubuntu-latest`, `windows-latest`
via GitHub Actions; skip the BLE-touching tests on CI. The 49 unit tests
we have today already work platform-agnostically.

## What can't be tested without hardware

- Geolocation on the platform you're not on (Linux/Windows backends from
  a Mac dev box).
- BLE on any platform without a real GLM in range.
- The OS-level permission prompts (those *only* fire interactively).

A working strategy: implement the backend skeletons with mock-tested
unit tests, then crowdsource live testing from anyone who tries the
project on their platform. Open issues template for "report your OS +
whether geolocation worked" would help.

## Bottom line on difficulty

| Layer | Difficulty | Time | Risk |
|-------|------------|------|------|
| Clipboard abstraction | **Easy** | 1h | Low |
| Backend protocol + restructure | **Easy** | 2h | Low |
| IP fallback | **Trivial** | 30m | Low (privacy doc) |
| Linux GeoClue | **Medium** | 3h | Can't test without Linux box |
| Windows WinRT | **Medium** | 2h | Can't test without Windows box |

**Total:** ~1 day of focused work to get the project genuinely
cross-platform, with the caveat that I can only validate the macOS path
locally. The other two need a real user (or a CI runner with mocks
exercising the abstraction layer) to confirm.
