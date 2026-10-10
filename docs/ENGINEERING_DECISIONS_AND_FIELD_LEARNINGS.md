# APMA Engineering Decisions And Field Learnings

Status: public-safe, evidence-linked delivery record.

## The problem being solved

APMA turns existing Southeast Asian recordings into reviewable evidence rather
than presenting one model's polished text as unquestioned truth. The difficult
case is a long, naturally code-switched conversation containing Hokkien,
Singlish, Mandarin, English, unclear audio, and imperfect speaker separation.

The system is intentionally an asynchronous, local-first workflow. It does not
compete with real-time meeting assistants. It concentrates on what each model
heard, what evidence covers the complete recording, what still needs attention,
and what a human is prepared to release.

## Inspectable decision chain

| Decision area | Shipped evidence | Practical effect | Current boundary |
| --- | --- | --- | --- |
| Product scope | [Product contract](../product-specs/APMA_TRANSCRIPTION_PRODUCT_CONTRACT.md) and [professional flow](../product-specs/APMA_PROFESSIONAL_UI_FLOW.md) | Converts an ambiguous transcription request into users, outcomes, non-goals, risks, and acceptance criteria | External workflow discovery is still limited |
| System boundaries | [Architecture map](../ARCHITECTURE.md), thin local API, Python services, and orchestration boundary | Keeps heavy processing, evidence authority, and user interaction separate and testable | Current deployment is local and single-user |
| Provider integration | Provider registry, adapters, routing, and [bounded live verification](LIVE_PROVIDER_VERIFICATION_2026-09-11.md) | Allows specialist and general models to be evaluated without silently blending their outputs | Connectivity checks are not dialect-accuracy proof |
| Evidence authority | Source hashes, raw provider artifacts, canonical JSON, review decisions, and derived exports | Prevents a polished view from replacing original evidence | Retention and role-based access still need hosted controls |
| Field reliability | Duration accounting, legacy-format normalization, safe chunking, retry/resume state, and duplicate-spend prevention | Handles long, awkward, interrupted, and provider-limited jobs conservatively | No production service-level objective or load record yet |
| Human verification | [Targeted verification](TARGETED_HUMAN_VERIFICATION.md), exact audio clips, separate content and speaker decisions | Directs human attention to uncertain or speaker-sensitive windows while preserving unresolved states | Improvement must be measured in a consented pilot |
| Cost and safety | Dry-run defaults, readiness checks, explicit live approval, per-job caps, and [quote model](COMMERCIAL_PRICING.md) | Makes provider and review cost visible before billable work starts | Live payment remains disabled |
| Learning loop | [Consented pilot runbook](CONSENTED_PILOT_RUNBOOK.md) and privacy-minimised aggregates | Connects product changes to adoption, correction effort, quality, trust, and unit economics | Real participant outcomes have not yet been collected |

## Architecture choices

1. **Choose a narrow operating problem.** APMA focuses on offline recorded
   conversations where dialect coverage, speaker attribution, and auditability
   matter more than real-time captions.
2. **Separate responsibilities.** The browser is a thin control surface;
   Python owns media processing, model calls, comparison, verification, and
   export; orchestration does not absorb heavy processing.
3. **Define data authority.** Source media, provider responses, canonical
   transcript data, human decisions, and presentation formats have distinct
   roles. Derived output never silently overwrites raw evidence.
4. **Treat providers as capabilities, not brands.** Routing records the model,
   readiness, cost, timing and speaker scope used for a job. Failure is visible
   and bounded rather than hidden behind fallback text.
5. **Design for interrupted work.** Hash-bound state, bounded retries, cached
   artifacts and resume rules reduce duplicate spend and make recovery
   inspectable.
6. **Spend human attention selectively.** The system selects uncertain or
   speaker-sensitive clips, but a reviewer can still mark words unclear,
   speakers uncertain, or audio overlapping.
7. **Sequence infrastructure to evidence.** Local-first delivery limits the
   initial security and operating surface. Authentication, workers,
   observability and service objectives belong to a later hosted phase after
   demand and workflow fit are measured.

## Field learnings converted into controls

- Legacy containers and inconsistent codecs became explicit ingest and media
  capability checks rather than undocumented operator knowledge.
- Provider availability, file limits and response shapes became adapter
  contracts, readiness failures and bounded compatibility paths.
- Speaker labels remain recording- and provider-scoped; they are never treated
  as real identities until a human maps them.
- Responsive browser testing exposed a tablet layout defect. The smallest CSS
  correction was applied and protected by continued browser review.
- A completed review is not automatically an accurate transcript. Completion,
  unresolved items, selected-audio coverage and source duration are reported
  separately.
- Pilot metrics use explicit denominators and privacy-minimised aggregates so a
  promising anecdote cannot become an unsupported performance claim.

## Delivery ownership

The product direction, operating boundary, provider trade-offs, implementation
sequence, cost and privacy controls, release decisions, and public claim limits
were set and reviewed by the project owner. AI coding agents accelerated
implementation and verification; responsibility for decisions, acceptance and
publication remained human.

## Next proof threshold

A small consented pilot should measure:

- upload-to-reviewed-transcript time and human correction minutes per audio hour;
- completion, repeat-use and reviewer-satisfaction rates;
- disagreement, omission, speaker-attribution and key-fact error rates;
- selected-review duration versus total source duration;
- provider cost, support time, selling price and gross margin by mode;
- one controlled failure-and-recovery exercise; and
- the resulting keep, change, pause or scale decision.

Those outcomes—not a job title or self-assigned level—are the next evidence of
whether the design creates useful and trustworthy work in practice.
