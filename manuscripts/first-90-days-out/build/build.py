#!/usr/bin/env python3
"""Build the First 90 Days Out PDF from the manuscript markdown files.

Usage: python3 build.py --fonts DIR --out FILE.pdf [--draft]

DIR must hold Oswald-500.ttf, Oswald-600.ttf, Oswald-700.ttf (static instances
of Google Fonts' Oswald). Inter must be installed system-wide.

Pipeline: markdown -> styled HTML -> Chromium (Playwright) PDF -> page-number
pass -> rebuild with real contents-page numbers -> stamp running headers and
footers with reportlab/pypdf.
"""
import argparse
import html
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import markdown
from pypdf import PdfReader, PdfWriter
from reportlab.lib.colors import HexColor
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

HERE = Path(__file__).resolve().parent
MS = HERE.parent
SOURCES = [
    "01-front-matter-and-part-one.md",
    "02-part-two.md",
    "03-part-three.md",
    "04-appendices.md",
]

ACCENT = "#2F6B4F"      # this book's color (Unbound uses red)
INK = "#1A1A1A"
MUTED = "#6B6B6B"
TITLE = "FIRST 90 DAYS OUT"


# ---------------------------------------------------------------- markdown

def load_manuscript() -> str:
    text = "\n\n".join((MS / f).read_text() for f in SOURCES)
    # Drop the markdown cover block: the cover is built by hand below.
    start = text.index("## IF YOU NEED HELP RIGHT NOW")
    return text[start:]


def md(s: str) -> str:
    return markdown.markdown(s, extensions=["tables", "sane_lists"])


def lines(n: int) -> str:
    return '<div class="lines">' + '<span></span>' * n + "</div>"


def mk(toc) -> str:
    return f'<span class="mk">QQMARK{len(toc)-1:03d}QQ</span>'


SMALL = {"a", "an", "and", "the", "of", "to", "for", "in", "on", "at", "with", "or", "but", "by"}


def tcase(s: str) -> str:
    words = s.lower().split(" ")
    out = []
    for i, w in enumerate(words):
        parts = w.split("-")
        parts = [pp if (pp in SMALL and (i > 0 or j > 0)) else pp[:1].upper() + pp[1:] for j, pp in enumerate(parts)]
        out.append("-".join(parts))
    return " ".join(out).replace("Days 1–30", "Days 1–30")


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def convert(body_md: str, draft: bool):
    """Turn the manuscript into HTML sections. Returns (html, toc_entries)."""
    toc = []  # (level, label, marker_key)
    out = []
    body_md = re.sub(r"^# (PART [^\n]+)\n+## ([^\n]+)$", r"# \1 || \2", body_md, flags=re.M)
    blocks = re.split(r"\n(?=#{1,2} )", "\n" + body_md)
    for block in blocks:
        block = block.strip("\n")
        if not block.strip():
            continue
        block = re.sub(r"^\s*---\s*$", "", block, flags=re.M)
        head, _, rest = block.partition("\n")
        m1 = re.match(r"^# (.+)$", head)
        m2 = re.match(r"^## (.+)$", head)

        if m1:  # Part divider or appendices divider
            label, _, sub = m1.group(1).strip().partition(" || ")
            intro = md(rest.strip()) if rest.strip() else ""
            if label.startswith("PART"):
                kicker, _, days = label.partition(" · ")
                toc.append((0, f"{kicker} · {sub}", sub))
                out.append(
                    f'<section class="part">{mk(toc)}<p class="kicker">{html.escape(kicker)}'
                    f' · {html.escape(days)}</p><h1>{html.escape(sub)}</h1>'
                    f'<div class="part-intro">{intro}</div></section>')
            else:
                toc.append((0, label, "APPENDICES"))
                out.append(f'<section class="part">{mk(toc)}<p class="kicker">BACK MATTER</p>'
                           f'<h1>{html.escape(label)}</h1></section>')
            continue

        if not m2:
            out.append(md(block))
            continue

        title = m2.group(1).strip()
        cm = re.match(r"^(\d\d) · (.+)$", title)
        am = re.match(r"^([A-I]) · (.+)$", title)
        if cm:
            num, name = cm.groups()
            dek = ""
            dm = re.match(r"^\s*\*(.+?)\*\s*\n", rest + "\n")
            if dm:
                dek = dm.group(1)
                rest = rest[dm.end():]
            toc.append((1, f"{int(num)}|{tcase(name)}", name))
            out.append(
                f'<section class="chapter"><div class="opener">{mk(toc)}<p class="kicker">CHAPTER {num}</p>'
                f'<h1>{html.escape(name)}</h1>'
                + (f'<p class="dek">{html.escape(dek)}</p>' if dek else "")
                + '</div>' + body_html(rest) + "</section>")
        elif am:
            letter, name = am.groups()
            toc.append((1, f"{letter}|{tcase(name)}", name))
            out.append(
                f'<section class="chapter appendix"><div class="opener">{mk(toc)}<p class="kicker">APPENDIX {letter}</p>'
                f'<h1>{html.escape(name)}</h1></div>' + body_html(rest) + "</section>")
        elif title.startswith("WORKBOOK"):
            toc.append((1, f"|{tcase(title)}", title))
            out.append(f'<section class="workbook"><div class="opener">{mk(toc)}<p class="kicker">WRITE IN THIS BOOK</p>'
                       f'<h1>{html.escape(title)}</h1></div>' + workbook_html(rest) + "</section>")
        elif title.startswith("SOURCES"):
            toc.append((1, f"|{tcase(title)}", title))
            out.append(f'<section class="sources">{mk(toc)}<h2>{html.escape(title)}</h2>{md(rest)}</section>')
        elif title == "IF YOU NEED HELP RIGHT NOW":
            out.append(f'<section class="crisis"><h1>{html.escape(title)}</h1>{md(rest)}</section>')
        elif title == "BEFORE YOU BEGIN":
            toc.append((0, "Before You Begin", "BEFORE YOU BEGIN"))
            out.append(f'<section class="chapter"><div class="opener">{mk(toc)}<p class="kicker">FRONT MATTER</p>'
                       f'<h1>BEFORE YOU BEGIN</h1></div>{body_html(rest)}</section>')
        else:
            out.append(f"<h2>{html.escape(title)}</h2>" + md(rest))

    doc = "\n".join(out)
    if draft:
        doc = re.sub(r"(VERIFY[^<.;]*)", r'<mark>\1</mark>', doc)
        doc = re.sub(r"(\[[A-Z][A-Z ,:'.\-]{3,}\])", r'<mark>\1</mark>', doc)
    return doc, toc


