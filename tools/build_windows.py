"""Build the standalone Windows desktop executable from the current checkout."""
from __future__ import annotations

import hashlib
from importlib.metadata import distribution
import json
import os
import re
from pathlib import Path
import subprocess
import sys

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
NAME = "Digital to UV Print Converter 0.2.0"


def create_icon(path):
    icon = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(icon)
    draw.rounded_rectangle((8, 8, 248, 248), radius=36, fill=(36, 39, 44), outline=(65, 70, 78), width=4)
    draw.rectangle((40, 183, 216, 197), fill=(54, 95, 142))
    font_path = Path(os.environ.get("WINDIR", "C:/Windows")) / "Fonts" / "seguisb.ttf"
    try:
        font = ImageFont.truetype(str(font_path), 112)
    except OSError:
        font = ImageFont.load_default(size=104)
    draw.text((128, 110), "UV", anchor="mm", fill=(228, 231, 235), font=font)
    icon.save(path, format="ICO", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


def main():
    if sys.platform != "win32":
        raise SystemExit("Build the Windows executable on Windows.")
    from uvconvert import load_profile, make_lut
    from loss_analysis import coverage_lut
    from lut_cache import CACHE_DIR, cache_key

    assets = ROOT / "build" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    icon = assets / "app.ico"
    create_icon(icon)
    version = assets / "version.txt"
    version.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=(0, 2, 0, 0), prodvers=(0, 2, 0, 0), mask=0x3f, flags=0x2, OS=0x40004, fileType=0x1, subtype=0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('FileDescription', 'Digital to UV Print Converter'),
    StringStruct('FileVersion', '0.2.0'),
    StringStruct('ProductName', 'Digital to UV Print Converter'),
    StringStruct('ProductVersion', '0.2.0'),
    StringStruct('OriginalFilename', '{NAME}.exe'),
    StringStruct('Comments', 'Independent print-preparation utility; screenshot-derived reference profile.')
  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)""", encoding="utf-8")
    data = [(icon, "."), (ROOT / "README.md", "."), (ROOT / "ASSET_NOTICES.md", "."), (ROOT / "LICENSE", ".")]
    python_license = Path(sys.base_prefix) / "LICENSE.txt"
    if python_license.is_file():
        data.append((python_license, "licenses/python"))
    for package in ("Pillow", "numpy", "pyopencl", "pytools", "platformdirs", "typing_extensions", "siphash24", "PyInstaller"):
        package_data = distribution(package)
        for item in package_data.files or []:
            if ".." in item.parts:
                continue
            if item.name.lower().startswith(("license", "copying")) or "licenses" in [part.lower() for part in item.parts]:
                source = Path(package_data.locate_file(item))
                if source.is_file():
                    data.append((source, "licenses/" + package + "/" + item.parent.as_posix()))
    for filename in ("reference-profile.json", "digital-atlas.png"):
        path = ROOT / "charts" / filename
        if not path.is_file():
            raise SystemExit(f"Missing reference asset: {path}")
        data.append((path, "charts"))
    profile = load_profile(ROOT / "charts" / "reference-profile.json")
    print("Preparing bundled reference tables...", flush=True)
    for direction in ("to-blacklight", "to-digital"):
        make_lut(profile, direction)
        coverage_lut(profile, direction)
        for kind, size in (("color-v1", profile.get("lut_size", 33)), ("coverage-v1", 33)):
            path = CACHE_DIR / (cache_key(kind, profile, direction, size) + ".npy")
            if path.is_file():
                data.append((path, "precomputed"))
    command = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--windowed",
               "--name", NAME, "--icon", str(icon), "--version-file", str(version), "--noupx",
               "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build" / "pyinstaller"),
               "--specpath", str(ROOT / "build"), "--paths", str(ROOT),
               "--collect-all", "pyopencl", "--hidden-import", "pyopencl._cl", "--hidden-import", "siphash24"]
    for source, destination in data:
        command += ["--add-data", str(source) + os.pathsep + destination]
    command.append(str(ROOT / "desktop.py"))
    environment = os.environ.copy()
    environment["PYINSTALLER_CONFIG_DIR"] = str(ROOT / "build" / "pyinstaller-config")
    subprocess.run(command, cwd=ROOT, env=environment, check=True)
    executable = ROOT / "dist" / (NAME + ".exe")
    digest = hashlib.sha256(executable.read_bytes()).hexdigest()
    release = ROOT / "build" / "release" / "0.2.0"
    release.mkdir(parents=True, exist_ok=True)
    (release / "SHA256SUMS.txt").write_text(f"{digest}  {executable.name}\n", encoding="utf-8")
    (release / "BUILD_INFO.json").write_text(json.dumps({"version": "0.2.0", "platform": "Windows x64",
        "file": executable.name, "bytes": executable.stat().st_size, "sha256": digest,
        "publication": "Source code: MIT. Example images: Pexels License. Only redesigned chart assets are bundled; see ASSET_NOTICES.md.",
        "runtime_testing": "Not launched or interactively tested."}, indent=2), encoding="utf-8")
    dist_directory = (ROOT / "dist").resolve()
    for previous in dist_directory.iterdir():
        if previous.name != executable.name and re.fullmatch(r"Digital to UV Print Converter(?: [0-9]+\.[0-9]+\.[0-9]+)?\.exe", previous.name):
            if previous.resolve().parent != dist_directory or not previous.is_file():
                raise RuntimeError("Unexpected old executable path.")
            previous.unlink()
    print(f"\nBuilt: {executable}\nSize: {executable.stat().st_size / 1048576:.1f} MiB\nSHA256: {digest}", flush=True)


if __name__ == "__main__":
    main()
