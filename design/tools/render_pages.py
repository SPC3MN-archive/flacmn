"""Render every Inkscape page of a multi-page SVG to PNG with headless Edge.

    python design/tools/render_pages.py design/edited-by-user.svg design/png-edited [A1 A2 ...]

Each page is cut out by rewriting the root viewBox to that page's rectangle; nothing in the file is changed.
"""
import re
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
NS = {"sodipodi": "http://sodipodi.sourceforge.net/DTD/sodipodi-0.dtd", "inkscape": "http://www.inkscape.org/namespaces/inkscape"}
LABEL = "{http://www.inkscape.org/namespaces/inkscape}label"


def pages(path: Path) -> list:
    root = ET.parse(path).getroot()
    nv = root.find("sodipodi:namedview", NS)
    out = []
    for p in nv.findall("inkscape:page", NS):
        label = p.get(LABEL, "")
        out.append((label.split(" ")[0], float(p.get("x")), float(p.get("y")), float(p.get("width")), float(p.get("height"))))
    return out


def render(src: Path, out_dir: Path, only=None) -> list:
    text = src.read_text(encoding="utf-8")
    out_dir.mkdir(parents=True, exist_ok=True)
    made = []
    with tempfile.TemporaryDirectory() as tmp:
        for code, x, y, w, h in pages(src):
            if only and code not in only:
                continue
            page_svg = re.sub(r'(<svg\b[^>]*?)\sviewBox="[^"]*"', rf'\1 viewBox="{x:g} {y:g} {w:g} {h:g}"', text, count=1)
            page_svg = re.sub(r'(<svg\b[^>]*?)\swidth="[^"]*"', rf'\1 width="{w:g}"', page_svg, count=1)
            page_svg = re.sub(r'(<svg\b[^>]*?)\sheight="[^"]*"', rf'\1 height="{h:g}"', page_svg, count=1)
            tmp_svg = Path(tmp) / f"{code}.svg"
            tmp_svg.write_text(page_svg, encoding="utf-8")
            png = out_dir / f"{code}.png"
            subprocess.run([EDGE, "--headless=new", "--disable-gpu", "--hide-scrollbars",
                            f"--user-data-dir={Path(tempfile.gettempdir()) / 'iaflac-edge-shot'}",
                            f"--window-size={int(w)},{int(h)}", f"--screenshot={png}", tmp_svg.as_uri()],
                           check=False, capture_output=True, timeout=120)
            made.append(png)
    return made


if __name__ == "__main__":
    for p in render(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve(), set(sys.argv[3:]) or None):
        print(p)
