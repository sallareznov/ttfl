# TTFL pick helper

Dashboard for TTFL picks: https://sallareznov.github.io/ttfl/

- `build_data.py` pulls the next NBA night from ESPN (schedule, rosters, injuries, game logs, box scores, shots) into `data.json`.
- `render_page.py` embeds `data.json` into `template.html`.
- `.github/workflows/pages.yml` rebuilds and publishes the page every hour. The Live tab reads ESPN's box scores directly in the browser.
