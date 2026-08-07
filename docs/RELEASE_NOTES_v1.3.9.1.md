# Mio RealTime Translator v1.3.9.1

## Changes

- Added no-key Microsoft Edge Web translation and improved Google Web response compatibility.
- Prepared manual and realtime translation clients in the background without blocking startup or typed input.
- Replaced slow synthetic Edge POST warm-ups with bounded transport-only connection probes.
- Reserved translation worker and queue capacity for reverse translation when desktop listening is enabled.
- Strengthened AI rewrite prompts and output validation so rewrite modes cannot answer the player or continue the conversation.
- Added an optimistic, gentle honor-student Japanese high-school rewrite persona.

## Upgrade

Users on v1.3.8.6 or later can update through Mio's in-app updater. Users on v1.3.8.5 or earlier must manually install this version once.
