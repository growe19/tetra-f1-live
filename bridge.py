#!/usr/bin/env python3
"""
F1 -> Solidpixels Tetra live bridge.

Connects to the official F1 live-timing SignalR hub (the same free, keyless feed
the F1 app and FastF1 use), follows one driver, renders app.star with the Tetra
CLI and pushes the 64x32 frame to your Solidpixels device every few seconds.

    python bridge.py                 # asks for layout + driver, then goes live
    python bridge.py --render-only   # live data, write frame.webp, no push
    python bridge.py --demo          # fake data, no hub needed
    python bridge.py --once          # one frame then exit
    python bridge.py --driver NOR --layout tower --no-prompt
    python bridge.py --devices       # list your devices and exit
    python bridge.py --clear         # remove the F1 entry from the device and exit

Settings live in config.json (see config.example.json). The API key can also be
given as the SOLIDPIXELS_API_KEY environment variable.
"""
import argparse, base64, json, os, subprocess, sys, threading, time
import urllib.request, urllib.error, urllib.parse

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ui  # noqa: E402
HUB_HTTP = "https://livetiming.formula1.com/signalrcore"
HUB_WS = "wss://livetiming.formula1.com/signalrcore"
API = "https://api.solidpixels.io"
TOPICS = ["Heartbeat", "ExtrapolatedClock", "TopThree", "TimingStats", "TimingAppData",
          "WeatherData", "TrackStatus", "DriverList", "RaceControlMessages", "SessionInfo",
          "SessionData", "LapCount", "TimingData", "SessionStatus"]
RS = "\x1e"
PUSH_DURATIONS = (5, 10, 15, 30, 35, 40, 45)
LAYOUTS = [
    ("focus", "Driver card: position, interval, gap, last lap, tyre, status"),
    ("tower", "Timing tower: your driver with two cars ahead and two behind"),
    ("top10", "Top ten in two columns, team colours, your driver highlighted"),
    ("big",   "Giant position with interval, last lap and tyre"),
]
LAYOUT_NAMES = [name for name, _ in LAYOUTS]


def log(*a):
    print(time.strftime("%H:%M:%S"), *a, flush=True)


# ----------------------------------------------------------------------------- config
CONFIG_PATH = os.path.join(HERE, "config.json")
CONFIG_DEFAULTS = {"driver": "VER", "layout": "focus", "api_key": "", "device_id": "", "push_every": 10,
                   "duration": 15, "mode": "installation", "installation_id": "f1-live", "tetra": "tetra"}


def load_config(args):
    cfg = dict(CONFIG_DEFAULTS)
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    if args.driver:
        cfg["driver"] = args.driver
    if args.layout:
        cfg["layout"] = args.layout
    if args.temporary:
        cfg["mode"] = "temporary"
    cfg["api_key"] = os.environ.get("SOLIDPIXELS_API_KEY") or cfg.get("api_key") or ""
    cfg["driver"] = cfg["driver"].upper()
    cfg["layout"] = cfg["layout"].lower()
    if cfg["layout"] not in LAYOUT_NAMES:
        cfg["layout"] = "focus"
    if cfg["duration"] not in PUSH_DURATIONS:
        cfg["duration"] = min(PUSH_DURATIONS, key=lambda d: abs(d - cfg["duration"]))
    return cfg


def save_choices(cfg):
    """Remember driver + layout in config.json without touching anything else in the file."""
    data = {}
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, encoding="utf-8") as f:
            data = json.load(f)
    data["driver"] = cfg["driver"]
    data["layout"] = cfg["layout"]
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")


def find_tetra(name):
    cands = [name, name + ".exe",
             os.path.join(os.environ.get("LOCALAPPDATA", ""), "Programs", "Tetra", "tetra.exe")]
    for cand in cands:
        if os.path.sep in cand and os.path.exists(cand):
            return cand
        try:
            subprocess.run([cand, "version"], capture_output=True, timeout=10)
            return cand
        except (OSError, subprocess.SubprocessError):
            pass
    sys.exit('tetra CLI not found - install it (see README.md) or set "tetra" in config.json')


