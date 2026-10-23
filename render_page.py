#!/usr/bin/env python3
"""Embed data.json into template.html. Usage: python3 render_page.py data.json out.html [--standalone]
--standalone wraps the page in a full HTML document (doctype, charset, mobile viewport) for hosting outside claude.ai."""
import sys, json, pathlib
here = pathlib.Path(__file__).parent
standalone = "--standalone" in sys.argv[3:]
doc = json.loads(pathlib.Path(sys.argv[1]).read_text())
# claude.ai's sandbox can't load outside images, so it keeps the embedded headshots;
# the hosted page links them from NBA.com, which makes it about 2 MB lighter
links = doc.pop("photoLinks", None) or {}
if standalone and links: doc["photos"] = links
data = json.dumps(doc, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
tpl = (here / "template.html").read_text()
page = tpl.replace("/*__DATA__*/null", data, 1)
if standalone:
    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            # no </head><body> here: the template's leading <title>, <link> and <meta> tags must stay in <head>
            # (iOS only reads the home screen icon from there); the parser opens <body> at the first element
            '<meta name="color-scheme" content="light dark">\n' + page + '\n</body></html>\n')
pathlib.Path(sys.argv[2]).write_text(page)