def body_html(s: str) -> str:
    """Body of a chapter, with WRITE IN THIS BOOK turned into a ruled box."""
    parts = re.split(r"^### WRITE IN THIS BOOK\s*$", s, flags=re.M)
    html_out = md(parts[0])
    for p in parts[1:]:
        qm = re.match(r"\s*((?:\d+\..*\n?)+)", p)
        qs = re.findall(r"^\d+\.\s+(.+)$", qm.group(1), flags=re.M) if qm else []
        after = p[qm.end():] if qm else p
        if not qs:
            html_out += "<h3>WRITE IN THIS BOOK</h3>" + md(p)
            continue
        box = '<div class="box"><p class="box-head">WRITE IN THIS BOOK</p>'
        for i, q in enumerate(qs, 1):
            box += f'<div class="q"><p><b>{i}.</b> {md_inline(q)}</p>{lines(4)}</div>'
        box += "</div>"
        html_out += box + md(after)
    return html_out


def md_inline(s: str) -> str:
    h = md(s)
    return re.sub(r"^<p>|</p>$", "", h.strip())


def workbook_html(s: str) -> str:
    out = []
    for line in s.split("\n"):
        line = line.strip()
        wm = re.match(r"^\*\*(Week \d+: .+)\*\*$", line)
        dm = re.match(r"^- \*\*Day (\d+)\.\*\*\s+(.+)$", line)
        if wm:
            out.append(f'<h3 class="week">{html.escape(wm.group(1).upper())}</h3>')
        elif dm:
            d, prompt = dm.groups()
            out.append(f'<div class="day"><p class="day-head"><span>DAY {d}</span>'
                       f'<span class="date">DATE ____________</span></p>'
                       f'<p class="prompt">{md_inline(prompt)}</p>{lines(9)}</div>')
        elif line:
            out.append(md(line))
    return "\n".join(out)


# ---------------------------------------------------------------- page HTML

