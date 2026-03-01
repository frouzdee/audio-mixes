# DJ AutoMix

> A Desktop Application for Automated Audio Mixing

DJ AutoMix is a Python-based desktop application that allows users to quickly create smooth audio mixes using automatic crossfades. The application provides a simple graphical interface built with Tkinter and uses FFmpeg for audio processing.

---

> **Academic context:** This project is related to the thesis:
> *Design and Implementation of a Desktop Application for Automated Audio Mixing* — Dmitrii Evseev

---

## 🛠 Requirements

- Python 3.10+
- FFmpeg (must include `ffmpeg` and `ffplay`)
- `pip` (Python package manager)

---

## 📦 Installation

### 1. Install Python packages

```bash
pip install numpy
pip install tkinterdnd2  # optional — enables drag & drop
```

### 2. Install FFmpeg

Download [FFmpeg](https://ffmpeg.org/download.html) and make sure `ffmpeg` and `ffplay` are available in your system `PATH`.

Verify the installation:

```bash
ffmpeg -version
ffplay -version
```

> If FFmpeg is not in your `PATH`, you can manually set the path inside the application.

---

## ▶️ Running the Application

From the project folder:

```bash
python version2_dj_automix_tk.py
```

---

## 🎧 How to Use

1. Click **Import…** or drag & drop audio files into the application.
2. Adjust transitions manually or click **Smart transitions**.
3. Click **Play mix preview** to listen.
4. Choose an export location.
5. Click **Export final mix**.

---

## 🧠 How It Works

| Component | Technology |
|---|---|
| GUI | Tkinter |
| Audio mixing | FFmpeg `filter_complex` |
| Crossfades | FFmpeg `acrossfade` |
| Waveform visualization | NumPy |
| Playback | `ffplay` |

---

## ⚠️ Notes

- If drag & drop does not work, install `tkinterdnd2`
- Performance depends on system speed and audio file size
- Large files may take longer to preview or export
- Currently optimized for single-user local mixing

---

## 🚀 Future Improvements

- [ ] BPM detection and beat matching
- [ ] Key detection for harmonic mixing
- [ ] Timeline drag editing
- [ ] Multi-track volume automation
- [ ] Real-time scrubbing playback
- [ ] Multiple-user support

---

## 📄 License

This project is created for academic and educational purposes.