"""Modal screens for the GLM TUI."""
from __future__ import annotations

from typing import Callable

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Footer, Input, Static

from ..format import IN_PER_M, format_imperial
from ..station import (
    PIPE_SIZES, PIPE_SIZE_DEFAULT, PRESET_LABELS,
    format_pipe_label, suggest_labels,
)


def render_visual_stack(members: list, labels: dict[int, str | None],
                        cursor_idx: int = -1,
                        box_width: int = 24,
                        min_gap_rows: int = 1,
                        target_max_gap_rows: int = 8) -> str:
    """Render a vertical stack diagram of station members.

    Members are expected in DESCENDING Z (highest first). Vertical spacing
    between boxes is proportional to the gap between adjacent measurements.
    Each box shows the label (or [unlabeled]); a horizontal connector trails
    off to the imperial value. The cursor row's box is highlighted.

    Returns Rich-markup text suitable for a Static widget."""
    if not members:
        return "[dim](no members)[/dim]"

    # Compute deltas (in inches) between adjacent members
    deltas_in = []
    for i in range(len(members) - 1):
        d_m = members[i]["result_m"] - members[i + 1]["result_m"]
        deltas_in.append(max(0.0, d_m * IN_PER_M))
    max_delta = max(deltas_in, default=1.0) or 1.0
    scale = max_delta / target_max_gap_rows

    out: list[str] = []
    indent = " " * box_width
    for i, m in enumerate(members):
        label = labels.get(m["meas_id"]) or "[dim italic](unlabeled)[/dim italic]"
        imp = format_imperial(m["result_m"])
        # Color the box border by cursor focus
        is_cursor = (i == cursor_idx)
        border_color = "bold cyan" if is_cursor else "white"
        connector_color = "bold cyan" if is_cursor else "dim"
        box_top = f"[{border_color}]┌{'─' * (box_width - 2)}┐[/{border_color}]"
        box_mid = (f"[{border_color}]│[/{border_color}] "
                   f"{label:^{box_width - 4}} "
                   f"[{border_color}]│[/{border_color}]"
                   f"[{connector_color}]──────[/{connector_color}] "
                   f"[bold]{imp}[/bold]")
        box_bot = f"[{border_color}]└{'─' * (box_width - 2)}┘[/{border_color}]"
        out.append(box_top)
        out.append(box_mid)
        out.append(box_bot)
        if i < len(members) - 1:
            delta = deltas_in[i]
            rows = max(min_gap_rows, int(round(delta / scale))) if scale else min_gap_rows
            mid_x = (box_width // 2) - 1
            spacer = " " * mid_x + "[dim]│[/dim]"
            for _ in range(rows):
                out.append(spacer)
            # Annotate the gap with the delta
            out.append(" " * (box_width // 2 + 2) + f"[dim italic]{delta:.1f}\" gap[/dim italic]")
    return "\n".join(out)


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


class StationReviewScreen(ModalScreen[bool]):
    """Modal for reviewing a station's measurements and assigning labels.

    Returns True if the user confirmed (Enter), False if cancelled (Esc).

    Per-row keys:
      1-6: pick from PRESET_LABELS (6 = pipe → opens PipeSizePicker)
      t: free-form custom label
      x: clear label
      j/k or arrows: navigate

    Footer keys:
      Enter: save + mark confirmed
      s: save drafts (don't confirm)
      Esc: cancel without saving
    """

    BINDINGS = [
        # priority=True so DataTable doesn't eat Enter for row-selection
        Binding("enter", "confirm", "Confirm", priority=True),
        Binding("escape", "cancel", "Cancel"),
        Binding("s", "save_draft", "Save draft"),
        Binding("j,down", "next", "Next row"),
        Binding("k,up", "prev", "Prev row"),
        Binding("x", "clear", "Clear label"),
        Binding("t", "custom", "Custom"),
        Binding("v", "toggle_visual", "Visual view"),
        # 1-6 fire via on_key
    ]

    DEFAULT_CSS = """
    StationReviewScreen { align: center middle; }
    #review-box {
        width: 100;
        height: auto;
        border: thick $accent;
        background: $surface;
        padding: 1 2;
    }
    DataTable { height: auto; max-height: 20; }
    #review-visual { height: auto; max-height: 30; padding: 1 0; }
    #review-visual.hidden { display: none; }
    DataTable.hidden { display: none; }
    #custom-input { dock: bottom; height: 3; }
    """

    def __init__(self, station_id: int, members: list,
                 on_apply: Callable[[dict[int, str | None], bool], None]) -> None:
        """`members` is a list of sqlite3.Row from store.station_members(); the
        store returns ascending Z. We display DESCENDING (highest Z at top)
        because that matches how vertical sections are drawn in CAD.
        `on_apply(labels_by_meas_id, confirmed)` is called when the user saves."""
        super().__init__()
        self.station_id = station_id
        # Reverse so highest Z is row 0
        self.members = list(reversed(list(members)))
        self.on_apply = on_apply
        self.labels: dict[int, str | None] = {}
        # Pre-fill with existing labels or suggestions. Suggestions are in
        # ascending Z order (beam → … → deck); since our display is now
        # descending, reverse them so deck lands on the top row.
        suggestions = list(reversed(suggest_labels(len(self.members))))
        for i, m in enumerate(self.members):
            existing = m["station_label"]
            if existing:
                self.labels[m["meas_id"]] = existing
            elif i < len(suggestions):
                self.labels[m["meas_id"]] = suggestions[i]
            else:
                self.labels[m["meas_id"]] = None
        self.cursor_row = 0
        self._custom_active = False
        self._visual_mode = False

    def compose(self) -> ComposeResult:
        with Vertical(id="review-box"):
            yield Static(f"[bold]Station {self.station_id}[/bold]  —  "
                         f"{len(self.members)} member(s)  (sorted high → low)",
                         id="review-title")
            preset_help = "  ".join(f"[bold cyan]{i+1}[/bold cyan]={lbl.split('-')[-1]}"
                                     for i, lbl in enumerate(PRESET_LABELS))
            yield Static(
                f"Pick a label for the highlighted row:  {preset_help}\n"
                f"[bold cyan]t[/bold cyan]=custom · "
                f"[bold cyan]x[/bold cyan]=clear+collapse · "
                f"[bold cyan]j/k[/bold cyan]=move · "
                f"[bold cyan]v[/bold cyan]=toggle visual"
            )
            yield DataTable(id="review-table", cursor_type="row")
            yield Static("", id="review-visual", classes="hidden")
            yield Static(
                "[bold green]Enter[/bold green]=confirm & save · "
                "[bold yellow]s[/bold yellow]=save as draft · "
                "[bold red]Esc[/bold red]=cancel"
            )
            yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#review-table", DataTable)
        table.add_columns("Z", "Result", "Imperial", "Label")
        self._render_rows()
        table.move_cursor(row=0)

    def _render_rows(self) -> None:
        table = self.query_one("#review-table", DataTable)
        table.clear()
        for i, m in enumerate(self.members):
            label = self.labels.get(m["meas_id"]) or "[dim]—[/dim]"
            table.add_row(str(i + 1), f"{m['result_m']:.4f} m",
                          format_imperial(m["result_m"]), label)
        # If visual mode is on, refresh that too
        if self._visual_mode:
            self._render_visual()

    def _render_visual(self) -> None:
        view = self.query_one("#review-visual", Static)
        view.update(render_visual_stack(self.members, self.labels, self.cursor_row))

    def action_toggle_visual(self) -> None:
        self._visual_mode = not self._visual_mode
        table = self.query_one("#review-table", DataTable)
        view = self.query_one("#review-visual", Static)
        if self._visual_mode:
            self._render_visual()
            table.add_class("hidden")
            view.remove_class("hidden")
        else:
            table.remove_class("hidden")
            view.add_class("hidden")

    def _current_meas_id(self) -> int | None:
        if 0 <= self.cursor_row < len(self.members):
            return self.members[self.cursor_row]["meas_id"]
        return None

    def action_next(self) -> None:
        if self.cursor_row < len(self.members) - 1:
            self.cursor_row += 1
            self.query_one("#review-table", DataTable).move_cursor(row=self.cursor_row)
            if self._visual_mode:
                self._render_visual()

    def action_prev(self) -> None:
        if self.cursor_row > 0:
            self.cursor_row -= 1
            self.query_one("#review-table", DataTable).move_cursor(row=self.cursor_row)
            if self._visual_mode:
                self._render_visual()

    def action_clear(self) -> None:
        """Clear the current row's label AND collapse labels below upward.

        If row N is cleared, every labeled row N+1, N+2, … shifts its label
        up by one position (last labeled row becomes empty). This avoids
        re-labeling everything when one slot is removed."""
        idx = self.cursor_row
        if not (0 <= idx < len(self.members)):
            return
        # Shift labels: row[i] gets row[i+1]'s label, for i = idx..len-2
        for i in range(idx, len(self.members) - 1):
            self.labels[self.members[i]["meas_id"]] = self.labels[self.members[i + 1]["meas_id"]]
        # Last row becomes empty
        self.labels[self.members[-1]["meas_id"]] = None
        self._render_rows()

    def action_custom(self) -> None:
        if self._custom_active:
            return
        self._custom_active = True
        prompt = Input(placeholder="custom label", id="custom-input")
        self.mount(prompt)
        prompt.focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "custom-input":
            return
        mid = self._current_meas_id()
        if mid is not None and event.value.strip():
            self.labels[mid] = event.value.strip()
            self._render_rows()
        event.input.remove()
        self._custom_active = False

    def on_key(self, event) -> None:
        if self._custom_active:
            return
        if event.key in ("1", "2", "3", "4", "5", "6"):
            idx = int(event.key) - 1
            label = PRESET_LABELS[idx]
            mid = self._current_meas_id()
            if mid is None:
                return
            event.stop()
            if label == "bottom-of-pipe":
                # Use callback pattern (works across Textual versions, doesn't
                # require an awaitable push_screen result).
                def _on_pipe(size: str | None) -> None:
                    if size:
                        self.labels[mid] = format_pipe_label(size)
                        self._render_rows()
                        self._advance()
                self.app.push_screen(PipeSizePicker(), _on_pipe)
            else:
                self.labels[mid] = label
                self._render_rows()
                self._advance()

    def _advance(self) -> None:
        """Auto-move cursor to next row after a label is applied."""
        if self.cursor_row < len(self.members) - 1:
            self.cursor_row += 1
            self.query_one("#review-table", DataTable).move_cursor(row=self.cursor_row)
            if self._visual_mode:
                self._render_visual()

    def action_confirm(self) -> None:
        self.on_apply(self.labels, True)
        self.dismiss(True)

    def action_save_draft(self) -> None:
        self.on_apply(self.labels, False)
        self.dismiss(True)

    def action_cancel(self) -> None:
        self.dismiss(False)
