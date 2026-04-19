"""Modal screens for the GLM TUI."""
from __future__ import annotations

from typing import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Static

from ..format import IN_PER_M, format_imperial
from ..setup import (
    PIPE_SIZES, PIPE_SIZE_DEFAULT, PRESET_LABELS,
    format_pipe_label, suggest_labels,
)


# Verbatim canonical roof-section diagram from the user's v2 sketch.
# Seven value slots: 4 structural (DECK, SUBPURLIN/FOIL, PURLIN, BEAM) on
# the left; 3 pipes (BELOW DECK / PURLIN / BEAM) in the middle column.
# Each {Sn} placeholder is exactly 20 chars wide so the diagram aligns.
_VISUAL_TEMPLATE = (
    "                           *────────▲────┬──┬──────────────────────────┬──┬───────*\n"
    "                                    │    │  │                          │  │\n"
    "                                    │    │  │                          │  │\n"
    "                                    │    │  │                          │  │\n"
    "┌────────────────────┐     *────────┼────┴▲─┴────────────────────────┬─┴──┴──┬────*\n"
    "│{S0}├──────────────┘     │                          │       │\n"
    "DECK─────────────────┘                    │                          │       │\n"
    "                          ┌───────────────┘                   .─.    │       │\n"
    "                          │                                  (   )   │       │\n"
    "┌────────────────────┐    │                                   `▲'    │       │\n"
    "│{S1}├────┘     ┌────────────────────┐         │     │       │\n"
    "SUBPURLIN / FOIL─────┘          │{S2}├─────────┘     │       │\n"
    "                                PIPE BELOW DECK──────┘               │       │\n"
    "                                                                     │       │\n"
    "                           *─────────────▲───────────────────────────┤       ├────*\n"
    "┌────────────────────┐                   │                           │       │\n"
    "│{S3}├───────────────────┘                    .─.    │       │\n"
    "PURLIN───────────────┘                                       (   )   │       │\n"
    "                                ┌────────────────────┐        `▲'    │       │\n"
    "                                │{S4}├─────────┘     │       │\n"
    "┌────────────────────┐          PIPE BELOW PURLIN────┘               │       │\n"
    "│{S5}├───────────────────────────────────────────────┼───┐   │\n"
    "BEAM─────────────────┘                                               │   │   │\n"
    "                                ┌────────────────────┐               │   │   │\n"
    "                                │{S6}├─────────┐     │   │   │\n"
    "                                PIPE BELOW BEAM──────┘         │     │   │   │\n"
    "                                                              .┼.    └───▼───┘\n"
    "                                                             ( │ )\n"
    "                                                              `▼'\n"
)
_SLOT_WIDTH = 20  # interior width of each value box


# Slot order = vertical traversal order from top to bottom in the diagram.
# Cursor j/k navigates through these in order; first measurement (highest Z)
# fills index 0, second fills index 1, etc.
SLOT_DECK         = 0
SLOT_FOIL_SUB     = 1
SLOT_PIPE_DECK    = 2
SLOT_PURLIN       = 3
SLOT_PIPE_PURLIN  = 4
SLOT_BEAM         = 5
SLOT_PIPE_BEAM    = 6
N_SLOTS = 7

# Per-slot metadata: (display name shown in the cleared placeholder, default
# label written to the store when this slot is filled, kind).
# kind in {"struct", "foil_sub", "pipe"} — drives the per-slot keybindings:
# foil_sub slots toggle subpurlin↔foil with `f`; pipe slots open the size
# picker with `p`.
_SLOT_SPECS: list[tuple[str, str, str]] = [
    ("DECK",              "bottom-of-deck",       "struct"),
    ("SUBPURLIN/FOIL",    "bottom-of-subpurlin",  "foil_sub"),
    ("PIPE BELOW DECK",   "",                     "pipe"),
    ("PURLIN",            "bottom-of-purlin",     "struct"),
    ("PIPE BELOW PURLIN", "",                     "pipe"),
    ("BEAM",              "bottom-of-beam",       "struct"),
    ("PIPE BELOW BEAM",   "",                     "pipe"),
]


def _slot_text(value_imperial: str | None, highlighted: bool) -> str:
    """Format the 20-char inner content of a value box. Empty slots render
    as a dim em-dash placeholder; filled slots show the centered imperial
    string. Highlighted (cursor) slots are rendered in reverse-video cyan."""
    if value_imperial is None:
        body = "—"
    else:
        body = value_imperial[:_SLOT_WIDTH]
    text = body.center(_SLOT_WIDTH)
    if highlighted:
        return f"[bold cyan reverse]{text}[/bold cyan reverse]"
    if value_imperial is None:
        return f"[dim italic]{text}[/dim italic]"
    return f"[bold green]{text}[/bold green]"


