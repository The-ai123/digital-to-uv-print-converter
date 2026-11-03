"""Local desktop UI. All image and profile processing stays on this computer."""
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from queue import SimpleQueue, Empty
from time import perf_counter
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageTk

from uvconvert import (calibration_chart, load_profile, load_rgb,
                       restore_project, save_project)
from loss_analysis import convert_with_analysis, export_with_analysis

APP_NAME = "Digital to UV Print Converter"
BG, PANEL, TEXT, MUTED, ACCENT = "#24272c", "#191c20", "#e4e7eb", "#aeb5bf", "#365f8e"
BORDER = "#41464e"


class Studio:
    def __init__(self, root):
        self.root = root
        self.pool = ThreadPoolExecutor(max_workers=1)
        self.future = None
        self.original = self.result = None
        self.analysis = None
        self.source_path = None
        self.preview_images = [None, None, None]
        self.preview_jobs = [None, None, None]
        example_profile = Path(__file__).resolve().parent / "charts" / "reference-profile.json"
        self.profile = load_profile(example_profile if example_profile.exists() else None)
        self.converted_profile = self.converted_direction = None
        root.title(APP_NAME)
        root.geometry("1280x840")
        root.minsize(1040, 720)
        root.configure(bg=BG)
        root.option_add("*Font", "{Segoe UI} 9")
        style = ttk.Style()
        style.theme_use("clam")
        style.configure("TButton", padding=(10, 6), background="#333840", foreground=TEXT, borderwidth=1, bordercolor=BORDER, font=("Segoe UI", 9))
        style.map("TButton", background=[("active", "#454d58"), ("disabled", "#2b2e33")], foreground=[("disabled", "#747b85")])
        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff")
        style.map("Accent.TButton", background=[("active", "#4675aa"), ("disabled", "#2b3541")])
        style.configure("TCombobox", fieldbackground=PANEL, foreground=TEXT, padding=5, arrowcolor=TEXT, bordercolor=BORDER)
        style.map("TCombobox", fieldbackground=[("readonly", PANEL)], selectbackground=[("readonly", PANEL)], selectforeground=[("readonly", TEXT)])
        style.configure("Work.Horizontal.TProgressbar", background=ACCENT, troughcolor=PANEL, bordercolor=BG, lightcolor=ACCENT, darkcolor=ACCENT)
        outer = tk.Frame(root, bg=BG, padx=16, pady=12)
        outer.pack(fill="both", expand=True)
        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x", pady=(0, 12))
        tk.Label(header, text=APP_NAME, bg=BG, fg=TEXT, font=("Segoe UI", 16, "bold")).pack(side="left")
        tk.Label(header, text="sRGB  |  8-bit RGB  |  PNG", bg=BG, fg=MUTED, font=("Consolas", 9)).pack(side="right")

        toolbar = tk.Frame(outer, bg=BG)
        toolbar.pack(fill="x", pady=(0, 10))
        self.controls = []
        self.button(toolbar, "Open image…", self.open_image).pack(side="left")
        self.button(toolbar, "Load profile…", self.open_profile).pack(side="left", padx=6)
        self.button(toolbar, "Open reference chart", self.use_example).pack(side="left")
        self.button(toolbar, "Profile details…", self.profile_details).pack(side="right")
        row = tk.Frame(outer, bg=PANEL, padx=10, pady=9, highlightbackground=BORDER, highlightthickness=1)
        row.pack(fill="x")
        tk.Label(row, text="Conversion", bg=PANEL, fg=TEXT).pack(side="left", padx=(0, 8))
        self.direction = tk.StringVar(value="Blacklight → digital")
        self.mode = ttk.Combobox(row, textvariable=self.direction, values=("Digital → blacklight", "Blacklight → digital"), state="readonly", width=24)
        self.mode.pack(side="left")
        self.mode.bind("<<ComboboxSelected>>", self.invalidate)
        tk.Label(row, text="Alpha matte", bg=PANEL, fg=TEXT).pack(side="left", padx=(18, 8))
        self.matte = tk.StringVar(value="Black")
        self.matte_box = ttk.Combobox(row, textvariable=self.matte, values=("Black", "White"), state="readonly", width=8)
        self.matte_box.pack(side="left")
        self.matte_box.bind("<<ComboboxSelected>>", self.reload_matte)
        tk.Label(row, text="Compute", bg=PANEL, fg=TEXT).pack(side="left", padx=(18, 8))
        self.backend = tk.StringVar(value="Auto")
        self.backend_box = ttk.Combobox(row, textvariable=self.backend, values=("Auto", "GPU (OpenCL)", "CPU"), state="readonly", width=14)
        self.backend_box.pack(side="left")
        self.backend_box.bind("<<ComboboxSelected>>", self.invalidate)
        self.convert_button = self.button(row, "Convert", self.run_convert, "Accent.TButton")
        self.convert_button.pack(side="right")

        profile_row = tk.Frame(outer, bg=BG)
        profile_row.pack(fill="x", pady=(9, 3))
        self.profile_label = tk.Label(profile_row, text="", bg=BG, fg=MUTED, anchor="w", wraplength=1180)
        self.profile_label.pack(side="left", fill="x", expand=True)
        self.profile_label.bind("<Configure>", lambda event: self.profile_label.configure(wraplength=max(1, event.width)))
        self.update_profile()
        self.source_summary = tk.StringVar(value="Input: none")
        tk.Label(outer, textvariable=self.source_summary, bg=BG, fg=MUTED, anchor="w", font=("Consolas", 9)).pack(fill="x", pady=(0, 9))
        previews = tk.Frame(outer, bg=BG)
        previews.pack(fill="both", expand=True)
        previews.columnconfigure((0, 1, 2), weight=1, uniform="preview")
        previews.rowconfigure(0, weight=1)
        self.preview_labels = []
        self.preview_titles = []
        self.diagnostic_view = tk.StringVar(value="Loss heatmap")
        for index, title in enumerate(("Input: desired UV appearance", "Output: digital artwork", "Diagnostics")):
            card = tk.Frame(previews, bg=PANEL, padx=10, pady=10, highlightbackground=BORDER, highlightthickness=1)
            card.grid(row=0, column=index, sticky="nsew", padx=(0, 4) if index == 0 else (4, 0) if index == 2 else (4, 4))
            heading = tk.Label(card, text=title, bg=PANEL, fg=TEXT, font=("Segoe UI", 9, "bold"))
            heading.pack(anchor="w")
            self.preview_titles.append(heading)
            if index == 2:
                self.diagnostic_box = ttk.Combobox(card, textvariable=self.diagnostic_view, values=("Loss heatmap", "Loss overlay", "Round-trip preview", "Calibration coverage"), state="disabled", width=22)
                self.diagnostic_box.pack(fill="x", pady=(8, 0))
                self.diagnostic_box.bind("<<ComboboxSelected>>", self.update_diagnostic)
            label = tk.Label(card, text="No input selected" if index == 0 else "No result", bg=PANEL, fg=MUTED, width=1, height=1)
            label.pack(fill="both", expand=True, pady=(10, 0))
            label.bind("<Configure>", lambda event, slot=index: self.schedule_preview(slot))
            self.preview_labels.append(label)
        self.analysis_caption = tk.StringVar(value="Loss: blue 0%, cyan 2%, yellow 5%, red 10%+ RGB mismatch. Coverage is a separate sampling-distance check.")
        analysis_label = tk.Label(outer, textvariable=self.analysis_caption, bg=BG, fg=MUTED, justify="left", anchor="w", wraplength=1100, font=("Segoe UI", 9))
        analysis_label.pack(fill="x", pady=(10, 0))
        analysis_label.bind("<Configure>", lambda event: analysis_label.configure(wraplength=max(1, event.width)))
        bottom = tk.Frame(outer, bg=BG)
        bottom.pack(fill="x", pady=(10, 8))
        self.export_button = self.button(bottom, "Export PNG + heatmap…", self.export, "Accent.TButton")
        self.export_button.pack(side="left")
        self.project_button = self.button(bottom, "Save project…", self.project)
        self.project_button.pack(side="left", padx=6)
        self.button(bottom, "Restore original…", self.restore).pack(side="left")
        tk.Label(outer, text="Digital to UV print preparation · Reference-based estimates; verify against a physical print proof.", bg=BG, fg=MUTED, anchor="w").pack(fill="x")
        statusbar = tk.Frame(root, bg=PANEL, padx=12, pady=5, highlightbackground=BORDER, highlightthickness=1)
        statusbar.pack(fill="x")
        self.status = tk.StringVar(value="Ready")
        tk.Label(statusbar, textvariable=self.status, bg=PANEL, fg=MUTED, anchor="w").pack(side="left", fill="x", expand=True)
        self.progress_value = tk.DoubleVar(value=0)
        self.progress_percent = tk.StringVar(value="")
        tk.Label(statusbar, textvariable=self.progress_percent, bg=PANEL, fg=MUTED, font=("Consolas", 9)).pack(side="right", padx=(8, 0))
        self.progress = ttk.Progressbar(statusbar, mode="determinate", maximum=100, variable=self.progress_value, length=180, style="Work.Horizontal.TProgressbar")
        self.progress.pack(side="right", padx=(12, 0))
        self.create_menus()
        self.set_busy(False)
        root.protocol("WM_DELETE_WINDOW", self.close)

    def button(self, parent, label, command, style="TButton"):
        button = ttk.Button(parent, text=label, command=lambda: self.safe(command), style=style)
        self.controls.append(button)
        return button

    def safe(self, command):
        if self.future is not None:
            return
        try:
            command()
        except Exception as exc:
            messagebox.showerror(APP_NAME, str(exc), parent=self.root)

    def create_menus(self):
        menubar = tk.Menu(self.root)
        file_menu = tk.Menu(menubar, tearoff=False)
        for label, shortcut, action in (("Open image…", "Ctrl+O", self.open_image), ("Load profile…", "Ctrl+L", self.open_profile),
                                        ("Export PNG + heatmap…", "Ctrl+S", self.export), ("Save project…", "Ctrl+Shift+S", self.project),
                                        ("Restore original…", "", self.restore)):
            file_menu.add_command(label=label, accelerator=shortcut, command=lambda fn=action: self.safe(fn))
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.close)
        menubar.add_cascade(label="File", menu=file_menu)
        self.file_menu = file_menu
        tools = tk.Menu(menubar, tearoff=False)
        tools.add_command(label="Convert", accelerator="Ctrl+Enter", command=lambda: self.safe(self.run_convert))
        tools.add_command(label="Open reference chart", command=lambda: self.safe(self.use_example))
        tools.add_command(label="Create calibration chart…", command=lambda: self.safe(self.chart))
        menubar.add_cascade(label="Tools", menu=tools)
        self.tools_menu = tools
        help_menu = tk.Menu(menubar, tearoff=False)
        help_menu.add_command(label="Profile and model details…", accelerator="F1", command=lambda: self.safe(self.profile_details))
        menubar.add_cascade(label="Help", menu=help_menu)
        self.help_menu = help_menu
        self.root.configure(menu=menubar)
        for key, action in (("<Control-o>", self.open_image), ("<Control-l>", self.open_profile), ("<Control-s>", self.export),
                            ("<Control-Shift-S>", self.project), ("<Control-Return>", self.run_convert), ("<F1>", self.profile_details)):
            self.root.bind(key, lambda event, fn=action: self.shortcut(fn))

    def shortcut(self, action):
        self.safe(action)
        return "break"

    def profile_details(self):
        kind = "Synthetic demonstration" if self.profile.get("demo") else "Screenshot-derived estimate" if self.profile.get("approximate") else "User-supplied calibration"
        messagebox.showinfo("Profile and model details", f"{APP_NAME}\nIntended workflow: UV/blacklight printing\n\nProfile: {self.profile['name']}\nType: {kind}\nRGB pairs: {len(self.profile['pairs'])}\n\n{self.profile.get('conditions', 'Capture conditions not specified.')}\n\nThe loss heatmap measures model round-trip RGB mismatch. Calibration coverage measures distance to chart samples. Neither certifies physical print accuracy.\n\nIndependent software; not a vendor-certified printer calibration.", parent=self.root)

    def set_busy(self, busy):
        for button in self.controls:
            button.configure(state="disabled" if busy else "normal")
        for box in (self.mode, self.matte_box, self.backend_box):
            box.configure(state="disabled" if busy else "readonly")
        self.diagnostic_box.configure(state="readonly" if not busy and self.analysis is not None else "disabled")
        if not busy and self.result is None:
            self.export_button.configure(state="disabled")
            self.project_button.configure(state="disabled")
        if not busy and self.original is None:
            self.convert_button.configure(state="disabled")
        for index in (0, 1, 4):
            self.file_menu.entryconfigure(index, state="disabled" if busy else "normal")
        self.file_menu.entryconfigure(2, state="normal" if not busy and self.result is not None and self.analysis is not None else "disabled")
        self.file_menu.entryconfigure(3, state="normal" if not busy and self.result is not None else "disabled")
        self.tools_menu.entryconfigure(0, state="normal" if not busy and self.original is not None else "disabled")
        for index in (1, 2):
            self.tools_menu.entryconfigure(index, state="disabled" if busy else "normal")
        self.help_menu.entryconfigure(0, state="disabled" if busy else "normal")

    def background(self, work, done, text, tracked=False):
        self.set_busy(True)
        events = SimpleQueue()
        started = perf_counter()
        self.progress_value.set(0)
        self.progress.configure(mode="determinate" if tracked else "indeterminate")
        self.progress_percent.set("0%" if tracked else "")
        if not tracked:
            self.progress.start(12)
        self.status.set(text)
        self.future = self.pool.submit(work, lambda fraction, message: events.put((fraction, message))) if tracked else self.pool.submit(work)

        def poll():
            latest = None
            while True:
                try:
                    latest = events.get_nowait()
                except Empty:
                    break
            if latest is not None:
                fraction, message = latest
                self.progress_value.set(max(self.progress_value.get(), min(100, fraction * 100)))
                self.status.set(message)
            elapsed = perf_counter() - started
            self.progress_percent.set(f"{self.progress_value.get():.0f}% · {elapsed:.1f}s" if tracked else f"{elapsed:.1f}s")
            if not self.future.done():
                self.root.after(60, poll)
                return
            try:
                value = self.future.result()
                elapsed = perf_counter() - started
                self.progress_value.set(100)
                self.progress_percent.set(f"100% · {elapsed:.1f}s" if tracked else f"{elapsed:.1f}s")
                done(value)
            except Exception as exc:
                self.status.set("Operation failed. Check the input and try again.")
                self.progress_value.set(0)
                self.progress_percent.set("Failed")
                messagebox.showerror(APP_NAME, str(exc), parent=self.root)
            finally:
                self.future = None
                if not tracked:
                    self.progress.stop()
                self.set_busy(False)
        self.root.after(60, poll)

    def show(self, image, index):
        self.preview_images[index] = image
        self.schedule_preview(index)

    def schedule_preview(self, index):
        if self.preview_jobs[index] is not None:
            self.root.after_cancel(self.preview_jobs[index])
        self.preview_jobs[index] = self.root.after(80, lambda: self.render_preview(index))

    def render_preview(self, index):
        self.preview_jobs[index] = None
        image = self.preview_images[index]
        if image is None:
            return
        label = self.preview_labels[index]
        preview = image.copy()
        preview.thumbnail((max(1, label.winfo_width() - 4), max(1, label.winfo_height() - 4)))
        photo = ImageTk.PhotoImage(preview)
        label.configure(image=photo, text="")
        label.image = photo

    def invalidate(self, event=None):
        self.result = None
        self.analysis = None
        for index, label in enumerate(self.preview_labels[1:], start=1):
            self.preview_images[index] = None
            label.configure(image="", text="No result · run conversion")
            label.image = None
        reverse = self.direction.get().startswith("Blacklight")
        self.preview_titles[0].configure(text="Input: desired UV appearance" if reverse else "Input: digital artwork")
        self.preview_titles[1].configure(text="Output: digital artwork" if reverse else "Output: predicted UV appearance")
        self.analysis_caption.set("Loss: blue 0%, cyan 2%, yellow 5%, red 10%+ RGB mismatch. Coverage is a separate sampling-distance check.")
        self.set_busy(False)

    def update_diagnostic(self, event=None):
        if self.analysis is None:
            return
        selection = self.diagnostic_view.get()
        key = {"Loss heatmap": "heatmap", "Loss overlay": "overlay", "Round-trip preview": "simulated", "Calibration coverage": "coverage"}[selection]
        self.show(self.analysis[key], 2)
        report = self.analysis["report"]
        if key == "coverage":
            self.analysis_caption.set("Coverage: blue near sampled colors; yellow/red farther away. Approximate RGB distance, not a probability or proof of color loss. Blue 0%, cyan 2%, yellow 5%, red 10%+.")
        elif key == "simulated":
            self.analysis_caption.set("Predicted UV after printing the estimated digital artwork." if report["direction"] == "to-digital" else "Estimated digital recovered from the predicted UV image.")
        else:
            stats = report["loss"]
            flagged = 100 * stats["pixels_at_or_above_5_percent"] / stats["pixel_count"]
            self.analysis_caption.set(f"Loss vs {report['comparison_space']}: mean {stats['mean_percent']:.2f}% RGB mismatch; {flagged:.1f}% of pixels at 5%+. Blue 0%, cyan 2%, yellow 5%, red 10%+. Model estimate; also inspect coverage.")

    def open_image(self):
        path = filedialog.askopenfilename(title="Open image", filetypes=[("Images", "*.png *.jpg *.jpeg *.tif *.tiff *.webp *.bmp"), ("All files", "*.*")])
        if path:
            self.load(path)

    def load(self, path):
        matte = (255, 255, 255) if self.matte.get() == "White" else (0, 0, 0)
        def done(image):
            self.original, self.source_path = image, Path(path)
            self.invalidate()
            self.show(image, 0)
            self.source_summary.set(f"Input: {Path(path).name}  |  {image.width} × {image.height} px  |  sRGB / RGB8")
            self.root.title(f"{Path(path).name} — {APP_NAME}")
            self.status.set("Input loaded")
        self.background(lambda: load_rgb(path, matte), done, "Normalizing to sRGB…")

    def reload_matte(self, event=None):
        if self.source_path:
            self.load(self.source_path)

    def update_profile(self):
        kind = "synthetic demo" if self.profile.get("demo") else "screenshot estimate" if self.profile.get("approximate") else "user calibration"
        name = self.profile["name"]
        if name == "User chart crops — approximate screenshot RGB mapping":
            name = "Sampled reference chart"
        self.profile_label.configure(text=f"Profile: {name}  |  {kind}  |  {len(self.profile['pairs'])} RGB pairs")

    def use_example(self):
        directory = Path(__file__).resolve().parent / "charts"
        self.profile = load_profile(directory / "reference-profile.json")
        self.direction.set("Digital → blacklight")
        self.update_profile()
        self.load(directory / "digital-atlas.png")

    def open_profile(self):
        path = filedialog.askopenfilename(title="Load RGB conversion profile", filetypes=[("Profile JSON", "*.json")])
        if path:
            profile = load_profile(path)
            self.profile = profile
            self.update_profile()
            self.invalidate()

    def run_convert(self):
        if self.original is None:
            self.open_image()
            return
        direction = "to-blacklight" if self.direction.get().startswith("Digital") else "to-digital"
        profile = self.profile
        backend = {"Auto": "auto", "GPU (OpenCL)": "gpu", "CPU": "cpu"}[self.backend.get()]
        def done(value):
            self.result, self.analysis = value
            self.converted_profile, self.converted_direction = profile, direction
            self.show(self.result, 1)
            self.diagnostic_view.set("Loss heatmap")
            self.update_diagnostic()
            processing = self.analysis["report"]["processing"]["conversion"]
            fallback = " · GPU unavailable" if processing.get("fallback_reason") else ""
            self.status.set(f"Complete · {processing['backend']} · {processing['device']}{fallback}")
        self.background(lambda progress: convert_with_analysis(self.original, profile, direction, backend=backend, progress=progress), done, "Converting and analyzing round-trip loss…", tracked=True)

    def destination(self, extension, title):
        name = (self.source_path.stem if self.source_path else "image") + "_" + (self.converted_direction or "restored")
        path = filedialog.asksaveasfilename(title=title, defaultextension=extension, initialfile=name + extension, filetypes=[(extension, "*" + extension)])
        if path and self.source_path and Path(path).resolve() == self.source_path.resolve():
            raise ValueError("Choose a different path to preserve your source image.")
        return path

    def export(self):
        if self.result is None or self.analysis is None:
            return
        path = self.destination(".png", "Save RGB artwork and diagnostic images")
        if path:
            def done(directory):
                self.status.set(f"PNG saved · maps in {directory.name}")
                messagebox.showinfo("Export complete", f"RGB artwork:\n{path}\n\nHeatmap, overlay, simulation, coverage and report:\n{directory}", parent=self.root)
            self.background(lambda progress: export_with_analysis(self.result, path, self.analysis, progress=progress), done, "Saving artwork and diagnostics…", tracked=True)

    def project(self):
        if self.result is None:
            return
        path = self.destination(".uvproject", "Save source, conversion and diagnostics")
        if path:
            self.background(lambda progress: save_project(path, self.original, self.result, self.converted_profile, self.converted_direction, self.analysis, progress=progress), lambda _: self.status.set("Project saved · original and diagnostics included"), "Saving project…", tracked=True)

    def restore(self):
        path = filedialog.askopenfilename(title="Restore original", filetypes=[("UV converter project", "*.uvproject")])
        if path:
            target = filedialog.asksaveasfilename(title="Save restored RGB image", defaultextension=".png", filetypes=[("RGB PNG", "*.png")])
            if target:
                self.background(lambda: restore_project(path, target), lambda _: self.status.set("Original RGB pixels restored"), "Restoring original…")

    def chart(self):
        target = filedialog.asksaveasfilename(title="Save RGB calibration chart", defaultextension=".png", filetypes=[("RGB PNG", "*.png")])
        if target:
            template = Path(target).with_suffix(".json")
            if template.exists() and not messagebox.askyesno("Replace measurement template?", f"Replace existing {template.name}?", parent=self.root):
                return
            calibration_chart(target)
            self.status.set("Chart + JSON template saved · fill in measured blacklight RGB values")

    def close(self):
        if self.future is not None:
            messagebox.showinfo(APP_NAME, "Please wait for the current operation to finish.", parent=self.root)
            return
        self.pool.shutdown(wait=False)
        for job in self.preview_jobs:
            if job is not None:
                self.root.after_cancel(job)
        self.root.destroy()


def launch():
    root = tk.Tk()
    icon = Path(__file__).resolve().parent / "app.ico"
    if icon.is_file():
        try:
            root.iconbitmap(str(icon))
        except tk.TclError:
            pass
    Studio(root)
    root.mainloop()


if __name__ == "__main__":
    launch()
