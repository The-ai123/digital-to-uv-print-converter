import io
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms

from uvconvert import (SRGB, calibration_chart, convert, demo_profile, load_profile,
                       load_rgb, png_bytes, restore_project, save_png, save_project,
                       validate_profile)


def affine_profile():
    # Known bijective mapping over a deliberately smaller UV gamut.
    return {"version": 1, "name": "Test affine calibration", "pairs": [
        {"digital": [r, g, b], "blacklight": [r * .8 + 10, g * .6 + 20, b * .4 + 30]}
        for r in (0, 255) for g in (0, 255) for b in (0, 255)]}


class ConverterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)

    def test_png_is_actual_three_channel_lossless_with_srgb(self):
        data = np.random.default_rng(1).integers(0, 256, (27, 31, 3), dtype=np.uint8)
        raw = png_bytes(Image.fromarray(data))
        self.assertEqual(raw[:8], b"\x89PNG\r\n\x1a\n")
        self.assertEqual(raw[24:26], bytes([8, 2]))  # 8-bit; truecolor RGB, not palette/RGBA.
        with Image.open(io.BytesIO(raw)) as saved:
            self.assertEqual(saved.mode, "RGB")
            self.assertIn("icc_profile", saved.info)
            np.testing.assert_array_equal(data, np.array(saved))

    def test_forward_mapping_on_unmeasured_colors(self):
        data = np.array([[[0, 255, 128], [173, 85, 210], [255, 0, 0]]], dtype=np.uint8)
        result = convert(Image.fromarray(data), affine_profile(), "to-blacklight")
        expected = np.rint(data * [.8, .6, .4] + [10, 20, 30]).astype(np.uint8)
        np.testing.assert_allclose(np.array(result), expected, atol=1)

    def test_reverse_mapping_inside_measured_gamut(self):
        result = convert(Image.new("RGB", (2, 3), (90, 80, 70)), affine_profile(), "to-digital")
        np.testing.assert_allclose(np.array(result), np.tile([100, 100, 100], (3, 2, 1)), atol=1)

    def test_project_recovers_original_after_noninvertible_mapping(self):
        original = Image.fromarray(np.random.default_rng(7).integers(0, 256, (15, 17, 3), dtype=np.uint8))
        converted = Image.new("RGB", original.size, "black")
        project, restored = self.directory / "test.uvproject", self.directory / "restored.png"
        save_project(project, original, converted, demo_profile(), "to-blacklight")
        restore_project(project, restored)
        np.testing.assert_array_equal(np.array(original), np.array(load_rgb(restored)))
        with zipfile.ZipFile(project) as archive:
            self.assertEqual(json.loads(archive.read("manifest.json"))["version"], 1)

    def test_alpha_flattening_and_palette_input(self):
        path = self.directory / "transparent.png"
        Image.new("RGBA", (1, 1), (255, 0, 0, 128)).save(path)
        self.assertEqual(load_rgb(path).getpixel((0, 0)), (128, 0, 0))
        self.assertEqual(load_rgb(path, (255, 255, 255)).getpixel((0, 0)), (255, 127, 127))
        Image.new("P", (2, 2)).save(path)
        self.assertEqual(load_rgb(path).mode, "RGB")

    def test_embedded_icc_and_exif_orientation(self):
        path = self.directory / "oriented.png"
        image = Image.new("RGB", (3, 7), (20, 30, 40))
        exif = image.getexif()
        exif[274] = 6
        image.save(path, icc_profile=SRGB.tobytes(), exif=exif)
        result = load_rgb(path)
        self.assertEqual(result.size, (7, 3))
        self.assertEqual(result.getpixel((0, 0)), (20, 30, 40))

    def test_cmyk_without_profile_rejected(self):
        path = self.directory / "cmyk.tif"
        Image.new("CMYK", (1, 1)).save(path)
        with self.assertRaisesRegex(ValueError, "ICC"):
            load_rgb(path)

    def test_profile_validation_and_ambiguous_inverse(self):
        for bad in ([0, -1, 20], [0, 256, 20], [0, float("nan"), 20], None):
            profile = affine_profile()
            profile["pairs"][0]["blacklight"] = bad
            with self.assertRaises(ValueError):
                validate_profile(profile)
        profile = affine_profile()
        for pair in profile["pairs"]:
            pair["blacklight"] = [0, 0, 0]
        result = convert(Image.new("RGB", (1, 1)), profile, "to-digital")
        self.assertTrue(all(abs(c - 128) <= 1 for c in result.getpixel((0, 0))))

    def test_output_restrictions(self):
        with self.assertRaises(ValueError):
            png_bytes(Image.new("RGBA", (1, 1)))
        with self.assertRaises(ValueError):
            save_png(Image.new("RGB", (1, 1)), self.directory / "wrong.jpg")

    def test_chart_and_unfilled_template(self):
        path = self.directory / "chart.png"
        calibration_chart(path)
        self.assertEqual(load_rgb(path).size, (1000, 500))
        with self.assertRaises(ValueError):
            load_profile(path.with_suffix(".json"))

    def test_cli_convert_and_restore(self):
        source = self.directory / "source.png"
        output, project, restored = [self.directory / f for f in ("out.png", "out.uvproject", "restored.png")]
        Image.new("RGB", (10, 10), (50, 100, 150)).save(source)
        for args in (["convert", str(source), str(output), "--project", str(project)], ["restore", str(project), str(restored)]):
            process = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "uvconvert.py"), *args], capture_output=True, text=True)
            self.assertEqual(process.returncode, 0, process.stderr)
        np.testing.assert_array_equal(np.array(load_rgb(source)), np.array(load_rgb(restored)))


if __name__ == "__main__":
    unittest.main()
