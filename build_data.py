#!/usr/bin/env python3
"""Pull tonight's NBA slate from ESPN and write data.json for the TTFL pick helper.
Usage: python3 build_data.py [YYYYMMDD|next] [out.json]   (default "next": the next night with games still to start)"""
import json, sys, urllib.request, datetime as dt, time, re, base64
from concurrent.futures import ThreadPoolExecutor
from zoneinfo import ZoneInfo

SITE = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba"
COMMON = "https://site.api.espn.com/apis/common/v3/sports/basketball/nba"

# ESPN abbreviation -> NBA.com team id (official logos on cdn.nba.com)
NBA_IDS = {"ATL":1610612737,"BOS":1610612738,"BKN":1610612751,"CHA":1610612766,"CHI":1610612741,"CLE":1610612739,
    "DAL":1610612742,"DEN":1610612743,"DET":1610612765,"GS":1610612744,"HOU":1610612745,"IND":1610612754,"LAC":1610612746,
    "LAL":1610612747,"MEM":1610612763,"MIA":1610612748,"MIL":1610612749,"MIN":1610612750,"NO":1610612740,"NY":1610612752,
    "OKC":1610612760,"ORL":1610612753,"PHI":1610612755,"PHX":1610612756,"POR":1610612757,"SAC":1610612758,"SA":1610612759,
    "TOR":1610612761,"UTAH":1610612762,"WSH":1610612764}

def logo(abbr, variant):
    """Official SVG logo as a data URI (variant L = for light backgrounds, D = for dark)."""
    tid = NBA_IDS.get(abbr)
    if not tid: return None
    for i in range(3):
        try:
            req = urllib.request.Request(f"https://cdn.nba.com/logos/nba/{tid}/primary/{variant}/logo.svg", headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=30) as r:
                svg = r.read().decode("utf-8")
            svg = re.sub(r"<\?xml.*?\?>|<!--.*?-->", "", svg, flags=re.S)
            svg = re.sub(r">\s+<", "><", svg).strip()
            return "data:image/svg+xml;base64," + base64.b64encode(svg.encode()).decode()
        except Exception as e:
            err = e; time.sleep(2 ** i)
    print("logo FAIL", abbr, variant, err, file=sys.stderr); return None

def get(url, tries=4):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                return json.load(r)
        except Exception as e:
            if i == tries - 1:
                print("FAIL", url, e, file=sys.stderr); return None
            time.sleep(2 ** i)

def pmap(fn, items, n=12):
    with ThreadPoolExecutor(n) as ex:
        return list(ex.map(fn, items))

def season_of(d):  # ESPN season year = year the season ends
    return d.year + 1 if d.month >= 8 else d.year

def team_games(tid, season):
    """Completed games for a team: list of dicts with date, opp, home, pf, pa."""
    d = get(f"{SITE}/teams/{tid}/schedule?season={season}&seasontype=2") or {}
    out, upcoming = [], []
    for ev in d.get("events", []):
        c = ev["competitions"][0]
        me = next(x for x in c["competitors"] if x["id"] == str(tid))
        op = next(x for x in c["competitors"] if x["id"] != str(tid))
        done = c.get("status", {}).get("type", {}).get("completed")
        g = {"id": ev["id"], "date": ev["date"], "opp": op["team"]["abbreviation"], "home": me["homeAway"] == "home"}
        if done and me.get("score") and op.get("score"):
            g["pf"] = float(me["score"]["value"]); g["pa"] = float(op["score"]["value"])
            out.append(g)
        else:
            upcoming.append(g)
    return out, upcoming

