"""Lift every diagram out of the walkthrough into a standalone SVG file.

    .venv/Scripts/python.exe scripts/15_extract_diagrams.py

The walkthrough draws its figures as inline SVG styled by the page's stylesheet.
That is right for the page and useless anywhere else: pull one out and it
renders as black-on-black, because every stroke is `currentColor` and every fill
is a CSS variable defined three hundred lines away.

This extracts each one and makes it self-sufficient — the palette and the class
rules are written into the file, including the dark-mode block, so the same SVG
reads correctly in a README on GitHub in either theme, in a slide, or opened on
its own. The walkthrough stays the source of truth; these are derived, and
rerunning this after editing a diagram is the whole maintenance story.

GitHub renders `.svg` referenced as a markdown image, so the README gets the
real vector diagrams rather than screenshots of them.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config import settings                      # noqa: E402
from src.logging_setup import configure          # noqa: E402

log = configure("extract_diagrams")

SOURCE = settings.PROJECT_ROOT / "docs" / "slopewatch-walkthrough.html"
OUT_DIR = settings.PROJECT_ROOT / "docs" / "diagrams"

# The palette and class rules the inline diagrams rely on, written into every
# extracted file. Both themes, because a README is read in both.
STYLE = """<style>
  svg { color: #151F1C; background: #F8F9F8;
        --surface:#FFFFFF; --sunk:#E9EDEA; --ink-2:#4C5955;
        --accent:#9A5615; --accent-wash:#F3E6D8; --water:#20666E;
        --water-wash:#DDEAEB; --crit:#8F2222; --high:#A2510A;
        --elev:#8A6208; --mod:#456F13; --low:#5A6B72; }
  @media (prefers-color-scheme: dark) {
    svg { color: #E3E9E5; background: #101714;
          --surface:#182220; --sunk:#202C28; --ink-2:#9DABA5;
          --accent:#DA9451; --accent-wash:#31241A; --water:#66B4BC;
          --water-wash:#16292C; --crit:#E07A7A; --high:#DFA05A;
          --elev:#CFB055; --mod:#9BC46A; --low:#93A5AC; }
  }
  .n      { fill: var(--surface); stroke: currentColor; stroke-width: 1.4; }
  .n-sunk { fill: var(--sunk);    stroke: currentColor; stroke-width: 1.4; }
  .n-acc  { fill: var(--accent-wash); stroke: var(--accent); stroke-width: 1.8; }
  .n-wat  { fill: var(--water-wash);  stroke: var(--water);  stroke-width: 1.4; }
  .n-ghost{ fill: none; stroke: currentColor; stroke-width: 1;
            stroke-dasharray: 3 3; opacity: .55; }
  .e      { stroke: currentColor; stroke-width: 1.3; fill: none; }
  .e-acc  { stroke: var(--accent); stroke-width: 2; fill: none; }
  .e-dash { stroke: currentColor; stroke-width: 1.2; fill: none;
            stroke-dasharray: 4 3; opacity: .7; }
  .t      { font: 500 12.5px system-ui, -apple-system, "Segoe UI", sans-serif;
            fill: currentColor; }
  .t-b    { font: 700 12.5px system-ui, -apple-system, "Segoe UI", sans-serif;
            fill: currentColor; }
  .t-s    { font: 400 10.5px system-ui, -apple-system, "Segoe UI", sans-serif;
            fill: var(--ink-2); }
  .t-m    { font: 400 10.5px ui-monospace, "Cascadia Mono", Consolas, monospace;
            fill: var(--ink-2); }
  .t-acc  { fill: var(--accent); font-weight: 600; }
  .t-wat  { fill: var(--water); }
  .ar     { fill: currentColor; }
  .ar-acc { fill: var(--accent); }
</style>"""

FIGURE = re.compile(
    r'<figure[^>]*>\s*<div class="fig-box">\s*(<svg class="dgm".*?</svg>)\s*</div>\s*'
    r'<figcaption>(.*?)</figcaption>\s*</figure>',
    re.DOTALL,
)


def slugify(text: str) -> str:
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"[^a-zA-Z0-9 ]+", " ", text)
    words = text.lower().split()[:5]
    return "-".join(words) or "diagram"


def plain(html: str) -> str:
    """Caption markup reduced to text, for the manifest and alt attributes."""
    text = re.sub(r"<[^>]+>", "", html)
    text = (text.replace("&amp;", "&").replace("&lt;", "<")
                .replace("&gt;", ">").replace("&nbsp;", " "))
    return re.sub(r"\s+", " ", text).strip()


def main() -> int:
    if not SOURCE.exists():
        raise FileNotFoundError(f"{SOURCE} is missing")

    html = SOURCE.read_text(encoding="utf-8")
    figures = FIGURE.findall(html)
    if not figures:
        raise RuntimeError("no figures matched — has the walkthrough markup changed?")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    for stale in OUT_DIR.glob("*.svg"):
        stale.unlink()

    manifest: list[dict] = []
    for index, (svg, caption) in enumerate(figures, start=1):
        label = re.search(r'aria-label="([^"]*)"', svg)
        alt = plain(label.group(1)) if label else plain(caption)

        # The extracted file needs its own namespace and its own stylesheet;
        # inside the page it inherited both.
        standalone = svg.replace(
            '<svg class="dgm"',
            '<svg xmlns="http://www.w3.org/2000/svg" class="dgm"',
            1,
        )
        standalone = standalone.replace(">", ">\n" + STYLE, 1)

        name = f"{index:02d}-{slugify(alt)}.svg"
        (OUT_DIR / name).write_text(
            '<?xml version="1.0" encoding="UTF-8"?>\n' + standalone,
            encoding="utf-8",
        )
        manifest.append({"file": name, "alt": alt, "caption": plain(caption)})
        log.info("wrote %s", name)

    lines = ["# Extracted from docs/slopewatch-walkthrough.html — do not edit by hand.",
             "# Regenerate with: scripts/15_extract_diagrams.py", ""]
    for entry in manifest:
        lines.append(f"{entry['file']}\t{entry['alt']}")
    (OUT_DIR / "MANIFEST.txt").write_text("\n".join(lines), encoding="utf-8")

    log.info("%d diagrams extracted to %s", len(manifest), OUT_DIR)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