# ----------------------------------------------------------------------------- SignalR state
def merge(dst, src):
    """SignalR feed patch semantics (port of live.c in the F1 companion firmware):
    objects merge, index-keyed patches address list items, a list patch replaces."""
    for k, v in src.items():
        if k == "_kf":
            continue
        if isinstance(dst, list):
            try:
                idx = int(k)
            except ValueError:
                continue
            while len(dst) <= idx:
                dst.append(None)
            if isinstance(dst[idx], (dict, list)) and isinstance(v, dict):
                merge(dst[idx], v)
            else:
                dst[idx] = v
            continue
        cur = dst.get(k)
        if isinstance(cur, (dict, list)) and isinstance(v, dict):
            merge(cur, v)
        else:
            dst[k] = v


class LiveState:
    def __init__(self):
        self.state = None
        self.lock = threading.Lock()
        self.updates = 0
        self.connected = False
        self.error = ""

    def handle_frames(self, text):
        changed = 0
        for part in text.split(RS):
            part = part.strip()
            if not part:
                continue
            try:
                j = json.loads(part)
            except ValueError:
                continue
            t = j.get("type")
            with self.lock:
                if t == 3 and isinstance(j.get("result"), dict):
                    self.state = j["result"]
                    changed += 1
                    log("state snapshot: %d topics" % len(self.state))
                elif t == 1 and self.state is not None:
                    args = j.get("arguments") or []
                    if len(args) >= 2 and isinstance(args[0], str):
                        topic, delta = args[0], args[1]
                        cur = self.state.get(topic)
                        if isinstance(cur, (dict, list)) and isinstance(delta, dict):
                            merge(cur, delta)
                        else:
                            self.state[topic] = delta
                        changed += 1
                self.updates += changed
        return changed


# ----------------------------------------------------------------------------- hub transports
def run_websocket(live, stop):
    import websocket  # websocket-client
    ws = websocket.create_connection(HUB_WS, timeout=30, header=["User-Agent: BestHTTP"],
                                     suppress_origin=True)
    ws.send('{"protocol":"json","version":1}' + RS)
    ws.recv()  # handshake reply {}
    ws.send(json.dumps({"arguments": [TOPICS], "invocationId": "0",
                        "target": "Subscribe", "type": 1}) + RS)
    live.connected = True
    log("websocket connected")
    last_ping = time.time()
    while not stop.is_set():
        if time.time() - last_ping > 15:
            ws.send('{"type":6}' + RS)
            last_ping = time.time()
        try:
            msg = ws.recv()
        except websocket.WebSocketTimeoutException:
            continue
        if not msg:
            break
        live.handle_frames(msg)
    ws.close()


def http_req(url, method="GET", body=None, cookie="", timeout=25):
    hdr = {"User-Agent": "BestHTTP", "Accept": "*/*"}
    if cookie:
        hdr["Cookie"] = cookie
    if method == "POST":
        hdr["Content-Type"] = "text/plain;charset=UTF-8"
    data = (body or "").encode() if method == "POST" else None
    req = urllib.request.Request(url, data=data, method=method, headers=hdr)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace"), r.headers.get_all("Set-Cookie") or []
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace"), []


