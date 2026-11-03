"""Windowed executable entry point; does not require a console or batch file."""
import sys
import traceback

from runtime_paths import app_data_dir


def main():
    try:
        from uv_gui import launch
        launch()
        return 0
    except Exception:
        details = traceback.format_exc()
        log = app_data_dir() / "startup-error.log"
        try:
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text(details, encoding="utf-8")
            message = f"The application could not start. Error details were saved to:\n{log}"
        except OSError:
            message = "The application could not start.\n\n" + details
        try:
            from tkinter import messagebox
            messagebox.showerror("Digital to UV Print Converter", message)
        except Exception:
            pass
        return 1


if __name__ == "__main__":
    sys.exit(main())