def slot_label_for(slot_idx: int, slot_options: dict[int, str]) -> str:
    """Compute the label that should be written to the store for a measurement
    at the given slot. `slot_options` carries per-slot overrides:
    - foil_sub slots: option value 'foil' or 'subpurlin' (default subpurlin)
    - pipe slots: option value is the size string (default PIPE_SIZE_DEFAULT)
    """
    _name, default_label, kind = _SLOT_SPECS[slot_idx]
    if kind == "foil_sub":
        choice = slot_options.get(slot_idx, "subpurlin")
        return f"bottom-of-{choice}"
    if kind == "pipe":
        size = slot_options.get(slot_idx, PIPE_SIZE_DEFAULT)
        return format_pipe_label(size)
    return default_label


def render_visual_stack(members: list,
                        slot_assignment: list[int | None],
                        slot_options: dict[int, str],
                        cursor_idx: int,
                        unassigned_meas_ids: list[int] | None = None) -> str:
    """Render the v2 roof diagram with measurement values dropped into
    their assigned slots. `slot_assignment[i]` is the meas_id at slot i, or
    None for an empty slot. `cursor_idx` highlights one slot.

    Members not assigned to any slot (overflow beyond 7) are listed below
    the diagram in an "Other measurements" block."""
    by_id = {m["meas_id"]: m for m in members}

    def fill(idx: int) -> str:
        meas_id = slot_assignment[idx]
        if meas_id is None or meas_id not in by_id:
            return _slot_text(None, idx == cursor_idx)
        imp = format_imperial(by_id[meas_id]["result_m"])
        # Foil-vs-subpurlin slot prefixes the value so the choice is visible.
        if _SLOT_SPECS[idx][2] == "foil_sub":
            choice = slot_options.get(idx, "subpurlin")
            prefix = "sub " if choice == "subpurlin" else "foil "
            imp = prefix + imp
        return _slot_text(imp, idx == cursor_idx)

    diagram = _VISUAL_TEMPLATE.format(**{f"S{i}": fill(i) for i in range(N_SLOTS)})

    extras = unassigned_meas_ids or []
    if extras:
        lines = ["", "[dim italic]Other measurements (no slot):[/dim italic]"]
        for mid in extras:
            m = by_id.get(mid)
            if m is None:
                continue
            lines.append(f"  {format_imperial(m['result_m'])}  [dim](meas #{mid})[/dim]")
        diagram += "\n".join(lines)

    return diagram


class PipeSizePicker(ModalScreen[str | None]):
    """Sub-modal: pick a pipe size from PIPE_SIZES. Returns the size string
    or None on cancel."""

    BINDINGS = [
        Binding("escape", "cancel", "Cancel"),
        Binding("enter", "select", "Select"),
        Binding("j,down", "next", "Next"),
        Binding("k,up", "prev", "Prev"),
    ]

    DEFAULT_CSS = """
    PipeSizePicker { align: center middle; }
    #pipe-box {
        width: 30;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    .pipe-row { padding: 0 1; }
    .pipe-row.selected { background: $accent; color: $text; }
    """

    def __init__(self) -> None:
        super().__init__()
        self.cursor = PIPE_SIZES.index(PIPE_SIZE_DEFAULT)

    def compose(self) -> ComposeResult:
        with Vertical(id="pipe-box"):
            yield Static("[bold]Pipe size[/bold]")
            for i, size in enumerate(PIPE_SIZES):
                cls = "pipe-row selected" if i == self.cursor else "pipe-row"
                yield Static(size, classes=cls, id=f"pipe-{i}")
            yield Static("[dim]j/k=move  Enter=pick  Esc=cancel[/dim]")

    def _refresh(self) -> None:
        for i in range(len(PIPE_SIZES)):
            row = self.query_one(f"#pipe-{i}", Static)
            row.set_classes("pipe-row selected" if i == self.cursor else "pipe-row")

    def action_next(self) -> None:
        self.cursor = (self.cursor + 1) % len(PIPE_SIZES)
        self._refresh()

    def action_prev(self) -> None:
        self.cursor = (self.cursor - 1) % len(PIPE_SIZES)
        self._refresh()

    def action_select(self) -> None:
        self.dismiss(PIPE_SIZES[self.cursor])

    def action_cancel(self) -> None:
        self.dismiss(None)


