# F1 Driver Focus - 64x32 live timing layouts for one driver on a Solidpixels Tetra.
#
# Fed by bridge.py (`tetra render app.star k=v ...`) or, in the cloud, by a `src`
# URL returning the same fields as JSON.
#
# Layouts (`layout` config):
#   focus  - one-driver card: position, interval, gap, last lap, tyre, status
#   tower  - timing tower window: your driver with two cars ahead and two behind
#   top10  - the top ten in two columns, team colours, your driver highlighted
#   big    - giant position with interval, gap, last lap and tyre
#
# Driver fields (strings): tla, team (hex, no #), pos, gap, interval, last,
# lastflag (pb|ob|none), best, tyre (SOFT|MEDIUM|HARD|INTERMEDIATE|WET), tyreage,
# stops, lap, total, clock, track (1..7 track status), status (session status),
# inpit, pitout, retired (1/0), session.
# Field `cars`: every car in running order, "pos,tla,team,interval,gap,tyre,age,inpit,retired;..."
# Field `idle` = 1 renders the "bridge off" card.

load("render.star", "render")
load("http.star", "http")

WHITE = "#ffffff"
GREY = "#9aa3ad"
DIM = "#5b6570"
GREEN = "#2fdc4a"
PURPLE = "#c04cff"
YELLOW = "#ffd400"
RED = "#ff2a2a"
BLACK = "#000000"

TYRE_COLOURS = {
    "SOFT": "#ff2a2a",
    "MEDIUM": "#ffd400",
    "HARD": "#e8e8e8",
    "INTERMEDIATE": "#2fdc4a",
    "WET": "#3a8dff",
}

TYRE_LETTERS = {
    "SOFT": "S",
    "MEDIUM": "M",
    "HARD": "H",
    "INTERMEDIATE": "I",
    "WET": "W",
}

DEFAULTS = {
    "layout": "focus",
    "idle": "0",
    "tla": "---",
    "team": "ffffff",
    "pos": "",
    "gap": "",
    "interval": "",
    "last": "",
    "lastflag": "none",
    "best": "",
    "tyre": "",
    "tyreage": "",
    "stops": "0",
    "lap": "",
    "total": "",
    "clock": "",
    "track": "1",
    "status": "",
    "inpit": "0",
    "pitout": "0",
    "retired": "0",
    "session": "",
    "cars": "",
}

# ----------------------------------------------------------------------------- data

def load_data(config):
    d = {}
    for k in DEFAULTS:
        d[k] = str(config.get(k, DEFAULTS[k]))
    src = config.get("src", "")
    if src:
        res = http.get(src, ttl_seconds = 10)
        if res.status_code == 200:
            body = res.json()
            for k in DEFAULTS:
                if k in body and body[k] != None:
                    d[k] = str(body[k])
    return d

def parse_cars(s):
    cars = []
    if s.strip() == "":
        return cars
    for item in s.split(";"):
        f = item.split(",")
        if len(f) < 9:
            continue
        cars.append({
            "pos": to_int(f[0]),
            "tla": f[1].upper(),
            "team": f[2] if f[2] != "" else "ffffff",
            "interval": f[3],
            "gap": f[4],
            "tyre": f[5].upper(),
            "age": f[6],
            "inpit": truthy(f[7]),
            "retired": truthy(f[8]),
        })
    return cars

def to_int(s, default = 0):
    s = s.strip()
    if s == "":
        return default
    digits = ""
    for ch in s.elems():
        if ch in "0123456789":
            digits += ch
        else:
            break
    if digits == "":
        return default
    return int(digits)

def truthy(s):
    return s in ("1", "true", "True", "yes")

def rpad(s, n):
    return s + " " * (n - len(s))

def lpad(s, n):
    return " " * (n - len(s)) + s

def fmt_delta(v, is_leader, leader_text):
    v = v.strip()
    if is_leader:
        return leader_text
    if v == "":
        return "--"
    if "LAP" in v.upper():
        n = to_int("".join([c for c in v.elems() if c in "0123456789"]), 1)
        return "+%dL" % n
    if not v.startswith("+") and not v.startswith("-"):
        v = "+" + v
    return v

def short_delta(v, is_leader, width):
    # fixed-width delta for monospace rows, e.g. "+1.23", " +12L", " LEAD"
    s = fmt_delta(v, is_leader, "LEAD")
    if len(s) > width:
        s = s[:width]
    return lpad(s, width)

def tyre_colour(t):
    return TYRE_COLOURS.get(t.upper(), DIM)

def tyre_letter(t):
    return TYRE_LETTERS.get(t.upper(), "-")

# ----------------------------------------------------------------------------- shared pieces

