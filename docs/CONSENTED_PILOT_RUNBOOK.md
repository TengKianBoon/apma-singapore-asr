# APMA Consented Pilot Runbook

Status: local, single-user pilot protocol. This is an operating aid, not legal
advice, permission to recruit, or permission to process a particular recording.

## Pilot question

Can APMA help independent users turn difficult, existing Southeast Asian audio
into a reviewable record with less effort and acceptable quality, cost, and
trust—without hiding model uncertainty or weakening data controls?

This is an initial proof pilot, not production deployment or broad capability proof.
Start with 3–5 consented design partners and require at least two completed jobs
per partner before interpreting repeat use. Expand only after the calibration
and governance gates below pass.

## What APMA records

The **Pilot outcome** tab records:

- pseudonymous cohort and participant codes;
- a structured use case and language mix;
- purpose-notice, consent-basis, provider-disclosure, withdrawal-route, and
  retention-review attestations;
- completion, usability, repeat-use intent, trust ratings, and a coded blocker;
- human review minutes, delivery labour/support cost, and price charged; and
- human-reference counts for dialect units, meaning units, critical terms,
  speaker turns, unsupported content, and omissions.

APMA derives duration, provider cost, segment count, correction count, review
state, and provider codes from the retained job. It does **not** copy audio,
transcript text, names, contact details, free-form notes, consent documents, or
participant codes into the aggregate export.

Each save creates a hash-linked local event. A later correction adds an event
and supersedes the current view; it does not silently erase the earlier pilot
record.

## Before inviting anyone

The owner should complete these outside APMA:

1. Name the pilot owner, data-protection contact, reviewer, and stop authority.
2. Fix the allowed use cases, providers, processing locations, retention
   period, deletion route, support channel, and incident route.
3. Prepare a plain-language purpose notice and consent or other reviewed lawful
   basis appropriate to the actual relationship and recording.
4. Explain that external AI providers may process the recording, accuracy is
   not guaranteed, and a human must verify consequential content.
5. Keep the participant-to-code mapping and consent record outside the APMA job
   folder and outside Git.
6. Decide whether participants may withdraw, what can be deleted, what audit
   fact must remain, and who performs the deletion.
7. Prohibit public excerpts by default. Aggregate outcome publication and media
   publication are separate decisions.

Singapore's PDPC lists notification, consent, purpose limitation, protection,
retention limitation, transfer limitation, breach notification, and
accountability among the relevant data-protection obligations. The in-product
attestations are evidence that a process was followed; they are not themselves
proof of legal compliance. See [PDPC Data Protection Obligations](https://www.pdpc.gov.sg/overview-of-pdpa/the-legislation/personal-data-protection-act/data-protection-obligations).

## Calibration stage

Before a participant pilot:

- complete at least five synthetic, licensed, or properly consented calibration
  jobs across the intended audio conditions;
- agree the annotation unit before counting accuracy—for example, a dialect
  phrase, semantic proposition, critical name/number/date, or speaker turn;
- have two reviewers independently score at least one shared sample and record
  their disagreements outside the aggregate;
- set baseline manual review time and current delivery cost using the same unit;
- test withdrawal/deletion, event-chain verification, and aggregate export; and
- fix thresholds before seeing pilot outcomes.

Zero reference units means **not measured**, not 100% accuracy.

## Per-job operating sequence

1. Verify the recording is in scope and the external governance record exists.
2. Assign cohort, participant, and consent-record codes; never enter a name.
3. Run the normal APMA cost preflight and obtain separate approval for any paid
   provider call.
4. Preserve the source and provider artifacts; complete transcript review and
   speaker correction.
5. Measure active human review time. Do not count unattended provider runtime as
   review labour.
6. Score the predetermined reference units against the reviewed transcript.
7. Ask the participant the same trust, usability, repeat-use, and blocker
   questions after every job.
8. Record the structured outcome. Confirm the event-chain status and cohort
   aggregate.
9. Review retention dates and withdrawal requests before any reuse or export.

## Metric definitions

| Decision area | Metric | Interpretation boundary |
| --- | --- | --- |
| Adoption | Completion, usable transcript, would-use-again, and repeat-participant rates | Repeat use requires two or more eligible jobs from the same pseudonymous participant |
| Correction effort | Review minutes per audio hour and corrections per 100 segments | Corrections measure human intervention, not necessarily model error unless the correction reason is independently classified |
| Dialect accuracy | Correct dialect reference units / checked dialect units | Report language mix, audio conditions, annotation method, sample size, and reviewer agreement |
| Decision accuracy | Meaning-unit and critical-term accuracy | Critical terms include predetermined names, numbers, dates, decisions, or domain terms; do not choose them after seeing errors |
| Speaker quality | Speaker-attribution errors / checked speaker turns | Provider labels remain provider/chunk scoped unless a human maps identity |
| Unit economics | Provider, labour, support, revenue, delivery cost/audio hour, and gross margin | Use actual cost where available; separate research/free access from a sustainable commercial price |
| Trust | Before/after trust, confidence to use/share, coded blocker | Trust is a user outcome, not proof of factual accuracy |

## Decision gates

Define numeric targets after calibration and before the participant stage. The
following gates apply regardless of commercial performance:

- **Stop:** missing consent/purpose record, unapproved processing, a material
  privacy/security incident, inability to honour withdrawal/deletion, evidence
  ledger failure, or consequential use without required human verification.
- **Hold and correct:** a recurring dialect, critical-term, speaker, workflow,
  cost, or trust failure has no bounded mitigation and retest.
- **Continue:** governance gates pass and the current tranche meets its
  precommitted accuracy, effort, adoption, trust, and cost thresholds.
- **Scale:** at least two independent-user cycles demonstrate repeat use, stable
  quality, supportable unit economics, controlled incidents, and a transferable
  operating process.

Do not replace a missed threshold after results arrive merely to make the pilot
look successful. Record the miss, correction, retest, and decision.

## Delivery evidence use

This pilot can strengthen evidence for:

- an end-to-end decision loop connecting users, providers, evidence, controls,
  cost, review, and exit criteria;
- field discovery and adaptation inside a real user workflow;
- incident, support, acceptance, and correction learning;
- named ownership, learning cadence, adoption, and changed work;
- consent, purpose, provider, retention, incident, and human-judgment controls;
- baseline/post effort, repeat use, willingness to pay, and unit economics;
- consented reference units, annotation quality, and drift history; and
- repeatable jobs, event integrity, tests, monitored controls, and evidence export.

It does not justify claiming production scale, broad dialect accuracy, or
repeatable commercial adoption. Those claims require larger, independent,
transferable, governed outcomes.

## Publication boundary

The downloadable cohort JSON is designed to omit participant codes,
consent-record references, source hashes, audio, and transcript text. Before
publication, still review small-cell re-identification risk, commercial
sensitivity, provider terms, participant expectations, sample size, missing
metrics, limitations, and whether public release was separately authorised.
