"""The two READMEs and their snapshots (ADLC-268).

The README is guidance first, with real command output as snapshots. These checks keep it honest:
the English and Traditional Chinese guidance have the same shape and the same snapshots, every
snapshot block is a verbatim excerpt of a captured file, every command shown exists, and nothing
private (a credential, a local path) got captured.
"""

from __future__ import annotations

import re
from pathlib import Path

from typer.main import get_command

from symphony_workflow.cli import app

REPO = Path(__file__).resolve().parent.parent
EN = REPO / "README.md"
ZH = REPO / "README.zh-TW.md"
SNAPSHOTS = REPO / "docs/snapshots"
MARKER = re.compile(r"<!-- snapshot: (docs/snapshots/[\w.-]+) -->")
PRIVATE = re.compile(
    r"/Users/|/var/folders|/private/|/tmp/|lin_api_|ATATT|AKIA[0-9A-Z]{8}|sk-ant-|"
    r"[\w.+-]+@(?!example\.com)[\w-]+\.(?:com|org|net|tw)"
)


def _guidance(path: Path) -> list[str]:
    """Lines before the Reference section (everything after it is the English reference)."""
    lines = path.read_text(encoding="utf-8").split("\n")
    return lines[: next(i for i, line in enumerate(lines) if line == "## Reference")]


def _outline(lines: list[str]) -> list[tuple[int, list[str]]]:
    """(heading level, snapshots referenced in that section), headings outside code fences only."""
    outline: list[tuple[int, list[str]]] = []
    fenced = False
    for line in lines:
        if line.startswith("```"):
            fenced = not fenced
        if fenced:
            continue
        if line.startswith("#"):
            outline.append((len(line) - len(line.lstrip("#")), []))
        elif (m := MARKER.search(line)) and outline:
            outline[-1][1].append(m.group(1))
    return outline


def _snapshot_blocks(lines: list[str]) -> list[tuple[str, list[str]]]:
    blocks, i = [], 0
    while i < len(lines):
        if m := MARKER.search(lines[i]):
            assert lines[i + 1].startswith("```"), f"{m.group(1)} marker without a code block"
            j = i + 2
            while not lines[j].startswith("```"):
                j += 1
            blocks.append((m.group(1), lines[i + 2 : j]))
            i = j
        i += 1
    return blocks


def test_both_readmes_link_to_each_other_at_the_top() -> None:
    for path in (EN, ZH):
        top = "\n".join(path.read_text(encoding="utf-8").split("\n")[:4])
        assert "(README.md)" in top and "(README.zh-TW.md)" in top


def test_the_two_guidance_parts_have_the_same_headings_and_snapshots() -> None:
    en, zh = _outline(_guidance(EN)), _outline(_guidance(ZH))
    assert en == zh
    assert sum(1 for level, _ in en if level == 3) >= 7  # at least seven use cases


def test_both_readmes_have_a_reference_section_and_the_chinese_one_links_to_it() -> None:
    assert "\n## Reference\n" in EN.read_text(encoding="utf-8")
    zh = ZH.read_text(encoding="utf-8")
    assert "\n## Reference\n" in zh and "(README.md#reference)" in zh


def test_every_snapshot_is_referenced_and_every_reference_exists() -> None:
    referenced = {name for path in (EN, ZH) for name, _ in _snapshot_blocks(_guidance(path))}
    on_disk = {f"docs/snapshots/{p.name}" for p in SNAPSHOTS.glob("*.txt")}
    assert referenced == on_disk
    assert all((REPO / name).is_file() for name in referenced)


def test_each_snapshot_block_is_a_verbatim_excerpt_of_the_captured_file() -> None:
    for path in (EN, ZH):
        for name, block in _snapshot_blocks(_guidance(path)):
            source = (REPO / name).read_text(encoding="utf-8").split("\n")
            position = 0
            for line in block:
                if line == "...":  # an excerpt marker, not captured output
                    continue
                assert line in source[position:], f"{path.name}: {name}: {line!r} not in order"
                position = source.index(line, position) + 1


def test_no_credential_or_local_path_in_the_guidance_or_the_snapshots() -> None:
    texts = {p.name: p.read_text(encoding="utf-8") for p in SNAPSHOTS.glob("*.txt")}
    texts["README.md"] = "\n".join(_guidance(EN))
    texts["README.zh-TW.md"] = "\n".join(_guidance(ZH))
    leaks = {n: PRIVATE.findall(t) for n, t in texts.items() if PRIVATE.search(t)}
    assert leaks == {}


def test_every_command_shown_exists_in_the_cli() -> None:
    commands = set(get_command(app).commands)
    shown: set[str] = set()
    for path in (EN, ZH):
        shown |= set(
            re.findall(r"^\$? ?symphony-workflow ([a-z][\w-]*)", "\n".join(_guidance(path)), re.M)
        )
    for snap in SNAPSHOTS.glob("*.txt"):
        shown |= set(
            re.findall(
                r"^\$ symphony-workflow ([a-z][\w-]*)", snap.read_text(encoding="utf-8"), re.M
            )
        )
    assert shown and shown <= commands | {"--help"}, shown - commands


def test_the_guidance_does_not_pin_a_release_that_will_go_stale() -> None:
    assert not re.search(r"symphony-workflow@v\d", "\n".join(_guidance(EN) + _guidance(ZH)))
