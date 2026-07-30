# Mio RealTime Translator v1.3.9

## Changes

- Improved startup responsiveness by moving nonessential device discovery and maintenance work to the background.
- Reduced first-use delays with safe connection preparation for online translation, ASR, and TTS providers without triggering inference or local-model downloads.
- Improved local ASR model loading, download integrity, provider reuse, and shutdown cleanup.

## Upgrade

Users on v1.3.8.6 or later can update through Mio's in-app updater. Users on v1.3.8.5 or earlier must manually install this version once.
