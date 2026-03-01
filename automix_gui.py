import shlex
import subprocess
import threading
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

AUDIO_EXTS = (".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac")


def quote_cmd(cmd):
    return " ".join(shlex.quote(str(x)) for x in cmd)


def build_filter(num_inputs: int, fade_sec: float):
    if num_inputs < 2:
        raise ValueError("Need at least 2 tracks")

    parts = []
    parts.append(f"[0:a][1:a]acrossfade=d={fade_sec}:c1=tri:c2=tri[a01];")

    for i in range(2, num_inputs):
        prev = "a01" if i == 2 else f"a0{i-1}"
        cur = f"a0{i}"
        parts.append(
            f"[{prev}][{i}:a]acrossfade=d={fade_sec}:c1=tri:c2=tri[{cur}];"
        )

    last = "a01" if num_inputs == 2 else f"a0{num_inputs-1}"
    return "".join(parts), last


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DJ AutoMix (Tkinter Prototype)")
        self.geometry("900x520")

        self.ffmpeg_path = tk.StringVar(value="ffmpeg")
        self.fade_sec = tk.DoubleVar(value=8.0)
        self.out_format = tk.StringVar(value="wav")
        self.out_path = tk.StringVar(
            value=str(Path.cwd() / "mixes" / "mix.wav")
        )
        self.status = tk.StringVar(value="Ready")

        self._ui()

    def _ui(self):
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        # Settings
        settings = ttk.LabelFrame(root, text="Settings", padding=10)
        settings.pack(fill="x")

        ttk.Label(settings, text="FFmpeg:").grid(row=0, column=0, sticky="w")
        ttk.Entry(settings, textvariable=self.ffmpeg_path, width=55).grid(
            row=0, column=1, padx=8, sticky="we"
        )
        ttk.Button(settings, text="Set FFmpeg path…", command=self.pick_ffmpeg).grid(
            row=0, column=2
        )
        ttk.Button(settings, text="Test", command=self.test_ffmpeg).grid(
            row=0, column=3, padx=(6, 0)
        )

        ttk.Label(settings, text="Crossfade (sec):").grid(
            row=1, column=0, sticky="w", pady=(10, 0)
        )
        ttk.Scale(
            settings, from_=1, to=20, variable=self.fade_sec, orient="horizontal"
        ).grid(row=1, column=1, padx=8, sticky="we", pady=(10, 0))

        ttk.Label(settings, text="Output format:").grid(
            row=2, column=0, sticky="w", pady=(10, 0)
        )
        ttk.Combobox(
            settings,
            textvariable=self.out_format,
            values=["wav", "mp3"],
            state="readonly",
            width=10,
        ).grid(row=2, column=1, sticky="w", padx=8, pady=(10, 0))

        settings.columnconfigure(1, weight=1)

        # Main area
        main = ttk.Frame(root)
        main.pack(fill="both", expand=True, pady=(12, 0))

        left = ttk.LabelFrame(main, text="Tracks", padding=10)
        left.pack(side="left", fill="both", expand=True)

        self.listbox = tk.Listbox(left, selectmode=tk.EXTENDED, height=14)
        self.listbox.pack(fill="both", expand=True)

        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=(8, 0))

        ttk.Button(btns, text="Add tracks…", command=self.add_tracks).pack(side="left")
        ttk.Button(btns, text="Remove", command=self.remove_selected).pack(
            side="left", padx=6
        )
        ttk.Button(btns, text="Clear", command=self.clear_tracks).pack(
            side="left", padx=6
        )
        ttk.Button(btns, text="Up", command=lambda: self.move(-1)).pack(side="right")
        ttk.Button(btns, text="Down", command=lambda: self.move(1)).pack(
            side="right", padx=6
        )

        right = ttk.LabelFrame(main, text="Export", padding=10)
        right.pack(side="right", fill="both", expand=True, padx=(12, 0))

        ttk.Entry(right, textvariable=self.out_path).pack(fill="x")
        ttk.Button(right, text="Browse…", command=self.pick_output).pack(
            fill="x", pady=6
        )
        ttk.Button(right, text="🚀 Export mix", command=self.export_mix).pack(
            fill="x", pady=6
        )

        ttk.Label(right, text="Log:").pack(anchor="w", pady=(6, 0))
        self.log = tk.Text(right, height=12, wrap="word")
        self.log.pack(fill="both", expand=True)

        ttk.Label(self, textvariable=self.status).pack(anchor="w", padx=12, pady=6)

    def log_msg(self, msg):
        self.log.insert("end", msg + "\n")
        self.log.see("end")

    def pick_ffmpeg(self):
        p = filedialog.askopenfilename(title="Select ffmpeg.exe")
        if p:
            self.ffmpeg_path.set(p)
            self.log_msg(f"FFmpeg set: {p}")

    def test_ffmpeg(self):
        try:
            p = subprocess.run(
                [self.ffmpeg_path.get(), "-version"],
                capture_output=True,
                text=True,
            )
            if p.returncode == 0:
                messagebox.showinfo("FFmpeg", "FFmpeg OK")
            else:
                raise RuntimeError(p.stderr)
        except Exception as e:
            messagebox.showerror("FFmpeg", str(e))

    def add_tracks(self):
        paths = filedialog.askopenfilenames(
            title="Select audio files",
            filetypes=[("Audio", "*.wav *.mp3 *.flac *.m4a *.ogg *.aac")],
        )
        for p in paths:
            if p.lower().endswith(AUDIO_EXTS):
                self.listbox.insert("end", p)
        self.status.set(f"Tracks: {self.listbox.size()}")

    def remove_selected(self):
        for i in reversed(self.listbox.curselection()):
            self.listbox.delete(i)

    def clear_tracks(self):
        self.listbox.delete(0, "end")

    def move(self, direction):
        sel = list(self.listbox.curselection())
        if not sel:
            return
        items = [self.listbox.get(i) for i in sel]
        for i in reversed(sel):
            self.listbox.delete(i)
        for i, item in zip(sel, items):
            self.listbox.insert(max(0, i + direction), item)

    def pick_output(self):
        fmt = self.out_format.get()
        p = filedialog.asksaveasfilename(
            defaultextension=f".{fmt}",
            filetypes=[("Audio", f"*.{fmt}")],
        )
        if p:
            self.out_path.set(p)

    def export_mix(self):
        tracks = [self.listbox.get(i) for i in range(self.listbox.size())]
        if len(tracks) < 2:
            messagebox.showwarning("Export", "Add at least 2 tracks")
            return

        def work():
            try:
                self.status.set("Exporting…")
                filt, last = build_filter(len(tracks), float(self.fade_sec.get()))
                out = Path(self.out_path.get())
                out.parent.mkdir(parents=True, exist_ok=True)

                cmd = [self.ffmpeg_path.get(), "-y"]
                for t in tracks:
                    cmd += ["-i", t]
                cmd += [
                    "-filter_complex",
                    filt,
                    "-map",
                    f"[{last}]",
                    "-ar",
                    "44100",
                    "-ac",
                    "2",
                    str(out),
                ]

                self.log_msg(">>> " + quote_cmd(cmd))
                p = subprocess.run(cmd, capture_output=True, text=True)
                if p.returncode != 0:
                    raise RuntimeError(p.stderr)

                self.status.set("Done")
                messagebox.showinfo("Export", f"Saved:\n{out}")
            except Exception as e:
                self.status.set("Failed")
                messagebox.showerror("Export failed", str(e))

        threading.Thread(target=work, daemon=True).start()


if __name__ == "__main__":
    App().mainloop()
