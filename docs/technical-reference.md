# Technical reference

[Project overview and standalone download](../README.md). Paths and commands below are relative to the repository root.

## Diagnostic interpretation

For Blacklight → digital, the analysis follows:

```text
desired UV image → estimated digital artwork → predicted UV image
       └──────────── pixel comparison ──────────────┘
```

The loss metric is:

```text
RGB mismatch (%) = 100 × sqrt(mean((input RGB − round-trip RGB)²)) / 255
```

It uses encoded 8-bit sRGB channels. It is not perceptual Delta E, a probability, or a percentage of ink lost.

| Color | Fixed scale |
| --- | --- |
| Blue | 0% |
| Cyan | 2% |
| Yellow | 5% |
| Red | 10% or more |

The scale remains fixed between images. These thresholds are editing heuristics, not certified tolerances. Large mismatch may reflect a limited modeled gamut, clipping, ambiguous reverse mapping, or imperfect interpolation. It does not prove that no digital RGB input could reproduce the target.

The coverage view uses the same color scale for approximate nearest-sample RGB distance. Yellow/red indicates colors farther from calibration samples. Coverage is calculated with a 33³ lookup table and 8-bit distance rounding; it is not an error bound. Low round-trip mismatch can occur even when calibration coverage is poor.

For Digital → blacklight, loss is measured against the original digital input after a digital → UV → digital round trip.

## RGB and storage

- All conversion input is normalized to three-channel, **8-bit sRGB**. Embedded ICC profiles are transformed using Pillow/LittleCMS. Untagged RGB and grayscale inputs are assumed to be sRGB.
- EXIF orientation is applied. Transparency is composited onto the selected black or white matte.
- CMYK/LAB input without an embedded ICC profile and untagged high-bit-depth input are rejected rather than silently tone-mapped.
- Single-frame PNG, JPEG, TIFF, BMP, and WebP are accepted when Pillow can decode them. Multipage/animated input is rejected. The input limit is 25 megapixels.
- Exports are lossless **PNG truecolor RGB, color type 2, 8 bits/channel**, with an embedded sRGB ICC profile. Exports are not palette, grayscale, CMYK, or RGBA.
- PNG preserves the converted pixels exactly. Color mapping, clipping, compositing, and 8-bit normalization are not mathematically reversible.
- Project restoration recovers normalized original RGB pixels, not the source file's original bytes, alpha, EXIF, or discarded high-bit-depth data. Retain source files separately.