class SetupReviewScreen(ModalScreen[bool]):
    """Modal for reviewing a setup's measurements and assigning labels.

    Slot model: the 7-slot diagram is the primary view. Measurements drop
    in top-down by Z; the user pushes them down (J) to land in the right
    structural slot, leaving gaps above. Each slot's position determines
    the label written to the store on confirm.

    Returns True if the user confirmed (Enter), False if cancelled (Esc).

    Visual-mode keys (default):
      j/k or arrows : move cursor between slots (incl. empty)
      J             : push current measurement and everything below DOWN by 1
                      (creates a gap above; only works if there's an empty
                      slot somewhere below)
      K             : pull current measurement UP by 1 (only if slot above
                      is empty) — undo for J
      f             : on the FOIL/SUBPURLIN slot, toggle which label applies
      p             : on a PIPE slot, open size picker (default 2-1/2")
      x             : clear current slot (measurement becomes unassigned)
      t             : free-form custom label override on the cursor's measurement
      v             : toggle to flat table view

    Footer keys:
      Enter: save + mark confirmed
      s    : save drafts (don't confirm)
      Esc  : cancel without saving
    """

    BINDINGS = [
        # priority=True so child widgets don't eat Enter
        Binding("enter", "confirm", "Confirm", priority=True),
        Binding("escape", "cancel", "Cancel"),
        Binding("s", "save_draft", "Save draft"),
        Binding("j,down", "next", "Next slot"),
        Binding("k,up", "prev", "Prev slot"),
        Binding("J,shift+down", "push_down", "Push down"),
        Binding("K,shift+up", "push_up", "Push up"),
        Binding("f", "toggle_foil", "Foil↔Sub"),
        Binding("p", "pick_pipe_size", "Pipe size"),
        Binding("x", "clear", "Clear slot"),
        Binding("t", "custom", "Custom"),
        Binding("v", "toggle_view", "Toggle view"),
    ]

    DEFAULT_CSS = """
    SetupReviewScreen { align: center middle; }
    #review-box {
        width: 100;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    DataTable { height: auto; max-height: 20; }
    #review-visual { height: auto; max-height: 32; padding: 1 0; }
    #review-visual.hidden { display: none; }
    DataTable.hidden { display: none; }
    #custom-input { dock: bottom; height: 3; }
    """

    def __init__(self, setup_id: int, members: list,
                 on_apply: Callable[[dict[int, str | None], bool], None]) -> None:
        """`members` is a list of sqlite3.Row from store.setup_members(); the
        store returns ascending Z. We display DESCENDING (highest Z at top)
        because that matches how vertical sections are drawn in CAD.
        `on_apply(labels_by_meas_id, confirmed)` is called when the user saves."""
        super().__init__()
        self.setup_id = setup_id
        # Reverse so highest Z is members[0]
        self.members = list(reversed(list(members)))
        self.on_apply = on_apply

        # Slot assignment: slot index → meas_id (or None for empty).
        # Default fill: top-down by Z, no gaps. Members beyond N_SLOTS
        # land in `unassigned` and surface below the diagram.
        self.slot_assignment: list[int | None] = [None] * N_SLOTS
        self.unassigned: list[int] = []
        for i, m in enumerate(self.members):
            if i < N_SLOTS:
                self.slot_assignment[i] = m["meas_id"]
            else:
                self.unassigned.append(m["meas_id"])

        # Per-slot overrides: foil_sub slots store 'foil' or 'subpurlin';
        # pipe slots store the size string.
        self.slot_options: dict[int, str] = {}

        self.cursor_idx = 0          # slot cursor (visual mode)
        self.labels: dict[int, str | None] = {}  # populated on confirm
        self._custom_active = False
        self._visual_mode = True     # visual is now the default

    def compose(self) -> ComposeResult:
        with Vertical(id="review-box"):
            yield Static(f"[bold]Setup {self.setup_id}[/bold]  —  "
                         f"{len(self.members)} member(s)  (sorted high → low)",
                         id="review-title")
            yield Static(
                "[bold cyan]j/k[/bold cyan]=move · "
                "[bold cyan]J/K[/bold cyan]=push down/up · "
                "[bold cyan]f[/bold cyan]=foil↔sub · "
                "[bold cyan]p[/bold cyan]=pipe size · "
                "[bold cyan]x[/bold cyan]=clear · "
                "[bold cyan]t[/bold cyan]=custom · "
                "[bold cyan]v[/bold cyan]=table view",
                id="review-help",
            )
            yield Static("", id="review-visual")
            yield DataTable(id="review-table", cursor_type="row",
                             classes="hidden")
            yield Static(
                "[bold green]Enter[/bold green]=confirm & save · "
                "[bold yellow]s[/bold yellow]=save as draft · "
                "[bold red]Esc[/bold red]=cancel"
            )
            yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#review-table", DataTable)
        table.add_columns("Z", "Result", "Imperial", "Slot / Label")
        self._render_rows()
        self._render_visual()

    # -- rendering ----------------------------------------------------------

    def _render_visual(self) -> None:
        view = self.query_one("#review-visual", Static)
        view.update(render_visual_stack(
            self.members, self.slot_assignment, self.slot_options,
            self.cursor_idx, self.unassigned,
        ))

    def _render_rows(self) -> None:
        table = self.query_one("#review-table", DataTable)
        table.clear()
        # Build a meas_id → slot_idx reverse map so each member's row shows
        # its current slot label.
        slot_by_meas: dict[int, int] = {
            mid: i for i, mid in enumerate(self.slot_assignment) if mid is not None
        }
        for i, m in enumerate(self.members):
            mid = m["meas_id"]
            slot_idx = slot_by_meas.get(mid)
            if slot_idx is None:
                cell = "[dim]unassigned[/dim]"
            else:
                cell = f"{_SLOT_SPECS[slot_idx][0]}"
            table.add_row(str(i + 1), f"{m['result_m']:.4f} m",
                          format_imperial(m["result_m"]), cell)

    def action_toggle_view(self) -> None:
        self._visual_mode = not self._visual_mode
        table = self.query_one("#review-table", DataTable)
        view = self.query_one("#review-visual", Static)
        if self._visual_mode:
            self._render_visual()
            table.add_class("hidden")
            view.remove_class("hidden")
        else:
            self._render_rows()
            table.remove_class("hidden")
            view.add_class("hidden")

    # -- navigation + slot manipulation -------------------------------------

    def action_next(self) -> None:
        if self.cursor_idx < N_SLOTS - 1:
            self.cursor_idx += 1
            self._render_visual()

    def action_prev(self) -> None:
        if self.cursor_idx > 0:
            self.cursor_idx -= 1
            self._render_visual()

    def action_push_down(self) -> None:
        """Push the cursor's measurement down by one slot. Cascades
        everything from the cursor downward; requires at least one empty
        slot at or below the last currently-occupied slot."""
        idx = self.cursor_idx
        if self.slot_assignment[idx] is None:
            return
        # Find the last non-empty slot from idx onward.
        last = idx
        while last < N_SLOTS - 1 and self.slot_assignment[last + 1] is not None:
            last += 1
        if last == N_SLOTS - 1:
            self.app.bell()
            return  # No room to push without losing the bottom item
        # Shift right by 1
        for i in range(last, idx - 1, -1):
            self.slot_assignment[i + 1] = self.slot_assignment[i]
            # Carry per-slot options along with the measurement
            if i in self.slot_options:
                self.slot_options[i + 1] = self.slot_options.pop(i)
        self.slot_assignment[idx] = None
        self.cursor_idx = idx + 1
        self._render_visual()

    def action_push_up(self) -> None:
        """Pull the cursor's measurement up one slot. Single-step undo for J;
        requires the slot above to be empty."""
        idx = self.cursor_idx
        if idx == 0 or self.slot_assignment[idx] is None:
            return
        if self.slot_assignment[idx - 1] is not None:
            self.app.bell()
            return
        self.slot_assignment[idx - 1] = self.slot_assignment[idx]
        self.slot_assignment[idx] = None
        if idx in self.slot_options:
            self.slot_options[idx - 1] = self.slot_options.pop(idx)
        self.cursor_idx = idx - 1
        self._render_visual()

    def action_clear(self) -> None:
        """Clear current slot — measurement becomes unassigned."""
        idx = self.cursor_idx
        mid = self.slot_assignment[idx]
        if mid is None:
            return
        self.slot_assignment[idx] = None
        self.slot_options.pop(idx, None)
        if mid not in self.unassigned:
            self.unassigned.append(mid)
        self._render_visual()

    def action_toggle_foil(self) -> None:
        """On a FOIL/SUBPURLIN slot, flip the label between subpurlin and foil."""
        idx = self.cursor_idx
        if _SLOT_SPECS[idx][2] != "foil_sub":
            return
        if self.slot_assignment[idx] is None:
            return
        current = self.slot_options.get(idx, "subpurlin")
        self.slot_options[idx] = "foil" if current == "subpurlin" else "subpurlin"
        self._render_visual()

    def action_pick_pipe_size(self) -> None:
        """On a PIPE slot, open the size picker."""
        idx = self.cursor_idx
        if _SLOT_SPECS[idx][2] != "pipe":
            return
        if self.slot_assignment[idx] is None:
            return

        def _on_size(size: str | None) -> None:
            if size:
                self.slot_options[idx] = size
                self._render_visual()

        self.app.push_screen(PipeSizePicker(), _on_size)

    def action_custom(self) -> None:
        """Custom label override for the cursor's measurement. Bypasses the
        slot-derived label entirely (recorded as a labels-dict override
        applied on confirm)."""
        if self._custom_active:
            return
        if self.slot_assignment[self.cursor_idx] is None:
            return
        self._custom_active = True
        prompt = Input(placeholder="custom label", id="custom-input")
        self.mount(prompt)
        prompt.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "custom-input":
            return
        mid = self.slot_assignment[self.cursor_idx]
        if mid is not None and event.value.strip():
            self.labels[mid] = event.value.strip()
            self._render_visual()
        event.input.remove()
        self._custom_active = False

    # -- confirm flow -------------------------------------------------------

    def _materialize_labels(self) -> dict[int, str | None]:
        """Compute final labels for every member based on slot assignment.
        Custom overrides in self.labels win; assigned slots get the
        slot-derived label; unassigned members get None."""
        out: dict[int, str | None] = {}
        for i, mid in enumerate(self.slot_assignment):
            if mid is None:
                continue
            if mid in self.labels and self.labels[mid] is not None:
                out[mid] = self.labels[mid]
            else:
                out[mid] = slot_label_for(i, self.slot_options)
        for mid in self.unassigned:
            out[mid] = self.labels.get(mid)  # may be None or custom
        # Members with no entry yet (shouldn't happen, but be safe)
        for m in self.members:
            out.setdefault(m["meas_id"], None)
        return out

    def action_confirm(self) -> None:
        self.on_apply(self._materialize_labels(), True)
        self.dismiss(True)

    def action_save_draft(self) -> None:
        self.on_apply(self._materialize_labels(), False)
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)


