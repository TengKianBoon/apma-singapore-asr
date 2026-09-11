# APMA Solution Architecture, FDE And AIRI Evidence

Status: public-safe, evidence-linked portfolio map. AIRI is used as a readiness
and governance lens; this document does not claim certification or assign a
technical job level.

## Executive Claim

APMA demonstrates end-to-end AI product and solution architecture for a narrow,
difficult workflow: accountable transcription of existing Southeast Asian
recordings containing Hokkien, Singlish, Mandarin, English, and other
code-switching.

The strongest demonstrated competencies are solution framing, provider-neutral
architecture, data and evidence authority, cost and privacy controls,
human-in-the-loop review, and rapid correction from observed product friction.
The project also demonstrates meaningful Forward Deployed Engineer (FDE)
behaviours through hands-on provider integration, legacy-audio handling,
failure recovery, browser workflow delivery, and test-backed iteration.

It does not yet prove senior field deployment, production-scale reliability,
multi-user security, customer adoption, or commercial expansion. Those require
external pilot evidence.

## Inspectable Evidence Chain

| Competency | Shipped evidence | What it justifies | Important limitation |
| --- | --- | --- | --- |
| Problem and niche definition | [Product contract](../product-specs/APMA_TRANSCRIPTION_PRODUCT_CONTRACT.md) and [professional UI decision](../product-specs/APMA_PROFESSIONAL_UI_FLOW.md) | Translates an ambiguous multilingual transcription problem into users, outcomes, non-goals, risk, and acceptance boundaries | Needs design-partner discovery records and baseline workflow measurements |
| End-to-end architecture | [Architecture map](../ARCHITECTURE.md), provider registry, canonical transcript, raw artifacts, review decisions, and derived exports | Separates experience, orchestration, processing, evidence, and human-decision responsibilities | Current deployment is local and single-user |
| Multi-provider integration | [Provider adapter](../services/transcription/external_adapter.py), [routing](../services/transcription/router.py), and [bounded live verification](LIVE_PROVIDER_VERIFICATION_2026-09-11.md) | Integrates OpenAI, MERaLiON, Gemini, and Qwen behind explicit model and evidence contracts | A bounded synthetic live check is connectivity evidence, not dialect-accuracy proof |
| Reliability under field constraints | Hash-bound ingest, duration accounting, format normalization, silence-aware chunking, retry/resume state, and duplicate-spend prevention | Shows pragmatic handling of long, legacy, size-limited, and partially failed jobs | No production SLO, load test, or incident record yet |
| Human and evidence authority | [Quality workflow](../services/quality_workflow.py), [human review](../services/human_review.py), and speaker mapping | Preserves unchanged candidates, isolates uncertainty, and records manual decisions without inventing speaker identity | Human-review efficiency still needs measurement on consented audio |
| Product usability and correction velocity | Guided dashboard, real upload progress, recent jobs, timestamp-linked playback, review filters, and [synthetic browser QA](evidence/ui-flow-showcase-2026-09-11.json) | Shows a user-facing workflow delivered inside existing architecture and corrected after a real responsive-layout defect was observed | Usability has been tested technically, not yet with external users |
| Governance and security-by-default | `DRY_RUN=true`, explicit live gates, per-job caps, local credentials, secret redaction, ignored private jobs/audio, and clean-history publication | Implements risk controls in code, operations, CI, and release process | Needs threat modelling, authenticated roles, retention controls, and independent review before hosting |
| Commercial architecture | Outcome-based modes, provider-cost preflight, capped approvals, and cost-plus positioning | Connects provider choice and assurance depth to a controllable unit-cost envelope | No willingness-to-pay, support-cost, gross-margin, or renewal evidence yet |

## Solution Architecture Competencies Demonstrated

1. **Requirements and anti-requirements.** APMA chooses offline file
   transcription assurance and explicitly rejects real-time meeting bots,
   unrestricted collaboration, and premature enterprise integrations.
2. **Separation of concerns.** The browser experience calls a thin local API;
   Python services own chunking, transcription, comparison, review, and export;
   `n8n` remains orchestration-only.
