# DJ AutoMix

> A desktop application for creating professional audio mixes with an interactive waveform timeline.

**DJ AutoMix** is a Python desktop app for building smooth DJ-style mixes.
Import tracks, visualise waveforms, tweak crossfades, preview in real-time, and export — no DAW required.

---

> **Academic context:** Related to the thesis
> *Design and Implementation of a Desktop Application for Automated Audio Mixing* — Dmitrii Evseev

---

## Features

| | |
|---|---|
| 🎵 **Multi-format import** | WAV, FLAC, OGG, AIFF, MP3, M4A, AAC — drag & drop or file picker |
| 🌊 **Waveform timeline** | Zoomable, scrollable, colour-filled waveform per track |
| ✂️ **Timeline editing** | Click to seek · double-click to split · right-click to trim or remove |
| 🎚️ **6 transition styles** | Per-transition crossfade curve (see below) |
| 🤖 **Smart transitions** | Auto-picks duration + style based on energy & BPM analysis |
| 🔊 **Sample-accurate playback** | `sounddevice`-based engine — no `ffplay` needed |
| 🎯 **BPM & key detection** | Background analysis via `librosa` (optional) |
| 📤 **Export** | WAV (native) or MP3 (requires ffmpeg) |

---

## Transition Styles

Each transition between two tracks can be set independently — either by the smart auto-mode or manually via the sidebar dropdown.

| Style | Colour | Description |
|---|---|---|
| **Equal Power** | 🟠 | Cosine fade curve — DJ industry standard, always sounds natural |
| **Linear** | 🟡 | Straight ramp — simple and predictable |
| **S-Curve** | 🟢 | Smoothstep (3t²−2t³) — barely audible start & end |
| **Echo Out** | 🟣 | Outgoing track decays exponentially like a reverb tail |
| **Fade to Black** | 🔵 | Both sides fade through silence — dramatic pause between tracks |
| **Hard Cut** | 🔴 | Instant switch with zero overlap — for beat-matched drops |

The transition marker on the timeline is **draggable** — pull it left/right to shorten or lengthen the crossfade in real time.

---

## Requirements

| Dependency | Purpose | Required? |
|---|---|---|
| `numpy` | Audio math & waveform rendering | ✅ Required |
| `soundfile` | Decoding WAV / FLAC / OGG / AIFF | ✅ Required |
| `sounddevice` | Real-time audio playback | ✅ Required |
| `tkinterdnd2` | Drag & drop support | ⭐ Recommended |
| `librosa` | BPM & key detection | ⭐ Recommended |
| `ffmpeg` (binary) | MP3 / M4A / AAC decoding + MP3 export | ⚠️ MP3 only |

---

## Installation

### 1 — Install Python packages

```bash
pip install numpy soundfile sounddevice tkinterdnd2
```

For BPM and key detection (optional but recommended):

```bash
pip install librosa
```

### 2 — Install FFmpeg *(only needed for MP3/M4A files and MP3 export)*

Download from [ffmpeg.org](https://ffmpeg.org/download.html) and add to your system `PATH`.

```bash
ffmpeg -version   # confirm it works
```

---

## Running

```bash
cd audio-mixes
python automix_gui.py
```

---

## How to Use

### Import tracks
- Click **⊕ Import** in the toolbar, or
- Drag and drop audio files directly onto the timeline or track list.

### Arrange & edit
- **Reorder** tracks with the ▲ / ▼ buttons in the sidebar.
- **Split** a track by double-clicking on the waveform.
- **Trim** start/end via right-click → *Trim start/end here*.
- **Remove** a track via right-click → *Remove*, or select it and press ✕.

### Set transitions
- **Smart mode** — click **✦ Smart Transitions** to automatically set duration and style for every pair of tracks based on energy and BPM analysis.
- **Manual mode** — use the dropdown and slider in the **Transitions** sidebar panel for each pair individually.
- **Drag on the timeline** — grab the coloured vertical marker between two tracks and drag left/right to adjust the fade length live.

### Playback
| Action | How |
|---|---|
| Play full mix | **▶ Play Mix** or `Space` |
| Play one track | Select it in the list, then **▶ Track** |
| Stop | **■ Stop** or `Space` |
| Rewind to start | **⏮ Rewind** or `Home` |
| Seek ±5 seconds | `←` / `→` arrow keys |
| Click timeline | Jump playhead to that position |
| Drag playhead | Scrub through the mix |

### Zoom the timeline
| Action | How |
|---|---|
| Zoom in / out | `Ctrl + Scroll` or `+` / `−` keys |
| Pan left / right | `Scroll` (mouse wheel) or drag the scrollbar |

> After manually scrolling or zooming, auto-follow of the playhead pauses for ~2.5 s so the view stays where you put it.

### Export
Click **🚀 Export**, choose a file name, and pick `.wav` (lossless) or `.mp3` (requires ffmpeg, ~192 kbps).

---

## Keyboard Shortcuts

| Key | Action |
|---|---|
| `Space` | Play / Stop |
| `Home` | Rewind to start |
| `←` / `→` | Seek −5 s / +5 s |
| `+` / `=` | Zoom in |
| `−` | Zoom out |
| `Ctrl + Scroll` | Zoom centred on cursor |
| `Scroll` | Pan timeline |

---

## Architecture

| Layer | Technology |
|---|---|
| GUI framework | Tkinter (+ tkinterdnd2 for DnD) |
| Waveform canvas | Custom `tk.Canvas` with NumPy envelope |
| Audio decoding | `soundfile` (native) · `ffmpeg` subprocess (fallback) |
| Playback engine | `sounddevice` OutputStream with position tracking |
| Crossfade engine | NumPy — 6 configurable curve shapes |
| BPM / key analysis | `librosa` (background thread) |
| Export | `soundfile` for WAV · `ffmpeg` subprocess for MP3 |

---

## Notes

- Playback uses pure Python/NumPy — the entire mix is built in memory before playback starts. For very long mixes (1+ hour) this can use significant RAM.
- `librosa` analysis runs in a background thread after import and updates the timeline labels when done.
- If `tkinterdnd2` is not installed, drag & drop is silently disabled; the Import button still works.
- MP3/M4A files require `ffmpeg` in `PATH` for decoding. Pure WAV/FLAC/OGG files work without it.

---

## License

Created for academic and educational purposes.
