"""RGB-only blacklight proofing, calibration, and lossless project storage."""
from __future__ import annotations

import argparse
import io
import json
import warnings
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageOps, PngImagePlugin
from lut_cache import cached_lut
from progress_utils import report, stage

SRGB = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB"))
MAX_PIXELS = 25_000_000
Image.MAX_IMAGE_PIXELS = MAX_PIXELS


def load_rgb(path, matte=(0, 0, 0)):
    """Normalize orientation and color space; flatten transparency onto a matte."""
    with warnings.catch_warnings():
        warnings.simplefilter("error", Image.DecompressionBombWarning)
        with Image.open(path) as source:
            if source.width * source.height > MAX_PIXELS:
                raise ValueError("Image exceeds the 25 megapixel limit.")
            if getattr(source, "n_frames", 1) != 1:
                raise ValueError("Use a single-frame image; animated and multipage inputs are unsupported.")
            source.load()
            im = ImageOps.exif_transpose(source)
            alpha = im.convert("RGBA").getchannel("A") if "A" in im.getbands() or "transparency" in im.info else None
            icc = im.info.get("icc_profile")
            if icc:
                profile = ImageCms.ImageCmsProfile(io.BytesIO(icc))
                # Preserve CMYK/LAB for the profile transform; expand palette/alpha otherwise.
                working = im if im.mode in ("RGB", "CMYK", "LAB", "L") else im.convert("RGB")
                im = ImageCms.profileToProfile(working, profile, SRGB, outputMode="RGB")
            else:
                if im.mode in ("CMYK", "LAB"):
                    raise ValueError("CMYK/LAB input needs an embedded ICC profile. Export it as sRGB first.")
                if im.mode.startswith("I") or im.mode == "F":
                    raise ValueError("High-bit-depth input needs explicit tone mapping to 8-bit sRGB first.")
                im = im.convert("RGB")
            if alpha is not None:
                background = Image.new("RGB", im.size, matte)
                background.paste(im, mask=alpha)
                im = background
            return im.copy()


def png_bytes(image, metadata=None):
    if image.mode != "RGB":
        raise ValueError("Output must be RGB, with exactly three 8-bit channels.")
    info = PngImagePlugin.PngInfo()
    for key, value in (metadata or {}).items():
        info.add_text(key, str(value))
    stream = io.BytesIO()
    image.save(stream, format="PNG", icc_profile=SRGB.tobytes(), pnginfo=info, compress_level=1)
    return stream.getvalue()


def save_png(image, path, metadata=None):
    path = Path(path)
    if path.suffix.lower() != ".png":
        raise ValueError("Output filename must end in .png (lossless RGB PNG).")
    path.write_bytes(png_bytes(image, metadata))


def demo_profile():
    """Illustrative mapping only, deliberately not a printer calibration."""
    values = np.linspace(0, 255, 5)
    digital = np.array(np.meshgrid(values, values, values, indexing="ij")).reshape(3, -1).T
    matrix = np.array([[0.92, 0.02, 0.04], [0.03, 0.92, 0.03], [0.10, 0.08, 0.78]])
    blacklight = np.clip(digital @ matrix.T + [5, 3, 24], 0, 255)
    return {"version": 1, "name": "Illustrative demo — not calibrated", "demo": True,
            "conditions": "Synthetic RGB mapping; no physical ink or UV prediction.",
            "pairs": [{"digital": d.tolist(), "blacklight": b.tolist()} for d, b in zip(digital, blacklight)]}


