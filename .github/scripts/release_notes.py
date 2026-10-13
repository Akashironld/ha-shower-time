"""Print the release title (first line), an empty line and the notes for a tag, from CHANGELOG.md.

A changelog section starts with "## vX.Y.Z" and may carry a title: "## v0.11.0 — Water meter, warnings".
Usage: release_notes.py v0.11.0"""
import re
import sys

tag = sys.argv[1]
text = open("CHANGELOG.md", encoding="utf-8").read()
m = re.search(rf"^## {re.escape(tag)}(?:[ \t]+[—-][ \t]+([^\n]+?))?[ \t]*\n(.*?)(?=^## v|\Z)", text, re.M | re.S)
if not m or not m.group(2).strip():
    sys.exit(f"CHANGELOG.md has no section for {tag}")
title = f"{tag} — {m.group(1).strip()}" if m.group(1) else tag
print(title)
print()
print(m.group(2).strip())
print()
print("---")
print("Like it? Support the project on [Ko-fi](https://ko-fi.com/akashironl) ☕")
