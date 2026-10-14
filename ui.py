"""
Console UI for the F1 -> Tetra bridge: 24-bit colour previews of the real 64x32
frames (two pixels per character cell using the upper-half block), a team-coloured
driver menu and a live dashboard that mirrors what the Tetra is showing.
"""
import os, shutil, sys, time

ESC = "\x1b"
RESET = ESC + "[0m"
BOLD = ESC + "[1m"
DIM = ESC + "[2m"

# palette (24-bit)
RED = (225, 6, 0)
WHITE = (240, 240, 240)
GREY = (140, 150, 160)
DARK = (70, 78, 86)
YELLOW = (255, 212, 0)
GREEN = (47, 220, 74)
PURPLE = (192, 76, 255)
CYAN = (34, 211, 238)

TYRE_COLOURS = {"SOFT": (255, 42, 42), "MEDIUM": (255, 212, 0), "HARD": (232, 232, 232),
                "INTERMEDIATE": (47, 220, 74), "WET": (58, 141, 255)}

_enabled = None

try:  # box-drawing and block characters need UTF-8 whatever the console code page is
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


def set_plain(plain):
    """Force the plain log output (no colour, no in-place dashboard)."""
    global _enabled
    _enabled = False if plain else None


def enabled():
    """True when stdout is an interactive terminal that can take ANSI colour."""
    global _enabled
    if _enabled is None:
        _enabled = bool(sys.stdout.isatty()) and os.environ.get("F1_PLAIN") != "1"
        if _enabled and os.name == "nt":
            try:
                import ctypes
                k32 = ctypes.windll.kernel32
                h = k32.GetStdHandle(-11)
                mode = ctypes.c_uint32()
                if k32.GetConsoleMode(h, ctypes.byref(mode)):
                    k32.SetConsoleMode(h, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING
            except Exception:
                pass
    return _enabled


def fg(rgb):
    return ESC + "[38;2;%d;%d;%dm" % rgb if enabled() else ""


def bg(rgb):
    return ESC + "[48;2;%d;%d;%dm" % rgb if enabled() else ""


def c(text, rgb, bold=False):
    if not enabled():
        return text
    return (BOLD if bold else "") + fg(rgb) + str(text) + RESET


def hex_rgb(h, default=WHITE):
    h = (h or "").strip().lstrip("#")
    if len(h) != 6:
        return default
    try:
        return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))
    except ValueError:
        return default


def width():
    return shutil.get_terminal_size((100, 30)).columns


def badge_text(text, rgb):
    """Text on a solid colour block, black or white lettering by luminance, readable on any background."""
    if not enabled():
        return text
    lum = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
    ink = (0, 0, 0) if lum > 140 else (255, 255, 255)
    return bg(rgb) + fg(ink) + BOLD + text + RESET


