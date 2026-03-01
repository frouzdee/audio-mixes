#!/usr/bin/env python3
"""
DJ AutoMix  –  Complete Rewrite

Dependencies:
    pip install numpy soundfile sounddevice tkinterdnd2

Optional (recommended):
    pip install librosa          # BPM + key detection
    ffmpeg in PATH              # MP3 / M4A / AAC import & MP3 export

Features:
  • Drag & drop audio files onto the timeline or track list
  • Zoomable, scrollable waveform timeline (Ctrl+Wheel or +/- keys)
  • Draggable crossfade markers between tracks
  • Click timeline to set playhead; drag playhead to seek
  • Double-click track to split; right-click for trim / split / remove
  • Smart auto-transitions based on tail/head energy analysis
  • Per-transition sliders in the sidebar
  • Sounddevice-based playback (sample-accurate, no ffplay needed)
  • BPM & key detection via librosa (runs in background)
  • Export to WAV or MP3 (MP3 needs ffmpeg)
  • Keyboard: Space = play/stop  |  +/- = zoom  |  ←/→ = seek 5s
"""

# ── stdlib ───────────────────────────────────────────────────────────────────
import os
import sys
import threading
import time
import tempfile
import subprocess
import shlex
import math
from dataclasses import dataclass, field
from pathlib import Path

# ── GUI ──────────────────────────────────────────────────────────────────────
import tkinter as tk
from tkinter import ttk, filedialog, messagebox, simpledialog

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    DND_OK = True
except ImportError:
    TkinterDnD = None
    DND_FILES = None
    DND_OK = False

# ── Audio ────────────────────────────────────────────────────────────────────
import numpy as np
import soundfile as sf
import sounddevice as sd

try:
    import librosa
    LIBROSA_OK = True
except ImportError:
    librosa = None
    LIBROSA_OK = False

# ─────────────────────────────────────────────────────────────────────────────
AUDIO_EXTS = {'.wav', '.flac', '.ogg', '.aiff', '.aif', '.mp3', '.m4a', '.aac'}

# Timeline layout constants
RULER_H   = 28      # height of time ruler (pixels)
TRACK_H   = 90      # height of each track row (pixels)
MIN_PPS   = 8.0     # min pixels-per-second (zoomed out)
MAX_PPS   = 600.0   # max pixels-per-second (zoomed in)
XFADE_HIT = 12      # pixel radius for crossfade marker hit-test

# Color palette
BG        = '#101214'
RULER_BG  = '#0c0e10'
TRACK_BG  = '#141c28'
TRACK_BOR = '#2a3545'
WAVE_FILL = '#1c4a7a'
WAVE_LINE = '#3aa7ff'
XFADE_COL = '#ffb020'
XFADE_BG  = '#2a1500'
PH_COL    = '#ff4d4d'
TEXT_COL  = '#c8d8e8'
DIM_COL   = '#4a5565'
GRID_COL  = '#1e2530'
SEL_COL   = '#243050'

# ─────────────────────────────────────────────────────────────────────────────
# Transition types
# ─────────────────────────────────────────────────────────────────────────────

# Each entry: display name, color on timeline, short description
TRANS_TYPES = {
    'equal_power': ('Equal Power',   '#ffb020', 'Cosine curves — sounds natural, default DJ fade'),
    'linear':      ('Linear',        '#e8e840', 'Straight ramp — simple and predictable'),
    'smooth':      ('S-Curve',       '#40d080', 'Smoothstep — gentle start & end, minimal harshness'),
    'echo':        ('Echo Out',      '#c060ff', 'Outgoing track decays exponentially like an echo'),
    'silence':     ('Fade to Black', '#4aabff', 'Both tracks fade through silence — dramatic pause'),
    'cut':         ('Hard Cut',      '#ff4a4a', 'Instant switch, no overlap — use for beat-matched drops'),
}

def trans_name(key: str) -> str:
    return TRANS_TYPES.get(key, TRANS_TYPES['equal_power'])[0]

def trans_color(key: str) -> str:
    return TRANS_TYPES.get(key, TRANS_TYPES['equal_power'])[1]

# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def fmt_time(sec: float) -> str:
    sec = max(0.0, sec)
    m = int(sec) // 60
    return f"{m}:{sec - m * 60:05.2f}"


def parse_dnd(data: str) -> list:
    """Parse tkinterdnd2 drop data into file paths."""
    out, cur, brace = [], '', False
    for c in data:
        if c == '{':
            brace = True; cur = ''
        elif c == '}':
            brace = False
            if cur:
                out.append(cur)
            cur = ''
        elif c.isspace() and not brace:
            if cur:
                out.append(cur)
            cur = ''
        else:
            cur += c
    if cur:
        out.append(cur)
    return out


def load_audio(path: str, ffmpeg: str = 'ffmpeg') -> tuple:
    """
    Load audio file → (float32 ndarray shape (N, 2), sample_rate).
    Uses soundfile for WAV/FLAC/OGG/AIFF; falls back to ffmpeg for MP3/M4A/AAC.
    """
    ext = Path(path).suffix.lower()
    if ext not in {'.mp3', '.m4a', '.aac'}:
        try:
            data, sr = sf.read(path, always_2d=True)
            arr = data.astype(np.float32)
            if arr.shape[1] == 1:
                arr = np.repeat(arr, 2, axis=1)
            return arr, sr
        except Exception:
            pass

    # Fallback: ffmpeg → raw interleaved float32 stereo
    cmd = [
        ffmpeg, '-hide_banner', '-loglevel', 'error',
        '-i', path, '-vn', '-ac', '2', '-ar', '44100',
        '-f', 'f32le', 'pipe:1',
    ]
    try:
        p = subprocess.run(cmd, capture_output=True, timeout=120)
    except FileNotFoundError:
        raise RuntimeError(
            f"Cannot load '{Path(path).name}'.\n"
            "soundfile could not read it and ffmpeg was not found.\n"
            "Install ffmpeg and add it to PATH, or use WAV/FLAC/OGG files."
        )
    if p.returncode != 0 or not p.stdout:
        raise RuntimeError(
            f"ffmpeg failed to decode '{Path(path).name}':\n"
            f"{p.stderr.decode(errors='replace')[:300]}"
        )
    arr = np.frombuffer(p.stdout, dtype=np.float32).reshape(-1, 2)
    return arr.copy(), 44100