def run_longpoll(live, stop):
    st, body, sc = http_req(HUB_HTTP + "/negotiate?negotiateVersion=1", "POST", "")
    if st != 200:
        raise RuntimeError("negotiate HTTP %d" % st)
    cookie = "; ".join(s.split(";")[0] for s in sc)
    j = json.loads(body)
    tok = j.get("connectionToken") or j.get("connectionId")
    url = HUB_HTTP + "?id=" + urllib.parse.quote(tok, safe="")
    subscribe = json.dumps({"arguments": [TOPICS], "invocationId": "0", "target": "Subscribe", "type": 1}) + RS
    steps = (("GET", None, "connect"),
             ("POST", '{"protocol":"json","version":1}' + RS, "handshake"),
             ("GET", None, "handshake reply"),
             ("POST", subscribe, "subscribe"))
    for method, payload, what in steps:
        st, body, _ = http_req(url, method, payload, cookie)
        if st != 200:
            raise RuntimeError("%s HTTP %d" % (what, st))
        if method == "GET":
            live.handle_frames(body)
    live.connected = True
    log("long-polling connected")
    last_ping = time.time()
    quiet = 0
    while not stop.is_set():
        if time.time() - last_ping >= 15:
            http_req(url, "POST", '{"type":6}' + RS, cookie, timeout=10)
            last_ping = time.time()
        try:
            st, body, _ = http_req(url, "GET", None, cookie)
        except Exception:
            quiet += 1
            if quiet > 3:
                raise
            time.sleep(2)
            continue
        if st != 200:
            raise RuntimeError("poll HTTP %d" % st)
        quiet = 0
        live.handle_frames(body)


def hub_thread(live, stop):
    attempt = 0
    while not stop.is_set():
        for transport in (run_websocket, run_longpoll):
            try:
                transport(live, stop)
                attempt = 0
            except Exception as e:
                live.error = "%s: %s" % (transport.__name__, e)
                log("live timing:", live.error)
            live.connected = False
            if stop.is_set():
                return
        attempt += 1
        time.sleep(min(60, 3 * attempt))


# ----------------------------------------------------------------------------- extraction
def jstr(o, k, default=""):
    v = o.get(k) if isinstance(o, dict) else None
    return default if v is None else str(v)


def jbool(o, k):
    v = o.get(k) if isinstance(o, dict) else None
    return v is True or v == "true"


def clean(s):
    return str(s).replace(",", "").replace(";", "")


def car_fields(state, num):
    """Per-car values from the merged hub state."""
    drivers = state.get("DriverList") or {}
    d = drivers.get(num) or {}
    line = ((state.get("TimingData") or {}).get("Lines") or {}).get(num) or {}
    app = ((state.get("TimingAppData") or {}).get("Lines") or {}).get(num) or {}
    ll = line.get("LastLapTime") or {}
    flag = "ob" if jbool(ll, "OverallFastest") else "pb" if jbool(ll, "PersonalFastest") else "none"
    stints = app.get("Stints") or []
    if isinstance(stints, dict):
        stints = [stints[k] for k in sorted(stints, key=lambda x: int(x) if x.isdigit() else 0)]
    stints = [s for s in stints if isinstance(s, dict)]
    cur = stints[-1] if stints else {}
    stops = jstr(line, "NumberOfPitStops", "")
    if not stops or stops == "0":
        stops = str(max(0, len(stints) - 1))
    interval = line.get("IntervalToPositionAhead")
    if isinstance(interval, dict):
        interval = jstr(interval, "Value")
    else:
        interval = jstr(line, "IntervalToPositionAhead")
    pos = jstr(line, "Position")
    return {
        "tla": jstr(d, "Tla").upper(),
        "number": num,
        "name": jstr(d, "FullName") or jstr(d, "BroadcastName"),
        "teamname": jstr(d, "TeamName"),
        "team": jstr(d, "TeamColour", "ffffff"),
        "pos": pos,
        "posn": int(pos) if pos.isdigit() else 99,
        "gap": jstr(line, "GapToLeader"),
        "interval": interval,
        "last": jstr(ll, "Value"),
        "lastflag": flag,
        "best": jstr(line.get("BestLapTime") or {}, "Value"),
        "tyre": jstr(cur, "Compound"),
        "tyreage": jstr(cur, "TotalLaps"),
        "stops": stops,
        "inpit": "1" if jbool(line, "InPit") else "0",
        "pitout": "1" if jbool(line, "PitOut") else "0",
        "retired": "1" if (jbool(line, "Retired") or jbool(line, "Stopped")) else "0",
    }