def parse_gamelog(d):
    """-> list of game rows [date, opp, home, min, pts, reb, ast, stl, blk, fgm, fga, tpm, tpa, ftm, fta, to, type]"""
    if not d or "names" not in d: return []
    names = d["names"]; idx = {n: i for i, n in enumerate(names)}
    rows = []
    for st in d.get("seasonTypes", []):
        name = st.get("displayName", "")
        typ = "post" if "Postseason" in name else "pre" if "Preseason" in name else "reg"
        if typ == "pre": continue
        for cat in st.get("categories", []):
            for e in cat.get("events", []) or []:
                meta = d["events"].get(e["eventId"]); s = e["stats"]
                if not meta: continue
                def num(k):
                    try: return float(s[idx[k]])
                    except: return 0.0
                def pair(k):
                    try: m, a = s[idx[k]].split("-"); return float(m), float(a)
                    except: return 0.0, 0.0
                fgm, fga = pair("fieldGoalsMade-fieldGoalsAttempted")
                tpm, tpa = pair("threePointFieldGoalsMade-threePointFieldGoalsAttempted")
                ftm, fta = pair("freeThrowsMade-freeThrowsAttempted")
                home = meta.get("atVs") == "vs"
                rows.append([meta["gameDate"][:10], meta["opponent"]["abbreviation"], int(home), num("minutes"),
                             num("points"), num("totalRebounds"), num("assists"), num("steals"), num("blocks"),
                             fgm, fga, tpm, tpa, ftm, fta, num("turnovers"), typ])
    rows.sort(key=lambda r: r[0], reverse=True)
    return rows

def play_secs(p):
    """Seconds of game time elapsed at a play (12-min quarters, 5-min overtimes)."""
    n = p["period"]["number"]; v = (p.get("clock") or {}).get("displayValue", "0")
    try: left = int(v.split(":")[0]) * 60 + float(v.split(":")[1]) if ":" in v else float(v)
    except: left = 0
    start, length = ((n - 1) * 720, 720) if n <= 4 else (2880 + (n - 5) * 300, 300)
    return round(start + length - left)

def play_ttfl(p):
    """TTFL points a play gives each player involved: list of (athlete id, points)."""
    t, txt = p["type"]["text"], p.get("text", "")
    ids = [x["athlete"]["id"] for x in p.get("participants", []) if x.get("athlete")]
    if not ids: return []
    if p.get("shootingPlay"):
        ft = "Free Throw" in t
        if p.get("scoringPlay"):
            v = p.get("scoreValue", 0)  # points + made shot (+ made three)
            return [(ids[0], v + 1 + (v == 3))] + ([(ids[1], 1)] if not ft and len(ids) > 1 else [])  # assist
        miss = -1 if ft else -(1 + (p.get("pointsAttempted") == 3))
        return [(ids[0], miss)] + ([(ids[1], 1)] if not ft and len(ids) > 1 and "block" in txt else [])
    if "Rebound" in t: return [(ids[0], 1)]
    if "Turnover" in t or "turnover" in txt:
        return [(ids[0], -1)] + ([(ids[1], 1)] if len(ids) > 1 and "steal" in txt else [])
    return []

def score_evolution(plays):
    """Running TTFL score per player from the play-by-play: {id: [[seconds, score], ...]}."""
    tot, ev = {}, {}
    for p in plays:
        for aid, v in play_ttfl(p):
            tot[aid] = tot.get(aid, 0) + v
            ev.setdefault(aid, [[0, 0]]).append([play_secs(p), tot[aid]])
    return ev

def box_rows(event_id, date):
    """TTFL rows for every player who played in a finished game: list of (player dict, row)."""
    d = get(f"{SITE}/summary?event={event_id}") or {}
    teams = d.get("boxscore", {}).get("players", [])
    if len(teams) != 2: return []
    plays = d.get("plays") or []
    evo = score_evolution(plays)
    end = max([play_secs(p) for p in plays] + [2880])
    out = []
    for i, t in enumerate(teams):
        me, opp = t["team"]["abbreviation"], teams[1 - i]["team"]["abbreviation"]
        home = None
        for c in (d.get("header", {}).get("competitions") or [{}])[0].get("competitors", []):
            if c.get("team", {}).get("abbreviation") == me: home = c.get("homeAway") == "home"
        for st in t.get("statistics", []):
            idx = {n: k for k, n in enumerate(st.get("names", []))}
            for a in st.get("athletes", []):
                s = a.get("stats") or []
                if a.get("didNotPlay") or not s: continue
                def num(k):
                    try: return float(s[idx[k]])
                    except: return 0.0
                def pair(k):
                    try: m, x = s[idx[k]].split("-"); return float(m), float(x)
                    except: return 0.0, 0.0
                fgm, fga = pair("FG"); tpm, tpa = pair("3PT"); ftm, fta = pair("FT")
                row = [date, opp, int(bool(home)), num("MIN"), num("PTS"), num("REB"), num("AST"), num("STL"), num("BLK"),
                       fgm, fga, tpm, tpa, ftm, fta, num("TO")]
                if row[3] <= 0: continue
                aid = a["athlete"]["id"]
                curve = evo.get(aid, [[0, 0]])
                score = row[4]+row[5]+row[6]+row[7]+row[8]+row[9]+row[11]+row[13]-row[15]-(row[10]-row[9])-(row[12]-row[11])-(row[14]-row[13])
                curve = curve + [[end, score]]  # flat to the final buzzer, ending on the official box-score total
                out.append(({"id": aid, "name": a["athlete"]["displayName"], "team": me,
                             "starter": bool(a.get("starter")), "evo": curve}, row))
    return out

