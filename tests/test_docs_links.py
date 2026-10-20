#!/usr/bin/env python3
"""Every relative link in the Markdown files points at a file that exists, and every #anchor at a heading that exists."""
import os
import re
import subprocess
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LINK = re.compile(r"(?<!\!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)|!\[[^\]]*\]\(([^)\s]+)\)")
SKIP = ("http://", "https://", "mailto:", "tel:", "<")


def md_files():
    out = subprocess.run(["git", "ls-files", "-co", "--exclude-standard", "*.md"], cwd=ROOT, capture_output=True, text=True)
    files = out.stdout.split() if out.returncode == 0 else []
    return [f for f in files if not f.startswith("bench/.work/")]


def slug(heading):
    h = re.sub(r"`", "", heading.strip().lower())
    h = re.sub(r"<[^>]+>", "", h)
    h = re.sub(r"[^\w\- ]", "", h, flags=re.UNICODE)
    return h.replace(" ", "-")


def anchors(path):
    found, seen, fenced = set(), {}, False
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.lstrip().startswith("```"):
                fenced = not fenced
                continue
            m = None if fenced else re.match(r"#{1,6}\s+(.*?)\s*#*\s*$", line)
            if m:
                s = slug(m.group(1))
                n = seen.get(s, 0)
                seen[s] = n + 1
                found.add(s if n == 0 else f"{s}-{n}")
            for a in re.findall(r'<a\s+(?:name|id)="([^"]+)"', line):
                found.add(a)
    return found


class DocsLinks(unittest.TestCase):
    def test_relative_links_resolve(self):
        bad = []
        cache = {}
        for rel in md_files():
            fenced = False
            with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
                for n, line in enumerate(f, 1):
                    if line.lstrip().startswith("```"):
                        fenced = not fenced
                    if fenced:
                        continue
                    for m in LINK.finditer(re.sub(r"`[^`]*`", "", line)):
                        target = m.group(1) or m.group(2)
                        if target.startswith(SKIP):
                            continue
                        path, _, frag = target.partition("#")
                        dest = os.path.normpath(os.path.join(ROOT, os.path.dirname(rel), path)) if path else os.path.join(ROOT, rel)
                        if path.startswith("/"):
                            dest = os.path.normpath(os.path.join(ROOT, path.lstrip("/")))
                        if not os.path.exists(dest):
                            bad.append(f"{rel}:{n}: {target} (no such file)")
                        elif frag and dest.endswith(".md"):
                            if dest not in cache:
                                cache[dest] = anchors(dest)
                            if frag.lower() not in cache[dest]:
                                bad.append(f"{rel}:{n}: {target} (no such heading)")
        self.assertEqual(bad, [], "broken links:\n" + "\n".join(bad))


if __name__ == "__main__":
    unittest.main()
