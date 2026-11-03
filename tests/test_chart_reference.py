"""Checks for the crop-derived workflow. Run manually with unittest."""
import unittest

import numpy as np
from PIL import Image

from tools.chart_reference import LAYOUTS, build_profile, coordinates, extract
from uvconvert import convert


class ReferenceTests(unittest.TestCase):
    def test_chart_patch_labels_and_sample_windows(self):
        for layout in LAYOUTS.values():
            image = Image.new("RGB", tuple(layout["size"]), (80, 120, 160))
            patches, _ = extract(image, layout)
            self.assertEqual(len(patches), 773)
            self.assertEqual(len({p["id"] for p in patches}), 773)
            self.assertEqual(patches[0]["id"], "A1")
            self.assertEqual(patches[369]["id"], "J37")
            self.assertEqual(patches[370]["id"], "K1")
            self.assertEqual(patches[739]["id"], "T37")
            self.assertEqual(patches[740]["id"], "X5")
            self.assertEqual(patches[-1]["id"], "X37")
            self.assertTrue(all(p["rgb"] == [80, 120, 160] for p in patches))

    def test_patches_use_interiors_not_black_grid_lines(self):
        image = Image.new("RGB", (2082, 912), (0, 0, 0))
        pixels = np.asarray(image).copy()
        for _, _, _, center, hw, hh in coordinates(image, LAYOUTS["digital"]):
            x, y = np.rint(center).astype(int)
            pixels[y - int(hh) - 2:y + int(hh) + 3, x - int(hw) - 2:x + int(hw) + 3] = [50, 100, 150]
        patches, _ = extract(Image.fromarray(pixels), LAYOUTS["digital"])
        self.assertTrue(all(p["rgb"] == [50, 100, 150] for p in patches))

    def test_duplicate_digital_measurements_are_averaged(self):
        source, target = [], []
        for index, color in enumerate([(r, g, b) for r in (0, 255) for g in (0, 255) for b in (0, 255)]):
            source.append({"id": str(index), "rgb": list(color)})
            target.append({"id": str(index), "rgb": list(color)})
        source += [{"id": "duplicate", "rgb": [0, 0, 0]}]
        target += [{"id": "duplicate", "rgb": [20, 40, 60]}]
        profile = build_profile(source, target, {})
        self.assertEqual(profile["merged_digital_duplicates"], 1)
        pair = next(p for p in profile["pairs"] if p["digital"] == [0, 0, 0])
        self.assertEqual(pair["blacklight"], [10, 20, 30])

    def test_local_model_preserves_known_cube_corner_mappings(self):
        profile = {"version": 1, "name": "Known nonlinear corners", "interpolation": "local-affine", "lut_size": 17,
                   "pairs": [{"digital": [r, g, b], "blacklight": [g, b, r]} for r in (0, 255) for g in (0, 255) for b in (0, 255)]}
        colors = np.array([p["digital"] for p in profile["pairs"]], dtype=np.uint8).reshape(1, 8, 3)
        expected = np.array([p["blacklight"] for p in profile["pairs"]], dtype=np.uint8).reshape(1, 8, 3)
        np.testing.assert_array_equal(np.array(convert(Image.fromarray(colors), profile, "to-blacklight")), expected)

    def test_local_inverse_handles_collapsed_uv_colors(self):
        profile = {"version": 1, "name": "Collapsed UV gamut", "interpolation": "local-affine", "lut_size": 17,
                   "pairs": [{"digital": [r, g, b], "blacklight": [0, 0, 0]} for r in (0, 255) for g in (0, 255) for b in (0, 255)]}
        result = convert(Image.new("RGB", (1, 1)), profile, "to-digital")
        self.assertEqual(result.getpixel((0, 0)), (128, 128, 128))


if __name__ == "__main__":
    unittest.main()