CACHE = __import__("pathlib").Path(__file__).resolve().parent / "cache"

def game_shots(event_id):
    """Field-goal attempts of a finished game from the play-by-play:
    {"date", "home", "away", "shots": [[athlete id, x, y, made, three], ...]}.
    x runs 0-50 ft across the court, y is feet from the basket toward half court."""
    d = get(f"{SITE}/summary?event={event_id}")
    comp = ((d or {}).get("header", {}).get("competitions") or [{}])[0]
    if not d or not comp.get("status", {}).get("type", {}).get("completed"): return None
    side = {c.get("homeAway"): c.get("team", {}).get("abbreviation") for c in comp.get("competitors", [])}
    day = dt.datetime.fromisoformat(comp["date"].replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York")).date()
    shots = []
    for p in d.get("plays") or []:
        if not p.get("shootingPlay") or "Free Throw" in p["type"]["text"]: continue
        if "heave" in p.get("text", "").lower(): continue  # end-of-quarter heaves are left out of the box score
        c, ids = p.get("coordinate") or {}, [x["athlete"]["id"] for x in p.get("participants", []) if x.get("athlete")]
        if not ids or not (0 <= c.get("x", -1) <= 50) or not (-5 <= c.get("y", -99) <= 94): continue
        shots.append([ids[0], c["x"], c["y"], int(bool(p.get("scoringPlay"))), int(p.get("pointsAttempted") == 3)])
    return {"date": day.isoformat(), "home": side.get("home"), "away": side.get("away"), "shots": shots}

def season_shots(event_ids, season):
    """Shots for every finished game of the season. Finished games never change, so they are cached on disk
    and each refresh only downloads the games played since the last one."""
    path = CACHE / f"shots-{season}.json"
    try: cache = json.loads(path.read_text())
    except Exception: cache = {}
    todo = [e for e in event_ids if e not in cache]
    for e, g in zip(todo, pmap(game_shots, todo)):
        if g: cache[e] = g
    try:
        CACHE.mkdir(exist_ok=True); tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, separators=(",", ":"))); tmp.replace(path)
    except Exception as e:
        print("shot cache not saved:", e, file=sys.stderr)
    return cache

