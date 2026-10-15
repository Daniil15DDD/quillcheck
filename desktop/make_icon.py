"""Draw the app icon (magnifying glass on a rounded square). Usage: python make_icon.py out.png"""
import sys

from PIL import Image, ImageDraw

S = 1024
img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
bg = Image.new("RGBA", (S, S))
top, bottom = (52, 120, 246), (24, 58, 140)
for y in range(S):
    t = y / S
    ImageDraw.Draw(bg).line([(0, y), (S, y)], fill=tuple(round(a + (b - a) * t) for a, b in zip(top, bottom)) + (255,))
mask = Image.new("L", (S, S))
ImageDraw.Draw(mask).rounded_rectangle([100, 100, S - 100, S - 100], radius=185, fill=255)
img.paste(bg, mask=mask)

d = ImageDraw.Draw(img)
for i, w in enumerate((330, 270, 300)):  # text lines behind the lens
    d.rounded_rectangle([240, 300 + i * 110, 240 + w, 340 + i * 110], radius=20, fill=(255, 255, 255, 120))
d.line([(620, 620), (790, 790)], fill="white", width=78)
d.ellipse([340, 340, 700, 700], outline="white", width=56)
img.save(sys.argv[1])
