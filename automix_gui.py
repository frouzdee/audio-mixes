import os
import sys
import shlex
import math
import time
import tempfile
import subprocess
import threading
from dataclasses import dataclass
from pathlib import Path

import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# Optional drag & drop support
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD  # type: ignore
    DND_OK = True
except Exception:
    TkinterDnD = None
    DND_FILES = None
    DND_OK = False

import numpy as np

AUDIO_EXTS = (".wav", ".mp3", ".flac", ".m4a", ".ogg", ".aac", ".aiff", ".aif")


def quote_cmd(cmd):
    return " ".join(shlex.quote(str(x)) for x in cmd)


def clamp(x, lo, hi):
    return max(lo, min(hi, x))


def parse_dnd_files(data: str):
    # tkinterdnd2 returns a string that may contain braces for paths with spaces
    # Example: "{C:/My Files/a.mp3} {C:/b.wav}"
    out = []
    cur = ""
    in_brace = False
    for ch in data:
        if ch == "{":
            in_brace = True
            cur = ""
        elif ch == "}":
            in_brace = False
            if cur:
                out.append(cur)
            cur = ""
        elif ch.isspace() and not in_brace:
            if cur:
                out.append(cur)
                cur = ""
        else:
            cur += ch
    if cur:
        out.append(cur)
    return out


@dataclass
class Track:
    path: str
    duration: float = 0.0  # seconds
    sr: int = 44100
    channels: int = 2
    waveform: np.ndarray | None = None  # downsampled abs waveform for display


class FFPlayer:
    """Simple playback using ffplay subprocess (robust across OS)."""

    def __init__(self):
        self.proc: subprocess.Popen | None = None

    def stop(self):
        if self.proc and self.proc.poll() is None:
            try:
                self.proc.terminate()
            except Exception:
                pass
        self.proc = None

    def play_file(self, ffplay_path: str, path: str, start_sec: float = 0.0):
        self.stop()
        cmd = [ffplay_path, "-nodisp", "-autoexit", "-hide_banner", "-loglevel", "error"]
        if start_sec > 0:
            cmd += ["-ss", f"{start_sec:.3f}"]
        cmd += [path]
        self.proc = subprocess.Popen(cmd)

    def play_wav_bytes(self, ffplay_path: str, wav_path: str):
        self.play_file(ffplay_path, wav_path, 0.0)