def all_cars(state):
    if not state:
        return []
    cars = [car_fields(state, num) for num in (state.get("DriverList") or {}) if num.isdigit()]
    return sorted(cars, key=lambda c: c["posn"])


def cars_string(cars):
    return ";".join(",".join(clean(c[k]) for k in ("pos", "tla", "team", "interval", "gap", "tyre", "tyreage", "inpit", "retired"))
                    for c in cars if c["pos"])


def extract(state, tla, layout):
    """Return the app.star fields for one driver, or None if the driver isn't in the session."""
    if not state:
        return None
    cars = all_cars(state)
    mine = None
    for c in cars:
        if c["tla"] == tla:
            mine = c
    if mine is None:
        return None
    si = state.get("SessionInfo") or {}
    lc = state.get("LapCount") or {}
    clock = state.get("ExtrapolatedClock") or {}
    status = jstr(state.get("SessionStatus") or {}, "Status")
    if not status:
        for s in reversed((state.get("SessionData") or {}).get("StatusSeries") or []):
            if isinstance(s, dict) and s.get("SessionStatus"):
                status = s["SessionStatus"]
                break
    fields = {k: v for k, v in mine.items() if k not in ("name", "teamname", "posn", "number")}
    fields.update({
        "layout": layout,
        "lap": jstr(lc, "CurrentLap"),
        "total": jstr(lc, "TotalLaps"),
        "clock": jstr(clock, "Remaining"),
        "track": jstr(state.get("TrackStatus") or {}, "Status", "1"),
        "status": status,
        "session": jstr(si, "Name"),
        "meeting": jstr(si.get("Meeting") or {}, "Name"),
        "cars": cars_string(cars),
    })
    return fields


def session_live(fields):
    return bool(fields) and fields["status"] not in ("Finished", "Finalised", "Ends")


DEMO_GRID = [  # pos, tla, number, team colour, interval, gap, tyre, age, name, team name (2026 grid, made-up race)
    (1, "NOR", "1", "F47600", "", "", "MEDIUM", "15", "Lando Norris", "McLaren"),
    (2, "VER", "3", "4781D7", "+0.512", "+0.512", "MEDIUM", "14", "Max Verstappen", "Red Bull Racing"),
    (3, "PIA", "81", "F47600", "+1.204", "+1.716", "HARD", "9", "Oscar Piastri", "McLaren"),
    (4, "LEC", "16", "ED1131", "+3.871", "+5.587", "HARD", "11", "Charles Leclerc", "Ferrari"),
    (5, "HAM", "44", "ED1131", "+0.433", "+6.020", "SOFT", "3", "Lewis Hamilton", "Ferrari"),
    (6, "RUS", "63", "00D7B6", "+7.912", "+13.932", "HARD", "12", "George Russell", "Mercedes"),
    (7, "ANT", "12", "00D7B6", "+2.118", "+16.050", "MEDIUM", "16", "Kimi Antonelli", "Mercedes"),
    (8, "HAD", "6", "4781D7", "+1.455", "+17.505", "HARD", "10", "Isack Hadjar", "Red Bull Racing"),
    (9, "ALO", "14", "229971", "+4.147", "+21.652", "HARD", "10", "Fernando Alonso", "Aston Martin"),
    (10, "SAI", "55", "1868DB", "+1.337", "+22.989", "MEDIUM", "18", "Carlos Sainz", "Williams"),
    (11, "LAW", "30", "6C98FF", "+2.210", "+25.199", "HARD", "9", "Liam Lawson", "Racing Bulls"),
    (12, "GAS", "10", "00A1E8", "+2.200", "+27.399", "HARD", "8", "Pierre Gasly", "Alpine"),
    (13, "HUL", "27", "F50537", "+0.980", "+28.379", "MEDIUM", "13", "Nico Hulkenberg", "Audi"),
    (14, "ALB", "23", "1868DB", "+2.655", "+31.034", "HARD", "7", "Alexander Albon", "Williams"),
    (15, "BEA", "87", "9C9FA2", "+3.302", "+34.336", "MEDIUM", "12", "Oliver Bearman", "Haas F1 Team"),
    (16, "OCO", "31", "9C9FA2", "+1.118", "+35.454", "HARD", "9", "Esteban Ocon", "Haas F1 Team"),
    (17, "LIN", "41", "6C98FF", "+4.870", "+40.324", "HARD", "11", "Arvid Lindblad", "Racing Bulls"),
    (18, "STR", "18", "229971", "+2.015", "+42.339", "MEDIUM", "14", "Lance Stroll", "Aston Martin"),
    (19, "COL", "43", "00A1E8", "+6.771", "+49.110", "HARD", "8", "Franco Colapinto", "Alpine"),
    (20, "BOR", "5", "F50537", "+1.902", "+51.012", "HARD", "10", "Gabriel Bortoleto", "Audi"),
    (21, "PER", "11", "909090", "+8.433", "+59.445", "MEDIUM", "15", "Sergio Perez", "Cadillac"),
    (22, "BOT", "77", "909090", "+3.117", "+1:02.562", "HARD", "9", "Valtteri Bottas", "Cadillac"),
]


