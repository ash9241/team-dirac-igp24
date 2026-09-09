"""Draw the standard local-search landscape; the curve is schematic, not data."""
from pathlib import Path
from html import escape
from math import cos, pi, hypot

OUT = Path(__file__).resolve().parents[1] / "public/figures"
KNOTS = [(0, .10), (.22, .57), (.40, .18), (.65, .87), (.82, .43), (.94, .43), (1, .20)]

def score(u):
    for (a, low), (b, high) in zip(KNOTS, KNOTS[1:]):
        if a <= u <= b:
            t = (u-a)/(b-a)
            return low + (high-low)*(1-cos(pi*t))/2
    raise ValueError(u)

def draw(mobile=False):
    w, h = (640, 620) if mobile else (1100, 530)
    left, right, bottom, scale = (64, 604, 435, 350) if mobile else (90, 1035, 386, 325)
    blue, ink, red = "#244bff", "#283342", "#a33e35"
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" role="img" aria-labelledby="title desc">',
        '<title id="title">Hill climbing can stop below the highest peak</title>',
        '<desc id="desc">The horizontal axis represents possible candidates, not time. Height represents score. Blue improving steps reach a local maximum. A valley separates this peak from the higher global maximum. A flat plateau on the far right gives neighboring candidates equal scores. This is a conceptual drawing, not IGP24 measurements.</desc>',
        f'<rect width="{w}" height="{h}" fill="white"/>',
        f'<defs><marker id="step" markerWidth="6" markerHeight="6" refX="5" refY="3" orient="auto"><path d="M0 0L6 3L0 6" fill="{blue}"/></marker></defs>']

    def point(u):
        return left + u*(right-left), bottom-score(u)*scale

    def text(x, y, value, size=20, color=ink, anchor="middle", weight="400"):
        parts.append(f'<text x="{x:.1f}" y="{y:.1f}" font-family="Arial,Helvetica,sans-serif" font-size="{size}" fill="{color}" text-anchor="{anchor}" font-weight="{weight}">{escape(value)}</text>')

    points = [point(i/500) for i in range(501)]
    curve = "M" + "L".join(f"{x:.2f} {y:.2f}" for x, y in points)
    parts.append(f'<path d="{curve}L{right} {bottom}H{left}Z" fill="#edf0f5"/>')
    parts.append(f'<path d="M{left} 65V{bottom}H{right+10}" fill="none" stroke="#aab5c4" stroke-width="1.5"/>')
    parts.append(f'<path d="{curve}" fill="none" stroke="#778599" stroke-width="3"/>')
    text(left, 35, "Better score ↑", 23 if mobile else 21, anchor="start")
    text((left+right)/2, bottom+49, "Possible candidates →", 23 if mobile else 21)

    steps = [.035, .075, .115, .155, .19, .22]
    for a, b in zip(steps, steps[1:]):
        x1, y1 = point(a); x2, y2 = point(b)
        d = hypot(x2-x1, y2-y1)
        dx, dy = (x2-x1)/d, (y2-y1)/d
        inset = min(8, d*.2)
        marker = ' marker-end="url(#step)"' if d > 32 else ''
        parts.append(f'<path d="M{x1+dx*inset:.2f} {y1+dy*inset:.2f}L{x2-dx*inset:.2f} {y2-dy*inset:.2f}" fill="none" stroke="{blue}" stroke-width="3"{marker}/>')
    for u in steps:
        x, y = point(u)
        parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="6" fill="{red if u == .22 else blue}" stroke="white" stroke-width="2"/>')
    x, y = point(.035)
    text(x + (28 if mobile else 8), y+34, "Start", 22 if mobile else 20, blue)
    x, y = point(.22)
    text(x, y-58, "Local peak", 26 if mobile else 25, red, weight="600")
    text(x, y-30, "Best nearby", 22 if mobile else 19, red)
    x, y = point(.65)
    parts.append(f'<circle cx="{x:.2f}" cy="{y:.2f}" r="6" fill="{ink}" stroke="white" stroke-width="2"/>')
    text(x, y-59, "Global peak", 26 if mobile else 25, weight="600")
    text(x, y-31, "Highest overall", 22 if mobile else 19)
    x, y = point(.88)
    text(x, y-54, "Plateau", 25 if mobile else 24, weight="600")
    text(x, y-27, "Flat patch" if mobile else "Same score nearby", 22 if mobile else 19)
    if mobile:
        text(w/2, 551, "Each blue step improves the score.", 25, blue, weight="600")
        text(w/2, 586, "The first peak need not be the best.", 23)
    else:
        text(w/2, 489, "Each blue step improves the score. The first peak need not be the best.", 22, blue, weight="600")
    parts.append('</svg>')
    return '\n'.join(parts) + '\n'

OUT.mkdir(parents=True, exist_ok=True)
for mobile in (False, True):
    name = 'hill-climbing-mobile.svg' if mobile else 'hill-climbing.svg'
    (OUT/name).write_text(draw(mobile))
    print(name)