3. **Data authority.** Source hashes, raw provider responses, canonical JSON,
   human decisions, and derived views have distinct authority. A polished view
   cannot silently replace evidence.
4. **Provider and failure architecture.** Models are registered by role and
   capability, readiness fails closed, retries are bounded, and cached work is
   source/settings-bound to avoid duplicate spend.
5. **Non-functional trade-offs.** Local-first, single-user delivery reduces the
   first release's security and operating surface. Authenticated hosting,
   workers, observability, and SLOs are sequenced after demand rather than
   disguised as completed features.
6. **Trust architecture.** Cost, provenance, timing authority, speaker scope,
   uncertainty, and manual correction are visible parts of the product flow.

## FDE Behaviours Demonstrated

1. **Work from messy reality.** The implementation supports long recordings,
   legacy containers, provider file limits, code-switching, missing timestamps,
   chunk-scoped speakers, and interrupted runs.
2. **Integrate and debug real providers.** MERaLiON and Qwen Filetrans were
   adapted and re-verified with synthetic audio, including Qwen's bounded
   `data_uri` compatibility path and artifact redaction.
3. **Ship through the existing system.** The professional workflow was added
   without moving heavy processing into the browser layer or weakening dry-run
   and cost gates.
4. **Observe, correct, verify.** Browser QA found a tablet grid-stretch defect;
   the smallest CSS correction was applied and rechecked at an 800-pixel
   viewport, where the recent-jobs card returned to intrinsic height.
5. **Turn learning into controls.** Provider failures, unsafe assumptions, and
   user friction are captured as tests, decision boundaries, or documented
   deferrals rather than one-off fixes.

These are selected FDE behaviours, not proof of a senior FDE level. The missing
evidence is delivery in an external customer environment, monitored production
operation, incident recovery, repeat adoption, and business expansion.

## AIRI-Aligned Evidence

| AIRI pillar | Current proof from APMA | Evidence still required for a credible Catalyst trajectory |
| --- | --- | --- |
| Leadership & Culture | Product direction, explicit decision ownership, rapid provider and UX corrections, and documented build/defer choices | Team work redesign, mentoring, operating cadence, and adoption across people |
| Ethics & Governance | Privacy boundary, human judgment, provenance, cost approval, fail-closed readiness, and clean release controls | Threat model, recurring risk review, incident exercise, assurance ownership, and independent challenge |
| Business Value | Defined niche, outcome modes, cost boundary, and proposed low-price/cost-plus model | Measured time saved, willingness to pay, repeat use, revenue, support cost, and gross margin |
| Data Foundation | Source hashes, duration coverage, raw artifacts, canonical schema, corrections, and synthetic fixtures | Licensed or consented dialect evaluation data, annotation quality, baselines, and drift history |
| Infrastructure & Standards | Docker, CI, provider contracts, retry/resume, artifact redaction, tests, and local credential handling | Authentication, roles, observability, SLOs, supply-chain controls, and production pilot evidence |

The evidence supports Builder-level orchestration and selected Catalyst
behaviours. It does not establish AIRI Level 4. The fastest honest upgrade is
not another self-description; it is a controlled pilot that produces adoption,
risk, quality, and unit-economics evidence.

## My Contribution

> I identified and scoped the underserved local-language workflow, established
> the product and evidence boundaries, selected and re-evaluated provider
> routes, directed the implementation sequence, enforced privacy and cost
> controls, and accepted release and commercial trade-offs. AI agents
> accelerated implementation and verification; I retained responsibility for
> product direction, risk acceptance, positioning, and publication decisions.

## Next Proof Threshold

Run a small, consented design-partner pilot and publish aggregate, non-sensitive
evidence for:

- upload-to-reviewed-transcript time and human correction minutes per audio hour;
- completion, repeat-use, and reviewer-satisfaction rates;
- disagreement, omission, speaker-attribution, and key-fact error rates;
- provider cost, support time, selling price, and gross margin by outcome mode;
- one failure/recovery exercise and one retention/deletion check; and
- the keep/change/stop decision resulting from those measurements.

That evidence would materially strengthen FDE, solution architecture, AI
product, and AIRI-aligned claims without overstating what a repository alone can
prove.
