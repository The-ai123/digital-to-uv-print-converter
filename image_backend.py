"""OpenCL GPU LUT application with native Pillow CPU fallback."""
from __future__ import annotations

import threading

import numpy as np
from PIL import Image, ImageFilter

from progress_utils import report
from runtime_paths import cache_dir

_ENGINE = None
_ATTEMPTED = False
_GPU_ERROR = ""
_LOCK = threading.Lock()
_LAST = {"backend": "not started", "device": "", "fallback_reason": ""}

KERNEL = r"""
#pragma OPENCL FP_CONTRACT OFF
__kernel void apply_lut(__global const uchar *input,
                        __global const float *lut,
                        __global uchar *output, int count, int size) {
    int i = get_global_id(0);
    if (i >= count) return;
    float scale = (float)(size - 1) / 255.0f;
    float pr = (float)input[3*i] * scale;
    float pg = (float)input[3*i+1] * scale;
    float pb = (float)input[3*i+2] * scale;
    int ir = min((int)floor(pr), size - 2);
    int ig = min((int)floor(pg), size - 2);
    int ib = min((int)floor(pb), size - 2);
    float fr = pr - (float)ir, fg = pg - (float)ig, fb = pb - (float)ib;
    float3 value = (float3)(0.0f);
    for (int r=0; r<2; ++r) for (int g=0; g<2; ++g) for (int b=0; b<2; ++b) {
        float weight = (r ? fr : 1.0f-fr) * (g ? fg : 1.0f-fg) * (b ? fb : 1.0f-fb);
        int index = (((ir+r)*size + ig+g)*size + ib+b)*3;
        value += vload3(0, lut+index) * weight;
    }
    vstore3(convert_uchar3_sat_rte(value), 0, output+3*i);
}
"""


class OpenCLEngine:
    def __init__(self):
        import pyopencl as cl
        self.cl = cl
        devices = []
        for platform in cl.get_platforms():
            try:
                devices.extend(platform.get_devices(device_type=cl.device_type.GPU))
            except cl.Error:
                continue
        if not devices:
            raise RuntimeError("No OpenCL GPU was found.")
        device = max(devices, key=lambda item: item.max_compute_units)
        self.name = device.name.strip()
        self.context = cl.Context([device])
        self.queue = cl.CommandQueue(self.context)
        folder = cache_dir() / "opencl"
        folder.mkdir(parents=True, exist_ok=True)
        self.program = cl.Program(self.context, KERNEL).build(options=["-cl-std=CL1.2"], cache_dir=str(folder))
        self.kernel = cl.Kernel(self.program, "apply_lut")

    def apply(self, image, lut, progress):
        cl = self.cl
        table = np.ascontiguousarray(lut, dtype=np.float32)
        table_buffer = cl.Buffer(self.context, cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR, hostbuf=table)
        result = Image.new("RGB", image.size)
        rows = max(1, 524288 // image.width)
        try:
            for top in range(0, image.height, rows):
                bottom = min(top + rows, image.height)
                source = np.ascontiguousarray(image.crop((0, top, image.width, bottom)), dtype=np.uint8)
                target = np.empty_like(source)
                source_buffer = cl.Buffer(self.context, cl.mem_flags.READ_ONLY | cl.mem_flags.COPY_HOST_PTR, hostbuf=source)
                target_buffer = cl.Buffer(self.context, cl.mem_flags.WRITE_ONLY, target.nbytes)
                try:
                    args = (source_buffer, table_buffer, target_buffer, np.int32(source.size // 3), np.int32(lut.shape[0]))
                    for index, value in enumerate(args):
                        self.kernel.set_arg(index, value)
                    cl.enqueue_nd_range_kernel(self.queue, self.kernel, (source.size // 3,), None)
                    cl.enqueue_copy(self.queue, target, target_buffer, is_blocking=True)
                    result.paste(Image.fromarray(target), (0, top))
                finally:
                    source_buffer.release()
                    target_buffer.release()
                report(progress, bottom / image.height, f"GPU mapping · {self.name}")
        finally:
            table_buffer.release()
        return result


def gpu_engine():
    global _ENGINE, _ATTEMPTED, _GPU_ERROR
    with _LOCK:
        if not _ATTEMPTED:
            _ATTEMPTED = True
            try:
                _ENGINE = OpenCLEngine()
            except Exception as exc:
                _GPU_ERROR = str(exc)
        return _ENGINE


def last_backend_info():
    return dict(_LAST)


def native_cpu(image, lut, progress):
    # Pillow stores R fastest, while the application's NumPy cube stores B fastest.
    table = np.ascontiguousarray(lut.transpose(2, 1, 0, 3) / 255, dtype=np.float32)
    transform = ImageFilter.Color3DLUT(lut.shape[0], table, channels=3, target_mode="RGB")
    result = Image.new("RGB", image.size)
    rows = max(1, 524288 // image.width)
    for top in range(0, image.height, rows):
        bottom = min(top + rows, image.height)
        result.paste(image.crop((0, top, image.width, bottom)).filter(transform), (0, top))
        report(progress, bottom / image.height, "CPU mapping · native LUT")
    return result


def apply_lut(image, lut, backend="auto", progress=None):
    global _LAST, _ENGINE, _GPU_ERROR
    if backend not in ("auto", "gpu", "cpu"):
        raise ValueError("Backend must be auto, gpu or cpu.")
    reason = ""
    if backend == "gpu" or (backend == "auto" and image.width * image.height >= 262144):
        report(progress, 0, "Initializing OpenCL GPU")
        engine = gpu_engine()
        if engine is not None:
            try:
                result = engine.apply(image, lut, progress)
                _LAST = {"backend": "OpenCL GPU", "device": engine.name, "fallback_reason": ""}
                return result
            except Exception as exc:
                _GPU_ERROR = str(exc)
                _ENGINE = None
        if backend == "gpu":
            raise RuntimeError(f"OpenCL GPU processing is unavailable: {_GPU_ERROR}. Select Auto or CPU.")
        reason = _GPU_ERROR
        report(progress, 0, "GPU unavailable; using native CPU")
    result = native_cpu(image, lut, progress)
    _LAST = {"backend": "native CPU", "device": "CPU", "fallback_reason": reason}
    return result