def demo_cars():
    return [{"pos": str(p), "posn": p, "tla": t, "number": num, "team": c, "interval": i, "gap": g, "tyre": ty,
             "tyreage": a, "inpit": "0", "retired": "0", "name": n, "teamname": tn}
            for p, t, num, c, i, g, ty, a, n, tn in DEMO_GRID]


def demo_fields(tla, layout):
    cars = demo_cars()
    mine = next((c for c in cars if c["tla"] == tla), None) or cars[1]
    f = dict(mine)
    f.update({"layout": layout, "last": "1:21.456", "lastflag": "pb", "best": "1:20.901", "stops": "1",
              "lap": "37", "total": "57", "clock": "", "track": "4", "status": "Started", "pitout": "0",
              "session": "Race", "meeting": "Demo Grand Prix", "cars": cars_string(cars)})
    for k in ("name", "teamname", "posn", "number"):
        f.pop(k, None)
    return f


# ----------------------------------------------------------------------------- interactive choices
def wait_for_hub(live, seconds):
    """Wait for the first state snapshot with a small spinner. Returns True when data arrived."""
    spinner = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
    t0 = time.time()
    i = 0
    while live.state is None and time.time() - t0 < seconds:
        if ui.enabled():
            sys.stdout.write("\r  " + ui.c(spinner[i % len(spinner)], ui.YELLOW) + ui.c("  fetching the driver list from F1 live timing...", ui.GREY))
            sys.stdout.flush()
        time.sleep(0.1)
        i += 1
    if ui.enabled():
        sys.stdout.write("\r" + " " * 70 + "\r")
        sys.stdout.flush()
    return live.state is not None


def choose(cfg, args, live, tetra):
    """Layout and driver menus in the console. Skipped with --no-prompt or when not interactive."""
    if args.no_prompt or not sys.stdin.isatty():
        return
    ui.banner("live F1 timing on your Solidpixels Tetra")

    if args.demo:
        drivers = demo_cars()
    else:
        wait_for_hub(live, 15)
        with live.lock:
            drivers = all_cars(live.state) if live.state else []
    if drivers:
        with live.lock if not args.demo else _nolock():
            si = (live.state or {}).get("SessionInfo") if not args.demo else None
        if si:
            print("  " + ui.c((si.get("Meeting") or {}).get("Name", ""), ui.WHITE, bold=True)
                  + ui.c(" · " + si.get("Name", "") + "  (latest session on the timing hub)", ui.GREY))
            print()

    # previews of every layout, with real data when the hub has it
    sample = cfg["driver"] if any(d["tla"] == cfg["driver"] for d in drivers) else (drivers[0]["tla"] if drivers else cfg["driver"])
    previews = {}
    pdir = os.path.join(HERE, ".previews")
    os.makedirs(pdir, exist_ok=True)
    for name in LAYOUT_NAMES:
        try:
            if args.demo or live.state is None:
                fields = demo_fields(sample, name)
            else:
                with live.lock:
                    fields = extract(live.state, sample, name) or demo_fields(sample, name)
            path = os.path.join(pdir, name + ".webp")
            render(tetra, fields, path)
            previews[name] = path
        except Exception as e:
            log("preview %s failed: %s" % (name, e))
            previews[name] = None

    if not args.layout:
        cfg["layout"] = ui.layout_menu(LAYOUTS, previews, cfg["layout"])
        print()
    if not args.driver:
        cfg["driver"] = ui.driver_menu(drivers, cfg["driver"])
        print()
    save_choices(cfg)


