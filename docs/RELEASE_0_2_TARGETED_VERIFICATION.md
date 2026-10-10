# Release 0.2 — Targeted Human Verification

Release date: 2026-10-10

## Outcome

APMA now identifies uncertain or speaker-sensitive windows and lets a reviewer
listen only to those exact clips before release. Content and speaker decisions
are recorded separately, original provider evidence remains unchanged, and the
system reports how much of the source audio was selected for review.

This release also adds the supporting operating loop: privacy-minimised pilot
evidence and a guarded commercial quote model. Live payment remains disabled.

## User workflow

`Import → Inspect & price → Transcribe → Compare → Verify selected clips → Export → Record pilot outcome`

The reviewer can:

- accept a provider candidate, enter a correction, or mark content unclear;
- confirm a speaker label, mark it uncertain, identify overlap, or record that
  speaker review does not apply;
- replay a clip and change playback speed;
- see selected clip count, selected duration, source-audio share, outstanding
  decisions, and the exact assurance label; and
- reopen the job without losing saved decisions.

## Engineering decisions

- The feature is optional and available only in Compare & Verify mode.
- Human verification does not bypass provider cost preflight or live-run approval.
- Provider text, timestamps and native speaker evidence remain immutable.
- `unclear`, `uncertain` and `overlap` are honest outcomes, not forced completions.
- Selected-window completion never implies that the complete recording was
  manually reviewed.
- Accuracy improvement is not claimed until a consented pilot measures it.
- Pilot aggregates exclude transcript text, audio, names, contact details and
  direct participant identifiers.
- Quote calculations enforce a cost floor and operating reserves; payments stay off.

## Verification

All verification used synthetic or repository-safe fixtures. No live provider
call or billable request was made.

- complete Docker suite: **308 passed** on Python 3.11;
- targeted verification and dashboard suite: **57 passed**;
- pilot evidence and dashboard suite: **38 passed**;
- commercial pricing suite: **14 passed**;
- runtime controls: `DRY_RUN=true`, mock transcription, live providers disabled,
  and Docker networking disabled; and
- confidentiality review: no credentials, private audio, private Windows path,
  or confidential-client material in the release diff.

## Current boundary

APMA remains a local, single-user product. It is not yet an authenticated hosted
service and does not claim perfect Hokkien recognition, production scale,
external adoption, or commercial traction. Those are evidence thresholds for a
consented external pilot and a later hosted-commercial decision.

See [Engineering Decisions and Field Learnings](ENGINEERING_DECISIONS_AND_FIELD_LEARNINGS.md)
for the design trade-offs and [AI Product Delivery Roadmap](AI_PRODUCT_DELIVERY_ROADMAP.md)
for the next proof thresholds.
