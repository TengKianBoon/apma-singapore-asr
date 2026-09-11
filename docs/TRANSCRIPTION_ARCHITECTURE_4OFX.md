# APMA 4ofX Transcription Architecture Status

This document separates the current runnable product from planned architecture.
It prevents a visible model name from being mistaken for a completed live route.

## Runnable Now

- `gpt-4o-mini-transcribe` remains the safe default.
- `gpt-transcribe`, `gpt-4o-transcribe`, and
  `gpt-4o-transcribe-diarize` are selectable OpenAI routes.
- Current external routes include `M3ASR` for hosted
  `MERaLiON-3-3B-ASR-Consortium`, `Gem35T` for Gemini
  `gemini-3.5-transcribe`, and `QwenA3FT` for
  `qwen-audio-3.0-asr-flash-filetrans`.
- `Gem35T` provides verbatim
  audio transcription, diarization, and word timestamps.
- `QwenA3FT` supports guarded asynchronous long-file transcription. Small
  chunks may use the size-bounded `data_uri` compatibility path; private OSS
  remains the supported larger-file staging path.
- Every live run still requires an explicit gate, a server-side credential,
  operator confirmation, and a locked cost cap.
- The diarization route preserves provider speaker labels and timestamps when
  the provider returns them. It does not infer speaker names.
- Ingest uses an application-level no-overwrite policy and verifies source,
  copied-file size, and SHA-256 equality before processing.
- FFprobe inspects the selected audio stream for every accepted media format.
  Ingest rejects files with no audio stream, invalid duration, or duration over
  the configured meeting limit.
- The processing WAV records its own SHA-256 plus deterministic PCM clipping,
  near-silence, sampling-band, and channel evidence. These signal checks are
  explicitly recorded as not being speech VAD.
- The canonical full transcript JSON preserves candidate provenance and source
  SHA-256 evidence. Deterministic HTML, SRT, and VTT are derived from that JSON;
  caption exports omit untimed segments instead of inventing timestamps.
- The quality workflow can retain selected provider candidates, align them on
  the application timeline, classify derived agreement, rescue bounded
  uncertain regions, prepare a strict final draft, and record exact human
  decisions without overwriting provider evidence.
- Meeting archives can derive TXT, HTML, SRT, VTT, DOCX, and PDF views while
  keeping final JSON authoritative.

## External Provider Decisions

- `M3ASR` targets the official hosted MERaLiON speech-transcription route,
  advertised as `MERaLiON-3-3B-ASR-Consortium`. It is not assumed to be
  identical to the public `MERaLiON/MERaLiON-3-3B-ASR` checkpoint, which is
  reserved for a future self-host or benchmark path.
- M3ASR retains provider outputs as `YYMMDDHHmm ShortName M3ASR.json` and
  `YYMMDDHHmm ShortName M3ASR.html`.
- `Gem35T` uses `gemini-3.5-transcribe` with verbatim audio transcription,
  diarization, and word timestamps. `Gem37F` is retired from current selection
  and submission paths while historical artifacts remain readable.
- `QwenA3FT` uses the asynchronous DashScope Filetrans contract. Retained
  evidence is redacted so signed URLs, authorization data, and embedded audio
  payloads do not enter public artifacts.
- All external providers are disabled by default. Each requires an explicit
  provider gate, credentials supplied at runtime, verified pricing, and a
  preflight cost cap. No credentials belong in documentation or source files.
- Provider provenance records the requested model and, when returned by the
  service, the resolved model or version.
- The benchmark does not hard-code a permanent winner. Future production
  routing must be selected from representative, like-for-like Singlish,
  Hokkien, Mandarin, Bahasa Indonesia, and code-switching evidence covering
  accuracy, review effort, latency, and cost.

## Provider-Neutral Contract

`services/transcription/contracts.py` defines the first normalized ASR candidate
record. It preserves provider text, segments, raw payload, model identity,
chunk identity, request identity, and a deterministic SHA-256 hash. It does not
invent speech, timestamps, speakers, or confidence values.

## Strict Reconciliation Foundation

`services/transcription/reconciliation.py`,
`services/transcript_aggregator.py`, and the quality modules provide an
additive, deterministic candidate and exception-review workflow. When multiple
transcript records are supplied for comparable audio, APMA:

- retains every candidate, provider/model/run identity, transcript path, source
  hash, segments, and text hash;
- selects exactly one existing non-empty candidate verbatim and records why;
- never synthesizes, rewrites, or translates candidate speech;
- grades review attention conservatively as Green, Amber, or Red; and
- aligns timestamped candidates on an application-controlled timeline;
- restricts selective rescue to bounded uncertain regions;
- prepares a strict final draft without synthesizing competing speech; and
- supports exact provider selection or manual correction with structured
  history while preserving all source candidates.

Green requires exact normalized agreement across at least two independent
providers. A single candidate is Amber. Empty candidates, conflicting source
hashes, or material disagreement cannot silently become accepted text.

The workflow does not calibrate provider confidence or claim correctness from
agreement. Meeting-global identity, semantic equivalence across dialects, and
representative provider ranking remain outside the demonstrated boundary.

`services/transcription/orchestration.py` defines a non-networked execution plan
for Economy, Recommended, and Max Quality / Benchmark. It exposes initial and
worst-case cost bounds, provider readiness, conditional Amber/Red rescue routes,
and blockers without returning credentials.

## Still Planned

The full target architecture is not yet complete. Remaining work includes:

- measured provider comparison and production routing based on the benchmark
  criteria above;
- filesystem or remote WORM retention and signed evidence bundles beyond the
  current application-level no-overwrite source copy;
- acoustic speech/noise scoring and real VAD beyond the current structural
  media and PCM signal checks;
- meeting-global speaker continuity and reviewed speaker-name mapping;
- calibrated linguistic/semantic comparison across Hokkien and code-switching
  varieties beyond timestamp alignment and text similarity;
- authenticated multi-user operation, scoped permissions, monitoring,
  incident response, and production service-level objectives;
- representative provider-specific golden audio, drift history, and production
  release gates. The current offline scorecard/release-gate contract is a
  deterministic foundation, not comparative production evidence.

Each item should land as a bounded, dry-run-tested increment. A provider must
not become runnable merely because its model ID is present in configuration.
