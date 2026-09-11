# APMA Transcription Product Contract

Status: implemented local MVP contract with explicitly identified production
and representative-evaluation gaps.

This document is the durable WHAT and WHY for APMA transcription development.
Implementation plans and code decide HOW. When implementation differs from this
target, the application and documentation must describe the implemented state
truthfully rather than imply the target has already been reached.

## Product Purpose

APMA is a local-first, file-based transcription-assurance application for
existing long-form recordings. It is optimized for English, Singapore
English/Singlish, Mandarin Chinese, Hokkien, Bahasa Indonesia, and natural
code-switching. It processes files asynchronously rather than competing as a
real-time meeting assistant. It must preserve source evidence, control billable
usage, retain provider outputs, and support useful human review. Minutes are an
optional derived output, not the primary product category.

## Input And Legacy Media

The target input set includes recordings exported from phones, recorders, PCs,
and messaging applications. FFprobe/FFmpeg runtime support determines whether a
particular file can be decoded. Target containers/codecs include WAV/PCM, MP3,
M4A/AAC, MP4, 3GP/3GA, AMR-NB, AMR-WB, WMA/ASF, OGG/Vorbis, OGG/Opus, FLAC,
WebM, and QCP/QCELP where the installed FFmpeg build supports them.

Invariants:

- Preserve the original media unchanged.
- Record a SHA-256 source hash.
- Avoid unnecessary lossy-to-lossy conversion.
- Preserve useful source-channel information.
- Do not claim that upsampling restores missing source information.
- Never commit private meeting audio.

## Approved Processing Target

Input recording -> immutable source copy -> SHA-256 -> FFprobe inspection ->
ingest QC -> FFmpeg decode -> canonical lossless audio -> quality classification
-> VAD -> silence-aware macro chunks -> resumable provider-neutral ASR -> raw
provider outputs -> global timestamp normalization -> meeting-global speaker
mapping -> candidate alignment -> uncertainty classification -> selective rescue
-> non-inventive reconciliation -> exception-based human QA -> canonical JSON ->
derived HTML -> optional text/document/subtitle exports.

## Current Implemented Baseline

The current repository implements this local, single-user baseline:

- Original source preservation, source metadata, SHA-256 verification, and a
  retained original/MP3/range-labelled MP3 chunk subproject.
- FFprobe inspection, FFmpeg normalization for supported modern and legacy
  inputs, deterministic PCM signal QC, and silence-aware macro chunk boundary
  selection with a bounded time-cut fallback. Silence detection is not claimed
  to be speech VAD.
- Dry-run mock transcription plus guarded OpenAI, MERaLiON, Gemini, and Qwen
  Filetrans adapters behind one registry and runtime-readiness policy.
- OpenAI roles for Economy/default, Recommended Multilingual, High Accuracy,
  and Speaker Labels, alongside explicitly selected external-provider routes.
- Independent raw provider artifacts, run-specific paths, canonical per-chunk
  records, source/global timestamps, and provider-native speaker evidence with
  explicit chunk-level identity scope.
- A manifest-backed quality workflow covering ingest, chunk, transcription,
  provider comparison, bounded selective rescue, strict final draft, and
  editable exception-based human review.
- Timestamp-based cross-provider alignment, conservative Green/Amber/Red
  agreement attention, and verbatim selection of an existing candidate for
  eligible Green regions. Similarity is not represented as confidence or
  correctness.
- Exact human candidate selection or manual correction with structured
  correction history; original provider evidence remains unchanged.
- Canonical JSON and derived HTML/TXT/SRT/VTT/DOCX/PDF exports. Untimed content
  does not receive fabricated timing.
- Preflight job cost caps, exact operator approval, run-aware actual-spend
  stopping, bounded retries, resumability, and SHA-256-validated cache reuse.
- Dependency-free offline WER/CER and transcript-contract regression checks,
  Docker dry-run CI, and bounded live-provider smoke-test procedures that use
  non-private audio.

The current baseline does not claim real speech VAD, meeting-global speaker
identity, provider superiority on representative Hokkien meetings, signed/WORM
evidence retention, authenticated multi-user operation, or production-scale
service reliability.

## Provider Architecture

Every provider/model run must retain an independent machine-readable artifact.
No provider output may overwrite another provider or prior materially different
run. Product roles must be registry-driven rather than duplicated in UI and
service code.

Provider codes:

