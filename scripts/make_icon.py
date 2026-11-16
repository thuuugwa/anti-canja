"""Gera assets/anticanja.ico (e um PNG 256px) com o logotipo do ANTI CANJA.

Uso:  python scripts/make_icon.py
Requer Pillow (instalado pelo build.ps1).
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

OUT = Path(__file__).resolve().parents[1] / "assets"
S = 1024  # desenha grande e reduz (antialias)


def bezier(p0, p1, p2, p3, n=40):
    pts = []
    for i in range(n + 1):
        t = i / n
        a = (1 - t) ** 3
        b = 3 * (1 - t) ** 2 * t
        c = 3 * (1 - t) * t ** 2
        d = t ** 3
        pts.append((a * p0[0] + b * p1[0] + c * p2[0] + d * p3[0], a * p0[1] + b * p1[1] + c * p2[1] + d * p3[1]))
    return pts


def shield(x, y, w, h):
    P = lambda fx, fy: (x + w * fx, y + h * fy)  # noqa: E731
    pts = []
    pts += bezier(P(.5, 0), P(.70, .08), P(.86, .10), P(1, .12))
    pts += bezier(P(1, .12), P(1, .55), P(.82, .82), P(.5, 1))
    pts += bezier(P(.5, 1), P(.18, .82), P(0, .55), P(0, .12))
    pts += bezier(P(0, .12), P(.14, .10), P(.30, .08), P(.5, 0))
    return pts


def gradient(size, c0, c1):
    g = Image.new("RGBA", (size, size))
    px = g.load()
    for yy in range(size):
        for xx in range(size):
            t = (xx + yy) / (2 * size)
            px[xx, yy] = tuple(int(c0[i] + (c1[i] - c0[i]) * t) for i in range(3)) + (255,)
    return g


def render() -> Image.Image:
    img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    bgmask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(bgmask).rounded_rectangle((0, 0, S - 1, S - 1), radius=int(S * .24), fill=255)
    img.paste(gradient(S, (31, 22, 48), (13, 10, 20)), (0, 0), bgmask)

    m = S * .16
    x, y, w, h = m, m * .9, S - 2 * m, S - 1.8 * m
    smask = Image.new("L", (S, S), 0)
    ImageDraw.Draw(smask).polygon(shield(x, y, w, h), fill=255)
    img.paste(gradient(S, (167, 139, 250), (124, 58, 237)), (0, 0), smask)

    d = ImageDraw.Draw(img)
    d.line([(x + w * .18, y + h * .78), (x + w * .82, y + h * .26)], fill=(13, 10, 20, 255), width=int(S * .06))
    try:
        font = ImageFont.truetype("segoeuib.ttf", int(S * .2))
    except OSError:
        try:
            font = ImageFont.truetype("DejaVuSans-Bold.ttf", int(S * .2))
        except OSError:
            font = ImageFont.load_default()
    d.text((S / 2, y + h * .52), "AC", font=font, fill="white", anchor="mm")
    return img


def main() -> None:
    OUT.mkdir(exist_ok=True)
    big = render()
    big.resize((256, 256), Image.LANCZOS).save(OUT / "anticanja.png")
    big.save(OUT / "anticanja.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    print("ícones gerados em", OUT)


if __name__ == "__main__":
    main()
