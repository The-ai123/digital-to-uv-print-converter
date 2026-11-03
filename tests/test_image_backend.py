"""Numerical and progress checks; no desktop app is launched."""
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

import image_backend
from uvconvert import convert, demo_profile
from loss_analysis import convert_with_analysis


class BackendTests(unittest.TestCase):
    def test_native_cpu_matches_reference_and_reports_completion(self):
        image = Image.fromarray(np.random.default_rng(2).integers(0, 256, (31, 29, 3), dtype=np.uint8))
        profile = demo_profile()
        events = []
        expected = convert(image, profile, "to-blacklight", backend="reference")
        actual = convert(image, profile, "to-blacklight", backend="cpu", progress=lambda value, message: events.append(value))
        difference = np.abs(np.asarray(actual, dtype=int) - np.asarray(expected, dtype=int))
        self.assertLessEqual(int(difference.max()), 1)
        self.assertEqual(events[-1], 1)
        self.assertEqual(events, sorted(events))

    def test_auto_falls_back_without_gpu(self):
        image = Image.new("RGB", (512, 512), (20, 40, 60))
        axis = np.linspace(0, 255, 17)
        table = np.stack(np.meshgrid(axis, axis, axis, indexing="ij"), axis=-1)
        with patch.object(image_backend, "gpu_engine", return_value=None):
            output = image_backend.apply_lut(image, table, backend="auto")
        self.assertEqual(output.getpixel((0, 0)), (20, 40, 60))
        self.assertEqual(image_backend.last_backend_info()["backend"], "native CPU")

    def test_full_analysis_progress_is_ordered(self):
        events = []
        _, analysis = convert_with_analysis(Image.new("RGB", (17, 13), (100, 150, 200)), demo_profile(), "to-digital", backend="cpu", progress=lambda value, message: events.append(value))
        self.assertEqual(events, sorted(events))
        self.assertEqual(events[-1], 1)
        self.assertEqual(analysis["report"]["processing"]["conversion"]["backend"], "native CPU")

    def test_gpu_when_available_matches_reference(self):
        if image_backend.gpu_engine() is None:
            self.skipTest("No OpenCL GPU available")
        image = Image.fromarray(np.random.default_rng(3).integers(0, 256, (32, 33, 3), dtype=np.uint8))
        expected = convert(image, demo_profile(), "to-digital", backend="reference")
        actual = convert(image, demo_profile(), "to-digital", backend="gpu")
        difference = np.abs(np.asarray(actual, dtype=int) - np.asarray(expected, dtype=int))
        self.assertLessEqual(int(difference.max()), 1)


if __name__ == "__main__":
    unittest.main()