class _nolock:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ----------------------------------------------------------------------------- render + push
def render(tetra, fields, out):
    args = [tetra, "render", os.path.join(HERE, "app.star")]
    for k, v in fields.items():
        if k != "meeting":
            args.append("%s=%s" % (k, v))
    args += ["-o", out]
    r = subprocess.run(args, capture_output=True, text=True, timeout=60)
    if r.returncode != 0:
        raise RuntimeError("tetra render failed: " + (r.stderr or r.stdout).strip())
    with open(out, "rb") as f:
        return f.read()


def api(cfg, method, path, body=None):
    if not cfg["api_key"]:
        raise RuntimeError("no API key: put it in config.json or SOLIDPIXELS_API_KEY")
    headers = {"Authorization": "Bearer " + cfg["api_key"], "Content-Type": "application/json",
               "Accept": "application/json", "User-Agent": "f1-tetra-bridge/1.0 (+Solidpixels Public API)"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, method=method, headers=headers, data=data)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            txt = r.read().decode()
            return r.status, (json.loads(txt) if txt else {})
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def resolve_device(cfg):
    if cfg["device_id"]:
        return cfg["device_id"]
    st, devs = api(cfg, "GET", "/v1/devices")
    if st != 200 or not devs:
        raise RuntimeError("could not list devices (HTTP %s): %s" % (st, devs))
    cfg["device_id"] = devs[0]["id"]
    cfg["device_name"] = devs[0].get("name") or devs[0]["id"]
    if not ui.enabled():
        log("using device '%s' (%s)" % (cfg["device_name"], devs[0]["id"]))
    return cfg["device_id"]


def push(cfg, webp, background=False):
    body = {"image": "data:image/webp;base64," + base64.b64encode(webp).decode()}
    if cfg["mode"] == "installation":
        body["installationId"] = cfg["installation_id"]
        if background:
            body["background"] = True
    else:
        body["duration"] = cfg["duration"]
    st, resp = api(cfg, "POST", "/v1/devices/%s/push" % resolve_device(cfg), body)
    if st != 200:
        raise RuntimeError("push HTTP %s: %s" % (st, resp))