def validate_profile(profile):
    if not isinstance(profile, dict):
        raise ValueError("Profile must be a JSON object.")
    if profile.get("version") != 1 or not isinstance(profile.get("name"), str):
        raise ValueError("Profile requires version: 1 and a name.")
    if profile.get("interpolation", "affine-residual") not in ("affine-residual", "local-affine"):
        raise ValueError("Unsupported interpolation method.")
    if profile.get("lut_size", 33) not in (17, 33, 65):
        raise ValueError("Lookup table size must be 17, 33 or 65.")
    pairs = profile.get("pairs", [])
    if not 8 <= len(pairs) <= 4096:
        raise ValueError("Supply 8–4096 measured digital/blacklight RGB pairs.")
    for key in ("digital", "blacklight"):
        try:
            points = np.array([pair[key] for pair in pairs], dtype=float)
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"Invalid {key} RGB measurements.") from exc
        if points.shape != (len(pairs), 3) or not np.isfinite(points).all() or (points < 0).any() or (points > 255).any():
            raise ValueError(f"{key} must contain finite RGB triples in the range 0–255.")
        if key == "digital" and len(np.unique(points, axis=0)) != len(points):
            raise ValueError("Digital calibration colors must be unique.")
    return profile


def load_profile(path=None):
    return validate_profile(json.loads(Path(path).read_text(encoding="utf-8")) if path else demo_profile())


def make_lut(profile, direction, size=None, progress=None):
    """Build an RGB lookup cube using the profile's interpolation method."""
    validate_profile(profile)
    size = profile.get("lut_size", 33) if size is None else size
    if size not in (17, 33, 65):
        raise ValueError("Lookup table size must be 17, 33 or 65.")
    if direction not in ("to-blacklight", "to-digital"):
        raise ValueError("Unknown conversion direction.")
    report(progress, 0, "Loading color table")
    table = cached_lut("color-v1", profile, direction, size, lambda: _build_lut(profile, direction, size, progress))
    report(progress, 1, "Color table ready")
    return table


def _build_lut(profile, direction, size, progress=None):
    source, target = ("digital", "blacklight") if direction == "to-blacklight" else ("blacklight", "digital")
    x = np.array([p[source] for p in profile["pairs"]], dtype=np.float64)
    y = np.array([p[target] for p in profile["pairs"]], dtype=np.float64)
    # Ambiguous inverse samples map to their mean; no false claim of exact reversal.
    unique, inverse = np.unique(x, axis=0, return_inverse=True)
    sums = np.zeros((len(unique), 3))
    np.add.at(sums, inverse, y)
    y = sums / np.bincount(inverse)[:, None]
    x = unique
    affine, *_ = np.linalg.lstsq(np.column_stack((x, np.ones(len(x)))), y, rcond=None)
    residual = y - np.column_stack((x, np.ones(len(x)))) @ affine
    axis = np.linspace(0, 255, size)
    grid = np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1).reshape(-1, 3)
    output = np.empty_like(grid)
    local = profile.get("interpolation") == "local-affine"
    k = min(16 if local else 8, len(x))
    for start in range(0, len(grid), 128):
        report(progress, start / len(grid), "Building color table")
        batch = grid[start:start + 128]
        distances = np.sum((batch[:, None, :] - x[None, :, :]) ** 2, axis=2)
        indices = np.argpartition(distances, k - 1, axis=1)[:, :k]
        nearest = np.take_along_axis(distances, indices, axis=1)
        if local:
            # Fit a small linear model around each query. Ridge stabilizes nearly
            # collinear colors; the neighboring target range bounds extrapolation.
            offsets = (x[indices] - batch[:, None, :]) / 255
            design = np.concatenate((np.ones((*offsets.shape[:2], 1)), offsets), axis=2)
            weights = 1 / (nearest + 4) ** 2
            weights /= weights.sum(axis=1, keepdims=True)
            normal = np.einsum("bki,bk,bkj->bij", design, weights, design)
            normal += np.diag([0, 0.0001, 0.0001, 0.0001])
            rhs = np.einsum("bki,bk,bkj->bij", design, weights, y[indices])
            estimate = np.linalg.solve(normal, rhs)[:, 0, :]
            estimate = np.clip(estimate, y[indices].min(axis=1), y[indices].max(axis=1))
            exact = nearest.min(axis=1) < 1e-12
            if exact.any():
                estimate[exact] = y[indices[exact, nearest[exact].argmin(axis=1)]]
            output[start:start + len(batch)] = estimate
            continue
        weights = 1 / np.maximum(nearest, 1e-12) ** 2
        baseline = np.column_stack((batch, np.ones(len(batch)))) @ affine
        output[start:start + len(batch)] = baseline + np.sum(residual[indices] * weights[:, :, None], axis=1) / weights.sum(axis=1)[:, None]
    return np.clip(output, 0, 255).reshape(size, size, size, 3)