# ----------------------------------------------------------------------------- frame preview
def frame_lines(webp_path, border=True):
    """Render a 64x32 WebP as 16 terminal lines, two pixels per cell."""
    from PIL import Image
    im = Image.open(webp_path).convert("RGB")
    if im.size != (64, 32):
        im = im.resize((64, 32))
    px = im.load()
    lines = []
    for y in range(0, 32, 2):
        row = []
        prev = None
        for x in range(64):
            top, bot = px[x, y], px[x, y + 1]
            key = (top, bot)
            if key != prev:
                row.append(ESC + "[38;2;%d;%d;%dm" % top + ESC + "[48;2;%d;%d;%dm" % bot)
                prev = key
            row.append("▀")
        lines.append("".join(row) + RESET)
    if not enabled():
        # monochrome fallback: brightness as characters
        lines = []
        for y in range(0, 32, 2):
            lines.append("".join(" .:-=+*#%@"[min(9, (sum(px[x, y]) + sum(px[x, y + 1])) // 160)] for x in range(64)))
    if border:
        edge = fg(DARK) if enabled() else ""
        reset = RESET if enabled() else ""
        top = edge + "┌" + "─" * 64 + "┐" + reset
        bottom = edge + "└" + "─" * 64 + "┘" + reset
        lines = [top] + [edge + "│" + reset + l + edge + "│" + reset for l in lines] + [bottom]
    return lines


def side_by_side(blocks, gap=3):
    """Join lists of equal-height lines horizontally. Each block must be 66 visible chars wide."""
    height = max(len(b) for b in blocks)
    out = []
    for i in range(height):
        out.append((" " * gap).join(b[i] if i < len(b) else " " * 66 for b in blocks))
    return out


# ----------------------------------------------------------------------------- pieces
def banner(subtitle=""):
    print()
    print("  " + c("██ F1", RED, bold=True) + c(" → ", GREY) + c("TETRA", WHITE, bold=True)
          + ("   " + c(subtitle, GREY) if subtitle else ""))
    print()


def hr():
    print(c("  " + "─" * min(width() - 4, 96), DARK))


def status_colour(fields):
    status = (fields.get("status") or "").upper()
    track = fields.get("track") or "1"
    if status in ("FINISHED", "FINALISED", "ENDS"):
        return ("FIN", GREY)
    if track == "5":
        return ("RED FLAG", RED)
    if track == "4":
        return ("SAFETY CAR", YELLOW)
    if track in ("6", "7"):
        return ("VSC", YELLOW)
    if track == "2":
        return ("YELLOW", YELLOW)
    if status in ("ABORTED", "INACTIVE"):
        return (status, GREY)
    return ("GREEN", GREEN)


def lap_or_clock(fields):
    if fields.get("total"):
        return "Lap %s/%s" % (fields.get("lap") or "-", fields["total"])
    if fields.get("clock"):
        return fields["clock"] + " left"
    return ""


# ----------------------------------------------------------------------------- menus
def layout_menu(layouts, previews, current):
    """layouts: [(name, desc)], previews: {name: webp path or None}. Returns the chosen name."""
    print(c("  Choose a layout", WHITE, bold=True) + c("   (what the Tetra will show)", GREY))
    print()
    blocks = []
    for i, (name, desc) in enumerate(layouts, 1):
        marker = c(" ●", GREEN) if name == current else "  "
        title = marker + c(" %d " % i, YELLOW, bold=True) + c(name, WHITE, bold=True) + c("  " + desc, GREY)
        if previews.get(name):
            lines = frame_lines(previews[name])
        else:
            lines = [c("  (preview unavailable)", GREY)] + [""] * 17
        blocks.append((title, lines))
    two_up = width() >= 2 * 66 + 5
    i = 0
    while i < len(blocks):
        group = blocks[i:i + 2] if two_up else blocks[i:i + 1]
        print("  " + "".join(pad_visible(t, 66 + 3) for t, _ in group).rstrip())
        for line in side_by_side([l for _, l in group]):
            print("  " + line)
        print()
        i += len(group)
    while True:
        v = ask("Layout", current)
        v = v.strip().lower()
        if v.isdigit() and 1 <= int(v) <= len(layouts):
            return layouts[int(v) - 1][0]
        names = [n for n, _ in layouts]
        if v in names:
            return v
        print(c("    pick a number 1-%d or a name: %s" % (len(layouts), ", ".join(names)), RED))


def driver_menu(drivers, current):
    """drivers: list of dicts with tla, name, teamname, team (hex), number, pos. Returns a TLA."""
    print(c("  Choose a driver", WHITE, bold=True) + c("   (type the three-letter code)", GREY))
    print()
    if not drivers:
        print(c("    driver list not available yet, type the code anyway (e.g. VER)", GREY))
        print()
    rows = []
    for d in drivers:
        rgb = hex_rgb(d.get("team"))
        pos = d.get("pos") or "-"
        name = (d.get("name") or "")[:22]
        team = (d.get("teamname") or "")[:18]
        num = d.get("number") or ""
        mark = c("● ", GREEN) if d["tla"] == current else "  "
        rows.append(mark + badge_text(" %-3s " % d["tla"], rgb) + c(" %3s " % num, GREY)
                    + c("%-22s" % name, WHITE) + c(" %-18s" % team, GREY) + c(" P%-2s" % pos, GREY))
    two_up = width() >= 2 * 58 + 6 and len(rows) > 6
    if two_up:
        half = (len(rows) + 1) // 2
        for a, b in zip(rows[:half], rows[half:] + [""] * (half - len(rows[half:]))):
            print("  " + pad_visible(a, 58) + "  " + b)
    else:
        for r in rows:
            print("  " + r)
    print()
    valid = {d["tla"] for d in drivers}
    while True:
        v = ask("Driver", current).strip().upper()
        if len(v) == 3 and (not valid or v in valid):
            return v
        print(c("    not in this session's driver list" if valid else "    three letters, e.g. VER", RED))


scripted_answers = []  # filled from --answers; each prompt pops one instead of waiting for input


def ask(label, default):
    prompt = "  " + c(label, WHITE, bold=True) + c(" › ", GREY) + c("[%s] " % default, DARK)
    if scripted_answers:
        v = scripted_answers.pop(0)
        print(prompt + v)
        return v.strip() or default
    try:
        v = input(prompt)
    except EOFError:
        return default
    return v.strip() or default


def visible_len(s):
    n = 0
    i = 0
    while i < len(s):
        if s[i] == ESC:
            j = s.find("m", i)
            i = j + 1 if j > 0 else len(s)
            continue
        n += 1
        i += 1
    return n


def pad_visible(s, n):
    return s + " " * max(0, n - visible_len(s))


# ----------------------------------------------------------------------------- live dashboard
class Dashboard:
    """Redraws in place every tick: header, the frame as the Tetra shows it, driver line, countdown."""

    def __init__(self, cfg, device_name):
        self.cfg = cfg
        self.device = device_name
        self.frame = None
        self.fields = None
        self.last_push = 0
        self.lines_drawn = 0
        self.hub_ok = False
        self.error = ""
        if enabled():
            sys.stdout.write(ESC + "[2J" + ESC + "[H")
            sys.stdout.flush()

    def set_frame(self, webp_path, fields, pushed):
        self.frame = frame_lines(webp_path)
        self.fields = fields
        if pushed:
            self.last_push = time.time()

    def draw(self, message=""):
        if not enabled():
            return
        f = self.fields or {}
        out = []
        meeting = f.get("meeting") or ""
        session = f.get("session") or ""
        head = "  " + c("██ F1", RED, bold=True) + c(" → ", GREY) + c("TETRA", WHITE, bold=True)
        if meeting:
            head += "   " + c(meeting, WHITE) + c(" · " + session, GREY)
        lc = lap_or_clock(f)
        if lc:
            head += c(" · " + lc, GREY)
        if f:
            label, rgb = status_colour(f)
            head += "   " + bg(rgb) + fg((0, 0, 0)) + BOLD + " " + label + " " + RESET
        out.append(head)
        out.append("")
        if self.frame:
            out += ["  " + l for l in self.frame]
        else:
            out += ["  " + c("waiting for the first frame...", GREY)] + [""] * 17
        out.append("")
        if f:
            rgb = hex_rgb(f.get("team"))
            tyre = (f.get("tyre") or "").upper()
            tyre_txt = c("● " + (tyre[:1] + (f.get("tyreage") or "")), TYRE_COLOURS.get(tyre, GREY)) if tyre else c("no tyre data", GREY)
            flag = (f.get("lastflag") or "none").lower()
            last_rgb = PURPLE if flag == "ob" else GREEN if flag == "pb" else WHITE
            pit = ""
            if f.get("retired") == "1":
                pit = "  " + bg(DARK) + fg(WHITE) + " OUT " + RESET
            elif f.get("inpit") == "1":
                pit = "  " + bg(WHITE) + fg((0, 0, 0)) + " PIT " + RESET
            elif f.get("pitout") == "1":
                pit = "  " + bg(GREEN) + fg((0, 0, 0)) + " OUT LAP " + RESET
            out.append("  " + badge_text(" %s " % f.get("tla", ""), rgb)
                       + c("  P" + (f.get("pos") or "-"), WHITE, bold=True)
                       + c("   int ", GREY) + c(f.get("interval") or "—", WHITE)
                       + c("   gap ", GREY) + c(f.get("gap") or "—", WHITE)
                       + c("   last ", GREY) + c(f.get("last") or "—", last_rgb)
                       + "   " + tyre_txt + c("   %s stop%s" % (f.get("stops") or "0", "" if f.get("stops") == "1" else "s"), GREY) + pit)
        else:
            out.append("")
        nxt = max(0, int(self.cfg["push_every"] - (time.time() - self.last_push))) if self.last_push else None
        status = c("  layout ", GREY) + c(self.cfg["layout"], WHITE) + c("   device ", GREY) + c(self.device or "-", WHITE)
        if self.last_push:
            status += c("   pushed ", GREY) + c(time.strftime("%H:%M:%S", time.localtime(self.last_push)), WHITE)
            status += c("   next in ", GREY) + c("%ds" % nxt, YELLOW if nxt <= 2 else WHITE)
        status += c("   hub ", GREY) + (c("connected", GREEN) if self.hub_ok else c("connecting...", YELLOW))
        out.append(status)
        if message:
            out.append("  " + c(message, YELLOW))
        if self.error:
            out.append("  " + c(self.error, RED))
        out.append("")
        out.append("  " + c("Ctrl+C to stop", GREY))
        # redraw in place
        buf = ESC + "[H"
        for line in out:
            buf += line + ESC + "[K\n"
        if self.lines_drawn > len(out):
            buf += (ESC + "[K\n") * (self.lines_drawn - len(out))
        self.lines_drawn = len(out)
        sys.stdout.write(buf)
        sys.stdout.flush()

    def finish(self):
        if enabled():
            sys.stdout.write("\n")
            sys.stdout.flush()
