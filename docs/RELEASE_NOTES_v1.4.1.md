# Mio RealTime Translator v1.4.1

## Changes

- Claude/Anthropic translation backends are disabled for this release. Existing Claude selections migrate to the default translation service, and the Anthropic SDK is not included in the runtime or installer.
- Remote catalogs, saved fallback lists, and provider creation are guarded so the disabled backend cannot be re-enabled accidentally.
- Retains the v1.4.0 Edge speech recognition, optional SenseVoice local ASR, Qwen cloud voice enrollment, and realtime reliability improvements.

## Upgrade

Users on v1.4.0 or earlier can install this release directly. Existing configuration and downloaded local models are preserved; any saved Claude/Anthropic selection is migrated to the default translation service.
