"""Run manually: python -m unittest -v test_loss_analysis."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

import numpy as np
from PIL import Image

from loss_analysis import (analyze_conversion, convert_with_analysis,
                           export_with_analysis, mismatch_maps)
from uvconvert import save_project


def profile(collapsed=False):
    return {"version": 1, "name": "Analysis fixture", "lut_size": 17,
            "pairs": [{"digital": [r, g, b], "blacklight": [0, 0, 0] if collapsed else [r, g, b]}
                      for r in (0, 255) for g in (0, 255) for b in (0, 255)]}


class LossAnalysisTests(unittest.TestCase):
    def test_known_rgb_error_and_overlay_alignment(self):
        target = Image.new("RGB", (2, 1), (10, 20, 30))
        proof = target.copy()
        proof.putpixel((1, 0), (40, 20, 30))
        heat, overlay, stats = mismatch_maps(target, proof)
        self.assertEqual(heat.mode, "RGB")
        self.assertEqual(heat.size, target.size)
        self.assertEqual(overlay.getpixel((0, 0)), target.getpixel((0, 0)))
        self.assertAlmostEqual(stats["max_percent"], 100 * np.sqrt(300) / 255, places=4)
        self.assertEqual(stats["pixels_at_or_above_5_percent"], 1)
        self.assertEqual(stats["pixel_count"], 2)

    def test_fixed_scale_does_not_invent_loss_for_identity(self):
        target = Image.fromarray(np.array([[[0, 0, 0], [100, 150, 200], [255, 255, 255]]], dtype=np.uint8))
        digital, analysis = convert_with_analysis(target, profile(), "to-digital")
        np.testing.assert_array_equal(np.array(digital), np.array(target))
        self.assertEqual(analysis["report"]["loss"]["max_percent"], 0)
        self.assertEqual(analysis["heatmap"].getpixel((1, 0)), (14, 30, 80))

    def test_unreproducible_target_is_red(self):
        target = Image.new("RGB", (3, 2), (0, 255, 0))
        _, analysis = convert_with_analysis(target, profile(collapsed=True), "to-digital")
        self.assertEqual(analysis["simulated"].getpixel((0, 0)), (0, 0, 0))
        self.assertEqual(analysis["heatmap"].getpixel((0, 0)), (240, 40, 55))
        self.assertEqual(analysis["report"]["loss"]["pixels_at_or_above_10_percent"], 6)

    def test_forward_analysis_is_labeled_digital_not_uv(self):
        source = Image.new("RGB", (1, 1), (80, 100, 120))
        _, analysis = convert_with_analysis(source, profile(), "to-blacklight")
        self.assertEqual(analysis["report"]["comparison_space"], "digital source")

    def test_mismatched_sizes_and_invalid_direction_are_rejected(self):
        with self.assertRaises(ValueError):
            mismatch_maps(Image.new("RGB", (1, 1)), Image.new("RGB", (2, 1)))
        with self.assertRaises(ValueError):
            analyze_conversion(Image.new("RGB", (1, 1)), Image.new("RGB", (1, 1)), profile(), "wrong")

    def test_export_keeps_old_diagnostics_and_project_contains_maps(self):
        target = Image.new("RGB", (1, 1), (0, 255, 0))
        digital, analysis = convert_with_analysis(target, profile(collapsed=True), "to-digital")
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / "artwork.png"
            first = export_with_analysis(digital, output, analysis)
            marker = first / "keep.txt"
            marker.write_text("preserve", encoding="utf-8")
            second = export_with_analysis(digital, output, analysis)
            self.assertNotEqual(first, second)
            self.assertEqual(marker.read_text(encoding="utf-8"), "preserve")
            self.assertTrue((second / "predicted-uv.png").exists())
            self.assertEqual(json.loads((second / "report.json").read_text())["direction"], "to-digital")
            for name in ("loss-heatmap.png", "loss-overlay.png", "calibration-coverage.png"):
                raw = (second / name).read_bytes()
                self.assertEqual(raw[24:26], bytes([8, 2]))
            project = Path(tmp) / "saved.uvproject"
            save_project(project, target, digital, profile(collapsed=True), "to-digital", analysis)
            with zipfile.ZipFile(project) as archive:
                self.assertIn("analysis/heatmap.png", archive.namelist())
                self.assertIn("analysis/report.json", archive.namelist())


if __name__ == "__main__":
    unittest.main()
