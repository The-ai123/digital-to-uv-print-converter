# Asset notices

## Application source code

Source code is provided under the [MIT License](LICENSE), preserving the license and copyright notice from the GitHub repository. The code license does not replace the licenses or ownership of third-party photographs, branding, or bundled runtime dependencies.

## Example photographs

Both photographs are by **Merlin Lightpainting**, provided through Pexels:

| Local source | Original page |
| --- | --- |
| `Examples/sources/abstract-light-art.jpg` | [Abstract Light Art, photo 14146747](https://www.pexels.com/photo/abstract-light-art-14146747/) |
| `Examples/sources/round-shapes.jpg` | [Colorful Abstract Illustration with Round Shapes, photo 14367716](https://www.pexels.com/photo/colorful-abstract-illustration-with-round-shapes-14367716/) |

The photographs remain subject to the [Pexels License](https://www.pexels.com/license/). Converted previews are modified examples produced with this application, not photographs of physical UV prints. No endorsement by the photographer or Pexels is implied. Public examples contain the original input JPEGs and small documentation previews. Full-size converted PNGs remain local in `Examples/full-resolution/` and are excluded from Git to keep the repository smaller. Previously unlicensed artwork examples are not included.

## Reference-chart assets

Only programmatically redesigned charts and sampled RGB data/profile in `charts/` are included in the repository or executable. They are reference-derived approximations, not original source artwork codes or an official printer calibration.

Original chart screenshots/crops and photo-based sampling overlays are retained only in the ignored `local-reference/` directory. They are not included in Git or in the executable. The calibration chart in `assets/calibration/` is generated directly from known RGB values by this program.

This independent project is not affiliated with a printing vendor and does not claim ownership of third-party branding or imagery.

## Bundled software

The standalone executable includes Python, Tcl/Tk, Pillow, NumPy, PyOpenCL and their runtime dependencies. Their respective licenses continue to apply. The application's MIT License does not change their licensing. Packaging uses PyInstaller; its bootloader is distributed under its own license and exception.
