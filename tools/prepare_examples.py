"""Generate small documentation previews without changing original artwork."""
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont, ImageOps

ROOT = Path(__file__).resolve().parents[1]


def comparison(destination, panels):
    width, height = 480, 320
    image = Image.new("RGB", (len(panels) * (width + 16) + 16, height + 74), (25, 28, 32))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=15)
    for index, (path, title) in enumerate(panels):
        with Image.open(path) as source:
            thumb = ImageOps.contain(ImageOps.exif_transpose(source).convert("RGB"), (width, height))
        x = 16 + index * (width + 16)
        draw.text((x, 14), title, fill=(228, 231, 235), font=font)
        image.paste(thumb, (x + (width - thumb.width) // 2, 44 + (height - thumb.height) // 2))
    image.save(destination, format="JPEG", quality=90, optimize=True)


def main():
    examples = ROOT / "Examples"
    previews = examples / "previews"
    previews.mkdir(parents=True, exist_ok=True)
    source, full = examples / "sources", examples / "full-resolution"
    comparison(previews / "round-shapes-comparison.jpg", [(source / "round-shapes.jpg", "Digital input"), (full / "round-shapes-uv.png", "Predicted UV appearance")])
    comparison(previews / "abstract-light-art-comparison.jpg", [(source / "abstract-light-art.jpg", "Desired UV appearance"), (full / "abstract-light-art-digital.png", "Estimated digital artwork"), (full / "abstract-light-art-roundtrip-uv.png", "Round-trip UV prediction")])
    print(f"Saved documentation previews to {previews}")


if __name__ == "__main__":
    main()