def encode_shots(cache):
    """Compact shot data for the page: games list, and per player "game:shots,game:shots" where a game is its
    index in base 36 and each shot is 3 base-36 chars of ((x * 100 + y + 5) * 2 + made) * 2 + three."""
    b36 = lambda n: "0123456789abcdefghijklmnopqrstuvwxyz"[n] if n < 36 else b36(n // 36) + b36(n % 36)
    games, per = [], {}
    for k, (eid, g) in enumerate(sorted(cache.items(), key=lambda kv: kv[1]["date"])):
        games.append([g["date"], g["home"], g["away"]])
        mine = {}
        for aid, x, y, made, three in g["shots"]:
            v = ((x * 100 + y + 5) * 2 + made) * 2 + three
            mine.setdefault(aid, []).append(b36(v).rjust(3, "0"))
        for aid, sh in mine.items(): per.setdefault(aid, []).append(b36(k) + ":" + "".join(sh))
    return games, {aid: ",".join(v) for aid, v in per.items()}

TEAM_STATS = ["FG", "Field Goal %", "3PT", "Three Point %", "FT", "Free Throw %", "Rebounds", "Offensive Rebounds",
              "Defensive Rebounds", "Assists", "Steals", "Blocks", "Total Turnovers", "Fouls", "Points in Paint",
              "Fast Break Points", "Points Conceded Off Turnovers", "Largest Lead", "Lead Changes", "Percent Led"]

def game_detail(event_id):
    """Game page data for a finished game: line score, team stats, box score and play-by-play.
    p (players): [side 0 away / 1 home, id, name, starter, min, pts, reb, ast, stl, blk, fgm, fga, tpm, tpa, ftm, fta, to, oreb, dreb, pf, +/-]
                 or [side, id, name, starter, "DNP reason"] for players who did not play.
    pbp: [period, clock, side (-1 none), away score, home score, scoring, text, [[player index, TTFL points], ...]]"""
    d = get(f"{SITE}/summary?event={event_id}")
    comp = ((d or {}).get("header", {}).get("competitions") or [{}])[0]
    if not d or not comp.get("status", {}).get("type", {}).get("completed"): return None
    cs = {c.get("homeAway"): c for c in comp.get("competitors", [])}
    if set(cs) != {"home", "away"}: return None
    side_of_id = {cs[k]["team"]["id"]: i for i, k in enumerate(("away", "home"))}
    side_of_abbr = {cs[k]["team"]["abbreviation"]: i for i, k in enumerate(("away", "home"))}
    day = dt.datetime.fromisoformat(comp["date"].replace("Z", "+00:00")).astimezone(ZoneInfo("America/New_York")).date()
    num = lambda v: float(v) if v not in (None, "", "--") and re.fullmatch(r"[+-]?\d+(\.\d+)?", str(v)) else 0.0
    players, idx = [], {}
    for t in d.get("boxscore", {}).get("players", []):
        side = side_of_abbr.get(t["team"]["abbreviation"])
        if side is None: continue
        for st in t.get("statistics", []):
            k = {n: i for i, n in enumerate(st.get("names", []))}
            for a in st.get("athletes", []):
                s, aid = a.get("stats") or [], a["athlete"]["id"]
                idx[aid] = len(players)
                head = [side, aid, a["athlete"]["displayName"], int(bool(a.get("starter")))]
                if a.get("didNotPlay") or not s:
                    players.append(head + [" ".join(w.capitalize() for w in (a.get("reason") or "DNP").split())]); continue
                g = lambda n: s[k[n]] if n in k and k[n] < len(s) else ""
                pr = lambda n: [num(x) for x in (g(n).split("-") + ["0", "0"])[:2]] if g(n) else [0.0, 0.0]
                players.append(head + [num(g("MIN")), num(g("PTS")), num(g("REB")), num(g("AST")), num(g("STL")), num(g("BLK")),
                                       *pr("FG"), *pr("3PT"), *pr("FT"), num(g("TO")), num(g("OREB")), num(g("DREB")), num(g("PF")), num(g("+/-"))])
    tstats = [{}, {}]
    for t in d.get("boxscore", {}).get("teams", []):
        side = side_of_abbr.get(t["team"]["abbreviation"])
        if side is not None: tstats[side] = {x["label"]: x.get("displayValue") for x in t.get("statistics", [])}
    pbp = []
    for p in d.get("plays") or []:
        tt = [[idx[a], v] for a, v in play_ttfl(p) if a in idx]
        pbp.append([p["period"]["number"], (p.get("clock") or {}).get("displayValue", ""), side_of_id.get((p.get("team") or {}).get("id"), -1),
                    p.get("awayScore", 0), p.get("homeScore", 0), int(bool(p.get("scoringPlay"))), p.get("text", ""), tt])
    return {"date": day.isoformat(), "away": cs["away"]["team"]["abbreviation"], "home": cs["home"]["team"]["abbreviation"],
            "score": [cs["away"].get("score"), cs["home"].get("score")],
            "lines": [[x.get("displayValue") for x in cs[k].get("linescores") or []] for k in ("away", "home")],
            "teamStats": [[tstats[i].get(n, "") for n in TEAM_STATS] for i in (0, 1)], "p": players, "pbp": pbp}

def season_games(event_ids, season, pbp_games=80):
    """Game pages for every finished game of the season, cached on disk like the shots. Box scores for all of them,
    play-by-play for the most recent pbp_games only, to keep the page under its size limit."""
    path = CACHE / f"games-{season}.json"
    try: cache = json.loads(path.read_text())
    except Exception: cache = {}
    todo = [e for e in event_ids if e not in cache]
    for e, g in zip(todo, pmap(game_detail, todo)):
        if g: cache[e] = g
    try:
        CACHE.mkdir(exist_ok=True); tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, separators=(",", ":"))); tmp.replace(path)
    except Exception as e:
        print("game cache not saved:", e, file=sys.stderr)
    recent = set(sorted(cache, key=lambda e: (cache[e]["date"], e))[-pbp_games:])
    return {e: {**g, "pbp": g["pbp"] if e in recent else None} for e, g in cache.items()}

