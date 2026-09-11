# Live provider verification — 2026-09-11

This record covers one bounded MERaLiON request and one bounded Qwen Filetrans
request made by APMA against the same 11.627-second synthetic, non-private WAV.
Neither run used meeting audio.

| Provider code | Requested model | Result | Evidence boundary |
| --- | --- | --- | --- |
| `M3ASR` | `MERaLiON-3-3B-ASR-Consortium` | Completed; hosted response resolved to `MERaLiON/MERaLiON-3-3B-ASR-CTM` | Bounded research/evaluation access supplied by the MERaLiON team was used. Future availability remains a provider dependency. The request ran under a US$0.01 hard cap and recorded no charge. |
| `QwenA3FT` | `qwen-audio-3.0-asr-flash-filetrans` | Completed with provider-native timing and one provider speaker label | DashScope International accepted the size-bounded `data_uri` compatibility path. APMA estimated US$0.000420 under a US$0.01 hard cap and recorded US$0.000315 provider usage. |

Both jobs retained provider-coded JSON/HTML and raw-response evidence under
ignored local `jobs/` directories. Automated post-run checks confirmed that no
credential, authorization header, or embedded audio payload appeared in the
retained Qwen artifacts. A public-safe aggregate is committed at
[`evidence/live-provider-showcase-2026-09-11.json`](evidence/live-provider-showcase-2026-09-11.json).

The current [Qwen speech-to-text documentation](https://docs.qwencloud.com/developer-guides/speech/speech-to-text-models)
recommends `qwen-audio-3.0-asr-flash-filetrans` for offline/file transcription.
Because public documentation describes URL input rather than guaranteeing
`data:` URLs, APMA labels this as a compatibility path, enforces a raw-audio
limit, and retains private OSS as the documented larger-file route.

Thank you to the MERaLiON team at A*STAR I2R for making the Singapore-focused
live evaluation possible. This acknowledgement does not imply endorsement.