COVER = f"""
<section class="cover">
  <div class="bar"></div>
  <p class="brand">W H A T &nbsp; T H E Y &nbsp; S K I P P E D</p>
  <h1>FIRST<br>90 DAYS<br>OUT</h1>
  <p class="sub">A Workbook for the Space Between</p>
  <div class="told"><p class="strike">WHAT YOU WERE TOLD</p><p>WHAT HAPPENS NEXT</p></div>
  <p class="blurb">Practical steps, honest context, and a page a day for the first three months
  after the floor moves. Every claim sourced.</p>
  <div class="foot"><p>WORKBOOK · FIRST EDITION</p><p class="url">WHATTHEYSKIPPED.COM</p></div>
</section>
"""


def toc_html(toc, pages):
    rows = []
    for level, label, key in toc:
        pg = pages.get(key, "")
        if level == 0:
            rows.append(f'<p class="toc-part"><span>{html.escape(label.upper())}</span>'
                        f'<span class="pg">{pg}</span></p>')
        else:
            num, _, name = label.partition("|")
            rows.append(f'<p class="toc-row"><span class="n">{html.escape(num)}</span>'
                        f'<span class="t">{html.escape(name)}</span><span class="dots"></span>'
                        f'<span class="pg">{pg}</span></p>')
    return '<section class="toc"><h1>CONTENTS</h1>' + "".join(rows) + "</section>"