def convert(image, profile, direction, lut=None, backend="auto", progress=None):
    if image.mode != "RGB":
        raise ValueError("Conversion requires normalized RGB input.")
    if lut is None:
        lut = make_lut(profile, direction, progress=stage(progress, 0, .15, ""))
    if backend != "reference":
        from image_backend import apply_lut
        return apply_lut(image, lut, backend, progress=stage(progress, .15, 1, ""))
    return _convert_reference(image, lut, progress=stage(progress, .15, 1, "Reference mapping"))


def _convert_reference(image, lut, progress=None):
    """Original float64 NumPy implementation retained for numerical comparison."""
    size = lut.shape[0]
    result = Image.new("RGB", image.size)
    # Strip processing bounds temporary allocations for large images.
    strip_height = max(1, 131072 // image.width)
    for top in range(0, image.height, strip_height):
        bottom = min(top + strip_height, image.height)
        pixels = np.asarray(image.crop((0, top, image.width, bottom)), dtype=np.float64) * ((size - 1) / 255)
        low = np.minimum(np.floor(pixels).astype(int), size - 2)
        fraction = pixels - low
        mapped = np.zeros_like(pixels)
        for r in (0, 1):
            for g in (0, 1):
                for b in (0, 1):
                    w = np.prod(np.where(np.array([r, g, b]), fraction, 1 - fraction), axis=-1)
                    mapped += lut[low[..., 0] + r, low[..., 1] + g, low[..., 2] + b] * w[..., None]
        result.paste(Image.fromarray(np.clip(np.rint(mapped), 0, 255).astype(np.uint8)), (0, top))
        report(progress, bottom / image.height, "Reference CPU mapping")
    return result


def save_project(path, original, converted, profile, direction, analysis=None, progress=None):
    if Path(path).suffix.lower() != ".uvproject":
        raise ValueError("Project filename must end in .uvproject.")
    manifest = {"version": 1, "direction": direction, "profile": profile,
                "original": "original.png", "result": "converted.png",
                "note": "Original is the normalized 8-bit sRGB image, after alpha compositing."}
    if analysis is not None:
        manifest["analysis"] = "analysis/report.json"
    total = 8 if analysis is not None else 3
    report(progress, 0, "Saving project")
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_STORED) as archive:
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False))
        report(progress, 1 / total, "Saving project original")
        archive.writestr("original.png", png_bytes(original))
        report(progress, 2 / total, "Saving project conversion")
        archive.writestr("converted.png", png_bytes(converted))
        report(progress, 3 / total, "Project images saved")
        if analysis is not None:
            for index, key in enumerate(("simulated", "heatmap", "overlay", "coverage"), start=4):
                archive.writestr(f"analysis/{key}.png", png_bytes(analysis[key]))
                report(progress, index / total, f"Saving project · {key}")
            archive.writestr("analysis/report.json", json.dumps(analysis["report"], indent=2, ensure_ascii=False))
    report(progress, 1, "Project saved")


def restore_project(path, output):
    with zipfile.ZipFile(path) as archive:
        info = archive.getinfo("original.png")
        if info.file_size > MAX_PIXELS * 4 + 1_000_000:
            raise ValueError("Project image exceeds the supported size.")
        raw = archive.read(info)
    image = load_rgb(io.BytesIO(raw))
    save_png(image, output, {"restored": "Exact normalized original RGB pixels"})