References: [Pillow ImageCms](https://pillow.readthedocs.io/en/stable/reference/ImageCms.html) and [Pillow image formats](https://pillow.readthedocs.io/en/stable/handbook/image-file-formats.html).

## Reference profile

The supplied digital and blacklight chart crops contain 773 corresponding patches: 37 columns × 20 rows, plus 33 grayscale patches. The wider separator between rows J and K is handled separately. The middle daylight-print chart from the original screenshot is not used.

Small interior sample windows avoid grid separators. Channel-wise medians reduce local noise. Fourteen repeated digital RGB readings are merged, leaving 759 unique RGB pairs. All correspondences and sampling coordinates are retained in the audit files.

The screenshot model fits a local linear map using the 16 nearest RGB samples with distance weighting and a stabilizing penalty. Predictions are bounded by neighboring target channel ranges. The fitted map is sampled into a 65³ RGB lookup table and applied using trilinear interpolation. Forward and inverse models are fitted separately; duplicate inverse measurements are averaged.

Profiles without an `interpolation` setting use the original affine model plus inverse-distance weighted residuals from eight nearby samples and a 33³ table.

### Reference files

| Path | Contents |
| --- | --- |
| `charts/reference-profile.json` | Reusable screenshot-derived RGB profile |
| `local-reference/digital-reference.png`, `local-reference/blacklight-reference.png` | Original crops; local only and excluded from Git |
| `charts/recreated-pair.png` | Reconstructed digital and blacklight charts |
| `charts/digital-recreated.png`, `charts/blacklight-recreated.png` | Individual sampled charts |
| `charts/blacklight-predicted.png`, `charts/digital-predicted.png` | Forward and inverse model outputs |
| `charts/comparison.png` | Sampled blacklight colors beside the model prediction |
| `charts/digital-atlas.png` | Main digital swatches without chart labels or gutters |
| `local-reference/sampling-overlay-*.png` | Photo-based sampling overlays; local only and excluded from Git |
| `charts/samples.json`, `charts/patch-pairs.csv` | Sample colors, coordinates, variation, and provenance |

The comparison uses the same patches used to fit the model; it shows reconstruction, not independent validation. The chart profile estimates screenshot colors, not original artwork codes or absolute fluorescence. Capture exposure, color management, ink, substrate, UV lamp, and viewing conditions affect physical results. Validate production artwork with a physical proof under the intended setup.

Rebuild reference assets without desktop automation:

```powershell
.\.venv\Scripts\python.exe -m tools.chart_reference --digital input/digital.png --blacklight input/blacklight.png
```

The default layout is specific to the supplied crops: digital 2082 × 912 and blacklight 2070 × 896. Other crops require updating `LAYOUTS` in `chart_reference.py`. Running the script without arguments uses the saved local crops in `local-reference/`, which are not distributed. Original-photo copies and overlays are always written to that ignored folder; public chart outputs are redrawn. Generated files in `charts/` are replaced.

## Measured calibration

Generate a known RGB chart and JSON template:

```powershell
.\.venv\Scripts\python.exe uvconvert.py chart calibration.png
```

Print through the intended UV printing production workflow. Use the relevant ink, substrate, UV lamp, and viewing/capture conditions. Capture the illuminated chart consistently with color-managed sRGB output, avoiding clipped channels and automatic adjustments.

Sample the center of each numbered patch. Replace each `blacklight: null` in `calibration.json` with a measured `[R, G, B]` triple, set a descriptive profile name, and record the physical and capture conditions. Validate the profile against separate printed patches not used for fitting.

A complete profile requires 8–4096 pairs, unique digital RGB inputs, and finite channel values from 0 to 255. Pair format:

```json
{"digital": [0, 0, 0], "blacklight": [8, 4, 29]}
```

The containing object requires `"version": 1`, a `"name"`, and a `"pairs"` array. Optional settings include `"interpolation": "local-affine"` and `"lut_size": 65`; supported table sizes are 17, 33, and 65.

## Command line

The CLI uses the synthetic demo unless `--profile` is specified.

```powershell
# Prepare digital artwork from a desired UV appearance, including diagnostics.
.\.venv\Scripts\python.exe uvconvert.py convert desired-uv.png digital-artwork.png --direction to-digital --profile charts/reference-profile.json

# Preview digital artwork under the selected UV profile and save a project.
.\.venv\Scripts\python.exe uvconvert.py convert artwork.png uv-preview.png --profile calibration.json --project artwork.uvproject

# Skip diagnostics when only a converted PNG is needed.
.\.venv\Scripts\python.exe uvconvert.py convert artwork.png uv-preview.png --profile calibration.json --no-analysis

# Restore the normalized original from a project.
.\.venv\Scripts\python.exe uvconvert.py restore artwork.uvproject restored.png
```

## Performance

### GPU and CPU processing

**Compute: Auto** uses an OpenCL GPU for images of 262,144 pixels or larger. Small images use the native CPU path to avoid GPU setup overhead. Auto falls back to CPU if GPU initialization or mapping fails. **GPU (OpenCL)** explicitly requires the GPU and reports an error if unavailable. **CPU** always uses Pillow's native compiled LUT filter. No graphics driver is installed or changed by the application.

GPU acceleration covers the three full-image LUT passes: artwork conversion, round-trip prediction, and coverage lookup. Heatmap statistics/rendering, color-profile normalization, new calibration-table fitting, and PNG encoding remain on the CPU. The final status and exported report identify the actual processing backend and device.

The OpenCL path uses float32 interpolation. The native CPU path uses Pillow's LUT implementation. They can round differently from the original float64 NumPy calculation; this is separate from PNG compression, which remains lossless. The CLI retains `--backend reference` for numerical comparison, and accepts `--backend auto`, `gpu`, or `cpu`.

For source installations, GPU support is optional:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-gpu.txt
```

The Windows build includes these bindings. It does not require a separate CUDA SDK and supports compatible OpenCL GPU drivers rather than a single GPU vendor. See [PyOpenCL installation](https://documen.tician.de/pyopencl/misc.html) and [Pillow's native LUT filter](https://pillow.readthedocs.io/en/stable/reference/ImageFilter.html#PIL.ImageFilter.Color3DLUT).

A local 4000 × 3000 synthetic-image check on Intel Iris Xe measured a single mapping pass at 26.98 s for the original NumPy path, 1.32 s for native CPU, and 0.79 s for OpenCL GPU, including GPU initialization. GPU conversion plus diagnostics took 6.99 s, excluding export. Both accelerated paths differed from the reference by at most one RGB code value in this check. These measurements are not a performance or precision guarantee for other images, hardware, or profiles.

### Progress and export

Conversion reports progress through table loading/fitting, pixel conversion, round-trip prediction, heatmap calculation, coverage lookup, and coverage rendering. The percentage is phase-based completed work, not a remaining-time prediction. Export and project saving report progress as files finish; individual PNG encodes do not report byte-level progress. Image loading uses an activity indicator while decoding and color normalization run.

PNG compression uses level 1 for faster lossless export. Files can be larger than with the previous default compression; the stored RGB pixels are unchanged. Project archives store the already-compressed PNGs without an unnecessary second compression pass.

### Lookup tables

Color and coverage tables are cached in memory and on disk: `.uv-cache/` beside the source checkout, or `%LOCALAPPDATA%\DigitalToUVPrintConverter\cache` for the standalone build. They are reused across conversions and app restarts. Profile changes create separate cache entries automatically. The cache contains numeric tables, not input images. Cached tables retain the precision of freshly built tables.

New profiles take longer on first conversion. Pixel mapping, diagnostic rendering, and PNG export still scale with image size. Processing runs in a worker thread; GUI progress updates are delivered to the main thread through a queue.

## Development

### Build the Windows executable

Build on Windows from an environment with the source dependencies installed:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-build.txt
.\.venv\Scripts\python.exe -m tools.build_windows
```

The build script uses [PyInstaller](https://pyinstaller.org/en/stable/operating-mode.html) to produce `dist/Digital to UV Print Converter 0.2.0.exe`, with checksums and build metadata kept separately in `build/release/0.2.0/`. It packages only an explicit list of application assets; `Examples/` is not included. Only the redesigned chart atlas and sampled profile are bundled, not the original chart photographs. Build artifacts and caches are excluded from Git.

### Source modules

| Module | Responsibility |
| --- | --- |
| `uv_gui.py` | Desktop interface and background processing |
| `uvconvert.py` | RGB normalization, mapping, PNG/project storage, CLI |
| `loss_analysis.py` | Round-trip mismatch, coverage, diagnostic export |
| `image_backend.py` | OpenCL GPU mapping and native CPU fallback |
| `progress_utils.py` | Phase progress callbacks |
| `lut_cache.py` | Numeric table cache |
| `runtime_paths.py` | Resource and writable-data locations |
| `desktop.py` | Windowed executable entry point and startup error reporting |
| `tools/build_windows.py` | Reproducible Windows packaging command |
| `tools/chart_reference.py` | Reference extraction and chart reconstruction |

Run the provided automated checks manually:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

The current interface update has not been interactively tested. No desktop automation is used for verification.

## Licenses and assets

Source code retains the repository's [MIT License](../LICENSE). The example photos use the Pexels License; original chart photos are not distributed. See [asset notices](../ASSET_NOTICES.md).
