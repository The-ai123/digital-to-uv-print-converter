"""Optional progress callbacks; values are fractions of completed work."""


def report(callback, value, message):
    if callback is not None:
        callback(max(0.0, min(1.0, float(value))), message)


def stage(callback, start, end, label):
    if callback is None:
        return None
    def update(value, message=""):
        text = f"{label} · {message}" if label and message and label != message else message or label
        report(callback, start + (end - start) * value, text)
    return update