# ─────────────────────────────────────────────────────────────────────────────
# Track
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class Track:
    path: str
    data: np.ndarray = field(default=None, repr=False)
    sr: int = 44100
    channels: int = 2
    duration: float = 0.0          # full file duration
    trim_start: float = 0.0        # trim in-point (seconds into raw audio)
    trim_end: float = 0.0          # trim out-point
    waveform: np.ndarray = field(default=None, repr=False)  # normalized RMS envelope
    bpm: float = None
    key: str = None

    # ── loading ──────────────────────────────────────────────────────────────

    def load(self, ffmpeg: str = 'ffmpeg'):
        self.data, self.sr = load_audio(self.path, ffmpeg)
        self.channels = self.data.shape[1]
        self.duration = self.data.shape[0] / self.sr
        self.trim_start = 0.0
        self.trim_end = self.duration
        self._compute_waveform()

    # ── segment helpers ──────────────────────────────────────────────────────

    @property
    def seg_dur(self) -> float:
        return max(0.0, self.trim_end - self.trim_start)

    def get_segment(self) -> np.ndarray:
        s = int(self.trim_start * self.sr)
        e = int(self.trim_end * self.sr)
        return self.data[s:e]

    # ── waveform envelope ─────────────────────────────────────────────────────

    def _compute_waveform(self, resolution: int = 2000):
        seg = self.get_segment()
        if seg.size < 4:
            self.waveform = None
            return
        mono = np.mean(seg, axis=1)
        hop = max(1, mono.size // resolution)
        n = mono.size // hop
        blocks = mono[: n * hop].reshape(n, hop)
        env = np.sqrt(np.mean(blocks ** 2, axis=1))
        mx = env.max()
        if mx > 1e-9:
            env /= mx
        self.waveform = env.astype(np.float32)

    # ── editing ───────────────────────────────────────────────────────────────

    def split(self, at_seg_sec: float):
        """Split at `at_seg_sec` seconds from trim_start. Returns (left, right) or None."""
        abs_t = self.trim_start + clamp(at_seg_sec, 0.001, self.seg_dur - 0.001)
        if abs_t <= self.trim_start or abs_t >= self.trim_end:
            return None
        left = Track(
            path=self.path, data=self.data, sr=self.sr, channels=self.channels,
            duration=self.duration, trim_start=self.trim_start, trim_end=abs_t,
        )
        right = Track(
            path=self.path, data=self.data, sr=self.sr, channels=self.channels,
            duration=self.duration, trim_start=abs_t, trim_end=self.trim_end,
        )
        left._compute_waveform()
        right._compute_waveform()
        return left, right

    # ── analysis (librosa) ────────────────────────────────────────────────────

    def analyze(self):
        if not LIBROSA_OK:
            return
        try:
            y = np.mean(self.data, axis=1).astype(np.float32)
            tempo, _ = librosa.beat.beat_track(y=y, sr=self.sr)
            self.bpm = float(np.atleast_1d(tempo)[0])
            chroma = librosa.feature.chroma_cqt(y=y, sr=self.sr)
            keys = ['C', 'C#', 'D', 'D#', 'E', 'F', 'F#', 'G', 'G#', 'A', 'A#', 'B']
            self.key = keys[int(np.argmax(np.mean(chroma, axis=1)))]
        except Exception:
            pass


# ─────────────────────────────────────────────────────────────────────────────
# Audio Engine
# ─────────────────────────────────────────────────────────────────────────────

class AudioEngine:
    """
    Sounddevice-based playback engine.
    Thread-safe: play/stop from any thread.
    """

    def __init__(self):
        self._stream: sd.OutputStream = None
        self._data: np.ndarray = None
        self._sr: int = 44100
        self._pos: int = 0
        self._stopping: bool = False
        self.volume: float = 1.0
        self.finished_cb = None          # called from audio thread when playback ends

    @property
    def position(self) -> float:
        return self._pos / self._sr if self._sr else 0.0

    @property
    def is_playing(self) -> bool:
        return self._stream is not None and self._stream.active and not self._stopping

    def play(self, arr: np.ndarray, sr: int, start_sec: float = 0.0):
        self.stop()
        if arr.ndim == 1:
            arr = arr[:, np.newaxis]
        if arr.shape[1] == 1:
            arr = np.repeat(arr, 2, axis=1)
        self._data = arr.astype(np.float32)
        self._sr = sr
        self._pos = int(clamp(start_sec * sr, 0, arr.shape[0] - 1))
        self._stopping = False

        def _cb(outdata, frames, _time, _status):
            if self._stopping:
                outdata[:] = 0
                raise sd.CallbackStop()
            chunk = self._data[self._pos: self._pos + frames]
            vol = self.volume
            n = chunk.shape[0]
            if n < frames:
                if n:
                    outdata[:n] = chunk * vol
                outdata[n:] = 0
                raise sd.CallbackStop()
            else:
                outdata[:] = chunk * vol
            self._pos += frames

        def _done():
            if self.finished_cb:
                self.finished_cb()

        self._stream = sd.OutputStream(
            samplerate=sr, channels=2, dtype='float32',
            callback=_cb, finished_callback=_done,
        )
        self._stream.start()

    def stop(self):
        self._stopping = True
        if self._stream:
            try:
                self._stream.stop()
            except Exception:
                pass
            try:
                self._stream.close()
            except Exception:
                pass
            self._stream = None
        self._stopping = False

    def seek(self, sec: float):
        if self._data is not None:
            self._pos = int(clamp(sec * self._sr, 0, self._data.shape[0] - 1))


# ─────────────────────────────────────────────────────────────────────────────
# Timeline Canvas
# ─────────────────────────────────────────────────────────────────────────────

class TimelineCanvas(tk.Canvas):
    """
    Multi-track waveform timeline.

    Interactions
    ─────────────
    Left-click          → set playhead
    Drag playhead       → seek
    Drag orange marker  → adjust crossfade duration
    Double-click        → split track at cursor
    Right-click         → context menu (split / trim / remove)
    Ctrl + Scroll       → zoom (centered on cursor)
    Scroll              → horizontal pan
    +/-  keys           → zoom in / out
    """

    def __init__(self, parent, app, hscroll, **kw):
        super().__init__(parent, bg=BG, highlightthickness=0, **kw)
        self.app = app
        self.hscroll = hscroll

        self.tracks: list = []
        self.transitions: list = []
        self.trans_types: list = []
        self.pps: float = 80.0       # pixels per second
        self.x_off: float = 0.0      # horizontal scroll offset in pixels
        self.playhead: float = 0.0   # seconds

        # After the user scrolls/zooms, suppress auto-follow for this many seconds
        self._user_scrolled_at: float = 0.0
        _FOLLOW_COOLDOWN = 2.5       # seconds of inactivity before auto-follow resumes
        self._follow_cooldown = _FOLLOW_COOLDOWN

        # drag state: None | ('playhead',) | ('xfade', i, orig_fade, orig_x)
        self._drag = None

        self.bind('<Configure>', lambda e: self.redraw())
        self.bind('<ButtonPress-1>', self._on_press)
        self.bind('<B1-Motion>', self._on_drag)
        self.bind('<ButtonRelease-1>', self._on_release)
        self.bind('<Double-Button-1>', self._on_dbl)
        self.bind('<Button-3>', self._on_rclick)
        self.bind('<Control-MouseWheel>', self._on_ctrl_wheel)
        self.bind('<MouseWheel>', self._on_wheel)
        self.bind('<Button-4>', lambda e: self._zoom(1.12, e.x))   # Linux scroll up
        self.bind('<Button-5>', lambda e: self._zoom(1 / 1.12, e.x))

    # ── coordinate helpers ────────────────────────────────────────────────────

    def _px(self, sec: float) -> float:
        return sec * self.pps - self.x_off

    def _sec(self, px: float) -> float:
        return (px + self.x_off) / self.pps

    def _track_starts(self) -> list:
        starts, t = [], 0.0
        for i, tr in enumerate(self.tracks):
            starts.append(t)
            fade = self.transitions[i] if i < len(self.transitions) else 0.0
            t += tr.seg_dur - (fade if i < len(self.tracks) - 1 else 0.0)
        return starts

    def _total_dur(self) -> float:
        if not self.tracks:
            return 0.0
        starts = self._track_starts()
        return starts[-1] + self.tracks[-1].seg_dur

    def _hit_xfade(self, x: float, y: float):
        """Return index of crossfade marker that (x,y) hits, or None."""
        starts = self._track_starts()
        for i in range(len(self.tracks) - 1):
            fade = self.transitions[i] if i < len(self.transitions) else 0.0
            if fade <= 0:
                continue
            xfade_x = self._px(starts[i] + self.tracks[i].seg_dur - fade)
            row_top = RULER_H + i * TRACK_H
            row_bot = row_top + TRACK_H * 2
            if abs(x - xfade_x) <= XFADE_HIT and row_top <= y <= row_bot:
                return i
        return None

    def _track_at(self, x: float, y: float):
        """Return (track_index, seg_offset_sec) or (None, None)."""
        starts = self._track_starts()
        ry = (y - RULER_H) / TRACK_H
        if ry < 0:
            return None, None
        i = int(ry)
        if i >= len(self.tracks):
            return None, None
        seg_sec = self._sec(x) - starts[i]
        if 0 <= seg_sec <= self.tracks[i].seg_dur:
            return i, seg_sec
        return None, None

    # ── event handlers ────────────────────────────────────────────────────────

    def _on_press(self, e):
        self.focus_set()
        xf = self._hit_xfade(e.x, e.y)
        if xf is not None:
            orig = self.transitions[xf] if xf < len(self.transitions) else 0.0
            self._drag = ('xfade', xf, orig, e.x)
            self.config(cursor='sb_h_double_arrow')
            return
        # Otherwise: move playhead
        self._drag = ('playhead',)
        self.config(cursor='crosshair')
        sec = clamp(self._sec(e.x), 0.0, max(0.0, self._total_dur()))
        self.playhead = sec
        self.app.on_playhead_seek(sec)
        self.redraw()

    def _on_drag(self, e):
        if self._drag is None:
            return
        if self._drag[0] == 'playhead':
            sec = clamp(self._sec(e.x), 0.0, max(0.0, self._total_dur()))
            self.playhead = sec
            self.app.on_playhead_seek(sec)
            self.redraw()
        elif self._drag[0] == 'xfade':
            _, i, orig_fade, orig_x = self._drag
            # dragging left increases fade, right decreases
            delta = (orig_x - e.x) / self.pps
            tr_a = self.tracks[i]
            tr_b = self.tracks[i + 1]
            max_fade = min(0.45 * tr_a.seg_dur, 0.45 * tr_b.seg_dur, 30.0)
            new_fade = clamp(orig_fade + delta, 0.0, max(0.0, max_fade))
            if i < len(self.transitions):
                self.transitions[i] = new_fade
            self.app.on_transition_drag(i, new_fade)
            self.redraw()

    def _on_release(self, e):
        self._drag = None
        self.config(cursor='')

    def _on_dbl(self, e):
        i, seg_sec = self._track_at(e.x, e.y)
        if i is not None:
            self.app.split_track(i, seg_sec)

    def _on_rclick(self, e):
        i, seg_sec = self._track_at(e.x, e.y)
        xf = self._hit_xfade(e.x, e.y)
        menu = tk.Menu(self, tearoff=0, bg='#1a2030', fg=TEXT_COL,
                       activebackground=SEL_COL, activeforeground='white',
                       bd=0, relief='flat')
        if xf is not None:
            menu.add_command(
                label=f'⏱  Set transition {xf + 1}→{xf + 2} duration…',
                command=lambda: self.app.prompt_fade(xf),
            )
            menu.add_separator()
        if i is not None:
            menu.add_command(
                label=f'✂  Split track {i + 1} here',
                command=lambda: self.app.split_track(i, seg_sec),
            )
            menu.add_command(
                label=f'◀  Trim start of track {i + 1} here',
                command=lambda: self.app.trim_track(i, seg_sec, 'start'),
            )
            menu.add_command(
                label=f'▶  Trim end of track {i + 1} here',
                command=lambda: self.app.trim_track(i, seg_sec, 'end'),
            )
            menu.add_separator()
            menu.add_command(
                label=f'✕  Remove track {i + 1}',
                command=lambda: self.app.remove_track(i),
            )
        if menu.index('end') is not None:
            menu.post(e.x_root, e.y_root)

    def _on_ctrl_wheel(self, e):
        factor = 1.15 if e.delta > 0 else (1 / 1.15)
        self._zoom(factor, e.x)

    def _on_wheel(self, e):
        self._scroll(-60 if e.delta > 0 else 60)

    # ── scroll / zoom ─────────────────────────────────────────────────────────

    def _scroll(self, dpx: float):
        self._user_scrolled_at = time.time()
        total_px = self._total_dur() * self.pps
        w = self.winfo_width()
        self.x_off = clamp(self.x_off + dpx, 0.0, max(0.0, total_px - w + 100))
        self._update_sb()
        self.redraw()

    def _zoom(self, factor: float, cx: float = None):
        self._user_scrolled_at = time.time()
        if cx is None:
            cx = self.winfo_width() / 2
        sec_at = self._sec(cx)
        self.pps = clamp(self.pps * factor, MIN_PPS, MAX_PPS)
        self.x_off = max(0.0, sec_at * self.pps - cx)
        self._update_sb()
        self.redraw()

    def zoom_in(self):
        self._zoom(1.2)

    def zoom_out(self):
        self._zoom(1 / 1.2)

    def _update_sb(self):
        total_px = max(1.0, self._total_dur() * self.pps)
        w = max(1, self.winfo_width())
        lo = self.x_off / total_px
        hi = (self.x_off + w) / total_px
        self.hscroll.set(lo, min(1.0, hi))

    def scroll_cmd(self, *args):
        """Scrollbar command handler."""
        self._user_scrolled_at = time.time()
        total_px = max(1.0, self._total_dur() * self.pps)
        if args[0] == 'moveto':
            self.x_off = float(args[1]) * total_px
        elif args[0] == 'scroll':
            n, unit = int(args[1]), args[2]
            dpx = n * (50 if unit == 'units' else self.winfo_width())
            self.x_off += dpx
        self.x_off = max(0.0, self.x_off)
        self._update_sb()
        self.redraw()

    # ── public API ────────────────────────────────────────────────────────────

    def set_data(self, tracks: list, transitions: list, trans_types: list = None):
        self.tracks = tracks
        self.transitions = transitions
        self.trans_types = trans_types or []
        self._update_sb()
        self.redraw()

    def set_playhead(self, sec: float):
        self.playhead = sec
        # Auto-follow the playhead only when the user hasn't recently scrolled/zoomed
        user_idle = time.time() - self._user_scrolled_at > self._follow_cooldown
        if user_idle:
            px = self._px(sec)
            w = self.winfo_width()
            if px < 20 or px > w - 20:
                self.x_off = max(0.0, sec * self.pps - w * 0.25)
        self.redraw()

    # ── drawing ───────────────────────────────────────────────────────────────

    def redraw(self):
        self.delete('all')
        cw = max(10, self.winfo_width())
        ch = max(10, self.winfo_height())

        # Time ruler background
        self.create_rectangle(0, 0, cw, RULER_H, fill=RULER_BG, outline='')

        if not self.tracks:
            self.create_text(
                cw // 2, ch // 2, fill=DIM_COL,
                text='Drop audio files here  •  or click Import',
                font=('Segoe UI', 14),
            )
            return

        starts = self._track_starts()

        # Grid lines + time labels
        self._draw_ruler(cw, ch)

        # Track rows
        for i, tr in enumerate(self.tracks):
            self._draw_track(i, tr, starts[i], cw)

        # Crossfade markers
        for i in range(len(self.tracks) - 1):
            self._draw_xfade(i, starts, cw)

        # Playhead (drawn last = on top)
        phx = self._px(self.playhead)
        if -2 <= phx <= cw + 2:
            self.create_line(phx, 0, phx, ch, fill=PH_COL, width=2)
            self.create_polygon(
                phx - 7, 0, phx + 7, 0, phx, 14,
                fill=PH_COL, outline='',
            )

    def _draw_ruler(self, cw: int, ch: int):
        tick = self._nice_interval()
        t = 0.0
        while True:
            x = self._px(t)
            if x > cw:
                break
            if x >= 0:
                self.create_line(x, RULER_H - 6, x, ch, fill=GRID_COL)
                self.create_line(x, RULER_H - 6, x, RULER_H, fill='#3a4550')
                self.create_text(
                    x + 3, 5, anchor='nw', fill='#6b7785',
                    text=fmt_time(t), font=('Segoe UI', 8),
                )
            t = round(t + tick, 6)

    def _nice_interval(self) -> float:
        candidates = [0.5, 1, 2, 5, 10, 15, 30, 60, 120, 300, 600]
        for c in candidates:
            if c * self.pps >= 70:
                return c
        return candidates[-1]

    def _draw_track(self, i: int, tr, start_sec: float, cw: int):
        top = RULER_H + i * TRACK_H
        bot = top + TRACK_H
        mid = (top + bot) // 2

        x0 = self._px(start_sec)
        x1 = self._px(start_sec + tr.seg_dur)
        cx0 = max(0, int(x0))
        cx1 = min(cw, int(x1) + 1)
        if cx1 <= cx0:
            return

        # Track background
        self.create_rectangle(cx0, top + 2, cx1, bot - 2, fill=TRACK_BG, outline=TRACK_BOR)

        # Track label
        name = Path(tr.path).name
        extras = []
        if tr.bpm:
            extras.append(f'{tr.bpm:.0f} BPM')
        if tr.key:
            extras.append(tr.key)
        label = name + ('  ' + '  '.join(extras) if extras else '')
        self.create_text(
            cx0 + 8, top + 8, anchor='nw', fill=TEXT_COL,
            text=label, font=('Segoe UI', 9, 'bold'),
        )

        # Duration label (right side)
        dur_str = fmt_time(tr.seg_dur)
        self.create_text(
            cx1 - 6, bot - 8, anchor='se', fill=DIM_COL,
            text=dur_str, font=('Segoe UI', 8),
        )

        # Waveform
        if tr.waveform is not None and tr.waveform.size > 2:
            wave_h = max(4, (TRACK_H - 32) // 2)
            wf = tr.waveform
            vis_x0 = max(0, int(x0))
            vis_x1 = min(cw, int(x1) + 1)
            width_px = max(1, x1 - x0)

            top_pts = []
            bot_pts = []
            step = max(1, (vis_x1 - vis_x0) // 1500)  # limit point count for speed
            for px in range(vis_x0, vis_x1, step):
                frac = clamp((px - x0) / width_px, 0.0, 1.0)
                wf_i = int(frac * (wf.size - 1))
                amp = int(wf[wf_i] * wave_h)
                top_pts += [px, mid - amp]
                bot_pts += [px, mid + amp]

            if len(top_pts) >= 4:
                # Reverse bot_pts as (x,y) pairs so the polygon closes correctly
                bot_rev = []
                for j in range(len(bot_pts) - 2, -1, -2):
                    bot_rev += [bot_pts[j], bot_pts[j + 1]]
                all_pts = top_pts + bot_rev
                self.create_polygon(*all_pts, fill=WAVE_FILL, outline='')
                self.create_line(*top_pts, fill=WAVE_LINE, width=1)
                self.create_line(*bot_pts, fill=WAVE_LINE, width=1)

        # Trim handles
        if cx0 >= 0:
            self.create_rectangle(cx0, top + 2, cx0 + 5, bot - 2, fill='#50c050', outline='')
        if cx1 <= cw:
            self.create_rectangle(cx1 - 5, top + 2, cx1, bot - 2, fill='#50c050', outline='')

        # Track number badge
        self.create_text(
            cx0 + 8, bot - 8, anchor='sw', fill=DIM_COL,
            text=f'#{i + 1}', font=('Segoe UI', 8),
        )

    def _draw_xfade(self, i: int, starts: list, cw: int):
        ttype = self.trans_types[i] if i < len(self.trans_types) else 'equal_power'
        col   = trans_color(ttype)
        name  = trans_name(ttype)

        # Hard Cut: just draw a thin line at the boundary, no shading
        if ttype == 'cut':
            tr_a = self.tracks[i]
            cut_sec = starts[i] + tr_a.seg_dur
            cx = self._px(cut_sec)
            if -XFADE_HIT <= cx <= cw + XFADE_HIT:
                row_top = RULER_H + i * TRACK_H
                self.create_line(cx, row_top, cx, row_top + TRACK_H * 2, fill=col, width=2)
                self.create_text(
                    cx + 4, row_top + TRACK_H // 2,
                    anchor='w', fill=col, text=name,
                    font=('Segoe UI', 8, 'bold'),
                )
            return

        fade = self.transitions[i] if i < len(self.transitions) else 0.0
        if fade < 0.05:
            return

        tr_a = self.tracks[i]
        xfade_sec = starts[i] + tr_a.seg_dur - fade
        x_start = self._px(xfade_sec)
        x_end   = self._px(xfade_sec + fade)

        row_top_a = RULER_H + i * TRACK_H
        row_top_b = row_top_a + TRACK_H

        # Shaded zone (colour-tinted per type)
        cx0 = max(0, int(x_start))
        cx1 = min(cw, int(x_end) + 1)
        if cx1 > cx0:
            self.create_rectangle(
                cx0, row_top_a + 2, cx1, row_top_a + TRACK_H - 2,
                fill=XFADE_BG, outline='',
            )
            if i + 1 < len(self.tracks):
                self.create_rectangle(
                    cx0, row_top_b + 2, cx1, row_top_b + TRACK_H - 2,
                    fill=XFADE_BG, outline='',
                )

        # Vertical marker line (type colour)
        if -XFADE_HIT <= x_start <= cw + XFADE_HIT:
            self.create_line(
                x_start, row_top_a, x_start, row_top_b + TRACK_H,
                fill=col, width=2,
            )
            # Label: type name + duration
            label_y = row_top_a + TRACK_H // 2
            self.create_text(
                x_start + 5, label_y - 8,
                anchor='w', fill=col,
                text=name,
                font=('Segoe UI', 8, 'bold'),
            )
            self.create_text(
                x_start + 5, label_y + 6,
                anchor='w', fill=col,
                text=f'↔ {fade:.1f}s',
                font=('Segoe UI', 8),
            )
            # Drag grip dots
            for dy in (-10, 0, 10):
                self.create_oval(
                    x_start - 3, label_y + dy - 3,
                    x_start + 3, label_y + dy + 3,
                    fill=col, outline='',
                )


# ─────────────────────────────────────────────────────────────────────────────
# Application
# ─────────────────────────────────────────────────────────────────────────────

class App(TkinterDnD.Tk if DND_OK else tk.Tk):

    def __init__(self):
        super().__init__()
        self.title('DJ AutoMix')
        self.geometry('1280x720')
        self.minsize(900, 600)
        self.configure(bg='#0e1014')

        self.tracks: list = []
        self.transitions: list = []      # float durations
        self.trans_types: list = []      # str keys from TRANS_TYPES
        self.engine = AudioEngine()
        self.engine.finished_cb = self._on_playback_finished

        self.ffmpeg: str = 'ffmpeg'

        # Playhead timer
        self._ph_job = None
        self._ph_start_wall: float = None
        self._ph_base_sec: float = 0.0
        self._ph_total: float = 0.0

        self._build_ui()

        if DND_OK:
            self._setup_dnd()

        # Keyboard shortcuts
        self.bind('<space>', lambda e: self.toggle_play())
        self.bind('<plus>', lambda e: self.timeline.zoom_in())
        self.bind('<equal>', lambda e: self.timeline.zoom_in())  # = same key as +
        self.bind('<minus>', lambda e: self.timeline.zoom_out())
        self.bind('<Left>', lambda e: self._seek_rel(-5.0))
        self.bind('<Right>', lambda e: self._seek_rel(5.0))
        self.bind('<Home>', lambda e: self.set_playhead(0.0))

    # ── UI construction ───────────────────────────────────────────────────────

    def _build_ui(self):
        # ── Toolbar ──────────────────────────────────────────────────────────
        tb = tk.Frame(self, bg='#161a20', pady=5, padx=8)
        tb.pack(fill='x', side='top')

        def btn(parent, text, cmd, accent=False, width=None):
            kw = dict(
                text=text, command=cmd, relief='flat', padx=10, pady=5,
                font=('Segoe UI', 9), activeforeground='white', cursor='hand2',
            )
            if accent:
                kw.update(bg='#1e5fa0', fg='white', activebackground='#2a7ad0')
            else:
                kw.update(bg='#1e2535', fg=TEXT_COL, activebackground='#2a3545')
            if width:
                kw['width'] = width
            b = tk.Button(parent, **kw)
            b.pack(side='left', padx=3)
            return b

        def sep():
            tk.Frame(tb, bg='#2a3545', width=1, height=26).pack(
                side='left', padx=8, fill='y', pady=3,
            )

        btn(tb, '⊕  Import', self.import_tracks, accent=True)
        sep()
        self.btn_play = btn(tb, '▶  Play Mix', self.play_mix, accent=True)
        btn(tb, '▶  Track', self.play_selected_track)
        btn(tb, '■  Stop', self.stop_playback)
        btn(tb, '⏮  Rewind', lambda: self.set_playhead(0.0))
        sep()
        btn(tb, '✦  Smart Transitions', self.smart_transitions)
        btn(tb, '◀ Zoom ▶', None)   # placeholder label
        btn(tb, '+', self.timeline_zoom_in_safe)
        btn(tb, '−', self.timeline_zoom_out_safe)
        sep()
        btn(tb, '🚀  Export', self.export_mix, accent=True)

        # Volume
        tk.Label(tb, text='Volume', bg='#161a20', fg=DIM_COL,
                 font=('Segoe UI', 8)).pack(side='left', padx=(14, 3))
        self.vol_var = tk.DoubleVar(value=1.0)
        tk.Scale(
            tb, from_=0.0, to=1.0, resolution=0.01, orient='horizontal',
            variable=self.vol_var, length=90, bg='#161a20', fg=TEXT_COL,
            troughcolor='#2a3545', highlightthickness=0, showvalue=False,
            command=lambda v: setattr(self.engine, 'volume', float(v)),
        ).pack(side='left')

        # Status label
        self.status_var = tk.StringVar(value='Ready  —  DJ AutoMix')
        tk.Label(
            tb, textvariable=self.status_var, bg='#161a20', fg=DIM_COL,
            font=('Segoe UI', 9),
        ).pack(side='right', padx=12)

        # ── Main body ─────────────────────────────────────────────────────────
        body = tk.PanedWindow(
            self, orient='horizontal', bg='#0e1014',
            sashwidth=5, sashrelief='flat', handlesize=0,
        )
        body.pack(fill='both', expand=True)

        # Left sidebar
        left = tk.Frame(body, bg='#0e1014', width=230)
        body.add(left, minsize=190)
        self._build_sidebar(left)

        # Right: timeline
        right = tk.Frame(body, bg='#0e1014')
        body.add(right, minsize=500)
        self._build_timeline(right)

        # Log bar at bottom
        self._build_log()

    def _build_sidebar(self, parent):
        # Track list
        tk.Label(
            parent, text='TRACKS', bg='#0e1014', fg=DIM_COL,
            font=('Segoe UI', 8, 'bold'),
        ).pack(anchor='w', padx=10, pady=(10, 2))

        list_frame = tk.Frame(parent, bg='#0e1014')
        list_frame.pack(fill='both', expand=True, padx=6)

        sb = tk.Scrollbar(list_frame, bg='#1a2030', troughcolor='#0e1014',
                          relief='flat', width=10)
        self.listbox = tk.Listbox(
            list_frame, bg='#141c28', fg=TEXT_COL,
            selectbackground=SEL_COL, activestyle='none',
            relief='flat', bd=0, font=('Segoe UI', 9),
            yscrollcommand=sb.set, selectmode=tk.EXTENDED,
        )
        sb.config(command=self.listbox.yview)
        sb.pack(side='right', fill='y')
        self.listbox.pack(fill='both', expand=True)
        self.listbox.bind('<Double-Button-1>', lambda e: self.play_selected_track())

        # List action buttons
        lb = tk.Frame(parent, bg='#0e1014')
        lb.pack(fill='x', padx=6, pady=4)
        for text, cmd in [
            ('▲', lambda: self.move_track(-1)),
            ('▼', lambda: self.move_track(1)),
            ('✕', self.remove_selected),
        ]:
            tk.Button(
                lb, text=text, command=cmd, bg='#1a2230', fg=TEXT_COL,
                relief='flat', padx=8, pady=3, font=('Segoe UI', 9),
                activebackground='#2a3240', activeforeground='white',
                cursor='hand2',
            ).pack(side='left', padx=2)

        # Transitions panel
        tk.Frame(parent, bg='#2a3545', height=1).pack(fill='x', padx=6, pady=8)
        tk.Label(
            parent, text='TRANSITIONS', bg='#0e1014', fg=DIM_COL,
            font=('Segoe UI', 8, 'bold'),
        ).pack(anchor='w', padx=10, pady=(0, 4))

        trans_scroll_frame = tk.Frame(parent, bg='#0e1014')
        trans_scroll_frame.pack(fill='x', padx=6)

        self.trans_canvas = tk.Canvas(
            trans_scroll_frame, bg='#0e1014', highlightthickness=0, height=180,
        )
        trans_sb = tk.Scrollbar(
            trans_scroll_frame, orient='vertical', command=self.trans_canvas.yview,
            bg='#1a2030', troughcolor='#0e1014', relief='flat', width=8,
        )
        self.trans_canvas.config(yscrollcommand=trans_sb.set)
        trans_sb.pack(side='right', fill='y')
        self.trans_canvas.pack(fill='x', expand=False)

        self.trans_inner = tk.Frame(self.trans_canvas, bg='#0e1014')
        self.trans_canvas.create_window((0, 0), window=self.trans_inner, anchor='nw')
        self.trans_inner.bind(
            '<Configure>',
            lambda e: self.trans_canvas.config(
                scrollregion=self.trans_canvas.bbox('all'),
            ),
        )

    def _build_timeline(self, parent):
        hscroll = ttk.Scrollbar(parent, orient='horizontal')
        hscroll.pack(side='bottom', fill='x')

        self.timeline = TimelineCanvas(parent, app=self, hscroll=hscroll)
        self.timeline.pack(fill='both', expand=True)

        hscroll.config(command=self.timeline.scroll_cmd)

    def _build_log(self):
        log_outer = tk.Frame(self, bg='#0c0e10', height=70)
        log_outer.pack(fill='x', side='bottom')
        log_outer.pack_propagate(False)

        tk.Label(
            log_outer, text='LOG', bg='#0c0e10', fg='#2a3540',
            font=('Segoe UI', 7, 'bold'),
        ).pack(anchor='w', padx=6, pady=(2, 0))

        self.log_text = tk.Text(
            log_outer, bg='#0c0e10', fg='#3a5060',
            font=('Consolas', 8), relief='flat', wrap='word', state='disabled',
        )
        self.log_text.pack(fill='both', expand=True, padx=6, pady=(0, 4))

    def _setup_dnd(self):
        self.timeline.drop_target_register(DND_FILES)
        self.timeline.dnd_bind('<<Drop>>', lambda e: self._add_paths(parse_dnd(e.data)))
        self.listbox.drop_target_register(DND_FILES)
        self.listbox.dnd_bind('<<Drop>>', lambda e: self._add_paths(parse_dnd(e.data)))
        self.log('Drag & drop enabled.')

    # Proxy methods called before timeline exists
    def timeline_zoom_in_safe(self):
        if hasattr(self, 'timeline'):
            self.timeline.zoom_in()

    def timeline_zoom_out_safe(self):
        if hasattr(self, 'timeline'):
            self.timeline.zoom_out()

    # ── logging ───────────────────────────────────────────────────────────────

    def log(self, msg: str):
        self.log_text.config(state='normal')
        self.log_text.insert('end', msg + '\n')
        self.log_text.see('end')
        self.log_text.config(state='disabled')

    def set_status(self, msg: str):
        self.status_var.set(msg)

    # ── track management ──────────────────────────────────────────────────────

    def import_tracks(self):
        paths = filedialog.askopenfilenames(
            title='Select audio files',
            filetypes=[('Audio files', '*.wav *.mp3 *.flac *.ogg *.m4a *.aac *.aif *.aiff')],
        )
        self._add_paths(list(paths))

    def _add_paths(self, paths: list):
        valid = [
            p for p in paths
            if p and Path(p).exists() and Path(p).suffix.lower() in AUDIO_EXTS
        ]
        if not valid:
            return
        self.set_status(f'Loading {len(valid)} file(s)…')
        self.log(f'Importing: {", ".join(Path(p).name for p in valid)}')

        def work():
            errors = []
            for p in valid:
                try:
                    tr = Track(path=p)
                    tr.load(self.ffmpeg)
                    self.tracks.append(tr)
                    self._auto_transition()
                    self.after(0, self._refresh)
                    # BPM / key in background
                    threading.Thread(
                        target=self._run_analysis, args=(tr,), daemon=True,
                    ).start()
                except Exception as ex:
                    errors.append(f'{Path(p).name}: {ex}')
                    self.log(f'ERROR: {ex}')
            if errors:
                self.after(0, lambda: messagebox.showerror(
                    'Import errors', '\n'.join(errors),
                ))
            self.after(0, lambda: self.set_status(f'{len(self.tracks)} track(s) loaded'))

        threading.Thread(target=work, daemon=True).start()

    def _run_analysis(self, tr: Track):
        tr.analyze()
        self.after(0, self._refresh)

    def _auto_transition(self):
        """Compute a smart transition for the last pair of tracks."""
        i = len(self.tracks) - 2
        if i < 0:
            return
        while len(self.transitions) < len(self.tracks) - 1:
            self.transitions.append(8.0)
        while len(self.trans_types) < len(self.tracks) - 1:
            self.trans_types.append('equal_power')
        tr_a = self.tracks[i]
        tr_b = self.tracks[i + 1]
        self.transitions[i] = self._smart_fade(tr_a, tr_b)
        self.trans_types[i] = self._smart_type(tr_a, tr_b)

    def _smart_fade(self, tr_a: Track, tr_b: Track) -> float:
        e1 = self._tail_rms(tr_a)
        e2 = self._head_rms(tr_b)
        base = 4.0 + 10.0 * ((e1 + e2) / 2.0)
        if e1 < e2:
            base += 2.0
        max_fade = min(20.0, 0.3 * tr_a.seg_dur, 0.3 * tr_b.seg_dur)
        return round(clamp(base, 1.0, max(1.0, max_fade)), 1)

    def _smart_type(self, tr_a: Track, tr_b: Track) -> str:
        """Choose the most appropriate crossfade curve for this pair."""
        e1 = self._tail_rms(tr_a)
        e2 = self._head_rms(tr_b)
        avg = (e1 + e2) / 2.0
        bpm_a = tr_a.bpm or 0.0
        bpm_b = tr_b.bpm or 0.0
        bpm_diff = abs(bpm_a - bpm_b) if bpm_a and bpm_b else None
        # Very quiet on both sides → dramatic silence fade
        if avg < 0.02:
            return 'silence'
        # Big BPM difference → echo out hides the rhythmic mismatch
        if bpm_diff is not None and bpm_diff > 30:
            return 'echo' if avg > 0.08 else 'silence'
        # Both tracks energetic → equal-power (DJ standard)
        if e1 > 0.12 and e2 > 0.12:
            return 'equal_power'
        # Outgoing is louder → let it echo away
        if e1 > e2 * 1.5:
            return 'echo'
        # Gentle intro/outro → smooth S-curve
        if avg < 0.06:
            return 'smooth'
        return 'equal_power'

    def _tail_rms(self, tr: Track, dur: float = 20.0) -> float:
        seg = tr.get_segment()
        n = int(dur * tr.sr)
        chunk = seg[-n:] if seg.shape[0] >= n else seg
        return float(np.sqrt(np.mean(chunk ** 2)) + 1e-9)

    def _head_rms(self, tr: Track, dur: float = 20.0) -> float:
        seg = tr.get_segment()
        n = int(dur * tr.sr)
        chunk = seg[:n]
        return float(np.sqrt(np.mean(chunk ** 2)) + 1e-9)

    def remove_selected(self):
        sel = sorted(self.listbox.curselection(), reverse=True)
        for i in sel:
            self._delete_track(i)
        self._refresh()

    def remove_track(self, i: int):
        self._delete_track(i)
        self._refresh()

    def _delete_track(self, i: int):
        del self.tracks[i]
        if i < len(self.transitions):
            del self.transitions[i]
        if i < len(self.trans_types):
            del self.trans_types[i]
        self._fix_transitions_len()

    def move_track(self, direction: int):
        sel = list(self.listbox.curselection())
        if len(sel) != 1:
            return
        i = sel[0]
        j = clamp(i + direction, 0, len(self.tracks) - 1)
        if i == j:
            return
        self.tracks[i], self.tracks[j] = self.tracks[j], self.tracks[i]
        self._refresh()
        self.listbox.selection_clear(0, 'end')
        self.listbox.selection_set(j)

    def split_track(self, idx: int, at_sec: float):
        result = self.tracks[idx].split(at_sec)
        if result is None:
            return
        left, right = result
        self.tracks[idx] = left
        self.tracks.insert(idx + 1, right)
        # keep transitions sensible
        orig_fade = self.transitions[idx] if idx < len(self.transitions) else 4.0
        orig_type = self.trans_types[idx] if idx < len(self.trans_types) else 'equal_power'
        self.transitions[idx] = min(orig_fade, 0.2 * left.seg_dur)
        self.transitions.insert(idx + 1, min(orig_fade, 0.2 * right.seg_dur))
        self.trans_types[idx] = orig_type
        self.trans_types.insert(idx + 1, orig_type)
        self._fix_transitions_len()
        self._refresh()

    def trim_track(self, idx: int, at_sec: float, which: str):
        tr = self.tracks[idx]
        abs_t = tr.trim_start + at_sec
        if which == 'start':
            tr.trim_start = clamp(abs_t, 0.0, tr.trim_end - 0.1)
        else:
            tr.trim_end = clamp(abs_t, tr.trim_start + 0.1, tr.duration)
        tr._compute_waveform()
        self._refresh()

    def _fix_transitions_len(self):
        target = max(0, len(self.tracks) - 1)
        while len(self.transitions) < target:
            self.transitions.append(8.0)
        while len(self.transitions) > target:
            self.transitions.pop()
        while len(self.trans_types) < target:
            self.trans_types.append('equal_power')
        while len(self.trans_types) > target:
            self.trans_types.pop()

    # ── refresh UI ────────────────────────────────────────────────────────────

    def _refresh(self):
        self._rebuild_listbox()
        self.timeline.set_data(self.tracks, self.transitions, self.trans_types)
        self._rebuild_trans_panel()

    def _rebuild_listbox(self):
        self.listbox.delete(0, 'end')
        for i, tr in enumerate(self.tracks):
            name = Path(tr.path).name
            extras = []
            if tr.bpm:
                extras.append(f'{tr.bpm:.0f}bpm')
            if tr.key:
                extras.append(tr.key)
            suffix = '  ' + '  '.join(extras) if extras else ''
            self.listbox.insert('end', f'{i + 1}. {name}{suffix}')

    def _rebuild_trans_panel(self):
        for w in self.trans_inner.winfo_children():
            w.destroy()
        n = len(self.tracks)
        if n < 2:
            tk.Label(
                self.trans_inner, text='Add 2+ tracks', bg='#0e1014', fg=DIM_COL,
                font=('Segoe UI', 8),
            ).pack(anchor='w')
            return
        for i in range(n - 1):
            self._make_trans_row(i)

    def _make_trans_row(self, i: int):
        fade = self.transitions[i] if i < len(self.transitions) else 8.0
        ttype = self.trans_types[i] if i < len(self.trans_types) else 'equal_power'
        col = trans_color(ttype)

        outer = tk.Frame(self.trans_inner, bg='#0e1014')
        outer.pack(fill='x', pady=3)

        # ── header row: index + type dropdown ────────────────────────────────
        hdr = tk.Frame(outer, bg='#0e1014')
        hdr.pack(fill='x')

        tk.Label(
            hdr, text=f'{i + 1}→{i + 2}', bg='#0e1014', fg=DIM_COL,
            font=('Segoe UI', 8, 'bold'), width=5,
        ).pack(side='left')

        # Type selector using OptionMenu (dark-friendly)
        type_names = [v[0] for v in TRANS_TYPES.values()]
        type_keys  = list(TRANS_TYPES.keys())
        type_var = tk.StringVar(value=trans_name(ttype))

        def on_type(name, idx=i, tv=type_var):
            key = type_keys[type_names.index(name)]
            while len(self.trans_types) <= idx:
                self.trans_types.append('equal_power')
            self.trans_types[idx] = key
            # Re-render the whole panel so color/label refresh correctly
            self._rebuild_trans_panel()
            self.timeline.set_data(self.tracks, self.transitions, self.trans_types)

        opt = tk.OptionMenu(hdr, type_var, *type_names, command=on_type)
        opt.config(
            bg='#1a2230', fg=col, activebackground=SEL_COL,
            activeforeground='white', relief='flat', bd=0,
            font=('Segoe UI', 8), highlightthickness=0,
            indicatoron=True, width=13,
        )
        opt['menu'].config(
            bg='#1a2230', fg=TEXT_COL, activebackground=SEL_COL,
            activeforeground='white', font=('Segoe UI', 8),
        )
        opt.pack(side='left', fill='x', expand=True, padx=(4, 0))

        # ── duration row (hidden for Hard Cut) ───────────────────────────────
        if ttype != 'cut':
            dur_row = tk.Frame(outer, bg='#0e1014')
            dur_row.pack(fill='x', pady=(1, 0))

            lbl = tk.Label(
                dur_row, text=f'{fade:.1f}s', bg='#0e1014', fg=col,
                font=('Segoe UI', 8), width=5,
            )
            lbl.pack(side='right')

            var = tk.DoubleVar(value=fade)

            def on_change(v, idx=i, var_=var, lbl_=lbl):
                val = float(var_.get())
                if idx < len(self.transitions):
                    self.transitions[idx] = val
                lbl_.config(text=f'{val:.1f}s')
                self.timeline.set_data(self.tracks, self.transitions, self.trans_types)

            tk.Scale(
                dur_row, from_=0.0, to=30.0, resolution=0.5, orient='horizontal',
                variable=var, command=on_change, bg='#0e1014', fg=TEXT_COL,
                troughcolor='#1a2a3a', highlightthickness=0, showvalue=False,
            ).pack(side='left', fill='x', expand=True, padx=(16, 4))

        # separator line
        tk.Frame(outer, bg='#1e2530', height=1).pack(fill='x', pady=(4, 0))

    # ── playback ──────────────────────────────────────────────────────────────

    def toggle_play(self):
        if self.engine.is_playing:
            self.stop_playback()
        else:
            self.play_mix()

    def play_mix(self):
        if not self.tracks:
            messagebox.showwarning('Play', 'Import at least one track first.')
            return
        start = self.timeline.playhead
        self.set_status('Building mix…')

        def work():
            try:
                mix, sr = build_mix(self.tracks, self.transitions, self.trans_types)
                total = mix.shape[0] / sr
                self.after(0, lambda: self._start_playback(mix, sr, start, total))
            except Exception as ex:
                self.after(0, lambda: messagebox.showerror('Playback error', str(ex)))
                self.after(0, lambda: self.set_status('Error'))

        threading.Thread(target=work, daemon=True).start()

    def _start_playback(self, mix, sr, start_sec, total):
        self.engine.play(mix, sr, start_sec)
        self._start_ph_timer(start_sec, total)
        self.set_status('Playing mix…')
        self.btn_play.config(text='▐▐  Pause Mix')

    def play_selected_track(self):
        sel = list(self.listbox.curselection())
        if not sel:
            messagebox.showinfo('Play track', 'Select a track in the list first.')
            return
        tr = self.tracks[sel[0]]
        seg = tr.get_segment()
        self.engine.play(seg, tr.sr)
        self._start_ph_timer(0.0, tr.seg_dur)
        self.set_status(f'Playing: {Path(tr.path).name}')

    def stop_playback(self):
        self.engine.stop()
        self._stop_ph_timer()
        self.set_status('Stopped')
        self.btn_play.config(text='▶  Play Mix')

    def _on_playback_finished(self):
        self.after(0, lambda: [
            self._stop_ph_timer(),
            self.set_status('Finished'),
            self.btn_play.config(text='▶  Play Mix'),
        ])

    def on_playhead_seek(self, sec: float):
        if self.engine.is_playing:
            self.engine.seek(sec)
            self._ph_base_sec = sec
            self._ph_start_wall = time.time()

    def _seek_rel(self, delta: float):
        cur = self.timeline.playhead
        new = clamp(cur + delta, 0.0, max(0.0, self.timeline._total_dur()))
        self.set_playhead(new)

    def set_playhead(self, sec: float):
        self.timeline.set_playhead(sec)
        self.on_playhead_seek(sec)

    def _start_ph_timer(self, base: float, total: float):
        self._stop_ph_timer()
        self._ph_base_sec = base
        self._ph_start_wall = time.time()
        self._ph_total = total

        def tick():
            if self._ph_start_wall is None:
                return
            t = time.time() - self._ph_start_wall + self._ph_base_sec
            t = min(t, self._ph_total)
            self.timeline.set_playhead(t)
            if t < self._ph_total and self.engine.is_playing:
                self._ph_job = self.after(40, tick)
            else:
                self._ph_job = None

        tick()

    def _stop_ph_timer(self):
        if self._ph_job:
            try:
                self.after_cancel(self._ph_job)
            except Exception:
                pass
        self._ph_job = None
        self._ph_start_wall = None

    # ── transitions ───────────────────────────────────────────────────────────

    def on_transition_drag(self, idx: int, new_fade: float):
        while len(self.transitions) <= idx:
            self.transitions.append(8.0)
        self.transitions[idx] = new_fade
        self._rebuild_trans_panel()
        self.timeline.set_data(self.tracks, self.transitions, self.trans_types)

    def prompt_fade(self, idx: int):
        cur = self.transitions[idx] if idx < len(self.transitions) else 8.0
        val = simpledialog.askfloat(
            'Crossfade Duration',
            f'Transition {idx + 1} → {idx + 2}  (seconds):',
            initialvalue=round(cur, 1),
            minvalue=0.0,
            maxvalue=30.0,
            parent=self,
        )
        if val is not None:
            self.transitions[idx] = round(val, 1)
            self._refresh()

    def smart_transitions(self):
        if len(self.tracks) < 2:
            messagebox.showinfo('Smart Transitions', 'Add at least 2 tracks first.')
            return
        self.set_status('Computing smart transitions…')

        def work():
            try:
                new_fades, new_types = [], []
                for i in range(len(self.tracks) - 1):
                    tr_a, tr_b = self.tracks[i], self.tracks[i + 1]
                    new_fades.append(self._smart_fade(tr_a, tr_b))
                    new_types.append(self._smart_type(tr_a, tr_b))
                self.transitions = new_fades
                self.trans_types = new_types
                self.after(0, self._refresh)
                summary = ', '.join(
                    f'{trans_name(t)} {f:.1f}s'
                    for f, t in zip(new_fades, new_types)
                )
                self.after(0, lambda: self.set_status(f'Smart transitions set: {summary}'))
                self.log(f'Smart transitions: {summary}')
            except Exception as ex:
                self.after(0, lambda: messagebox.showerror('Smart Transitions', str(ex)))

        threading.Thread(target=work, daemon=True).start()

    # ── export ────────────────────────────────────────────────────────────────

    def export_mix(self):
        if not self.tracks:
            messagebox.showwarning('Export', 'No tracks to export.')
            return
        out_path = filedialog.asksaveasfilename(
            title='Save Mix As',
            defaultextension='.wav',
            filetypes=[
                ('WAV audio', '*.wav'),
                ('MP3 audio', '*.mp3'),
            ],
        )
        if not out_path:
            return
        out = Path(out_path)

        def work():
            try:
                self.after(0, lambda: self.set_status('Building mix for export…'))
                mix, sr = build_mix(self.tracks, self.transitions, self.trans_types)
                if out.suffix.lower() == '.mp3':
                    tmp = tempfile.NamedTemporaryFile(suffix='.wav', delete=False)
                    sf.write(tmp.name, mix, sr)
                    tmp.close()
                    cmd = [
                        self.ffmpeg, '-y', '-hide_banner', '-loglevel', 'error',
                        '-i', tmp.name,
                        '-ar', '44100', '-ac', '2', '-b:a', '192k',
                        str(out),
                    ]
                    subprocess.run(cmd, check=True)
                    os.unlink(tmp.name)
                else:
                    sf.write(str(out), mix, sr)
                self.after(0, lambda: self.set_status(f'Exported: {out.name}'))
                self.after(0, lambda: messagebox.showinfo('Export Complete', f'Saved:\n{out}'))
                self.log(f'Exported: {out}')
            except Exception as ex:
                self.after(0, lambda: messagebox.showerror('Export Failed', str(ex)))
                self.after(0, lambda: self.set_status('Export failed'))

        threading.Thread(target=work, daemon=True).start()

    # ── cleanup ───────────────────────────────────────────────────────────────

    def destroy(self):
        try:
            self.engine.stop()
        except Exception:
            pass
        super().destroy()


# ─────────────────────────────────────────────────────────────────────────────
# Mix builder
# ─────────────────────────────────────────────────────────────────────────────

def _xfade_curves(n: int, ttype: str):
    """
    Return (fade_out, fade_in) arrays of shape (n, 1) for the given transition type.
    Returns (None, None) for 'cut' (caller handles as hard cut).
    """
    if ttype == 'cut' or n <= 0:
        return None, None

    t = np.linspace(0.0, 1.0, n, dtype=np.float32)

    if ttype == 'linear':
        fo = 1.0 - t
        fi = t

    elif ttype == 'smooth':
        # Smoothstep: 3t²-2t³  — gentle acceleration at both ends
        s = t * t * (3.0 - 2.0 * t)
        fo = 1.0 - s
        fi = s

    elif ttype == 'echo':
        # Outgoing: exponential decay (sounds like reverb tail)
        # Incoming: smooth ramp in
        fo = np.exp(-4.0 * t)
        s  = t * t * (3.0 - 2.0 * t)
        fi = s

    elif ttype == 'silence':
        # Track A fades to silence in first half; track B fades from silence in second half
        half = n // 2
        fo_arr = np.zeros(n, dtype=np.float32)
        fi_arr = np.zeros(n, dtype=np.float32)
        fo_arr[:half] = np.cos(np.linspace(0.0, math.pi / 2, half))
        fi_arr[half:] = np.sin(np.linspace(0.0, math.pi / 2, n - half))
        fo = fo_arr
        fi = fi_arr

    else:
        # Default / 'equal_power': cosine crossfade — DJ industry standard
        angle = t * (math.pi / 2.0)
        fo = np.cos(angle)
        fi = np.sin(angle)

    return fo[:, np.newaxis], fi[:, np.newaxis]


def build_mix(tracks: list, transitions: list, trans_types: list = None) -> tuple:
    """
    Build the final mix as a float32 numpy array (N, 2).
    Applies per-transition curve shapes from trans_types.
    Returns (array, sample_rate).
    """
    if not tracks:
        return np.zeros((0, 2), dtype=np.float32), 44100

    if trans_types is None:
        trans_types = []

    sr = tracks[0].sr

    # Collect segments, resampling to common sr if needed
    segs = []
    for tr in tracks:
        seg = tr.get_segment()
        if tr.sr != sr:
            ratio = sr / tr.sr
            n_out = int(seg.shape[0] * ratio)
            x_in = np.linspace(0, seg.shape[0] - 1, n_out)
            seg = np.stack(
                [np.interp(x_in, np.arange(seg.shape[0]), seg[:, c]) for c in range(2)],
                axis=1,
            ).astype(np.float32)
        segs.append(seg)

    # Determine effective fade lengths (0 for 'cut')
    def _fade_samp(i: int) -> int:
        ttype = trans_types[i] if i < len(trans_types) else 'equal_power'
        if ttype == 'cut':
            return 0
        return int(transitions[i] * sr) if i < len(transitions) else 0

    # Calculate total output samples
    total = 0
    for i, seg in enumerate(segs):
        fs = _fade_samp(i) if i < len(segs) - 1 else 0
        if i < len(segs) - 1:
            total += seg.shape[0] - fs
        else:
            total += seg.shape[0]

    out = np.zeros((total + sr, 2), dtype=np.float32)  # +1s safety buffer
    cursor = 0

    for i, seg in enumerate(segs):
        if i > 0:
            fade_samp = _fade_samp(i - 1)
            ttype     = trans_types[i - 1] if (i - 1) < len(trans_types) else 'equal_power'
        else:
            fade_samp = 0
            ttype     = 'equal_power'

        if fade_samp > 0 and cursor > 0:
            fade_len = min(fade_samp, cursor, seg.shape[0])
            fo, fi = _xfade_curves(fade_len, ttype)

            write_pos = cursor - fade_len
            out[write_pos: cursor] *= fo
            out[write_pos: cursor] += seg[:fade_len] * fi

            rest = seg[fade_len:]
            out[cursor: cursor + rest.shape[0]] = rest
            cursor += rest.shape[0]
        else:
            end = cursor + seg.shape[0]
            out[cursor: end] = seg
            cursor = end

    # Normalize to prevent clipping
    mx = np.abs(out[:cursor]).max()
    if mx > 0.98:
        out[:cursor] *= 0.98 / mx

    return out[:cursor], sr


# ─────────────────────────────────────────────────────────────────────────────
if __name__ == '__main__':
    app = App()
    app.mainloop()
