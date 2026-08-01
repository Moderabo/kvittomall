"""A minimal live progress bar for the console, coordinated with logging_setup so an
ERROR printed mid-run doesn't get visually mangled by the bar's carriage-return redraws.
"""

import shutil
import sys

_active: "ProgressBar | None" = None


def active_bar() -> "ProgressBar | None":
    return _active


class ProgressBar:
    BAR_WIDTH = 30

    def __init__(self, total: int, label: str):
        self.total = total
        self.label = label
        self.count = 0
        self.enabled = total > 0 and sys.stderr.isatty()

    def _width(self) -> int:
        return shutil.get_terminal_size((80, 20)).columns

    def _render(self) -> None:
        frac = self.count / self.total
        filled = int(self.BAR_WIDTH * frac)
        bar = "#" * filled + "-" * (self.BAR_WIDTH - filled)
        text = f"{self.label}: [{bar}] {self.count}/{self.total} ({frac * 100:.0f}%)"
        width = self._width()
        sys.stderr.write("\r" + text[:width].ljust(width))
        sys.stderr.flush()

    def clear_line(self) -> None:
        """Wipes the current line so other output (e.g. a logged error) can print cleanly."""
        if self.enabled:
            width = self._width()
            sys.stderr.write("\r" + " " * width + "\r")
            sys.stderr.flush()

    def redraw(self) -> None:
        if self.enabled:
            self._render()

    def update(self, n: int = 1) -> None:
        self.count += n
        if self.enabled:
            self._render()

    def __enter__(self) -> "ProgressBar":
        global _active
        _active = self
        if self.enabled:
            self._render()
        return self

    def __exit__(self, *exc_info) -> None:
        global _active
        if self.enabled:
            sys.stderr.write("\n")
            sys.stderr.flush()
        _active = None
