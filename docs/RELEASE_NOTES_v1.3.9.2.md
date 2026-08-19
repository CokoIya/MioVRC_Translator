# Mio RealTime Translator v1.3.9.2

## Changes

- Fixed credential validation so locally hosted OpenAI-compatible AI services no longer require an unnecessary cloud API key.
- Fixed target-language routing for Spanish, French, Russian, Korean, and other languages across AI and web translation providers.
- Strengthened translation prompts and output validation so providers must return only the selected target language without answering the player or adding explanations.
- Added a strict corrective retry when a provider returns text in an obviously incorrect language.
- Added compatibility with single-field JSON translation responses from local AI services.
- Improved localhost connection behavior on Windows and clarified provider prewarm diagnostics.
- Reduced sensitive device, process, and audio inventory details in logs.

## Upgrade

Users on v1.3.8.6 or later can update through Mio's in-app updater. Users on v1.3.8.5 or earlier must manually install this version once.