def nba_person_ids():
    """Full name -> NBA.com person id (for headshots), from the nba_api static player list."""
    try:
        with urllib.request.urlopen("https://raw.githubusercontent.com/swar/nba_api/master/src/nba_api/stats/library/data.py", timeout=30) as r:
            src = r.read().decode("utf-8")
    except Exception as e:
        print("nba ids FAIL", e, file=sys.stderr); return {}
    out = {}
    for pid, name, active in re.findall(r'\[(\d+), "[^"]*", "[^"]*", "([^"]+)", (True|False)\]', src):
        if active == "True" or name_key(name) not in out: out[name_key(name)] = int(pid)
    return out

def name_key(n):  # "Jaren Jackson Jr." / "Nikola Jokić" -> "jaren jackson" / "nikola jokic"
    import unicodedata
    n = unicodedata.normalize("NFKD", n).encode("ascii", "ignore").decode().lower()
    n = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", n)
    return " ".join(re.sub(r"[^a-z ]", "", n).split())

def headshot(pid):
    """Small NBA.com headshot as a data URI (WebP when Pillow is available)."""
    try:
        req = urllib.request.Request(f"https://cdn.nba.com/headshots/nba/latest/260x190/{pid}.png", headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=30) as r: png = r.read()
    except Exception:
        return None
    try:
        from PIL import Image
        import io
        im = Image.open(io.BytesIO(png)).convert("RGBA"); im.thumbnail((130, 95))
        buf = io.BytesIO(); im.save(buf, "WEBP", quality=80)
        return "data:image/webp;base64," + base64.b64encode(buf.getvalue()).decode()
    except Exception:
        return "data:image/png;base64," + base64.b64encode(png).decode()

def finished_games(date):
    """Completed regular-season or playoff games on a date (YYYYMMDD)."""
    sb = get(f"{SITE}/scoreboard?dates={date}") or {}
    out = []
    for ev in sb.get("events", []):
        c = ev["competitions"][0]
        if (ev.get("season") or {}).get("type") not in (2, 3, 5): continue
        if not c["status"]["type"].get("completed"): continue
        h = next(x for x in c["competitors"] if x["homeAway"] == "home")
        a = next(x for x in c["competitors"] if x["homeAway"] == "away")
        out.append({"id": ev["id"], "home": h["team"]["abbreviation"], "away": a["team"]["abbreviation"],
                    "homeScore": h.get("score"), "awayScore": a.get("score")})
    return out

def schedule_day(date):
    """Every game on a date (YYYYMMDD) for the Schedule tab, finished ones with their score."""
    sb = get(f"{SITE}/scoreboard?dates={date}") or {}
    out = []
    for ev in sb.get("events", []):
        c = ev["competitions"][0]
        h = next(x for x in c["competitors"] if x["homeAway"] == "home")
        a = next(x for x in c["competitors"] if x["homeAway"] == "away")
        rec = lambda x: next((r.get("summary") for r in x.get("records") or [] if r.get("type") == "total"), None)
        tv = [n for b in c.get("broadcasts") or [] if b.get("market") == "national" for n in b.get("names") or []]
        st = c["status"]["type"]
        out.append({"id": ev["id"], "time": ev["date"], "state": st.get("state"), "detail": st.get("shortDetail"),
                    "home": h["team"]["abbreviation"], "away": a["team"]["abbreviation"],
                    "homeScore": h.get("score") if st.get("state") != "pre" else None,
                    "awayScore": a.get("score") if st.get("state") != "pre" else None,
                    "homeRec": rec(h), "awayRec": rec(a), "tv": tv,
                    "venue": (c.get("venue") or {}).get("fullName"),
                    "odds": ((c.get("odds") or [{}])[0]).get("details"),
                    "type": (ev.get("season") or {}).get("type")})
    return out