CSS = f"""
@font-face {{ font-family: Oswald; src: url('OSWALD_URL') format('woff2'); font-weight: 400 700; }}
@page {{ size: Letter; margin: 0.85in 0.8in 0.85in 0.8in; }}
* {{ box-sizing: border-box; }}
html {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
body {{ font-family: Inter, sans-serif; font-size: 10.2pt; line-height: 1.55; color: {INK}; margin: 0; }}
h1, h2, h3, .kicker, .box-head, .day-head, .brand {{ font-family: Oswald, sans-serif; }}
h1 {{ font-weight: 700; text-transform: uppercase; letter-spacing: .01em; line-height: 1.02; }}
h2 {{ font-size: 14pt; font-weight: 600; text-transform: uppercase; margin: 1.6em 0 .5em; break-after: avoid; }}
h3 {{ font-size: 11pt; font-weight: 600; text-transform: uppercase; letter-spacing: .03em;
      margin: 1.5em 0 .4em; padding-top: .7em; border-top: 1px solid #ddd; break-after: avoid; }}
p {{ margin: 0 0 .7em; orphans: 3; widows: 3; }}
ul, ol {{ margin: 0 0 .8em; padding-left: 1.3em; }}
li {{ margin-bottom: .25em; }}
em {{ font-style: italic; }}
blockquote {{ margin: .8em 0 1em; padding: .6em 1em; border-left: 3px solid {ACCENT};
              background: #F3F7F4; break-inside: avoid; }}
blockquote p {{ margin: 0; }}
blockquote p + p {{ margin-top: .6em; }}
table {{ width: 100%; border-collapse: collapse; margin: .6em 0 1.1em; font-size: 9.2pt; break-inside: avoid; }}
th {{ font-family: Oswald, sans-serif; font-weight: 600; text-transform: uppercase; letter-spacing: .04em;
      text-align: left; color: #fff; background: {ACCENT}; padding: 5px 8px; }}
td {{ padding: 5px 8px; border-bottom: 1px solid #ddd; vertical-align: top; }}
mark {{ background: #FFF2A8; }}
section {{ break-before: page; }}
.mk {{ font-size: 1px; color: #fff; position: absolute; }}
@page cover {{ margin: 0; }}
.kicker {{ color: {ACCENT}; font-weight: 600; letter-spacing: .18em; font-size: 9pt; margin: 0 0 .5em; }}

.cover {{ page: cover; break-before: auto; position: relative; height: 11in; width: 8.5in; padding: 1.1in .9in 0 1.35in; overflow: hidden; }}
.cover .bar {{ position: absolute; left: 0; top: 0; bottom: 0; width: .32in; background: {ACCENT}; }}
.cover .brand {{ color: {ACCENT}; font-size: 10pt; font-weight: 600; margin: .4in 0 1.1in; }}
.cover h1 {{ font-size: 64pt; margin: 0 0 .2in; }}
.cover .sub {{ font-size: 15pt; font-weight: 600; margin: 0 0 .7in; }}
.cover .told p {{ font-family: Oswald; font-weight: 700; font-size: 22pt; margin: 0; line-height: 1.1; }}
.cover .strike {{ color: {ACCENT}; text-decoration: line-through; text-decoration-thickness: 3px; }}
.cover .blurb {{ margin-top: .35in; max-width: 4.6in; color: #333; }}
.cover .foot {{ position: absolute; bottom: 1in; }}
.cover .foot p {{ font-family: Oswald; font-weight: 600; letter-spacing: .16em; font-size: 9pt; margin: 0 0 4px; }}
.cover .url {{ color: {ACCENT}; }}

.toc h1 {{ font-size: 30pt; margin: 0 0 .15in; }}
.toc-part {{ display: flex; justify-content: space-between; font-family: Oswald; font-weight: 600;
             letter-spacing: .14em; font-size: 9pt; color: {ACCENT}; border-bottom: 1px solid {ACCENT};
             margin: .16in 0 .04in; padding-bottom: 2px; }}
.toc-row {{ display: flex; align-items: baseline; margin: 0; font-size: 9.2pt; line-height: 1.5; }}
.toc-row .n {{ width: .35in; font-family: Oswald; font-weight: 600; color: {ACCENT}; }}
.toc-row .dots {{ flex: 1; border-bottom: 1px dotted #999; margin: 0 6px; transform: translateY(-3px); }}

.crisis h1 {{ font-size: 26pt; color: {ACCENT}; margin: 0 0 .2in; }}
.crisis ul {{ list-style: none; padding: 0; }}
.crisis li {{ padding: .45em .8em; border-left: 3px solid {ACCENT}; background: #F3F7F4; margin-bottom: .45em; }}

.part {{ display: flex; flex-direction: column; justify-content: center; height: 9.2in; }}
.part .mk {{ font-size: 1px; color: #fff; position: absolute; }}
@page cover {{ margin: 0; }}
.kicker {{ font-size: 11pt; }}
.part h1 {{ font-size: 46pt; margin: 0 0 .3in; }}
.part-intro {{ max-width: 5in; font-size: 11pt; color: #333; border-top: 3px solid {ACCENT}; padding-top: .2in; }}

.opener {{ margin-bottom: .3in; padding-bottom: .15in; border-bottom: 3px solid {ACCENT}; }}
.opener h1 {{ font-size: 34pt; margin: 0; }}
.opener .dek {{ font-size: 12pt; font-style: italic; color: #444; margin: .12in 0 0; }}
.chapter > h3:first-of-type, .opener + h3 {{ border-top: 0; padding-top: 0; }}

.box {{ border: 1.5px solid {ACCENT}; margin: 1.3em 0 1em; break-inside: avoid; }}
.box-head {{ background: {ACCENT}; color: #fff; font-weight: 600; letter-spacing: .16em;
             font-size: 9pt; padding: 4px 10px; margin: 0; }}
.box .q {{ padding: 8px 12px 2px; break-inside: avoid; }}
.box .q p {{ margin: 0 0 4px; }}
.lines span {{ display: block; height: 24px; border-bottom: 1px solid #C9C9C9; }}

.workbook .week {{ border-top: 0; color: {ACCENT}; font-size: 12pt; margin: .1in 0 .1in; }}
.day {{ break-inside: avoid; margin: 0 0 .22in; }}
.day-head {{ display: flex; justify-content: space-between; font-weight: 700; font-size: 12pt;
             border-bottom: 2px solid {ACCENT}; margin: 0 0 4px; padding-bottom: 2px; }}
.day-head .date {{ font-weight: 500; font-size: 8.5pt; letter-spacing: .1em; color: {MUTED}; }}
.day .prompt {{ margin: 0 0 2px; font-weight: 500; }}

.sources {{ font-size: 8.8pt; line-height: 1.45; }}
.sources h2 {{ margin-top: 0; font-size: 16pt; border-bottom: 3px solid {ACCENT}; padding-bottom: 6px; }}
.appendix p em:only-child {{ }}
.sources p strong {{ font-family: Oswald; font-weight: 600; text-transform: uppercase; letter-spacing: .06em; color: {ACCENT}; }}
.appendix p {{ break-inside: avoid; }}
"""


def page_html(body: str, toc: str, fonts: Path) -> str:
    css = CSS.replace("OSWALD_URL", (fonts / "Oswald-latin.woff2").as_uri())
    return (f'<!doctype html><html><head><meta charset="utf-8"><title>{TITLE}</title>'
            f"<style>{css}</style></head><body>{COVER}{toc}{body}</body></html>")


