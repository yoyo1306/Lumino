"""Genere l'icone Lumino : tuile sombre arrondie + L geometrique + LED."""
from PIL import Image, ImageDraw

S = 512
BG = (17, 24, 39, 255)      # #111827
L_COL = (255, 255, 255, 255)
LED = (0, 213, 255, 255)    # cyan Lumino

img = Image.new("RGBA", (S, S), (0, 0, 0, 0))
d = ImageDraw.Draw(img)

# tuile arrondie
d.rounded_rectangle([8, 8, S - 8, S - 8], radius=120, fill=BG)

# halo subtil derriere le L
halo = Image.new("RGBA", (S, S), (0, 0, 0, 0))
hd = ImageDraw.Draw(halo)
hd.ellipse([90, 70, 422, 402], fill=(0, 213, 255, 28))
img = Image.alpha_composite(img, halo)
d = ImageDraw.Draw(img)

# L geometrique (barres epaisses)
x0, y0 = 150, 130
bar, h, foot = 76, 252, 190
d.rounded_rectangle([x0, y0, x0 + bar, y0 + h], radius=20, fill=L_COL)
d.rounded_rectangle([x0, y0 + h - bar, x0 + foot, y0 + h], radius=20, fill=L_COL)

# LED : pastille cyan avec lueur
cx, cy, r = 372, 352, 34
d.ellipse([cx - r - 14, cy - r - 14, cx + r + 14, cy + r + 14], fill=(0, 213, 255, 60))
d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=LED)
d.ellipse([cx - r + 10, cy - r + 8, cx - r + 26, cy - r + 24], fill=(255, 255, 255, 220))

img.save("lumino.png")
img.save("lumino.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48),
                              (64, 64), (128, 128), (256, 256)])
print("icones OK")