def main():
    et = ZoneInfo("America/New_York")
    out_path = sys.argv[2] if len(sys.argv) > 2 else "data.json"
    if len(sys.argv) > 1 and sys.argv[1] != "next":
        date = sys.argv[1]; sb = get(f"{SITE}/scoreboard?dates={date}")
    else:
        # next set of games: the first day (from today, US Eastern) that still has a game not yet started
        d0 = dt.datetime.now(et).date()
        for i in range(21):
            date = (d0 + dt.timedelta(days=i)).strftime("%Y%m%d")
            sb = get(f"{SITE}/scoreboard?dates={date}") or {}
            if any(ev["competitions"][0]["status"]["type"]["state"] == "pre" for ev in sb.get("events", [])):
                break
    day = dt.datetime.strptime(date, "%Y%m%d").date()
    cur_season = season_of(day); prev_season = cur_season - 1
    games, teams_tonight = [], {}
    for ev in sb.get("events", []):
        c = ev["competitions"][0]
        h = next(x for x in c["competitors"] if x["homeAway"] == "home")
        a = next(x for x in c["competitors"] if x["homeAway"] == "away")
        odds = (c.get("odds") or [{}])[0]
        fav = None
        if odds.get("homeTeamOdds", {}).get("favorite"): fav = h["team"]["abbreviation"]
        elif odds.get("awayTeamOdds", {}).get("favorite"): fav = a["team"]["abbreviation"]
        g = {"id": ev["id"], "time": ev["date"], "status": c["status"]["type"]["name"],
             "home": h["team"]["abbreviation"], "away": a["team"]["abbreviation"],
             "homeId": h["team"]["id"], "awayId": a["team"]["id"],
             "overUnder": odds.get("overUnder"), "spread": abs(odds["spread"]) if odds.get("spread") is not None else None,
             "favorite": fav, "oddsText": odds.get("details")}
        games.append(g)
        teams_tonight[g["homeId"]] = g["home"]; teams_tonight[g["awayId"]] = g["away"]

    # Back-to-backs: teams that played yesterday
    yday = (day - dt.timedelta(days=1)).strftime("%Y%m%d")
    yb = get(f"{SITE}/scoreboard?dates={yday}") or {}
    played_yday = {x["team"]["abbreviation"] for ev in yb.get("events", []) for x in ev["competitions"][0]["competitors"]}

    # Team context for all 30 teams (defense and pace), previous + current season
    tl = get(f"{SITE}/teams")["sports"][0]["leagues"][0]["teams"]
    all_teams = {t["team"]["id"]: t["team"]["abbreviation"] for t in tl}
    team_names = {t["team"]["abbreviation"]: t["team"]["displayName"] for t in tl}
    def team_ctx(tid):
        prev, _ = team_games(tid, prev_season)
        cur, upcoming = team_games(tid, cur_season)
        season_ids.update(g["id"] for g in cur)
        def agg(gs):
            n = len(gs)
            return {"gp": n, "pf": sum(g["pf"] for g in gs) / n if n else None,
                    "pa": sum(g["pa"] for g in gs) / n if n else None}
        return {"abbr": all_teams[tid], "prev": agg(prev), "cur": agg(cur), "last5": agg(cur[-5:]) if cur else None}
    team_list = list(all_teams)
    season_ids = set()
    ctx = dict(zip(team_list, pmap(team_ctx, team_list)))
    teams = {v["abbr"]: v for v in ctx.values()}

    # Rosters for all 30 teams (Rosters tab); tonight's teams also get game logs below
    roster_raw = dict(zip(team_list, pmap(lambda tid: get(f"{SITE}/teams/{tid}/roster") or {}, team_list)))
    athletes = [(a, teams_tonight[tid]) for tid in teams_tonight for a in roster_raw[tid].get("athletes", [])]
    rosters = {}
    for tid, d in roster_raw.items():
        coach = (d.get("coach") or [{}])[0]
        rosters[all_teams[tid]] = {
            "coach": " ".join(x for x in (coach.get("firstName"), coach.get("lastName")) if x) or None,
            "players": [{"id": a["id"], "name": a["displayName"], "jersey": a.get("jersey"),
                         "pos": (a.get("position") or {}).get("abbreviation"), "age": a.get("age"),
                         "ht": a.get("displayHeight"), "wt": a.get("weight"),
                         "exp": (a.get("experience") or {}).get("years"),
                         "college": (a.get("college") or {}).get("shortName")} for a in d.get("athletes", [])]}

    # Schedule: the slate night and the 13 nights after it
    sched_days = [(day + dt.timedelta(days=k)).strftime("%Y%m%d") for k in range(14)]
    schedule = [{"date": f"{d[:4]}-{d[4:6]}-{d[6:]}", "games": g} for d, g in zip(sched_days, pmap(schedule_day, sched_days))]

    inj = get(f"{SITE}/injuries") or {}
    injuries = {}
    for t in inj.get("injuries", []):
        for i in t.get("injuries", []):
            aid = i["athlete"]["links"][0]["href"].split("/id/")[1].split("/")[0] if i["athlete"].get("links") else None
            if aid:
                injuries[aid] = {"status": i.get("status"), "short": i.get("shortComment"),
                                 "detail": (i.get("details") or {}).get("type"), "date": i.get("date"),
                                 "ret": (i.get("details") or {}).get("returnDate")}

    def player(item):
        a, team = item
        aid = a["id"]
        rows = parse_gamelog(get(f"{COMMON}/athletes/{aid}/gamelog?season={prev_season}"))
        rows = parse_gamelog(get(f"{COMMON}/athletes/{aid}/gamelog?season={cur_season}")) + rows
        # keep unique by date (current-season fetch can echo the previous season before games are played)
        seen, uniq = set(), []
        for r in rows:
            if r[0] not in seen: seen.add(r[0]); uniq.append(r)
        cur_from = f"{cur_season - 1}-08-01"
        return {"id": aid, "name": a["displayName"], "team": team, "pos": (a.get("position") or {}).get("abbreviation"),
                "exp": (a.get("experience") or {}).get("years"),
                "inj": injuries.get(aid),
                "cur": [r[:16] for r in uniq if r[0] >= cur_from and r[16] == "reg"],
                "prev": [r[:16] for r in uniq if r[0] < cur_from and r[16] == "reg"],
                "post": [r[:16] for r in uniq if r[0] < cur_from and r[16] == "post"]}
    players = pmap(player, athletes)

    # TTFL averages for every rostered player (Rosters tab): reuse tonight's logs, fetch the rest
    def ttfl_avg(rows):
        rows = [r for r in rows if r[3] > 0]
        if not rows: return None, 0
        tot = sum(r[4]+r[5]+r[6]+r[7]+r[8]+r[9]+r[11]+r[13]-r[15]-(r[10]-r[9])-(r[12]-r[11])-(r[14]-r[13]) for r in rows)
        return round(tot / len(rows), 1), len(rows)
    def stat_line(rows):  # per-game averages of [min, pts, reb, ast, stl, blk, fgm, fga, tpm, tpa, ftm, fta, to] + games
        rows = [r for r in rows if r[3] > 0]
        if not rows: return None
        return [round(sum(r[k] for r in rows) / len(rows), 1) for k in range(3, 16)] + [len(rows)]
    logs = {p["id"]: p for p in players}
    rest = [(a, all_teams[tid]) for tid, d in roster_raw.items() if tid not in teams_tonight for a in d.get("athletes", [])]
    logs.update({p["id"]: p for p in pmap(player, rest)})
    for r in rosters.values():
        for rp in r["players"]:
            lg = logs.get(rp["id"])
            if not lg: continue
            rp["avg"], rp["gp"] = ttfl_avg(lg["cur"])
            rp["prevAvg"], rp["prevGp"] = ttfl_avg(lg["prev"])
            # player page: season stat lines and every game of both seasons (game log, VS team/player, splits);
            # row = [date, opp, home, min, pts, reb, ast, stl, blk, fgm, fga, tpm, tpa, ftm, fta, to] + [1] for playoffs
            rp["line"], rp["prevLine"] = stat_line(lg["cur"]), stat_line(lg["prev"])
            rp["log"] = sorted(lg["cur"] + lg["prev"] + [r + [1] for r in lg["post"]], key=lambda x: x[0], reverse=True)

    shot_games, shot_map = encode_shots(season_shots(sorted(season_ids), cur_season))
    game_pages = season_games(sorted(season_ids), cur_season)

    for g in games:
        g["b2b"] = [t for t in (g["home"], g["away"]) if t in played_yday]

    # League-wide injury report
    injury_list = []
    for t in inj.get("injuries", []):
        abbr = all_teams.get(str(t.get("id")))
        for i in t.get("injuries", []):
            aid = i["athlete"]["links"][0]["href"].split("/id/")[1].split("/")[0] if i["athlete"].get("links") else None
            injury_list.append({"id": aid, "name": i["athlete"].get("displayName"), "team": abbr,
                                "pos": (i["athlete"].get("position") or {}).get("abbreviation"),
                                "status": i.get("status"), "short": i.get("shortComment"),
                                "detail": (i.get("details") or {}).get("type"), "date": i.get("date"),
                                "ret": (i.get("details") or {}).get("returnDate")})

    # Recent box scores (last 15 days before the slate): last night's scores and in-form players
    days = [(day - dt.timedelta(days=k)).strftime("%Y%m%d") for k in range(1, 16)]
    games_by_day = dict(zip(days, pmap(finished_games, days)))
    jobs = [(g["id"], f"{d[:4]}-{d[4:6]}-{d[6:]}") for d in days for g in games_by_day[d]]
    recent_players, evos = {}, {}
    for res in pmap(lambda j: box_rows(*j), jobs):
        for pl, row in res:
            e = recent_players.setdefault(pl["id"], {"name": pl["name"], "team": pl["team"], "rows": []})
            e["team"] = e["team"] if e["rows"] and e["rows"][0][0] > row[0] else pl["team"]
            e["rows"].append(row + [int(pl["starter"])])
            evos[(pl["id"], row[0])] = pl["evo"]
    for e in recent_players.values(): e["rows"].sort(key=lambda r: r[0], reverse=True)
    last_day = next((d for d in days if games_by_day[d]), None)
    last_night = {"date": f"{last_day[:4]}-{last_day[4:6]}-{last_day[6:]}", "games": games_by_day[last_day]} if last_day else None
    if last_night:
        # Score evolution and headshots for last night's players only (keeps the page small)
        ln_ids = {pid: e["name"] for pid, e in recent_players.items() if any(r[0] == last_night["date"] for r in e["rows"])}
        last_night["evo"] = {pid: evos[(pid, last_night["date"])] for pid in ln_ids if (pid, last_night["date"]) in evos}
        nba_ids = nba_person_ids()
        pairs = [(pid, nba_ids.get(name_key(n))) for pid, n in ln_ids.items()]
        shots = pmap(lambda x: headshot(x[1]) if x[1] else None, pairs)
        last_night["photos"] = {pid: s for (pid, _), s in zip(pairs, shots) if s}

    logos = {}
    for abbr in sorted(set(all_teams.values())):
        logos[abbr] = {"light": logo(abbr, "L"), "dark": logo(abbr, "D")}

    data = {"logos": logos, "injuryList": injury_list, "recent": recent_players, "lastNight": last_night,
            "recentFrom": f"{days[-1][:4]}-{days[-1][4:6]}-{days[-1][6:]}", "generated": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
            "date": day.isoformat(), "season": f"{cur_season-1}-{str(cur_season)[2:]}",
            "prevSeason": f"{prev_season-1}-{str(prev_season)[2:]}",
            "games": games, "teams": teams, "players": players,
            "schedule": schedule, "rosters": rosters, "teamNames": team_names, "shotGames": shot_games, "shots": shot_map,
            "gamePages": game_pages, "teamStatLabels": TEAM_STATS,
            "injuriesUpdated": inj.get("timestamp")}
    def compact(o):  # 38.0 -> 38 to keep the page small
        if isinstance(o, float) and o.is_integer(): return int(o)
        if isinstance(o, list): return [compact(x) for x in o]
        if isinstance(o, dict): return {k: compact(v) for k, v in o.items()}
        return o
    json.dump(compact(data), open(out_path, "w"), separators=(",", ":"))
    print(f"{len(games)} games, {len(players)} players, {sum(1 for p in players if p['inj'])} with injury notes -> {out_path}")

if __name__ == "__main__":
    main()
