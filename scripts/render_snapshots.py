#!/usr/bin/env python3
"""Render docs/snapshots/*.txt as terminal-style SVG images in docs/snapshots/img/.

The text files are real captured output (see the README section in CLAUDE.md); this only gives them
a terminal window and colours so the README can show a picture. The text inside the SVG is still
text. Re-run it after re-capturing a snapshot:  uv run python scripts/render_snapshots.py
"""

from __future__ import annotations

import io
import re
from pathlib import Path

from rich.console import Console
from rich.terminal_theme import MONOKAI
from rich.text import Text

ROOT = Path(__file__).resolve().parent.parent
SNAPSHOTS = ROOT / "docs/snapshots"
OUT = SNAPSHOTS / "img"


def style_line(line: str) -> Text:
    text = Text(line)
    if line.startswith("$ "):
        text.stylize("bold green", 0, 1)
        text.stylize("bold white", 2)
    elif line.startswith("# "):
        text.stylize("italic bright_black")
    elif re.fullmatch(r"\(exit \d+\)", line):
        text.stylize("bold red")
    elif line.startswith(("[you]", "you>")):
        text.stylize("bold cyan")
    elif line.startswith(("[agent]", "agent>")):
        text.stylize("bold magenta")
    else:
        for pattern, style in (
            (r"WARNING", "yellow"),
            (r"\b(MISSING|conflict|Conflicts were left)\b", "bold red"),
            (r"\b(ok|Created|Approved|Saved)\b", "green"),
            (r"\b(Would create|Would update|dry run)\b", "cyan"),
            (r"\b(Updated|pull|push)\b", "blue"),
        ):
            for match in re.finditer(pattern, line):
                text.stylize(style, match.start(), match.end())
    return text


def render(source: Path) -> Path:
    lines = source.read_text(encoding="utf-8").rstrip("\n").split("\n")
    width = min(max(max((len(line) for line in lines), default=60), 60), 118)
    console = Console(
        record=True, width=width, file=io.StringIO(), force_terminal=True, color_system="truecolor"
    )
    for line in lines:
        console.print(style_line(line), overflow="fold", highlight=False)
    OUT.mkdir(parents=True, exist_ok=True)
    target = OUT / f"{source.stem}.svg"
    console.save_svg(str(target), title="symphony-workflow", theme=MONOKAI)
    return target


def main() -> None:
    for source in sorted(SNAPSHOTS.glob("*.txt")):
        print("wrote", render(source).relative_to(ROOT))


if __name__ == "__main__":
    main()
