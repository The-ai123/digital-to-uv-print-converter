"""Reconstruct the user's 37-column reference charts and learn RGB mappings.

No desktop control or generative image editing. Crops remain unchanged; patch
medians produce clean charts. Coordinates below describe these specific crops.
"""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from uvconvert import convert, load_rgb, save_png, validate_profile

ROOT = Path(__file__).resolve().parents[1]
COLS, ROWS = 37, 20

# First/last patch CENTER coordinates, in TL/TR/BR/BL order. Separate blocks
# avoid treating the wider J/K gutter as an ordinary row. Gray has 33 cells,
# beginning under column 5. Values are scaled only when crop dimensions change.
LAYOUTS = {
    "digital": {
        "size": [2082, 912],
        "upper": [[85, 70], [2010, 70], [2010, 410], [85, 410]],
        "lower": [[85, 459], [2010, 459], [2010, 804], [85, 804]],
        "gray": [[299, 850], [2010, 850]],
    },
    "blacklight": {
        "size": [2070, 896],
        "upper": [[90, 59], [1995, 61], [2000, 392], [75, 390]],
        "lower": [[75, 441], [2000, 442], [2000, 783], [70, 783]],
        "gray": [[289, 833], [2001, 828]],
    },
}


def coordinates(image, layout):
    """Map row/column identities to interior sample windows, including mild skew."""
    scale = np.array(image.size) / np.array(layout["size"])
    samples = []
    for block, start_row in (("upper", 0), ("lower", 10)):
        tl, tr, br, bl = np.array(layout[block], dtype=float) * scale
        half_width = np.linalg.norm(tr - tl) / (COLS - 1) * .17
        half_height = min(np.linalg.norm(bl - tl), np.linalg.norm(br - tr)) / 9 * .17
        for row in range(10):
            v = row / 9
            left, right = (1 - v) * tl + v * bl, (1 - v) * tr + v * br
            for col in range(COLS):
                center = left + (right - left) * col / (COLS - 1)
                samples.append((chr(65 + row + start_row) + str(col + 1), row + start_row, col, center, half_width, half_height))
    first, last = np.array(layout["gray"], dtype=float) * scale
    for col in range(33):
        center = first + (last - first) * col / 32
        samples.append(("X" + str(col + 5), 20, col + 4, center, 8 * scale[0], 4 * scale[1]))
    return samples


def extract(image, layout):
    pixels = np.asarray(image)
    patches = []
    overlay = image.copy()
    draw = ImageDraw.Draw(overlay)
    for label, row, col, center, hw, hh in coordinates(image, layout):
        x, y = center
        box = (int(np.ceil(x - hw)), int(np.ceil(y - hh)), int(np.floor(x + hw)) + 1, int(np.floor(y + hh)) + 1)
        x0, y0, x1, y1 = box
        if not (0 <= x0 < x1 <= image.width and 0 <= y0 < y1 <= image.height):
            raise ValueError(f"Patch {label} falls outside the crop; check the layout.")
        window = pixels[y0:y1, x0:x1].reshape(-1, 3).astype(float)
        median = np.median(window, axis=0)
        rgb = np.rint(median).astype(int)
        patches.append({"id": label, "row": row, "column": col,
                        "rgb": rgb.tolist(), "center": center.tolist(), "box": list(box),
                        "median_absolute_deviation": np.median(np.abs(window - median), axis=0).tolist()})
        draw.rectangle((x0, y0, x1 - 1, y1 - 1), outline=(0, 255, 120), width=2)
    return patches, overlay


def build_profile(digital, blacklight, provenance):
    # Duplicate RGB readings can arise in a blurry screenshot. Preserve all
    # correspondences in samples.json, average their targets for a valid model.
    groups = {}
    for source, target in zip(digital, blacklight, strict=True):
        if source["id"] != target["id"]:
            raise ValueError("Chart patches are not aligned by row and column.")
        groups.setdefault(tuple(source["rgb"]), []).append(target["rgb"])
    profile = {
        "version": 1,
        "name": "User chart crops — approximate screenshot RGB mapping",
        "approximate": True,
        "interpolation": "local-affine",
        "lut_size": 65,
        "conditions": "Sampled from user-provided digital.png and blacklight.png. Unknown original RGB codes, capture exposure, ink, substrate and UV conditions. Not a measured printer calibration.",
        "provenance": provenance,
        "patch_count": len(digital),
        "merged_digital_duplicates": len(digital) - len(groups),
        "pairs": [{"digital": list(rgb), "blacklight": np.mean(targets, axis=0).tolist()} for rgb, targets in groups.items()],
    }
    return validate_profile(profile)


def draw_chart(patches, title, description):
    """Draw a crisp chart from sampled colors, keeping the two 10-row blocks."""
    cw, ch, left, top = 26, 23, 44, 86
    image = Image.new("RGB", (left + COLS * cw + 22, 656), (9, 10, 16))
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default(size=14)
    small = ImageFont.load_default(size=12)
    title_font = ImageFont.load_default(size=21)
    draw.text((left, 16), title, fill=(240, 242, 250), font=title_font)
    draw.text((left, 46), description, fill=(177, 184, 201), font=small)
    for col in range(COLS):
        draw.text((left + col * cw + cw / 2, top - 20), str(col + 1), anchor="mt", fill=(220, 224, 235), font=small)
    for row in range(ROWS):
        y = top + row * ch + (12 if row >= 10 else 0)
        draw.text((left - 16, y + ch / 2), chr(65 + row), anchor="mm", fill=(220, 224, 235), font=font)
    gray_y = top + ROWS * ch + 28
    draw.text((left - 16, gray_y + ch / 2), "X", anchor="mm", fill=(220, 224, 235), font=font)
    for patch in patches:
        row, col = patch["row"], patch["column"]
        x = left + col * cw
        y = gray_y if row == 20 else top + row * ch + (12 if row >= 10 else 0)
        draw.rectangle((x + 1, y + 1, x + cw - 3, y + ch - 3), fill=tuple(patch["rgb"]))
    draw.text((left, 614), "37 columns / rows A-T / 33 grayscale patches in row X", fill=(177, 184, 201), font=small)
    draw.text((left, 633), "8-bit sRGB PNG. Recreated from crop samples; no original color codes inferred.", fill=(177, 184, 201), font=small)
    return image