# ---------------------------------------------------------------- rendering

def render(html_path: Path, pdf_path: Path):
    js = f"""
const {{ chromium }} = require('playwright');
(async () => {{
  const b = await chromium.launch();
  const p = await b.newPage();
  await p.goto({json.dumps(html_path.as_uri())}, {{ waitUntil: 'load' }});
  await p.evaluate(() => document.fonts.ready);
  await p.pdf({{ path: {json.dumps(str(pdf_path))}, format: 'Letter', preferCSSPageSize: true, printBackground: true }});
  await b.close();
}})();
"""
    subprocess.run(["node", "-e", js], check=True)


def page_texts(pdf: Path):
    r = PdfReader(str(pdf))
    return [(pg.extract_text() or "") for pg in r.pages]


def find_pages(texts, toc):
    pages = {}
    for n, (_, _, key) in enumerate(toc):
        tag = f"QQMARK{n:03d}QQ"
        for i, t in enumerate(texts):
            if tag in t.replace(" ", ""):
                pages[key] = i + 1
                break
    return pages


def stamp(pdf_in: Path, pdf_out: Path, fonts: Path, toc, pages, draft: bool):
    pdfmetrics.registerFont(TTFont("Osw5", str(fonts / "Oswald-500.ttf")))
    pdfmetrics.registerFont(TTFont("Osw6", str(fonts / "Oswald-600.ttf")))
    reader = PdfReader(str(pdf_in))
    n = len(reader.pages)
    # running header label per page = most recent chapter/appendix/part opener
    starts = sorted((p, key) for key, p in pages.items())
    labels = {}
    for i in range(1, n + 1):
        cur = ""
        for p, key in starts:
            if p <= i:
                cur = key
        labels[i] = cur
    part_pages = {pages[key] for lvl, _, key in toc if lvl == 0 and key in pages and key != "BEFORE YOU BEGIN"}
    writer = PdfWriter()
    accent = HexColor(ACCENT)
    for i, page in enumerate(reader.pages, 1):
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)
        if i > 1 and i not in part_pages:
            buf = io.BytesIO()
            c = canvas.Canvas(buf, pagesize=(w, h))
            c.setFont("Osw6", 7.5)
            c.setFillColor(accent)
            c.drawString(58, h - 34, TITLE)
            c.setFillColor(HexColor(MUTED))
            if labels[i]:
                c.drawRightString(w - 58, h - 34, labels[i][:60])
            c.drawString(58, 30, "FIRST 90 DAYS OUT · WHAT THEY SKIPPED" + ("   ·   REVIEW DRAFT" if draft else ""))
            c.setFillColor(HexColor(INK))
            c.setFont("Osw6", 9)
            c.drawRightString(w - 58, 30, str(i))
            c.save()
            buf.seek(0)
            page.merge_page(PdfReader(buf).pages[0])
        writer.add_page(page)
    writer.add_metadata({"/Title": "First 90 Days Out: A Workbook for the Space Between",
                         "/Author": "What They Skipped"})
    with open(pdf_out, "wb") as f:
        writer.write(f)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fonts", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--draft", action="store_true")
    a = ap.parse_args()
    work = a.out.parent / "build-tmp"
    work.mkdir(parents=True, exist_ok=True)
    body, toc = convert(load_manuscript(), a.draft)

    h1 = work / "pass1.html"
    p1 = work / "pass1.pdf"
    h1.write_text(page_html(body, toc_html(toc, {}), a.fonts))
    render(h1, p1)
    pages = find_pages(page_texts(p1), toc)

    h2 = work / "pass2.html"
    p2 = work / "pass2.pdf"
    h2.write_text(page_html(body, toc_html(toc, pages), a.fonts))
    render(h2, p2)
    pages2 = find_pages(page_texts(p2), toc)
    if pages2 != pages:
        print("warning: page numbers shifted between passes", file=sys.stderr)
    missing = [k for _, _, k in toc if k not in pages2]
    if missing:
        print("warning: not found in PDF:", missing, file=sys.stderr)
    stamp(p2, a.out, a.fonts, toc, pages2, a.draft)
    print(f"wrote {a.out} ({len(PdfReader(str(a.out)).pages)} pages)")


if __name__ == "__main__":
    main()
