"""Print the release title (first line), an empty line and the notes for a tag, from CHANGELOG.md.

A changelog section starts with "## vX.Y.Z" and may carry a title: "## v0.11.0 — Water meter, warnings". It may
open with a short line on who should update, and group its bullets under "### " headings (Fixes, Improvements,
After updating…); bullets without a heading go under "### Changes". Lines wrapped in the changelog are joined:
GitHub and HACS show every line break in release notes.
Usage: release_notes.py v0.11.0"""
import re
import sys

tag = sys.argv[1]
text = open("CHANGELOG.md", encoding="utf-8").read()
m = re.search(rf"^## {re.escape(tag)}(?:[ \t]+[—-][ \t]+([^\n]+?))?[ \t]*\n(.*?)(?=^## v|\Z)", text, re.M | re.S)
if not m or not m.group(2).strip():
    sys.exit(f"CHANGELOG.md has no section for {tag}")
title = f"{tag} — {m.group(1).strip()}" if m.group(1) else tag

lines: list[str] = []
for raw in m.group(2).strip().splitlines():
    line = raw.rstrip()
    joins = lines and lines[-1] and line and not line.lstrip().startswith(("- ", "* ", "#", "|", ">"))
    if joins and (raw.startswith("  ") or not lines[-1].startswith(("#", "|"))):
        lines[-1] += " " + line.strip()          # a wrapped line: part of the bullet or paragraph above
    else:
        lines.append(line)
if not any(x.startswith("### ") for x in lines):
    first = next((i for i, x in enumerate(lines) if x.startswith("- ")), len(lines))
    lines[first:first] = ["### Changes"]
out: list[str] = []
for x in lines:
    if x.startswith("### ") and out and out[-1] != "":
        out.append("")
    out.append(x)
    if x.startswith("### "):
        out.append("")
notes = re.sub(r"\n{3,}", "\n\n", "\n".join(out)).strip()
print(title)
print()
print(notes)
print()
print("---")
print("Like it? Support the project on [Ko-fi](https://ko-fi.com/akashironl) ☕")