def badge(text, bg, fg = BLACK):
    return render.Box(
        height = 6,
        width = 4 * len(text) + 2,
        color = bg,
        child = render.Text(text, font = "CG-pixel-3x5-mono", color = fg),
    )

def status_badge(d):
    status = d["status"].upper()
    track = to_int(d["track"], 1)
    if truthy(d["retired"]):
        return badge("OUT", DIM, WHITE)
    if truthy(d["inpit"]):
        return badge("PIT", WHITE)
    if truthy(d["pitout"]):
        return badge("OUT", GREEN)
    if status in ("FINISHED", "FINALISED", "ENDS"):
        return badge("FIN", DIM, WHITE)
    if track == 5:
        return badge("RED", RED, WHITE)
    if track == 4:
        return badge("SC", YELLOW)
    if track in (6, 7):
        return badge("VSC", YELLOW)
    if track == 2:
        return badge("YEL", YELLOW)
    if status in ("ABORTED", "INACTIVE"):
        return badge(status[:3], DIM, WHITE)
    return None

def lap_text(d):
    lap = to_int(d["lap"])
    total = to_int(d["total"])
    if total > 0:
        return "L%d/%d" % (lap, total)
    if d["clock"] != "":
        return d["clock"]
    return d["session"][:9].upper()

def header(d):
    pos = d["pos"]
    left = [
        render.Box(width = 2, height = 8, color = "#" + d["team"]),
        render.Box(width = 2, height = 8),
        render.Text("P" + pos if pos else "P-", font = "tb-8", color = WHITE),
        render.Box(width = 3, height = 8),
        render.Text(d["tla"].upper(), font = "tb-8", color = WHITE),
    ]
    return render.Box(
        width = 64,
        height = 8,
        child = render.Row(
            expanded = True,
            main_align = "space_between",
            cross_align = "center",
            children = [
                render.Row(cross_align = "center", children = left),
                render.Text(lap_text(d), font = "tom-thumb", color = GREY),
            ],
        ),
    )

def kv(label, value, colour):
    return render.Box(
        width = 64,
        height = 6,
        child = render.Row(
            expanded = True,
            main_align = "space_between",
            cross_align = "center",
            children = [
                render.Text(label, font = "CG-pixel-3x5-mono", color = DIM),
                render.Text(value, font = "tb-8", height = 6, offset = -1, color = colour),
            ],
        ),
    )

def footer(d):
    left = []
    tyre = d["tyre"].upper()
    if tyre in TYRE_COLOURS:
        left.append(render.Circle(diameter = 5, color = TYRE_COLOURS[tyre]))
        left.append(render.Box(width = 2, height = 6))
        left.append(render.Text(TYRE_LETTERS[tyre] + d["tyreage"], font = "tom-thumb", color = WHITE))
    stops = to_int(d["stops"])
    left.append(render.Box(width = 4, height = 6))
    left.append(render.Text("%dSTP" % stops, font = "tom-thumb", color = GREY))

    children = [render.Row(cross_align = "center", children = left)]
    b = status_badge(d)
    if b != None:
        children.append(b)
    return render.Box(
        width = 64,
        height = 6,
        child = render.Row(
            expanded = True,
            main_align = "space_between",
            cross_align = "center",
            children = children,
        ),
    )

def message_card(line1, line2):
    return render.Root(
        child = render.Box(
            width = 64,
            height = 32,
            child = render.Column(
                main_align = "center",
                cross_align = "center",
                children = [
                    render.Text(line1, font = "tb-8", color = WHITE),
                    render.Text(line2, font = "tom-thumb", color = GREY),
                ],
            ),
        ),
    )

def last_lap(d):
    flag = d["lastflag"].lower()
    colour = PURPLE if flag == "ob" else GREEN if flag == "pb" else WHITE
    return (d["last"] if d["last"] != "" else "--", colour)

# ----------------------------------------------------------------------------- layouts

def layout_focus(d):
    is_leader = d["pos"] == "1"
    last, last_colour = last_lap(d)
    return render.Column(
        children = [
            header(d),
            kv("INT", fmt_delta(d["interval"], is_leader, "LEADER"), WHITE),
            kv("GAP", fmt_delta(d["gap"], is_leader, "---"), WHITE),
            kv("LAST", last, last_colour),
            footer(d),
        ],
    )

