"""Draw exact root permutations for x^4 - 2; no simulated or measured data."""
from pathlib import Path
from html import escape

ROOT = Path(__file__).resolve().parents[1] / "public/figures"

def diagram(mobile=False):
    width, height = (600, 1070) if mobile else (1100, 535)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" aria-labelledby="title desc">',
      '<title id="title">Which permutations belong to the Galois group of x⁴ − 2?</title>',
      '<desc id="desc">Complex conjugation swaps iα and −iα and fixes α and −α. It is a field automorphism. Exchanging α and iα alone fails: α + (−α) = 0 would become iα − α = 0, which is false. Here α is the positive fourth root of two. The Galois group has eight elements, whereas four roots have twenty-four permutations.</desc>',
      f'<rect width="{width}" height="{height}" fill="#ffffff"/>',
      '<defs><marker id="blue" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto-start-reverse"><path d="M0 0L7 3.5L0 7" fill="#315dff"/></marker><marker id="red" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto-start-reverse"><path d="M0 0L7 3.5L0 7" fill="#a33e35"/></marker></defs>']

    def text(x, y, value, size=22, color="#1b2531", anchor="start", weight="400"):
        parts.append(f'<text x="{x}" y="{y}" font-family="Arial,Helvetica,sans-serif" font-size="{size}" fill="{color}" text-anchor="{anchor}" font-weight="{weight}">{escape(value)}</text>')

    for rejected in (False, True):
        x = 18 if mobile else 20 + int(rejected) * 550
        y = 18 + int(rejected) * 474 if mobile else 18
        w, h = (564, 450) if mobile else (510, 383)
        color, marker = ("#a33e35", "red") if rejected else ("#315dff", "blue")
        parts.append(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="2" fill="{("#f6eeee" if rejected else "#eef2ff")}"/>')
        text(x + 25, y + 41, "FORBIDDEN" if rejected else "ALLOWED", 25 if mobile else 17, color, weight="700")
        text(x + 25, y + 80, "Swap two adjacent roots" if rejected else "Complex conjugation", 30 if mobile else 26, weight="600")
        cx, cy, radius = x + w / 2, y + (240 if mobile else 194), 90 if mobile else 72
        parts.append(f'<path d="M{cx-radius-12} {cy}H{cx+radius+12}M{cx} {cy-radius-12}V{cy+radius+12}" fill="none" stroke="#c4ccd8" stroke-width="1.5"/>')
        roots = [(cx + radius, cy, "α", "start", cx + radius + 17, cy + 8, rejected),
                 (cx - radius, cy, "−α", "end", cx - radius - 17, cy + 8, False),
                 (cx, cy - radius, "iα", "middle", cx, cy - radius - 19, True),
                 (cx, cy + radius, "−iα", "middle", cx, cy + radius + 35, not rejected)]
        for px, py, label, anchor, tx, ty, active in roots:
            parts.append(f'<circle cx="{px}" cy="{py}" r="7" fill="{color if active else "#697586"}"/>')
            text(tx, ty, label, 30 if mobile else 25, color if active else "#465465", anchor)
        if rejected:
            path = f'M{cx+14} {cy-radius+2}Q{cx+radius+8} {cy-radius-8} {cx+radius-2} {cy-14}'
        else:
            path = f'M{cx+10} {cy-radius+12}C{cx+radius*.85} {cy-radius*.6} {cx+radius*.85} {cy+radius*.6} {cx+10} {cy+radius-12}'
        parts.append(f'<path d="{path}" fill="none" stroke="{color}" stroke-width="3" marker-start="url(#{marker})" marker-end="url(#{marker})"/>')
        baseline = y + h - (48 if mobile else 47)
        text(cx, baseline, "α ↔ iα; −α stays fixed" if rejected else "iα ↔ −iα; α and −α stay fixed", 25 if mobile else 21, color, "middle")
        text(cx, baseline + 31, "α + (−α) = 0 → iα − α ≠ 0." if rejected else "Every rational algebraic relation survives.", 23 if mobile else 18, "#465465", "middle")
    text(width / 2, 1002 if mobile else 456, "8 automorphisms. 24 permutations.", 30 if mobile else 29, anchor="middle", weight="600")
    text(width / 2, 1042 if mobile else 493, "The algebra decides which permutations are allowed.", 24 if mobile else 19, "#546174", "middle")
    parts.append('</svg>')
    return '\n'.join(parts) + '\n'

ROOT.mkdir(parents=True, exist_ok=True)
for mobile in (False, True):
    path = ROOT / ('galois-symmetries-mobile.svg' if mobile else 'galois-symmetries.svg')
    path.write_text(diagram(mobile))
    print(path.name)
