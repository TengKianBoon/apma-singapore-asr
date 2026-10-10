# APMA Consented Pilot Evidence Plan

Status: approved for local implementation on 2026-09-11. This plan does not
authorise recruitment, external messages, public hosting, real recordings,
provider expenditure, or publication of pilot results.

## Outcome

Turn APMA's next-proof statement into a small, usable evidence loop for a
consented external pilot. The product should measure whether independent users
can complete the offline transcription-assurance workflow, how much human
correction and review it needs, how well dialect and critical content survive,
what delivery costs, and whether users trust and would reuse the result.

## Design

1. Add a Python-owned pilot evidence service. It reads existing job duration,
   provider cost, review state, segments, and correction history; it never
   copies transcript text into the pilot dataset.
2. Require pseudonymous cohort and participant codes. Reject names, contact
   details, notes, excerpts, transcript content, and other free text.
3. Keep synthetic QA records distinct from evidence-eligible consented pilot
   records. Aggregate outcome evidence excludes synthetic records by default.
4. For a real pilot record, require operator attestations that purpose notice,
   consent or another documented basis, provider processing disclosure, a
   withdrawal route, and a retention-review date exist outside APMA. Store only
   the external consent-record reference.
5. Capture bounded, structured inputs for review effort, reference accuracy,
   delivery cost, price, usability, trust, repeat-use intent, and blockers.
6. Calculate correction density, review effort per audio hour, provider and
   delivery cost per reviewed audio hour, gross margin, dialect/meaning/critical
   term accuracy, speaker-attribution error, and trust change.
7. Preserve each submission as a hash-linked event and expose only the latest
   record per job to cohort aggregation.
8. Add a progressive Pilot outcome tab and JSON evidence export to the local
   dashboard. It remains local/single-user and makes no provider call.

## Acceptance criteria

- Real pilot evidence cannot be saved without every required governance
  attestation and a valid retention-review date.
- Pseudonymous codes are constrained; direct identifiers and free-text content
  are rejected recursively.
- Pilot artifacts contain no audio bytes, transcript text, participant names,
  email addresses, phone numbers, or credentials.
- Derived job facts come from retained APMA artifacts rather than user re-entry.
- Correct counts cannot exceed reference totals; ratings and monetary inputs
  have explicit bounds.
- Aggregate results include adoption, correction effort, dialect accuracy,
  unit economics, and trust while suppressing participant codes and consent
  references.
- Synthetic records are visible for QA but excluded from evidence-eligible
  pilot outcomes.
- Unit and dashboard integration tests cover validation, aggregation, security
  boundaries, update history, and UI/API exposure.
- The complete Docker dry-run suite passes with no live provider call.

## Go/no-go boundary

The implementation is pilot-ready only after tests and synthetic browser QA.
It is not production-ready and does not prove adoption. Recruiting participants,
using real recordings, agreeing commercial terms, or publishing aggregates
requires a separate owner decision and an appropriate privacy/legal review.
