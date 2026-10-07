"""Package the transparent imagegen master into Windows ICO, using artifact Python/Pillow."""

import shutil
import sys
from pathlib import Path

from PIL import Image, ImageDraw

root = Path(__file__).resolve().parents[1]
assets = root / "src" / "usagedesk" / "assets"
source = Path(sys.argv[1])
master = Image.open(source).convert("RGBA")
assert master.width == master.height, "Icon master must be square"
assert master.getchannel("A").getextrema() == (0, 255), "Actual transparent master required"
shutil.copyfile(source, assets / "usagedesk-icon.png")
sizes = [(n, n) for n in (16, 24, 32, 48, 64, 128, 256)]
destination = assets / "usagedesk.ico"
master.save(destination, format="ICO", sizes=sizes)
with Image.open(destination) as icon:
    assert icon.ico.sizes() == set(sizes)
    for size in sizes:
        frame = icon.ico.getimage(size)
        assert frame.size == size
        assert frame.convert("RGBA").getpixel((0, 0))[3] == 0
    sheet = Image.new("RGB", (760, 350), "white")
    draw = ImageDraw.Draw(sheet)
    draw.rectangle((0, 175, 760, 350), fill="#20242c")
    x = 16
    for size in sizes:
        n = size[0]
        frame = icon.ico.getimage(size).convert("RGBA")
        if n == 256:
            frame = frame.resize((128, 128), Image.Resampling.LANCZOS)
        for y, color in ((5, "#20242c"), (180, "white")):
            draw.text((x, y), f"{n}px" + (" (50%)" if n == 256 else ""), fill=color)
            sheet.paste(frame, (x, y + 22), frame)
        x += frame.width + 20
    sheet.save(root / ".test-data" / "icon-size-preview.png")
print("ICO verified:", sorted(sizes))