def clear_installation(cfg):
    st, resp = api(cfg, "DELETE", "/v1/devices/%s/installations/%s" % (resolve_device(cfg), cfg["installation_id"]))
    if st == 200:
        log("removed '%s' from the device rotation" % cfg["installation_id"])
    elif st == 404:
        log("no '%s' entry on the device" % cfg["installation_id"])
    else:
        raise RuntimeError("delete HTTP %s: %s" % (st, resp))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--driver", help="three-letter code, e.g. VER, NOR, HAM")
    ap.add_argument("--layout", choices=LAYOUT_NAMES, help="card layout")
    ap.add_argument("--no-prompt", action="store_true", help="skip the layout/driver menus")
    ap.add_argument("--demo", action="store_true", help="use built-in demo data instead of the hub")
    ap.add_argument("--render-only", action="store_true", help="render frame.webp but do not push")
    ap.add_argument("--once", action="store_true", help="render (and push) one frame, then exit")
    ap.add_argument("--always", action="store_true", help="keep pushing after the session has finished")
    ap.add_argument("--temporary", action="store_true", help="push temporary images instead of a rotation entry")
    ap.add_argument("--devices", action="store_true", help="list devices on the account and exit")
    ap.add_argument("--clear", action="store_true", help="remove the F1 entry from the device rotation and exit")
    ap.add_argument("--plain", action="store_true", help="plain log lines instead of the colour dashboard")
    ap.add_argument("--answers", help="comma-separated answers for the menus, e.g. \"2,NOR\" (for scripting)")
    args = ap.parse_args()
    if args.plain:
        ui.set_plain(True)
    if args.answers:
        ui.scripted_answers[:] = args.answers.split(",")
    cfg = load_config(args)
    tetra = find_tetra(cfg["tetra"])
    out = os.path.join(HERE, "frame.webp")

    if args.devices:
        st, devs = api(cfg, "GET", "/v1/devices")
        print("HTTP", st)
        print(json.dumps(devs, indent=2))
        return
    if args.clear:
        clear_installation(cfg)
        return

    live = LiveState()
    stop = threading.Event()
    if not args.demo:
        threading.Thread(target=hub_thread, args=(live, stop), daemon=True).start()
    choose(cfg, args, live, tetra)
    dash = ui.Dashboard(cfg, cfg.get("device_name", "")) if (ui.enabled() and not args.once) else None
    if dash is None:
        log("following %s, layout %s, mode %s, every %ss%s" % (cfg["driver"], cfg["layout"], cfg["mode"], cfg["push_every"],
                                                                " (render only)" if args.render_only else ""))

    def say(msg):
        if dash:
            dash.draw(msg)
        else:
            log(msg)

    last_fields = None
    last_push = 0
    pushed_any = False
    warned = False
    try:
        while True:
            if dash:
                dash.hub_ok = args.demo or live.connected
                dash.error = "" if (args.demo or live.connected) else live.error
            if args.demo:
                fields = demo_fields(cfg["driver"], cfg["layout"])
            else:
                with live.lock:
                    fields = extract(live.state, cfg["driver"], cfg["layout"])
                if fields is None:
                    if live.state is not None and not warned:
                        say("driver %s is not in this session's driver list" % cfg["driver"])
                        warned = True
                        if args.once:
                            break
                    elif dash:
                        dash.draw()
                    time.sleep(1)
                    continue
            if not args.always and not args.demo and not session_live(fields) and last_fields is not None:
                if args.once:
                    break
                if dash:
                    dash.draw("session is not live (%s); waiting for the next one. --always pushes regardless" % fields["status"])
                time.sleep(1 if dash else 5)
                continue
            if time.time() - last_push >= cfg["push_every"]:
                webp = render(tetra, fields, out)
                if not args.render_only:
                    push(cfg, webp)
                    pushed_any = True
                last_push = time.time()
                last_fields = fields
                if dash:
                    dash.device = cfg.get("device_name", "")
                    dash.set_frame(out, fields, pushed=not args.render_only)
                else:
                    log("%s P%s int %s gap %s last %s tyre %s%s [%s %s L%s/%s %s]" % (
                        fields["tla"], fields["pos"], fields["interval"] or "-", fields["gap"] or "-",
                        fields["last"] or "-", fields["tyre"][:1], fields["tyreage"], fields.get("meeting", ""),
                        fields["session"], fields["lap"], fields["total"], fields["status"]))
                if args.once:
                    break
            if dash:
                dash.draw("render only, nothing is sent to the device" if args.render_only else "")
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        stop.set()
        if dash:
            dash.finish()
        if pushed_any and cfg["mode"] == "installation" and not args.once:
            try:
                push(cfg, render(tetra, {"idle": "1"}, out))
                print("  " + ui.c("Stopped.", ui.WHITE, bold=True) + ui.c(" The Tetra now shows the 'bridge off' card; run ", ui.GREY)
                      + ui.c(".\\f1 --clear", ui.WHITE) + ui.c(" to remove it.", ui.GREY))
            except Exception as e:
                log("could not push the idle card:", e)


if __name__ == "__main__":
    main()
