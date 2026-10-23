#!/usr/bin/env python3
"""Embed data.json into template.html. Usage: python3 render_page.py data.json out.html [--standalone]
--standalone wraps the page in a full HTML document (doctype, charset, mobile viewport) for hosting outside claude.ai."""
import sys, pathlib
here = pathlib.Path(__file__).parent
data = pathlib.Path(sys.argv[1]).read_text().replace("</", "<\\/")
tpl = (here / "template.html").read_text()
page = tpl.replace("/*__DATA__*/null", data, 1)
if "--standalone" in sys.argv[3:]:
    page = ('<!doctype html><html lang="en"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">'
            '<meta name="color-scheme" content="light dark"></head><body>\n' + page + '\n</body></html>\n')
pathlib.Path(sys.argv[2]).write_text(page)