def calibration_chart(path):
    """5³ known digital colors, with matching RGB values in a JSON template."""
    from PIL import ImageDraw
    values = [0, 64, 128, 192, 255]
    colors = [(r, g, b) for r in values for g in values for b in values]
    image = Image.new("RGB", (1000, 500), "black")
    draw = ImageDraw.Draw(image)
    for i, color in enumerate(colors):
        x, y = i % 25 * 40, i // 25 * 100
        draw.rectangle((x + 2, y + 2, x + 37, y + 73), fill=color)
        draw.text((x + 3, y + 80), str(i + 1), fill="white")
    save_png(image, path)
    template = {"version": 1, "name": "Measured printer / ink / substrate / lamp profile",
                "conditions": "Record UV wavelength, exposure, camera profile, ink and substrate. Replace every null with a measured sRGB triple.",
                "pairs": [{"digital": c, "blacklight": None} for c in colors]}
    Path(path).with_suffix(".json").write_text(json.dumps(template, indent=2), encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command")
    cmd = sub.add_parser("convert", help="Convert a normalized image using a measured or demo profile")
    cmd.add_argument("input", type=Path)
    cmd.add_argument("output", type=Path)
    cmd.add_argument("--direction", choices=("to-blacklight", "to-digital"), default="to-blacklight")
    cmd.add_argument("--profile", type=Path)
    cmd.add_argument("--project", type=Path, help="Also preserve the normalized original for exact restore")
    cmd.add_argument("--matte", choices=("black", "white"), default="black")
    cmd.add_argument("--no-analysis", action="store_true", help="Skip round-trip loss and coverage maps")
    cmd.add_argument("--backend", choices=("auto", "gpu", "cpu", "reference"), default="auto", help="Pixel mapping backend; auto uses OpenCL for large images with CPU fallback")
    cmd = sub.add_parser("restore", help="Restore the exact normalized original from a project")
    cmd.add_argument("project", type=Path)
    cmd.add_argument("output", type=Path)
    cmd = sub.add_parser("chart", help="Create a known RGB calibration chart and measurement template")
    cmd.add_argument("output", type=Path)
    sub.add_parser("gui", help="Launch the desktop application (default)")
    args = parser.parse_args()
    try:
        if args.command == "convert":
            if args.input.resolve() == args.output.resolve():
                raise ValueError("Choose a different output path to preserve your source.")
            profile = load_profile(args.profile)
            image = load_rgb(args.input, (255, 255, 255) if args.matte == "white" else (0, 0, 0))
            analysis = None
            if args.no_analysis:
                result = convert(image, profile, args.direction, backend=args.backend)
                save_png(result, args.output, {"profile": profile["name"], "direction": args.direction})
            else:
                from loss_analysis import convert_with_analysis, export_with_analysis
                result, analysis = convert_with_analysis(image, profile, args.direction, backend=args.backend)
                directory = export_with_analysis(result, args.output, analysis)
                print(f"Heatmap, overlay, simulation, coverage and report: {directory}")
            if args.project:
                save_project(args.project, image, result, profile, args.direction, analysis)
            print(f"Saved {args.output} — 8-bit sRGB PNG, {image.width} × {image.height}")
            print("Illustrative demo only." if profile.get("demo") else "Approximate mapping from screenshot colors, not a measured printer calibration." if profile.get("approximate") else "Profile-based estimate; validate against held-out measured patches.")
            if args.direction == "to-digital":
                print("Reverse mapping is an estimate; use restore for exact original RGB recovery.")
        elif args.command == "restore":
            restore_project(args.project, args.output)
            print(f"Restored {args.output}")
        elif args.command == "chart":
            calibration_chart(args.output)
            print(f"Saved chart and measurement template: {args.output}")
        else:
            from uv_gui import launch
            launch()
    except (ValueError, OSError, KeyError, RuntimeError, zipfile.BadZipFile, ImageCms.PyCMSError) as exc:
        parser.exit(1, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
