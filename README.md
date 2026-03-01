DJ AutoMix

A Desktop Application for Automated Audio Mixing

DJ AutoMix is a Python-based desktop application that allows users to quickly create smooth audio mixes using automatic crossfades.
The application provides a simple graphical interface built with Tkinter and uses FFmpeg for audio processing.

This project is related to the thesis:

Design and Implementation of a Desktop Application for Automated Audio Mixing
Dmitrii Evseev

🛠 Requirements

Python 3.10+

FFmpeg (must include ffmpeg and ffplay)

pip (Python package manager)

📦 Installation
1️⃣ Install Python packages
pip install numpy
pip install tkinterdnd2   # optional (enables drag & drop)
2️⃣ Install FFmpeg

Download FFmpeg and make sure ffmpeg and ffplay are available in your system PATH.

Test it:

ffmpeg -version
ffplay -version

If not in PATH, you can manually set the path inside the application.

▶️ Running the Application

From the project folder:

python version2_dj_automix_tk.py
🎧 How to Use

Click Import… or drag & drop audio files.

Adjust transitions manually or click Smart transitions.

Click Play mix preview to listen.

Choose export location.

Click Export final mix.

🧠 How It Works

The GUI is built using Tkinter

Audio mixing is performed via FFmpeg filter_complex

Crossfades are applied using FFmpeg's acrossfade

Waveform visualization is generated using NumPy

Playback is handled using ffplay

⚠️ Notes

If drag & drop does not work, install tkinterdnd2

Performance depends on system speed and audio file size

Large files may take longer to preview/export

Currently optimized for single-user local mixing

🚀 Future Improvements

BPM detection and beat matching

Key detection for harmonic mixing

Timeline drag editing

Multi-track volume automation

Real-time scrubbing playback

Multiple-user support

📄 License

This project is created for academic and educational purposes.