def tower_row(c, mine):
    # 16 columns of CG-pixel-3x5-mono (4 px each) = 64 px:
    #  pos(2) sp tla(3) sp delta(5) sp tyre+age(3)
    colour = YELLOW if mine else WHITE
    if c["retired"]:
        delta = "  OUT"
    elif c["inpit"]:
        delta = "  PIT"
    else:
        delta = short_delta(c["interval"], c["pos"] == 1, 5)
    age = c["age"] if c["age"] != "" else "-"
    tyre = tyre_letter(c["tyre"]) + age
    return render.Box(
        width = 64,
        height = 6,
        child = render.Row(
            cross_align = "center",
            children = [
                render.Text(lpad(str(c["pos"]), 2) + " ", font = "CG-pixel-3x5-mono", color = GREY if not mine else YELLOW),
                render.Text(c["tla"], font = "CG-pixel-3x5-mono", color = "#" + c["team"] if not mine else YELLOW),
                render.Text(" " + delta + " ", font = "CG-pixel-3x5-mono", color = colour),
                render.Text(lpad(tyre, 3), font = "CG-pixel-3x5-mono", color = tyre_colour(c["tyre"])),
            ],
        ),
    )

def layout_tower(d, cars):
    tla = d["tla"].upper()
    idx = -1
    for i in range(len(cars)):
        if cars[i]["tla"] == tla:
            idx = i
    if idx < 0:
        return layout_focus(d)
    start = idx - 2
    if start < 0:
        start = 0
    if start > len(cars) - 5:
        start = len(cars) - 5
    if start < 0:
        start = 0
    rows = []
    for i in range(start, min(start + 5, len(cars))):
        rows.append(tower_row(cars[i], i == idx))
    return render.Column(
        children = [render.Box(width = 64, height = 1)] + rows,
    )

def top10_cell(c, mine, width):
    return render.Box(
        width = width,
        height = 6,
        child = render.Row(
            cross_align = "center",
            children = [
                render.Text(lpad(str(c["pos"]), 2) + " ", font = "CG-pixel-3x5-mono", color = YELLOW if mine else GREY),
                render.Text(c["tla"], font = "CG-pixel-3x5-mono", color = YELLOW if mine else "#" + c["team"]),
                render.Text(" " + (tyre_letter(c["tyre"]) if c["tyre"] != "" else ""), font = "CG-pixel-3x5-mono", color = tyre_colour(c["tyre"])),
            ],
        ),
    )

def layout_top10(d, cars):
    tla = d["tla"].upper()
    if len(cars) == 0:
        return layout_focus(d)
    shown = cars[:10]
    mine_in = False
    for c in shown:
        if c["tla"] == tla:
            mine_in = True
    if not mine_in:
        for c in cars:
            if c["tla"] == tla and len(shown) == 10:
                shown = shown[:9] + [c]
    left = []
    right = []
    for i in range(len(shown)):
        cell = top10_cell(shown[i], shown[i]["tla"] == tla, 32 if i < 5 else 31)
        if i < 5:
            left.append(cell)
        else:
            right.append(cell)
    return render.Column(
        children = [
            render.Box(width = 64, height = 1),
            render.Row(
                children = [
                    render.Column(children = left),
                    render.Box(width = 1, height = 30),
                    render.Column(children = right),
                ],
            ),
        ],
    )

def layout_big(d):
    is_leader = d["pos"] == "1"
    last, last_colour = last_lap(d)
    pos = d["pos"] if d["pos"] != "" else "-"
    return render.Column(
        children = [
            render.Box(
                width = 64,
                height = 20,
                child = render.Row(
                    expanded = True,
                    main_align = "space_between",
                    cross_align = "center",
                    children = [
                        render.Row(
                            cross_align = "center",
                            children = [
                                render.Box(width = 2, height = 20, color = "#" + d["team"]),
                                render.Box(width = 1, height = 20),
                                render.Text("P" + pos, font = "10x20", color = WHITE),
                            ],
                        ),
                        render.Column(
                            cross_align = "end",
                            children = [
                                render.Text(d["tla"].upper(), font = "tb-8", color = WHITE),
                                render.Text(fmt_delta(d["interval"], is_leader, "LEADER"), font = "tb-8", height = 6, offset = -1, color = WHITE),
                                render.Text(lap_text(d), font = "tom-thumb", color = GREY),
                            ],
                        ),
                    ],
                ),
            ),
            kv("LAST", last, last_colour),
            footer(d),
        ],
    )

# ----------------------------------------------------------------------------- main

def main(config):
    d = load_data(config)
    if truthy(d["idle"]):
        return message_card("F1 LIVE", "bridge off")
    if d["pos"] == "" and d["tla"] == "---":
        return message_card("F1 LIVE", "waiting")

    cars = parse_cars(d["cars"])
    layout = d["layout"].lower()
    if layout == "tower":
        body = layout_tower(d, cars)
    elif layout == "top10":
        body = layout_top10(d, cars)
    elif layout == "big":
        body = layout_big(d)
    else:
        body = layout_focus(d)
    return render.Root(max_age = 120, child = body)
