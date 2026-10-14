"""
Render the console UI to PNG for the README: docs/console-layouts.png,
docs/console-drivers.png and docs/console-dashboard.png.

It runs the real menus and dashboard with demo data, captures the ANSI output
and draws it with Cascadia Mono inside a Windows Terminal style window, so the
images are crisp and show only the window.

    python tools/make_screenshots.py
"""
import io, os, re, sys, types, builtins, shutil

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.argv = ["bridge.py", "--demo"]
import ui, bridge  # noqa: E402
from PIL import Image, ImageDraw, ImageFont  # noqa: E402

FONT_CANDIDATES = [r"C:\Windows\Fonts\CascadiaMono.ttf", r"C:\Windows\Fonts\consola.ttf",
                   "/System/Library/Fonts/Menlo.ttc", "/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf"]
FONT_SIZE = 15
BG = (12, 12, 12)
FG = (204, 204, 204)
TITLE_BG = (32, 32, 32)
TITLE_FG = (230, 230, 230)
PAD = 12
TITLE_H = 36

ESC = "\x1b"
SGR = re.compile(r"\x1b\[([0-9;]*)m")
OTHER = re.compile(r"\x1b\[[0-9;]*[A-LN-Za-ln-z]")  # cursor moves, clears: dropped


def load_font():
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            return ImageFont.truetype(p, FONT_SIZE), ImageFont.truetype(p, FONT_SIZE)
    return ImageFont.load_default(), ImageFont.load_default()


def parse(text):
    """ANSI text -> list of lines; each line a list of (char, fg, bg, bold)."""
    lines = []
    fg, bg, bold = FG, None, False
    cur = []
    i = 0
    text = OTHER.sub("", text)
    while i < len(text):
        m = SGR.match(text, i)
        if m:
            params = [int(p) for p in m.group(1).split(";") if p != ""] or [0]
            j = 0
            while j < len(params):
                p = params[j]
                if p == 0:
                    fg, bg, bold = FG, None, False
                elif p == 1:
                    bold = True
                elif p == 2:
                    bold = False
                elif p == 38 and params[j + 1:j + 2] == [2]:
                    fg = tuple(params[j + 2:j + 5]); j += 4
                elif p == 48 and params[j + 1:j + 2] == [2]:
                    bg = tuple(params[j + 2:j + 5]); j += 4
                j += 1
            i = m.end()
            continue
        ch = text[i]
        if ch == "\n":
            lines.append(cur); cur = []
        elif ch == "\r":
            cur = []
        elif ch != ESC:
            cur.append((ch, fg, bg, bold))
        i += 1
    if cur:
        lines.append(cur)
    return lines


def render(lines, title, out_path):
    font, bold_font = load_font()
    bbox = font.getbbox("M")
    cw = font.getlength("M")
    ch = int(FONT_SIZE * 1.35)
    cols = max((len(l) for l in lines), default=0)
    W = int(PAD * 2 + cols * cw)
    H = TITLE_H + PAD * 2 + ch * len(lines)
    im = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(im)
    # title bar in the Windows Terminal style
    d.rectangle([0, 0, W, TITLE_H], fill=TITLE_BG)
    d.rectangle([8, 6, 8 + 180, TITLE_H - 2], fill=BG)
    d.text((18, 9), "\u25b6  " + title, font=font, fill=TITLE_FG)
    cy = TITLE_H // 2
    x = W - 110
    d.line([x, cy, x + 10, cy], fill=TITLE_FG)                       # minimise
    d.rectangle([x + 36, cy - 5, x + 46, cy + 5], outline=TITLE_FG)  # maximise
    d.line([x + 72, cy - 5, x + 82, cy + 5], fill=TITLE_FG)          # close
    d.line([x + 72, cy + 5, x + 82, cy - 5], fill=TITLE_FG)
    y = TITLE_H + PAD
    for line in lines:
        x = PAD
        for c, fg, bg, bold in line:
            x0, y0, x1, y1 = x, y, x + cw, y + ch
            if bg:
                d.rectangle([x0, y0, x1, y1], fill=bg)
            if c == "\u2580":          # upper half block: top = fg, bottom = bg (already drawn)
                d.rectangle([x0, y0, x1, y0 + ch / 2], fill=fg)
            elif c == "\u2588":        # full block
                d.rectangle([x0, y0, x1, y1], fill=fg)
            elif c == "\u2500":
                d.line([x0, y0 + ch / 2, x1, y0 + ch / 2], fill=fg)
            elif c == "\u2502":
                d.line([x0 + cw / 2, y0, x0 + cw / 2, y1], fill=fg)
            elif c in "\u250c\u2510\u2514\u2518":
                mx, my = x0 + cw / 2, y0 + ch / 2
                d.line([mx, my, x1 if c in "\u250c\u2514" else x0, my], fill=fg)
                d.line([mx, my, mx, y1 if c in "\u250c\u2510" else y0], fill=fg)
            elif c == "\u25cf":
                r = cw * 0.35
                d.ellipse([x0 + cw / 2 - r, y0 + ch / 2 - r, x0 + cw / 2 + r, y0 + ch / 2 + r], fill=fg)
            elif c != " ":
                d.text((x0, y0 + (ch - (bbox[3] - bbox[1])) / 2 - bbox[1]), c, font=bold_font if bold else font, fill=fg)
            x += cw
        y += ch
    im.save(out_path)
    print("wrote", os.path.relpath(out_path, ROOT), im.size)