class WaveformCanvas(tk.Canvas):
    """Very lightweight waveform/timeline view."""
    def __init__(self, parent, **kwargs):
        super().__init__(parent, bg="#101214", highlightthickness=0, **kwargs)
        self.tracks: list[Track] = []
        self.transitions: list[float] = []  # crossfade seconds per boundary (len = len(tracks)-1)
        self.total_duration = 0.0
        self.pixels_per_sec = 80.0  # zoom factor
        self.playhead_sec = 0.0

        self.bind("<MouseWheel>", self._on_wheel_zoom)  # Windows/mac
        self.bind("<Button-4>", lambda e: self._zoom(1.1))  # Linux up
        self.bind("<Button-5>", lambda e: self._zoom(0.9))  # Linux down

    def set_data(self, tracks: list[Track], transitions: list[float]):
        self.tracks = tracks
        self.transitions = transitions
        self.total_duration = max(0.0, sum(t.duration for t in tracks) - sum(transitions))
        self.redraw()

    def set_playhead(self, sec: float):
        self.playhead_sec = clamp(sec, 0.0, max(0.01, self.total_duration))
        self.redraw()

    def _on_wheel_zoom(self, e):
        if e.delta > 0:
            self._zoom(1.1)
        else:
            self._zoom(0.9)

    def _zoom(self, factor: float):
        self.pixels_per_sec = clamp(self.pixels_per_sec * factor, 20.0, 400.0)
        self.redraw()

    def redraw(self):
        self.delete("all")
        w = max(10, self.winfo_width())
        h = max(10, self.winfo_height())

        # Draw timeline background grid
        secs_visible = w / self.pixels_per_sec
        tick = 5 if self.pixels_per_sec < 60 else 1
        for s in range(0, int(secs_visible) + 2, tick):
            x = int(s * self.pixels_per_sec)
            self.create_line(x, 0, x, h, fill="#1d2328")
            self.create_text(x + 4, 10, anchor="nw", fill="#6b7785", text=f"{s}s", font=("Segoe UI", 9))

        # Nothing to draw
        if not self.tracks:
            self.create_text(w//2, h//2, fill="#6b7785", text="Drop audio files here (or use Import…)", font=("Segoe UI", 14))
            return

        # Layout per track row
        row_h = max(60, (h - 40) // max(1, len(self.tracks)))
        y0 = 30

        # Compute each track's start time in the mix timeline
        starts = []
        tcur = 0.0
        for i, tr in enumerate(self.tracks):
            starts.append(tcur)
            if i < len(self.transitions):
                tcur += tr.duration - self.transitions[i]
            else:
                tcur += tr.duration

        # Draw waveforms + transition markers
        for i, tr in enumerate(self.tracks):
            top = y0 + i * row_h
            mid = top + row_h // 2
            self.create_text(8, top + 4, anchor="nw", fill="#cbd5df",
                             text=f"{i+1}. {Path(tr.path).name}  ({tr.duration:.1f}s)",
                             font=("Segoe UI", 10, "bold"))

            # track rectangle
            x_start = int(starts[i] * self.pixels_per_sec)
            x_end = int((starts[i] + tr.duration) * self.pixels_per_sec)
            self.create_rectangle(x_start, top + 18, x_end, top + row_h - 8, outline="#2a323a")

            # waveform
            wf = tr.waveform
            if wf is not None and wf.size > 10:
                # wf is normalized 0..1 abs envelope
                usable_h = (row_h - 34) // 2
                x0 = x_start
                x1 = x_end
                width = max(1, x1 - x0)
                # sample wf to canvas width for this track
                idx = np.linspace(0, wf.size - 1, num=width, dtype=np.int32)
                vals = wf[idx]
                for px in range(width):
                    amp = int(vals[px] * usable_h)
                    self.create_line(x0 + px, mid - amp, x0 + px, mid + amp, fill="#3aa7ff")

            # transition marker (between i and i+1)
            if i < len(self.transitions):
                fade = self.transitions[i]
                fade_start = starts[i] + (tr.duration - fade)
                x = int(fade_start * self.pixels_per_sec)
                self.create_line(x, top + 18, x, top + row_h - 8, fill="#ffb020", width=2)
                self.create_text(x + 4, top + 20, anchor="nw", fill="#ffb020",
                                 text=f"xfade {fade:.1f}s", font=("Segoe UI", 9, "bold"))

        # Playhead
        phx = int(self.playhead_sec * self.pixels_per_sec)
        self.create_line(phx, 0, phx, h, fill="#ff4d4d", width=2)
        self.create_text(phx + 6, 2, anchor="nw", fill="#ff4d4d", text="▶", font=("Segoe UI", 14, "bold"))


class AppBase(tk.Tk):
    pass


class AppDND(TkinterDnD.Tk):  # type: ignore
    pass


class App(AppDND if DND_OK else AppBase):
    def __init__(self):
        super().__init__()
        self.title("DJ AutoMix — Tkinter Prototype")
        self.geometry("1100x680")
        self.minsize(980, 620)

        self.ffmpeg_path = tk.StringVar(value="ffmpeg")
        self.ffplay_path = tk.StringVar(value="ffplay")
        self.out_format = tk.StringVar(value="wav")
        self.out_path = tk.StringVar(value=str(Path.cwd() / "mixes" / "mix.wav"))
        self.status = tk.StringVar(value="Ready")

        # Tracks and transitions
        self.tracks: list[Track] = []
        self.transitions: list[float] = []  # len = len(tracks)-1, per-boundary crossfade seconds

        # Playback
        self.player = FFPlayer()
        self.mix_preview_path: str | None = None
        self._playhead_job = None
        self._playhead_start_time = None
        self._playhead_base = 0.0

        self._ui()

        if DND_OK:
            self._enable_dnd()

    # ---------- UI ----------
    def _ui(self):
        root = ttk.Frame(self, padding=12)
        root.pack(fill="both", expand=True)

        # Settings panel
        settings = ttk.LabelFrame(root, text="Settings", padding=10)
        settings.pack(fill="x")

        ttk.Label(settings, text="FFmpeg:").grid(row=0, column=0, sticky="w")
        ttk.Entry(settings, textvariable=self.ffmpeg_path, width=50).grid(row=0, column=1, padx=8, sticky="we")
        ttk.Button(settings, text="Set…", command=self.pick_ffmpeg).grid(row=0, column=2, padx=(0, 6))

        ttk.Label(settings, text="FFplay:").grid(row=0, column=3, sticky="w")
        ttk.Entry(settings, textvariable=self.ffplay_path, width=35).grid(row=0, column=4, padx=8, sticky="we")
        ttk.Button(settings, text="Set…", command=self.pick_ffplay).grid(row=0, column=5, padx=(0, 6))

        ttk.Button(settings, text="Test FFmpeg", command=self.test_ffmpeg).grid(row=0, column=6, padx=(6, 0))
        ttk.Button(settings, text="Test FFplay", command=self.test_ffplay).grid(row=0, column=7, padx=(6, 0))

        ttk.Label(settings, text="Output format:").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Combobox(
            settings,
            textvariable=self.out_format,
            values=["wav", "mp3"],
            state="readonly",
            width=10,
        ).grid(row=1, column=1, sticky="w", padx=8, pady=(10, 0))

        settings.columnconfigure(1, weight=1)
        settings.columnconfigure(4, weight=1)

        # Main layout
        main = ttk.Frame(root)
        main.pack(fill="both", expand=True, pady=(12, 0))

        left = ttk.LabelFrame(main, text="Tracks", padding=10)
        left.pack(side="left", fill="y")

        self.listbox = tk.Listbox(left, selectmode=tk.EXTENDED, width=45, height=18)
        self.listbox.pack(fill="y", expand=False)

        btns = ttk.Frame(left)
        btns.pack(fill="x", pady=(8, 0))

        ttk.Button(btns, text="Import…", command=self.add_tracks).pack(side="left")
        ttk.Button(btns, text="Remove", command=self.remove_selected).pack(side="left", padx=6)
        ttk.Button(btns, text="Clear", command=self.clear_tracks).pack(side="left", padx=6)

        ttk.Button(btns, text="Up", command=lambda: self.move(-1)).pack(side="right")
        ttk.Button(btns, text="Down", command=lambda: self.move(1)).pack(side="right", padx=6)

        # Transition editor
        trans_box = ttk.LabelFrame(left, text="Transitions (between tracks)", padding=10)
        trans_box.pack(fill="x", pady=(10, 0))
        self.trans_frame = ttk.Frame(trans_box)
        self.trans_frame.pack(fill="x")

        ttk.Button(trans_box, text="Smart transitions (auto)", command=self.smart_transitions).pack(fill="x", pady=(8, 0))

        # Right side: waveform + transport + export + log
        right = ttk.Frame(main)
        right.pack(side="right", fill="both", expand=True, padx=(12, 0))

        transport = ttk.LabelFrame(right, text="Transport", padding=10)
        transport.pack(fill="x")

        ttk.Button(transport, text="▶ Play mix preview", command=self.play_mix_preview).pack(side="left")
        ttk.Button(transport, text="■ Stop", command=self.stop_playback).pack(side="left", padx=8)

        ttk.Button(transport, text="▶ Play selected track", command=self.play_selected_track).pack(side="left", padx=18)

        ttk.Label(transport, text="Zoom: mouse wheel").pack(side="right")

        # Waveform view
        self.wave = WaveformCanvas(right, height=320)
        self.wave.pack(fill="both", expand=False, pady=(10, 0))
        self.wave.bind("<Configure>", lambda e: self.wave.redraw())

        # Export panel
        export = ttk.LabelFrame(right, text="Export", padding=10)
        export.pack(fill="x", pady=(10, 0))

        ttk.Entry(export, textvariable=self.out_path).pack(fill="x")
        ttk.Button(export, text="Browse…", command=self.pick_output).pack(fill="x", pady=6)
        ttk.Button(export, text="🚀 Export final mix", command=self.export_mix).pack(fill="x")

        # Log
        logbox = ttk.LabelFrame(right, text="Log", padding=10)
        logbox.pack(fill="both", expand=True, pady=(10, 0))

        self.log = tk.Text(logbox, height=10, wrap="word")
        self.log.pack(fill="both", expand=True)

        ttk.Label(self, textvariable=self.status).pack(anchor="w", padx=12, pady=6)

    def _enable_dnd(self):
        # Make waveform accept drops
        self.wave.drop_target_register(DND_FILES)
        self.wave.dnd_bind("<<Drop>>", self._on_drop_files)

        # Also allow drop on listbox
        self.listbox.drop_target_register(DND_FILES)
        self.listbox.dnd_bind("<<Drop>>", self._on_drop_files)

        self.log_msg("Drag & drop enabled (tkinterdnd2).")

    # ---------- Helpers ----------
    def log_msg(self, msg: str):
        self.log.insert("end", msg + "\n")
        self.log.see("end")

    def _run(self, cmd: list[str], check=True) -> subprocess.CompletedProcess:
        self.log_msg(">>> " + quote_cmd(cmd))
        p = subprocess.run(cmd, capture_output=True, text=True)
        if check and p.returncode != 0:
            raise RuntimeError(p.stderr.strip() or p.stdout.strip() or f"Command failed: {p.returncode}")
        return p

    def pick_ffmpeg(self):
        p = filedialog.askopenfilename(title="Select ffmpeg")
        if p:
            self.ffmpeg_path.set(p)
            self.log_msg(f"FFmpeg set: {p}")

    def pick_ffplay(self):
        p = filedialog.askopenfilename(title="Select ffplay")
        if p:
            self.ffplay_path.set(p)
            self.log_msg(f"FFplay set: {p}")

    def test_ffmpeg(self):
        try:
            p = subprocess.run([self.ffmpeg_path.get(), "-version"], capture_output=True, text=True)
            if p.returncode == 0:
                messagebox.showinfo("FFmpeg", "FFmpeg OK")
            else:
                raise RuntimeError(p.stderr)
        except Exception as e:
            messagebox.showerror("FFmpeg", str(e))

    def test_ffplay(self):
        try:
            p = subprocess.run([self.ffplay_path.get(), "-version"], capture_output=True, text=True)
            if p.returncode == 0:
                messagebox.showinfo("FFplay", "FFplay OK")
            else:
                raise RuntimeError(p.stderr)
        except Exception as e:
            messagebox.showerror("FFplay", str(e))

    # ---------- Drag & Drop ----------
    def _on_drop_files(self, event):
        paths = parse_dnd_files(event.data)
        self._add_paths(paths)

    # ---------- Tracks management ----------
    def add_tracks(self):
        paths = filedialog.askopenfilenames(
            title="Select audio files",
            filetypes=[("Audio", "*.wav *.mp3 *.flac *.m4a *.ogg *.aac *.aif *.aiff")],
        )
        self._add_paths(list(paths))

    def _add_paths(self, paths: list[str]):
        good = []
        for p in paths:
            if not p:
                continue
            if p.lower().endswith(AUDIO_EXTS) and Path(p).exists():
                good.append(p)

        if not good:
            return

        self.status.set("Importing… (probing files)")
        self.log_msg(f"Adding {len(good)} file(s)…")

        def work():
            try:
                for p in good:
                    tr = Track(path=p)
                    self._probe_track(tr)
                    tr.waveform = self._build_waveform(tr.path, seconds=60.0)  # display proxy
                    self.tracks.append(tr)
                    self.listbox.insert("end", p)

                # transitions default (smart)
                self.smart_transitions(rebuild_only=True)

                self._refresh_transition_ui()
                self._refresh_wave()
                self.status.set(f"Tracks: {len(self.tracks)}")
            except Exception as e:
                self.status.set("Failed")
                messagebox.showerror("Import failed", str(e))

        threading.Thread(target=work, daemon=True).start()

    def remove_selected(self):
        sel = list(self.listbox.curselection())
        if not sel:
            return
        for i in reversed(sel):
            self.listbox.delete(i)
            del self.tracks[i]

        self.smart_transitions(rebuild_only=True)
        self._refresh_transition_ui()
        self._refresh_wave()

    def clear_tracks(self):
        self.stop_playback()
        self.listbox.delete(0, "end")
        self.tracks.clear()
        self.transitions.clear()
        self._refresh_transition_ui()
        self._refresh_wave()

    def move(self, direction: int):
        sel = list(self.listbox.curselection())
        if not sel or len(sel) != 1:
            return
        i = sel[0]
        j = clamp(i + direction, 0, len(self.tracks) - 1)
        if i == j:
            return

        # swap
        self.tracks[i], self.tracks[j] = self.tracks[j], self.tracks[i]

        # update listbox
        items = [self.listbox.get(k) for k in range(self.listbox.size())]
        items[i], items[j] = items[j], items[i]
        self.listbox.delete(0, "end")
        for it in items:
            self.listbox.insert("end", it)

        self.listbox.selection_clear(0, "end")
        self.listbox.selection_set(j)

        self.smart_transitions(rebuild_only=True)
        self._refresh_transition_ui()
        self._refresh_wave()

    # ---------- Probing + waveform ----------
    def _probe_track(self, tr: Track):
        # Use ffprobe via ffmpeg -i parsing (portable without requiring ffprobe)
        cmd = [self.ffmpeg_path.get(), "-hide_banner", "-i", tr.path]
        p = subprocess.run(cmd, capture_output=True, text=True)
        text = (p.stderr or "") + "\n" + (p.stdout or "")

        # duration parse
        dur = 0.0
        for line in text.splitlines():
            line = line.strip()
            if line.startswith("Duration:"):
                # Duration: 00:03:25.12, start: 0.000000, bitrate: 320 kb/s
                try:
                    t = line.split("Duration:")[1].split(",")[0].strip()
                    hh, mm, ss = t.split(":")
                    dur = int(hh) * 3600 + int(mm) * 60 + float(ss)
                    break
                except Exception:
                    pass
        tr.duration = dur if dur > 0 else 0.0

        # Try to parse sample rate and channels roughly
        sr = 44100
        ch = 2
        for line in text.splitlines():
            if "Audio:" in line and "Hz" in line:
                # ... 44100 Hz, stereo, ...
                try:
                    parts = line.split(",")
                    for part in parts:
                        part = part.strip()
                        if part.endswith("Hz"):
                            sr = int(part.replace("Hz", "").strip())
                        if part in ("mono", "stereo"):
                            ch = 1 if part == "mono" else 2
                except Exception:
                    pass
        tr.sr = sr
        tr.channels = ch

        self.log_msg(f"Probed: {Path(tr.path).name}  duration={tr.duration:.2f}s  sr={tr.sr}  ch={tr.channels}")

    def _build_waveform(self, path: str, seconds: float = 60.0) -> np.ndarray:
        """
        Create a downsampled absolute amplitude envelope for drawing.
        We decode (up to `seconds`) into raw s16le mono and compute RMS window envelope.
        """
        # Decode the first N seconds (fast enough for UI)
        # -ac 1 for mono, -f s16le for raw PCM
        cmd = [
            self.ffmpeg_path.get(), "-hide_banner", "-loglevel", "error",
            "-i", path,
            "-t", f"{seconds:.3f}",
            "-vn",
            "-ac", "1",
            "-ar", "22050",
            "-f", "s16le",
            "pipe:1",
        ]
        p = subprocess.run(cmd, capture_output=True)
        if p.returncode != 0 or not p.stdout:
            return None

        pcm = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32)
        if pcm.size < 1000:
            return None

        # Envelope: RMS over windows
        win = 1024
        n = pcm.size // win
        pcm = pcm[: n * win].reshape(n, win)
        rms = np.sqrt(np.mean(pcm * pcm, axis=1))
        rms = rms / (np.max(rms) + 1e-9)
        return rms.astype(np.float32)

    # ---------- Transition logic ----------
    def smart_transitions(self, rebuild_only: bool = False):
        """
        Smart default transitions:
        - Choose crossfade length per boundary based on end energy of track i and start energy of track i+1.
        - If both are energetic -> longer fade; if quiet -> shorter fade.
        - Clamp to safe bounds and not longer than 20% of either track duration.
        """
        if len(self.tracks) < 2:
            self.transitions = []
            self._refresh_transition_ui()
            self._refresh_wave()
            return

        self.status.set("Computing smart transitions…")

        def energy_tail(path: str, tail_sec: float = 25.0) -> float:
            # RMS over last tail_sec seconds
            cmd = [
                self.ffmpeg_path.get(), "-hide_banner", "-loglevel", "error",
                "-sseof", f"-{tail_sec:.3f}",
                "-i", path,
                "-vn",
                "-ac", "1",
                "-ar", "22050",
                "-t", f"{tail_sec:.3f}",
                "-f", "s16le",
                "pipe:1",
            ]
            p = subprocess.run(cmd, capture_output=True)
            if p.returncode != 0 or not p.stdout:
                return 0.2
            pcm = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32)
            if pcm.size < 2000:
                return 0.2
            rms = float(np.sqrt(np.mean(pcm * pcm)) / 32768.0)
            return clamp(rms, 0.0, 1.0)

        def energy_head(path: str, head_sec: float = 25.0) -> float:
            cmd = [
                self.ffmpeg_path.get(), "-hide_banner", "-loglevel", "error",
                "-i", path,
                "-vn",
                "-ac", "1",
                "-ar", "22050",
                "-t", f"{head_sec:.3f}",
                "-f", "s16le",
                "pipe:1",
            ]
            p = subprocess.run(cmd, capture_output=True)
            if p.returncode != 0 or not p.stdout:
                return 0.2
            pcm = np.frombuffer(p.stdout, dtype=np.int16).astype(np.float32)
            if pcm.size < 2000:
                return 0.2
            rms = float(np.sqrt(np.mean(pcm * pcm)) / 32768.0)
            return clamp(rms, 0.0, 1.0)

        def work():
            try:
                new = []
                for i in range(len(self.tracks) - 1):
                    a = self.tracks[i]
                    b = self.tracks[i + 1]

                    # If we are only rebuilding list length (after remove/reorder), keep existing where possible
                    if rebuild_only and i < len(self.transitions):
                        new.append(self.transitions[i])
                        continue

                    e1 = energy_tail(a.path)
                    e2 = energy_head(b.path)

                    # map energies to a fade length
                    # quiet -> short; loud -> longer
                    base = 4.0 + 10.0 * ((e1 + e2) / 2.0)  # 4..14 sec typical
                    # bonus if outgoing is quieter than incoming (classic DJ handover)
                    if e1 < e2:
                        base += 2.0

                    # hard constraints
                    max_allowed = min(20.0, 0.2 * a.duration if a.duration else 20.0, 0.2 * b.duration if b.duration else 20.0)
                    fade = clamp(base, 2.0, max(2.0, max_allowed))
                    new.append(float(fade))

                self.transitions = new
                self.log_msg("Smart transitions set: " + ", ".join(f"{x:.1f}s" for x in self.transitions))
                self._refresh_transition_ui()
                self._refresh_wave()
                self.status.set("Ready")
            except Exception as e:
                self.status.set("Failed")
                messagebox.showerror("Smart transitions failed", str(e))

        threading.Thread(target=work, daemon=True).start()

    def _refresh_transition_ui(self):
        # clear
        for child in self.trans_frame.winfo_children():
            child.destroy()

        if len(self.tracks) < 2:
            ttk.Label(self.trans_frame, text="Add 2+ tracks to edit transitions.").pack(anchor="w")
            return

        # create per-boundary sliders
        for i in range(len(self.tracks) - 1):
            row = ttk.Frame(self.trans_frame)
            row.pack(fill="x", pady=3)

            name = f"{i+1}→{i+2}"
            ttk.Label(row, text=name, width=6).pack(side="left")

            var = tk.DoubleVar(value=self.transitions[i] if i < len(self.transitions) else 8.0)

            def on_change(_v, idx=i, v=var):
                self.transitions[idx] = float(v.get())
                self._refresh_wave()

            scale = ttk.Scale(row, from_=2, to=20, variable=var, orient="horizontal", command=on_change)
            scale.pack(side="left", fill="x", expand=True, padx=6)

            lbl = ttk.Label(row, text=f"{var.get():.1f}s", width=6)
            lbl.pack(side="left")

            def update_label(*_args, v=var, l=lbl):
                l.config(text=f"{v.get():.1f}s")

            var.trace_add("write", update_label)

    def _refresh_wave(self):
        self.wave.set_data(self.tracks, self.transitions)

    # ---------- Playback ----------
    def play_selected_track(self):
        sel = list(self.listbox.curselection())
        if len(sel) != 1:
            messagebox.showinfo("Play track", "Select exactly one track in the list.")
            return
        idx = sel[0]
        tr = self.tracks[idx]
        self.status.set(f"Playing: {Path(tr.path).name}")
        self.player.play_file(self.ffplay_path.get(), tr.path)

    def stop_playback(self):
        self.player.stop()
        self.status.set("Stopped")
        self._stop_playhead_timer()

    def _stop_playhead_timer(self):
        if self._playhead_job is not None:
            try:
                self.after_cancel(self._playhead_job)
            except Exception:
                pass
        self._playhead_job = None
        self._playhead_start_time = None
        self._playhead_base = 0.0
        self.wave.set_playhead(0.0)

    def _start_playhead_timer(self, total_duration: float):
        self._stop_playhead_timer()
        self._playhead_start_time = time.time()
        self._playhead_base = 0.0

        def tick():
            if self._playhead_start_time is None:
                return
            t = time.time() - self._playhead_start_time + self._playhead_base
            self.wave.set_playhead(min(t, total_duration))
            if t < total_duration and (self.player.proc and self.player.proc.poll() is None):
                self._playhead_job = self.after(60, tick)
            else:
                self._playhead_job = None

        tick()

    def play_mix_preview(self):
        if len(self.tracks) < 2:
            messagebox.showwarning("Preview", "Add at least 2 tracks.")
            return

        self.status.set("Building preview…")

        def work():
            try:
                tmpdir = Path(tempfile.gettempdir())
                out = tmpdir / f"tk_automix_preview_{int(time.time())}.wav"
                self._build_mix(str(out), preview=True)
                self.mix_preview_path = str(out)
                self.status.set("Playing mix preview…")
                self.player.play_file(self.ffplay_path.get(), str(out))
                self._start_playhead_timer(self.wave.total_duration)
            except Exception as e:
                self.status.set("Failed")
                messagebox.showerror("Preview failed", str(e))

        threading.Thread(target=work, daemon=True).start()

    # ---------- Export ----------
    def pick_output(self):
        fmt = self.out_format.get()
        p = filedialog.asksaveasfilename(
            defaultextension=f".{fmt}",
            filetypes=[("Audio", f"*.{fmt}")],
        )
        if p:
            self.out_path.set(p)

    def export_mix(self):
        if len(self.tracks) < 2:
            messagebox.showwarning("Export", "Add at least 2 tracks.")
            return

        out = Path(self.out_path.get())
        out.parent.mkdir(parents=True, exist_ok=True)

        def work():
            try:
                self.status.set("Exporting…")
                self._build_mix(str(out), preview=False)
                self.status.set("Done")
                messagebox.showinfo("Export", f"Saved:\n{out}")
            except Exception as e:
                self.status.set("Failed")
                messagebox.showerror("Export failed", str(e))

        threading.Thread(target=work, daemon=True).start()

    # ---------- Mix building (FFmpeg) ----------
    def _build_mix(self, out_path: str, preview: bool):
        """
        Build a full mix using iterative acrossfade, with per-boundary durations.
        This is robust and simple.
        """
        tracks = [t.path for t in self.tracks]
        fades = list(self.transitions)

        # Safety checks
        if len(tracks) < 2:
            raise ValueError("Need at least 2 tracks")
        if len(fades) != len(tracks) - 1:
            # fallback to 8 sec
            fades = [8.0] * (len(tracks) - 1)

        # Build filter_complex
        # acrossfade expects both streams and produces a single output stream label.
        parts = []
        # First pair
        parts.append(f"[0:a][1:a]acrossfade=d={fades[0]:.3f}:c1=tri:c2=tri[a01];")
        # Remaining
        for i in range(2, len(tracks)):
            prev = "a01" if i == 2 else f"a0{i-1}"
            cur = f"a0{i}"
            parts.append(f"[{prev}][{i}:a]acrossfade=d={fades[i-1]:.3f}:c1=tri:c2=tri[{cur}];")

        last = "a01" if len(tracks) == 2 else f"a0{len(tracks)-1}"
        filt = "".join(parts)

        cmd = [self.ffmpeg_path.get(), "-y"]
        for t in tracks:
            cmd += ["-i", t]
        cmd += ["-filter_complex", filt, "-map", f"[{last}]"]

        # Output codec params
        fmt = Path(out_path).suffix.lower().lstrip(".")
        if fmt == "mp3":
            cmd += ["-ar", "44100", "-ac", "2", "-b:a", "192k"]
        else:
            cmd += ["-ar", "44100", "-ac", "2"]

        # Preview can be shortened to first N seconds
        if preview:
            cmd += ["-t", "180"]  # 3 minutes preview cap (change if you want)

        cmd += [out_path]

        self._run(cmd, check=True)

    # ---------- Exit ----------
    def destroy(self):
        try:
            self.player.stop()
        except Exception:
            pass
        super().destroy()


if __name__ == "__main__":
    App().mainloop()