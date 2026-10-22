#!/usr/bin/env python3
"""Embed data.json into template.html. Usage: python3 render_page.py data.json out.html"""
import sys, pathlib
here = pathlib.Path(__file__).parent
data = pathlib.Path(sys.argv[1]).read_text().replace("</", "<\\/")
tpl = (here / "template.html").read_text()
pathlib.Path(sys.argv[2]).write_text(tpl.replace("/*__DATA__*/null", data, 1))