class Capture:
    """Run a piece of the UI with a fake interactive terminal and collect its ANSI output."""

    def __init__(self, width=140, answers=()):
        self.width = width
        self.answers = list(answers)

    def __enter__(self):
        self.buf = io.StringIO()
        self.saved = (sys.stdout, sys.stdin, builtins.input, shutil.get_terminal_size, ui._enabled)
        sys.stdout = self.buf
        sys.stdin = types.SimpleNamespace(isatty=lambda: True)
        builtins.input = self.fake_input
        shutil.get_terminal_size = lambda fallback=None: os.terminal_size((self.width, 60))
        ui._enabled = True
        return self

    def fake_input(self, prompt=""):
        v = self.answers.pop(0) if self.answers else ""
        sys.stdout.write(prompt + v + "\n")
        return v

    def __exit__(self, *a):
        sys.stdout, sys.stdin, builtins.input, shutil.get_terminal_size, ui._enabled = self.saved
        return False

    def text(self):
        return self.buf.getvalue()


def main():
    docs = os.path.join(ROOT, "docs")
    os.makedirs(docs, exist_ok=True)
    tetra = bridge.find_tetra(bridge.load_config(types.SimpleNamespace(driver=None, layout=None, temporary=False))["tetra"])
    cfg_path = os.path.join(ROOT, "config.json")
    saved_cfg = open(cfg_path, "rb").read() if os.path.exists(cfg_path) else None
    try:
        args = types.SimpleNamespace(driver=None, layout=None, no_prompt=False, demo=True)
        cfg = bridge.load_config(types.SimpleNamespace(driver=None, layout=None, temporary=False))
        cfg["driver"], cfg["layout"] = "HAM", "tower"

        # 1. layout menu (stops after the layout answer) and 2. driver menu
        with Capture(answers=["2", "HAM"]) as cap:
            bridge.choose(cfg, args, None, tetra)
        text = cap.text()
        layouts_part, drivers_part = text.split("  Choose a driver", 1)
        render(parse(layouts_part.rstrip("\n")), "F1 Tetra", os.path.join(docs, "console-layouts.png"))
        render(parse(("  Choose a driver" + drivers_part).rstrip("\n")), "F1 Tetra", os.path.join(docs, "console-drivers.png"))

        # 3. dashboard
        fields = bridge.demo_fields("HAM", "tower")
        frame = os.path.join(ROOT, "frame.webp")
        bridge.render(tetra, fields, frame)
        with Capture() as cap:
            dash = ui.Dashboard(dict(cfg, push_every=10), "Desk Tetra")
            dash.hub_ok = True
            dash.set_frame(frame, fields, pushed=True)
            dash.draw()
        render(parse(cap.text().rstrip("\n")), "F1 Tetra", os.path.join(docs, "console-dashboard.png"))
    finally:
        if saved_cfg is not None:
            open(cfg_path, "wb").write(saved_cfg)


if __name__ == "__main__":
    main()