- Hosted MERaLiON-3-ASR route: `M3ASR`
- OpenAI `gpt-transcribe`: `gptTr`
- OpenAI `gpt-4o-mini-transcribe`: `gpt4oMini`
- OpenAI `gpt-4o-transcribe`: `gpt4oTr`
- OpenAI `gpt-4o-transcribe-diarize`: `gpt4oDiarz`
- Gemini `gemini-3.5-transcribe`: `Gem35T`
- Qwen `qwen-audio-3.0-asr-flash-filetrans`: `QwenA3FT`

The retired Gemini `Gem37F` route remains historically readable but is not
selectable or submittable through the current application.

Provider selection must ultimately be based on APMA eval evidence. MERaLiON is
a specialist candidate, not a permanently hard-coded winner. Provider names,
availability, capabilities, and pricing must be revalidated before production
rollout.

## Transcript Authority And Provenance

JSON is the canonical machine/provenance representation. HTML is the planned
primary human view and must be derived from JSON. Human corrections must update
the structured record and correction history before views are regenerated.

Overlapping chunk content is preserved in canonical output. The current
baseline does not silently deduplicate or discard overlapping speech; a future
semantic merge stage must prove its behavior before changing this policy.

Each transcript record must identify, where applicable:

- schema version, meeting/job ID, provider, model, provider code, and run ID;
- source and chunk hashes;
- transcription-affecting options;
- original/global timestamps;
- canonical and provider-native speaker labels;
- raw provider artifact path;
- estimated and actual cost;
- errors and completion state.

## Diarization

Diarization is reliable only when speaker evidence survives every stage.
Provider/chunk-local labels must not be represented as meeting-global identity.
The current contract therefore records `speaker_identity_scope: chunk`.
Meeting-global mapping requires a later evidence-backed alignment stage.

Speaker name hints remain out of scope until true speaker labels exist. The
application must not infer speaker names from transcript order alone.

## Cost And Rerun Safety

- `DRY_RUN=true` remains the default.
- Every billable path requires explicit enablement and user confirmation.
- Preflight estimates must enforce configured job caps.
- Cumulative newly incurred cost must be checked before each billable chunk.
- Cached results count as zero new spend for the current run.
- Cache reuse requires matching source/chunk hash and transcription settings.
- Materially different reruns receive distinct run identity and retained raw
  artifacts; they must not silently overwrite evidence.

## Reconciliation And Quality Modes

Reconciliation is evidence alignment and selection, not creative rewriting. It
must preserve code-switching, unresolved uncertainty, candidates, and
provenance; it must not silently translate or invent speech for fluency.

- Economy: one provider.
- Recommended: primary provider plus selective rescue where eval policy calls
  for it.
- Max Quality / Benchmark: selected provider outputs retained and compared.

Do not run every paid provider over every meeting by default.

The executable plan must record the selected mode, ordered routes, readiness,
conditional rescue trigger, initial estimate, worst-case estimate, and cost-cap
result before any provider call. Planned provider/model visibility alone never
authorizes execution.

## Human Review

- Green: derived provider agreement permits deterministic selection of one
  unchanged candidate; it is not a correctness claim.
- Amber: uncertain or disagreement requiring review.
- Red: significant disagreement requiring stronger review.

The implemented exception-review view presents timestamps, speaker evidence,
unchanged provider candidates, nearby bounded audio, provider-candidate
selection, exact manual correction, and correction history. Human decisions do
not overwrite the retained provider artifacts.

## Archive Naming Target

Human-readable meeting directory:

`YYMMDDHHmm MeetingDate LikelyProject Content`

The timestamp represents the actual or best-established meeting start time, not
upload time. If unavailable, record the uncertainty and source of the fallback;
do not invent a meeting time. Retain a stable internal `meeting_id` separately.

Provider files:

`YYMMDDHHmm ShortName ProviderCode.json`

`YYMMDDHHmm ShortName ProviderCode.html`

Final files:

`YYMMDDHHmm ShortName FINAL.json`

`YYMMDDHHmm ShortName FINAL.html`

## Final Acceptance Target

The completed product can process a supported modern or legacy multi-hour
recording while preserving the source, identifying actual media properties,
decoding and chunking safely, resuming without duplicate charges, retaining all
selected provider evidence, preserving timestamps and speaker evidence,
reviewing uncertain regions, and producing provenance-aware final JSON plus a
derived human HTML view and common exports.

The local components of this flow are implemented and dry-run tested. Reports
must still distinguish component/test evidence from representative Hokkien
accuracy, sustained real-provider multi-hour operation, production security,
external adoption, or service-level proof.
