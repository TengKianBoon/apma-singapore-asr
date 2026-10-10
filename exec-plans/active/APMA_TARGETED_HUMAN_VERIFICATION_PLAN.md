# APMA Targeted Human Verification Upgrade

## Objective

Add an optional, low-friction human audio verification step to Compare & Verify. APMA should identify the uncertain or speaker-sensitive windows; the human should listen only to those exact clips and record content and speaker decisions before release.

This increment remains a local, single-user workflow. It does not add payment, marketplace, reviewer-ordering, or multi-tenant features.

## Design

1. Add an opt-in control to Compare & Verify. The choice records the user's review intent; it does not make a provider call or claim a guaranteed accuracy improvement.
2. Persist the requested scope in the quality job manifest and return it in the dashboard result.
3. Extend the existing file-backed review workspace instead of creating a parallel review system:
   - content: provider candidate, manual correction, or still unclear;
   - speaker: confirmed, uncertain, overlap, or not applicable;
   - original provider candidates and provider-native speaker evidence remain unchanged.
4. Show exact operational evidence: selected clip count and duration, source-audio share, RED/AMBER counts, content decisions, speaker decisions, and unresolved items.
5. Use honest release labels:
   - Automated transcript;
   - Selected-window human confirmed;
   - Selected-window human reviewed with unresolved items.
6. Improve the review screen with replay, playback-speed controls, explicit speaker checks, autosave, and a clear next-window action.

## Acceptance criteria

- Targeted verification is optional, off by default, and available only for Compare & Verify.
- The preference is persisted and is not silently reset when a quality job resumes.
- Marking a clip still unclear records human attention but leaves it unresolved and does not invent final text.
- Content decisions and speaker decisions are stored separately and survive refresh/reopen.
- Metrics are calculated from retained artifacts and decisions; no generic accuracy uplift is claimed.
- A selected-window completion label never implies that the full recording was manually reviewed.
- Provider candidates, hashes, timestamps, and speaker evidence are not overwritten.
- Existing cost preflight and live-call confirmation remain mandatory; dry-run remains the development and CI default.
- Unit and dry-run integration tests pass inside Docker on Python 3.11 or 3.12.
- Documentation describes the improved workflow and its assurance boundary without secrets or private audio.

## Verification plan

- Focused tests for content/speaker decisions, unresolved handling, metrics, manifest persistence, API payloads, and dashboard contract.
- Full pytest suite in Docker.
- Browser QA of the opt-in control, targeted review summary, clip controls, decision persistence, and responsive flow using only synthetic/test artifacts.

## Implementation record — 2026-10-10

1. Added a Compare & Verify opt-in that is hidden and reset for single-route modes. The existing cost estimate and explicit live-run approval remain separate and unchanged.
2. Persisted the requested assurance scope in the quality manifest without allowing a resumed job to silently lose a prior opt-in.
3. Extended the review record with `unclear` content decisions and a separate speaker-decision schema. Provider text and provider-native speaker evidence remain immutable.
4. Added speaker-sensitive selection for opt-in jobs when provider-native evidence contains multiple speakers, missing speaker labels, or overlapping differently labelled segments. Classic non-opt-in jobs do not acquire a new speaker-review completion gate.
5. Added selected-window metrics and the three bounded assurance labels. The workflow explicitly records that no accuracy uplift is claimed.
6. Added replay and playback speed controls, separate speaker buttons, filter-aware outstanding state, and a visible evidence-preservation message.
7. Updated the public-safe architecture, showcase, professional-flow, README, and solution-architecture/FDE evidence documents.

Verification completed with no live provider call:

- focused Docker suite: 57 passed;
- complete Docker suite: 308 passed on Python 3.11 with `DRY_RUN=true`;
- browser QA: synthetic file inspection, opt-in mode boundary, selected-window summary, persisted unclear/speaker decisions, and 390-pixel responsive review;
- browser QA found and corrected one speaker-status spacing defect;
- changed-file confidentiality scan found no credential, private path, or confidential-client reference.