class HelpScreen(ModalScreen[None]):
    """One-screen legend for the icons, colors, and key bindings."""

    BINDINGS = [
        Binding("escape,q,question_mark,enter,space", "dismiss", "Close"),
    ]

    DEFAULT_CSS = """
    HelpScreen { align: center middle; }
    #help-box {
        width: 78;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    """

    HELP_TEXT = (
        "[bold]Setup vs. station[/bold]\n"
        "  A [bold]setup[/bold] is a batch of consecutive shots taken within a\n"
        "  short idle window — your vertical stack at one spot. (\"Station\"\n"
        "  is reserved for the X-Y datum itself.)\n"
        "\n"
        "[bold]History glyphs (leftmost column)[/bold]\n"
        "  [dim]·[/dim]   blank — a singleton, not part of a setup\n"
        "  [bold yellow]◐[/bold yellow]   [yellow]draft setup[/yellow] — grouped but not yet reviewed\n"
        "  [bold green]●[/bold green]   [green]confirmed setup[/green] — reviewed and labeled\n"
        "\n"
        "[bold]Label color[/bold]\n"
        "  [yellow]yellow[/yellow] = draft (you can still change it via [bold]l[/bold])\n"
        "  [green]green[/green]  = confirmed (locked in for export)\n"
        "  [strike dim]strikethrough[/strike dim] = soft-deleted (hidden by default)\n"
        "\n"
        "[bold]Keys[/bold]\n"
        "  [bold]q[/bold] quit       [bold]c[/bold] copy last      [bold]o[/bold] set offset\n"
        "  [bold]r[/bold] refresh    [bold]s[/bold] sync settings  [bold]l[/bold] review setup\n"
        "  [bold]D[/bold] show/hide deleted  [bold]U[/bold] undelete last\n"
        "  [bold]?[/bold] this help\n"
        "\n"
        "[bold]Gestures[/bold]\n"
        "  Two error readings within 3s soft-delete the last good shot.\n"
        "  Setups auto-close after 20s of inactivity, then await review."
    )

    def compose(self) -> ComposeResult:
        with Vertical(id="help-box"):
            yield Static(self.HELP_TEXT)
            yield Static("[dim]Press Esc, q, ?, Enter, or Space to close.[/dim]")

    def action_dismiss(self) -> None:
        self.dismiss(None)
