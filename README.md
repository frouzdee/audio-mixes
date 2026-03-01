# DJ AutoMix (Tkinter Prototype)

# A simple desktop app to build audio mixes by combining tracks with automatic crossfades. It supports

# importing audio files, preview playback, a basic waveform/timeline view, per-transition crossfade

# control, and exporting the final mix using FFmpeg.

# Requirements

# • Python 3.10+

# • FFmpeg installed (must include ffmpeg and ffplay in PATH, or set paths inside the app)

# Quick check (terminal):

# ffmpeg -version

# ffplay -version

# Install

# Install Python packages:

# pip install numpy

# pip install tkinterdnd2 (optional: enables drag \& drop)

# Run

# python version2\_dj\_automix\_tk.py

# How to use

# 1 Click Import… (or drag \& drop if enabled) to add audio files.

# 2 Adjust crossfades in Transitions (or click Smart transitions).

# 3 Click Play mix preview to listen.

# 4 Choose an output path in Export and click Export final mix.

# Notes

# • If drag \& drop does not work, install tkinterdnd2 (the app still works via Import).

# • Export/preview speed depends on FFmpeg and your audio formats.

