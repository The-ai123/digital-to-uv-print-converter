"""Model-based round-trip RGB mismatch and calibration coverage maps."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
from PIL import Image

from uvconvert import convert, save_png, validate_profile
from lut_cache import cached_lut
from progress_utils import report, stage

# Fixed across images, never normalized to an image's largest error.
HEAT_SCALE_PERCENT = 10.0
PALETTE_POSITIONS = np.array([0, .2, .5, 1])
PALETTE_RGB = np.array([[14, 30, 80], [38, 150, 210], [255, 215, 60], [240, 40, 55]])


def colorize_percent(values):
    positions = np.clip(values / HEAT_SCALE_PERCENT, 0, 1)
    colors = np.stack([np.interp(positions, PALETTE_POSITIONS, PALETTE_RGB[:, c]) for c in range(3)], axis=-1)
    return np.rint(colors).astype(np.uint8)


def summarize(values):
    return {"mean_percent": float(np.mean(values)), "max_percent": float(np.max(values)),
            "pixels_at_or_above_2_percent": int(np.count_nonzero(values >= 2)),
            "pixels_at_or_above_5_percent": int(np.count_nonzero(values >= 5)),
            "pixels_at_or_above_10_percent": int(np.count_nonzero(values >= 10)),
            "pixel_count": int(values.size)}


def mismatch_maps(target, simulated, progress=None):
    if target.mode != "RGB" or simulated.mode != "RGB" or target.size != simulated.size:
        raise ValueError("Loss analysis requires two RGB images of the same size.")
    heatmap = Image.new("RGB", target.size)
    overlay = Image.new("RGB", target.size)
    errors = np.empty((target.height, target.width), dtype=np.float32)
    strip = max(1, 131072 // target.width)
    for top in range(0, target.height, strip):
        bottom = min(top + strip, target.height)
        box = (0, top, target.width, bottom)
        desired = np.asarray(target.crop(box), dtype=np.float32)
        predicted = np.asarray(simulated.crop(box), dtype=np.float32)
        difference = np.sqrt(np.mean((desired - predicted) ** 2, axis=-1)) * (100 / 255)
        errors[top:bottom] = difference
        colors = colorize_percent(difference)
        # Exact/near-exact pixels retain the target; larger errors get stronger tint.
        opacity = (.7 * np.clip(difference / HEAT_SCALE_PERCENT, 0, 1))[..., None]
        tinted = np.rint(desired * (1 - opacity) + colors * opacity).astype(np.uint8)
        heatmap.paste(Image.fromarray(colors), (0, top))
        overlay.paste(Image.fromarray(tinted), (0, top))
        report(progress, bottom / target.height, "Calculating loss heatmap and overlay")
    return heatmap, overlay, summarize(errors)


def coverage_lut(profile, direction, size=33, progress=None):
    """Nearest calibration distance (RGB RMS channel units), replicated in RGB.

    This is a sampling-distance diagnostic, never an uncertainty probability or
    evidence that a target color cannot be produced. A coarse cube bounds work
    for large images. Trilinear resampling makes the diagnostic approximate.
    """
    validate_profile(profile)
    if direction not in ("to-blacklight", "to-digital") or size not in (17, 33, 65):
        raise ValueError("Invalid coverage direction or table size.")
    report(progress, 0, "Loading coverage table")
    table = cached_lut("coverage-v1", profile, direction, size, lambda: _build_coverage_lut(profile, direction, size, progress))
    report(progress, 1, "Coverage table ready")
    return table


def _build_coverage_lut(profile, direction, size, progress=None):
    source = "blacklight" if direction == "to-digital" else "digital"
    points = np.array([p[source] for p in profile["pairs"]], dtype=np.float64)
    points = np.unique(points, axis=0)
    axis = np.linspace(0, 255, size)
    grid = np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1).reshape(-1, 3)
    distances = np.empty(len(grid))
    for start in range(0, len(grid), 128):
        batch = grid[start:start + 128]
        squares = np.sum((batch[:, None, :] - points[None, :, :]) ** 2, axis=2)
        distances[start:start + len(batch)] = np.sqrt(squares.min(axis=1) / 3)
        report(progress, min(start + len(batch), len(grid)) / len(grid), "Building coverage table")
    return np.repeat(distances[:, None], 3, axis=1).reshape(size, size, size, 3)


def analyze_conversion(original, converted, profile, direction, backend="auto", progress=None):
    validate_profile(profile)
    if direction not in ("to-digital", "to-blacklight"):
        raise ValueError("Unknown conversion direction.")
    if original.mode != "RGB" or converted.mode != "RGB" or original.size != converted.size:
        raise ValueError("Analysis requires matching normalized RGB input and conversion.")
    inverse = "to-blacklight" if direction == "to-digital" else "to-digital"
    from image_backend import last_backend_info
    label = "UV prediction" if inverse == "to-blacklight" else "Digital recovery"
    simulated = convert(converted, profile, inverse, backend=backend, progress=stage(progress, 0, .4, label))
    round_trip_backend = last_backend_info() if backend != "reference" else {"backend": "reference CPU", "device": "CPU"}
    heatmap, overlay, stats = mismatch_maps(original, simulated, progress=stage(progress, .4, .65, "Loss heatmap"))
    table = coverage_lut(profile, direction, progress=stage(progress, .65, .75, ""))
    distance_image = convert(original, profile, direction, lut=table, backend=backend, progress=stage(progress, .75, .9, "Coverage"))
    coverage_backend = last_backend_info() if backend != "reference" else {"backend": "reference CPU", "device": "CPU"}
    # The replicated channels represent a scalar, rounded to one RGB code unit.
    distances = np.asarray(distance_image)[:, :, 0].astype(np.float32) * (100 / 255)
    coverage = Image.new("RGB", original.size)
    strip = max(1, 131072 // original.width)
    for top in range(0, original.height, strip):
        bottom = min(top + strip, original.height)
        coverage.paste(Image.fromarray(colorize_percent(distances[top:bottom])), (0, top))
        report_progress = .9 + .09 * bottom / original.height
        report(progress, report_progress, "Rendering calibration coverage")
    space = "UV target" if direction == "to-digital" else "digital source"
    analysis_report = {
        "version": 1, "direction": direction, "profile": profile["name"],
        "width": original.width, "height": original.height,
        "comparison_space": space,
        "round_trip": "desired UV -> estimated digital -> predicted UV" if direction == "to-digital" else "digital -> predicted UV -> recovered digital",
        "loss_metric": "100 * sqrt(mean((target_RGB - round_trip_RGB)^2)) / 255; encoded 8-bit sRGB channels",
        "loss": stats,
        "coverage_metric": "Approximate nearest source-calibration RGB distance, same RMS percentage scale; 33^3 cube with trilinear interpolation and 8-bit distance rounding",
        "coverage": summarize(distances),
        "legend": {"blue_percent": 0, "cyan_percent": 2, "yellow_percent": 5, "red_percent_and_above": HEAT_SCALE_PERCENT},
        "processing": {"round_trip": round_trip_backend, "coverage": coverage_backend,
                       "precision": "GPU float32 / native CPU fixed-point interpolation can differ from float64 reference by rounding. PNG stores the resulting RGB values losslessly."},
        "thresholds": "Fixed visual editing heuristics, not validated perceptual tolerances or probabilities.",
        "limitations": [
            "Round-trip mismatch diagnoses this fitted model; it does not prove that a color is physically impossible or that a better inverse cannot match it.",
            "A blue loss pixel can still be poorly calibrated; check the separate coverage map.",
            "Coverage measures distance from sampled colors, not a calibrated error bound or confidence score.",
            "The screenshot profile estimates screenshot appearance; actual printed UV appearance requires physical validation.",
            "This metric is RGB error, not a perceptual Delta E measurement. PNG encoding remains lossless.",
        ],
    }
    report(progress, 1, "Analysis complete")
    return {"simulated": simulated, "heatmap": heatmap, "overlay": overlay, "coverage": coverage, "report": analysis_report}


def convert_with_analysis(image, profile, direction, backend="auto", progress=None):
    from image_backend import last_backend_info
    converted = convert(image, profile, direction, backend=backend, progress=stage(progress, 0, .38, "Artwork"))
    first_backend = last_backend_info() if backend != "reference" else {"backend": "reference CPU", "device": "CPU"}
    analysis = analyze_conversion(image, converted, profile, direction, backend=backend, progress=stage(progress, .38, 1, ""))
    analysis["report"]["processing"]["conversion"] = first_backend
    return converted, analysis


def save_analysis(directory, analysis, progress=None):
    """Save diagnostic RGB PNGs and a machine-readable legend/report."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    report = analysis["report"]
    metadata = {"profile": report["profile"], "comparison_space": report["comparison_space"],
                "legend": "Blue 0%, cyan 2%, yellow 5%, red 10%+ RGB RMS. Fixed scale; approximate model diagnostic."}
    name = "predicted-uv.png" if report["direction"] == "to-digital" else "recovered-digital.png"
    outputs = [("simulated", name, {"profile": report["profile"]}),
               ("heatmap", "loss-heatmap.png", dict(metadata, metric=report["loss_metric"])),
               ("overlay", "loss-overlay.png", dict(metadata, metric=report["loss_metric"])),
               ("coverage", "calibration-coverage.png", dict(metadata, metric=report["coverage_metric"]))]
    for index, (key, filename, info) in enumerate(outputs):
        report_progress = index / 5
        if progress is not None:
            progress(report_progress, f"Saving {filename}")
        save_png(analysis[key], directory / filename, info)
        if progress is not None:
            progress((index + 1) / 5, f"Saved {filename}")
    (directory / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    if progress is not None:
        progress(1, "Diagnostic files saved")


def export_with_analysis(image, output, analysis, progress=None):
    """Save artwork plus a fresh sibling folder; don't overwrite old diagnostics."""
    output = Path(output)
    if output.suffix.lower() != ".png":
        raise ValueError("Output filename must end in .png.")
    base = output.with_name(output.stem + "_analysis")
    directory = base
    sequence = 2
    while True:
        try:
            directory.mkdir()
            break
        except FileExistsError:
            directory = base.with_name(base.name + f"-{sequence}")
            sequence += 1
    save_analysis(directory, analysis, progress=stage(progress, 0, .85, "Saving diagnostics"))
    analysis_report = analysis["report"]
    report(progress, .85, "Saving artwork PNG")
    save_png(image, output, {"profile": analysis_report["profile"], "direction": analysis_report["direction"], "analysis_folder": directory.name})
    report(progress, 1, "Export complete")
    return directory