def predict_patches(patches, profile, direction):
    # Map only swatches: chart borders and labels are presentation, not ink samples.
    pixels = np.array([p["rgb"] for p in patches], dtype=np.uint8).reshape(1, -1, 3)
    mapped = np.asarray(convert(Image.fromarray(pixels), profile, direction)).reshape(-1, 3)
    return [dict(patch, rgb=rgb.tolist()) for patch, rgb in zip(patches, mapped, strict=True)]


def recreate(digital_path, blacklight_path, directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    private_reference = ROOT / "local-reference"
    private_reference.mkdir(parents=True, exist_ok=True)
    digital_image, blacklight_image = load_rgb(digital_path), load_rgb(blacklight_path)
    # These default coordinates belong to the supplied crops. Other shapes need
    # updated LAYOUTS rather than silently sampling the wrong locations.
    if digital_image.size != tuple(LAYOUTS["digital"]["size"]) or blacklight_image.size != tuple(LAYOUTS["blacklight"]["size"]):
        raise ValueError("Expected the supplied crops: digital 2082x912; blacklight 2070x896.")
    digital, d_overlay = extract(digital_image, LAYOUTS["digital"])
    blacklight, b_overlay = extract(blacklight_image, LAYOUTS["blacklight"])
    provenance = {"digital_sha256": hashlib.sha256(Path(digital_path).read_bytes()).hexdigest(),
                  "blacklight_sha256": hashlib.sha256(Path(blacklight_path).read_bytes()).hexdigest(),
                  "layout": LAYOUTS,
                  "sampling": "Channel-wise median of small interior windows; RGB rounded to 8-bit. Green rectangles in sampling overlays mark exactly those windows."}
    profile = build_profile(digital, blacklight, provenance)
    (directory / "reference-profile.json").write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")
    (directory / "samples.json").write_text(json.dumps({"provenance": provenance, "digital": digital, "blacklight": blacklight}, indent=2), encoding="utf-8")
    with (directory / "patch-pairs.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream)
        writer.writerow(["patch", "digital_r", "digital_g", "digital_b", "blacklight_r", "blacklight_g", "blacklight_b"])
        for source, target in zip(digital, blacklight, strict=True):
            writer.writerow([source["id"], *source["rgb"], *target["rgb"]])
    for name, image in (("digital-reference.png", digital_image), ("blacklight-reference.png", blacklight_image),
                        ("sampling-overlay-digital.png", d_overlay), ("sampling-overlay-blacklight.png", b_overlay)):
        save_png(image, private_reference / name)
    digital_chart = draw_chart(digital, "DIGITAL / recreated reference", "Interior RGB samples from your digital crop")
    blacklight_chart = draw_chart(blacklight, "BLACKLIGHT / recreated reference", "Interior RGB samples from your blacklight crop")
    save_png(digital_chart, directory / "digital-recreated.png")
    save_png(blacklight_chart, directory / "blacklight-recreated.png")
    pair = Image.new("RGB", (digital_chart.width * 2 + 12, digital_chart.height), (9, 10, 16))
    pair.paste(digital_chart, (0, 0))
    pair.paste(blacklight_chart, (digital_chart.width + 12, 0))
    save_png(pair, directory / "recreated-pair.png")
    # Atlas contains only the colors, so it can be opened and converted normally
    # in the desktop UI without mapping decorative chart labels and gutters.
    atlas = np.array([p["rgb"] for p in digital[:ROWS * COLS]], dtype=np.uint8).reshape(ROWS, COLS, 3)
    save_png(Image.fromarray(atlas).resize((COLS * 26, ROWS * 23), Image.Resampling.NEAREST), directory / "digital-atlas.png")
    print("Crops, sample windows, 773 correspondences and recreated charts saved.", flush=True)
    print("Building forward and inverse RGB estimates...", flush=True)
    forward = predict_patches(digital, profile, "to-blacklight")
    reverse = predict_patches(blacklight, profile, "to-digital")
    forward_chart = draw_chart(forward, "BLACKLIGHT / algorithm prediction", "Digital sample colors converted using the learned RGB mapping")
    reverse_chart = draw_chart(reverse, "DIGITAL / algorithm estimate", "Reverse RGB mapping is ambiguous; this is not exact recovery")
    save_png(forward_chart, directory / "blacklight-predicted.png")
    save_png(reverse_chart, directory / "digital-predicted.png")
    comparison = Image.new("RGB", (blacklight_chart.width * 2 + 12, blacklight_chart.height), (9, 10, 16))
    comparison.paste(blacklight_chart, (0, 0))
    comparison.paste(forward_chart, (blacklight_chart.width + 12, 0))
    save_png(comparison, directory / "comparison.png")
    print(f"Saved {directory.resolve()}\nProfile: {len(profile['pairs'])} unique RGB pairs; screenshot approximation only.", flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--digital", type=Path, default=ROOT / "local-reference" / "digital-reference.png")
    parser.add_argument("--blacklight", type=Path, default=ROOT / "local-reference" / "blacklight-reference.png")
    parser.add_argument("--output", type=Path, default=ROOT / "charts")
    args = parser.parse_args()
    try:
        recreate(args.digital, args.blacklight, args.output)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
