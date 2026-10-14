"""Release steps that scripts/release.sh does not do: a CHANGELOG draft from the commits, the
tag pushed, and a GitHub Release whose note is the CHANGELOG section. ADLC-336.

release.sh stays the gate (versions agree, CHANGELOG has the heading, tag not taken, tests pass,
then it tags). `publish` runs it, pushes the tag and makes the Release, so a tag with no release
note, which is how GitHub's "Latest" ended up three minors behind, cannot happen.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from symphony_workflow.ship import Runner, ShipError, _git, run_cmd

VERSION_RE = re.compile(r"^\d+\.\d+\.\d+$")
SQUASH_SUFFIX = re.compile(r" \(#\d+\)$")
GROUPS = (
    ("feat", "Features"),
    ("fix", "Fixes"),
    ("perf", "Performance"),
    ("refactor", "Refactoring"),
    ("docs", "Documentation"),
    ("test", "Tests"),
    ("ci", "CI and build"),
    ("build", "CI and build"),
    ("chore", "Maintenance"),
    ("revert", "Reverts"),
)
_TYPE_RE = re.compile(r"^(?P<type>[a-z]+)(?:\((?P<scope>[^)]+)\))?!?: (?P<rest>.+)$")


def _check_version(version: str) -> None:
    if not VERSION_RE.match(version):
        raise ShipError(f"{version!r} is not a version like 0.9.4 (no leading v)")


def last_tag(repo: Path, run: Runner = run_cmd) -> str:
    return _git(run, repo, "describe", "--tags", "--abbrev=0", "--match", "v*", ok=True)


def draft_changelog(
    version: str,
    *,
    repo: Path,
    since: str | None = None,
    today: date | None = None,
    run: Runner = run_cmd,
) -> str:
    """A `## version` section from the commit subjects since the last tag, grouped by type."""
    _check_version(version)
    since = since if since is not None else last_tag(repo, run)
    span = f"{since}..HEAD" if since else "HEAD"
    out = _git(run, repo, "log", "--format=%s", "--no-merges", span)
    subjects = [s for s in out.splitlines() if s.strip()]
    grouped: dict[str, list[str]] = {}
    for subject in subjects:
        match = _TYPE_RE.match(subject.strip())
        kind, rest = (match["type"], match["rest"]) if match else ("other", subject.strip())
        label = dict(GROUPS).get(kind, "Other")
        scope = f"**{match['scope']}**: " if match and match["scope"] else ""
        grouped.setdefault(label, []).append(f"- {scope}{rest}")
    order = [label for _, label in GROUPS if label in grouped]
    order = list(dict.fromkeys(order)) + (["Other"] if "Other" in grouped else [])
    stamp = (today or date.today()).isoformat()
    lines = [f"## {version} — {stamp}", ""]
    if not subjects:
        lines += ["No changes since the last tag.", ""]
    for label in order:
        lines += [f"### {label}", "", *grouped[label], ""]
    return "\n".join(lines).rstrip() + "\n"


def extract_notes(changelog: str, version: str) -> str:
    """The body of the `## version` section (heading excluded), or '' when there is none."""
    lines = changelog.splitlines()
    heading = re.compile(rf"^## {re.escape(version)}(?=\s|$)")
    start = next((i for i, ln in enumerate(lines) if heading.match(ln)), None)
    if start is None:
        return ""
    end = next((i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")), len(lines))
    return "\n".join(lines[start + 1 : end]).strip()


def release_exists(repo: Path, tag: str, run: Runner = run_cmd) -> bool:
    return run(["gh", "release", "view", tag], repo).returncode == 0


def _create_release(repo: Path, tag: str, notes: str, *, latest: bool, run: Runner) -> None:
    cmd = [
        "gh",
        "release",
        "create",
        tag,
        "--title",
        tag,
        "--notes",
        notes or f"See CHANGELOG.md for {tag}.",
        "--verify-tag",
        "--latest" if latest else "--latest=false",
    ]
    result = run(cmd, repo)
    if result.returncode != 0:
        raise ShipError(f"gh release create {tag} failed: {result.stderr.strip()}")


@dataclass
class PublishResult:
    version: str
    tag: str
    steps: list[str] = field(default_factory=list)


def publish(
    version: str,
    *,
    repo: Path,
    push: bool = True,
    script: str = "scripts/release.sh",
    run: Runner = run_cmd,
) -> PublishResult:
    """release.sh (gate + tag), push the tag, then the GitHub Release. Safe to re-run."""
    _check_version(version)
    tag = f"v{version}"
    result = PublishResult(version, tag)
    exists_locally = bool(_git(run, repo, "tag", "--list", tag))
    if not exists_locally:
        gate = run(["bash", script, version], repo)
        if gate.returncode != 0:
            raise ShipError(
                "release gate failed, nothing tagged:\n"
                + (gate.stdout + gate.stderr).strip()[-600:]
            )
        result.steps.append(f"gate passed, tagged {tag}")
    else:
        result.steps.append(f"{tag} already tagged locally")
    notes = extract_notes((repo / "CHANGELOG.md").read_text(encoding="utf-8"), version)
    if not notes:
        raise ShipError(f"CHANGELOG.md has no body under '## {version}'")
    if push:
        _git(run, repo, "push", "origin", tag)
        result.steps.append(f"pushed {tag}")
    if release_exists(repo, tag, run):
        result.steps.append(f"GitHub Release {tag} already exists")
    else:
        _create_release(repo, tag, notes, latest=True, run=run)
        result.steps.append(f"created GitHub Release {tag} from the CHANGELOG")
    return result


def _version_key(tag: str) -> tuple[int, ...]:
    return tuple(int(p) for p in re.findall(r"\d+", tag))


def missing_releases(repo: Path, run: Runner = run_cmd) -> list[str]:
    """Tags that have no GitHub Release, oldest first."""
    tags = _git(run, repo, "tag", "--list", "v*").split()
    listed = run(
        ["gh", "release", "list", "--limit", "200", "--json", "tagName", "--jq", ".[].tagName"],
        repo,
    )
    if listed.returncode != 0:
        raise ShipError(f"gh release list failed: {listed.stderr.strip()}")
    have = set(listed.stdout.split())
    return sorted((t for t in tags if t not in have), key=_version_key)


def backfill(*, repo: Path, dry_run: bool = False, run: Runner = run_cmd) -> list[str]:
    """A GitHub Release for every tag without one, notes from CHANGELOG. The newest tag overall
    is marked Latest only if it is among those created; older ones never steal the badge."""
    changelog = (repo / "CHANGELOG.md").read_text(encoding="utf-8")
    todo = missing_releases(repo, run)
    all_tags = sorted(_git(run, repo, "tag", "--list", "v*").split(), key=_version_key)
    newest = all_tags[-1] if all_tags else None
    done: list[str] = []
    for tag in todo:
        if not dry_run:
            notes = extract_notes(changelog, tag.lstrip("v"))
            _create_release(repo, tag, notes, latest=(tag == newest), run=run)
        done.append(tag)
    return done
