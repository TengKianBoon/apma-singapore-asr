# APMA Architecture

## Authority

The product contract is `product-specs/APMA_TRANSCRIPTION_PRODUCT_CONTRACT.md`.
This document maps that intent to the current code. Active delivery work is
tracked under `exec-plans/active/`.

## Current Runtime Flow

1. `services.ingest` validates and copies an input into the job source folder,
   then records metadata and SHA-256.
2. `services.preprocess` validates WAV input or uses FFmpeg to normalize a
   supported non-WAV file to canonical WAV.
3. `services.chunker` writes bounded WAV chunks and chunk metadata, preferring
   nearby detected silence for a cut when it remains within the hard size and
   duration constraints.
4. `services.transcription.models` and `services.transcription.router` supply
   one registry for model roles, provider codes, readiness, response formats,
   diarization capability, and billing policy across OpenAI, MERaLiON, Gemini,
   and Qwen Filetrans.
5. Provider adapters write canonical per-chunk transcript JSON and retain raw
   provider responses by run. Live execution remains opt-in and cost-gated.
6. `services.quality_workflow` coordinates the resumable multi-provider quality
   stages: ingest, chunk, transcribe, compare, selective rescue, final draft,
   and review. Cache reuse remains bound to source/chunk hashes and settings.
7. `services.transcript_alignment`, `services.transcript_agreement`, and
   `services.selective_rescue` align application-timed evidence, assign
   conservative Green/Amber/Red attention, and restrict paid rescue to bounded
   regions. Derived similarity is not provider confidence or correctness.
8. `services.final_draft` selects an existing provider candidate verbatim for
   eligible Green regions. Amber and Red remain review-required; the system
   does not silently merge, translate, or clean competing speech.
9. `services.human_review` and `services.transcript_corrections` preserve exact
   human content decisions, separate speaker-verification decisions, and
   correction history without modifying source provider evidence. Targeted
   verification reports selected clip duration and completion scope from
   retained artifacts rather than presenting a generic accuracy score.
10. `services.transcript_exports` and `services.meeting_archive` derive
    HTML/TXT/SRT/VTT/DOCX/PDF views and durable meeting packages from canonical
    JSON. Untimed segments do not receive invented timestamps.
11. `services.minutes` produces dry-run or separately approved live minutes.
12. `services.runner` owns job state, errors, costs, and output references in
    the manifest.
13. `services.pilot_evidence` derives correction, duration, review and provider
    cost facts from retained jobs; validates structured consent, accuracy,
    adoption, trust and commercial inputs; preserves a hash-linked local event
    history; and emits privacy-minimised cohort summaries.
14. `scripts/local_dashboard.py` is a local browser interface over the service
    layer. It must not become the owner of heavy processing.

## Experience And Control Plane

`web/dashboard.html` implements the outcome-led operator journey without
duplicating processing logic. It consumes bounded local endpoints for provider
readiness, upload/preflight, job lists, job details, audio playback, approved
runs, review, minutes, exports, and structured pilot outcomes.

The dashboard design preserves these architectural decisions:

- user-facing modes select an existing configured provider route or the fixed
  comparison workflow; the browser does not invent a separate routing engine;
- real upload progress and job states are visible, but durable distributed
  queues and leave-the-page processing are not claimed by the local MVP;
- recent jobs are derived from retained manifests rather than a competing UI
  database;
- byte-range audio responses support timestamp seeking while path validation
  prevents a job identifier from escaping the storage root;
- transcript segments seek audio by retained application/provider timing, while
  exact exception correction and speaker relabelling remain provenance-bearing
  human decisions;
- the optional targeted-verification preference is manifest-backed; its review
  screen keeps content and speaker adjudication separate and never labels
  selected-window review as full-audio human review; and
- minutes and presentation exports remain visibly derived from the canonical
  transcript rather than becoming new transcript authorities; and
- the pilot tab sends structured outcomes to the Python evidence service;
  browser code does not calculate official cohort metrics or copy transcript
  text into the pilot dataset.

This boundary allows the interface to improve independently while Python
services remain the single owner of provider calls, costs, retries, evidence,
and final job state.

## Data Authority

- Job manifest: processing state, input/chunks, outputs, errors, and costs.
- Canonical transcript JSON: transcript content and provenance authority.
- Raw provider artifact: immutable evidence of each provider response/run.
- TXT and future HTML/DOCX/PDF/SRT/VTT: derived views or exports.
- Pilot event ledger: append-only structured outcome evidence and derived job
  facts; the latest event is the current per-job pilot view.
- Pilot cohort summary: derived, privacy-minimised aggregate; never a transcript
  or participant-identity authority.

No derived view may silently become a competing source of truth.

## Safety Boundaries

- Dry-run is the default and CI/test mode.
- Live use requires explicit gates, an environment API key, confirmation, and
  cost preflight.
- Source and chunk hashes protect cache reuse.
- Job IDs are validated before filesystem path construction.
- Private audio, provider responses, and job outputs remain ignored local data.
- Consented pilot records require purpose, consent-basis, provider-processing,
  withdrawal-route, and retention-review attestations. Synthetic QA is excluded
  from evidence-eligible aggregates.
- Pilot inputs are allowlisted and reject names, contact fields, transcript
  content, excerpts, notes, and other unsupported/free-text fields.

## Diarization Boundary

`gpt-4o-transcribe-diarize` can provide speaker-labelled segments. Canonical
records preserve both normalized and provider-native labels. Labels are scoped
to a chunk until a future global speaker-mapping stage proves continuity across
chunks. Speaker names are not guessed.

## Evaluation Boundary

`services.evaluation.asr` provides offline WER/CER metrics and canonical
contract validation. `scripts/run_asr_evals.py` converts the synthetic golden
set into a machine-readable report. Provider ranking requires representative,
lawfully usable audio and must not be inferred from synthetic text-only cases.

## Planned Extensions

Planned work remains incremental:

- representative, lawfully usable Hokkien/Singlish/Mandarin/English evaluation
  audio, human reference annotations, and provider drift history;
- calibrated linguistic evaluation and routing based on like-for-like evidence,
  rather than a permanently hard-coded provider winner;
- acoustic speech/noise classification and real VAD beyond the current PCM
  signal checks and FFmpeg silence-boundary selection;
- meeting-global speaker continuity and name mapping backed by reviewable
  evidence rather than chunk-local labels;
- signed or WORM evidence bundles beyond application-level no-overwrite and
  SHA-256 controls; and
- authenticated multi-user deployment, scoped roles, monitoring, incident
  response, and production service-level objectives.

The current product remains a local, single-user MVP. Each later stage requires
tests and must preserve the data-authority and safety boundaries above.
