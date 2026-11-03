"""Resource and writable-data locations for source and packaged desktop runs."""
import os
from pathlib import Path
import sys

RESOURCE_DIR = Path(__file__).resolve().parent


def app_data_dir():
    if getattr(sys, "frozen", False):
        local = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData" / "Local")))
        return local / "DigitalToUVPrintConverter"
    return RESOURCE_DIR


def cache_dir():
    return app_data_dir() / ("cache" if getattr(sys, "frozen", False) else ".uv-cache")
