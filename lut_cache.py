"""Small memory/disk cache for deterministic numeric color lookup tables."""
from collections import OrderedDict
import hashlib
import json
from pathlib import Path
import uuid
import sys

import numpy as np
from runtime_paths import RESOURCE_DIR, cache_dir

CACHE_DIR = cache_dir()
_MEMORY = OrderedDict()


def cache_key(kind, profile, direction, size):
    payload = json.dumps([kind, profile, direction, size], sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def cached_lut(kind, profile, direction, size, build):
    key = cache_key(kind, profile, direction, size)
    if key in _MEMORY:
        _MEMORY.move_to_end(key)
        return _MEMORY[key]
    path = CACHE_DIR / (key + ".npy")
    array = None
    candidates = [path]
    if getattr(sys, "frozen", False):
        candidates.append(RESOURCE_DIR / "precomputed" / (key + ".npy"))
    for candidate in candidates:
        try:
            loaded = np.load(candidate, allow_pickle=False)
            if loaded.shape == (size, size, size, 3) and loaded.dtype == np.float64 and np.isfinite(loaded).all() and loaded.min() >= 0 and loaded.max() <= 255:
                array = loaded
                break
        except (OSError, ValueError, EOFError):
            pass
    if array is None:
        array = build()
        temporary = CACHE_DIR / (key + "." + uuid.uuid4().hex + ".tmp")
        try:
            CACHE_DIR.mkdir(parents=True, exist_ok=True)
            with temporary.open("wb") as stream:
                np.save(stream, array, allow_pickle=False)
            temporary.replace(path)
        except OSError:
            # A read-only installation still works using the memory cache.
            pass
        finally:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass
    array.setflags(write=False)
    _MEMORY[key] = array
    if len(_MEMORY) > 6:
        _MEMORY.popitem(last=False)
    return array
