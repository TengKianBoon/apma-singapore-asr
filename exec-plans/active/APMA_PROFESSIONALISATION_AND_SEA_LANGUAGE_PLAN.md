# APMA Professionalisation And Southeast Asia Language Plan

Status: decision-ready product and delivery plan. No public deployment, live
provider spend, customer-data processing, or production-support claim is
authorised by this document.

## Outcome

Make APMA professional enough to represent Yingfluence publicly while keeping
its strongest distinction clear:

> APMA shows what each model heard, what the evidence supports, and what still
> requires human review in Southeast Asian multilingual conversations.

The first release is a public-safe, synthetic-data showcase. A separate,
invite-only processing pilot follows only after hosted security and operating
controls pass their own gate.

The detailed screen flow, competitive workflow decisions, and UI acceptance
criteria are defined in
[`APMA_PROFESSIONAL_UI_FLOW.md`](../../product-specs/APMA_PROFESSIONAL_UI_FLOW.md).

## Strategic Position

APMA's chosen category is **Southeast Asia multilingual transcription
assurance for existing audio files**. It processes long or legacy recordings
asynchronously and helps people review model evidence after recording. It
should not try to out-build general meeting assistants on live capture,
calendar bots, real-time note-taking, CRM integrations, or cross-meeting chat.

Its defensible wedge is governed review of speech that changes among languages
and dialects, especially where a fluent-looking transcript can conceal
omissions, wrong language assumptions, speaker errors, or model disagreement.
Useful market-leader patterns may be adopted for import, progress, playback,
editing, and export, but APMA does not inherit the meeting-assistant category.

The primary early buyer is not necessarily an individual seeking inexpensive
transcription. Better initial users are:

- AI and product companies evaluating speech systems for Southeast Asia;
- research, oral-history, and qualitative-insight teams;
- professional-services teams reviewing multilingual interviews or meetings;
  and
- organisations that need an accountable review record rather than an opaque
  transcript.

Suggested Yingfluence offer ladder:

1. **SEA Speech AI Diagnostic** — test a defined language, data, privacy, and
   deployment problem.
2. **SEA Speech AI Evaluation Sprint** — compare providers using lawful test
   audio and an agreed scorecard.
3. **Governed Pilot And Adoption** — configure review, security, operating
   controls, training, and outcome measurement for a bounded team.

APMA is the working evidence engine behind these services, not merely a demo
claiming to transcribe every regional language.

## Product Principles

1. **Reviewable before readable.** Preserve candidates, provenance, uncertainty,
   and human decisions before improving presentation.
2. **User outcomes before model names.** Put provider and model controls under
   an Advanced section; lead with the task the user needs to complete.
3. **No blanket language support claim.** Every language or dialect moves
   through a visible evidence lifecycle.
4. **Unknown is a valid result.** Auto-detection must allow user override and an
   explicit unknown or uncertain state.
5. **Do not invent orthography.** Where a dialect does not have one consistently
   used written form, preserve the provider candidate or reviewed romanisation
   and mark uncertainty rather than forcing confident Chinese characters.
6. **Preserve code-switching.** English remains English. Chinese-script
   normalisation or translation is a separate, traceable view and never alters
   the retained source candidate.
7. **Public means synthetic or licensed.** No private recording, transcript,
   credential, customer name, or confidential family or customer material
   enters the public repository or showcase.
8. **Dry-run and cost limits remain defaults.** Every future billable route keeps
   preflight estimation, a job cap, exact approval, and retained usage evidence.

## Language Capability Lifecycle

A language badge in the product must state one of these stages:

1. **Provider-listed** — a provider says it can process the language.
2. **Adapter-ready** — APMA can route and retain the provider response.
3. **Live-smoke-tested** — one bounded, non-private request completed.
4. **Representative-benchmarked** — a consented or licensed test set passed
   recorded quality thresholds.
5. **Pilot-accepted** — native or qualified reviewers accepted results for a
   defined use case.
6. **Production-supported** — service, security, monitoring, support, and drift
   review are operating.

Stages are cumulative. Provider documentation is evidence for stage 1 only;
it is not APMA accuracy evidence.

### Initial language portfolio

| Track | Language or variety | Current evidence basis | Next honest step |
| --- | --- | --- | --- |
| Core evaluation | English and Singapore English/Singlish | MERaLiON and other providers describe relevant coverage; APMA has provider-neutral routing | Build clean, noisy, and code-switched representative cases |
| Core evaluation | Mandarin | Broad provider coverage; APMA preserves Chinese candidates | Benchmark characters, meaning units, names, and numbers |
| Core evaluation | Singapore Hokkien | Qwen and MERaLiON describe Hokkien capability; current APMA proof is not representative | Obtain lawful local speech and native review, including Singapore/Xiamen/Quanzhou variation |
| Core evaluation | Cantonese | Qwen and MERaLiON describe Cantonese capability | Run the same representative benchmark and code-switch tests |
| Core evaluation | Malay | Qwen and MERaLiON describe Malay capability | Test Singapore/Malaysia usage, English switching, names, and domain terms |
| Core evaluation | Tamil | MERaLiON describes Singapore Tamil and code-switch capability | Recruit qualified review and benchmark script, switching, names, and numbers |
| Core evaluation | Indonesian | Qwen and MERaLiON describe Indonesian capability | Test Indonesian rather than assuming Malay performance transfers |
| Next validation | Hakka/Kejia | Qwen documentation lists Hakka; APMA has no representative proof | Source a lawful fixture set and a qualified reviewer before promotion |
| Research candidate | Teochew/Chaozhou | Not explicitly supported in the provider documents currently used for APMA claims | Discovery interviews, lawful samples, provider experiments, and expert review |
| Research candidate | Hainanese | Not explicitly supported in the provider documents currently used for APMA claims | Discovery interviews, lawful samples, provider experiments, and expert review |

References for the provider-listed boundary:

- [Qwen speech-to-text models](https://docs.qwencloud.com/developer-guides/speech/speech-to-text-models)
- [MERaLiON-3-3B-ASR model card](https://huggingface.co/MERaLiON/MERaLiON-3-3B-ASR)

The public capability matrix must use the current APMA stage, not the highest
stage claimed by any provider.

## Experience Target

### Customer-facing modes

Replace model-code-first choices with four task modes:

- **Fast Draft** — one cost-efficient provider for a quick first pass.
- **Southeast Asia Multilingual** — a specialist route selected for the declared
  or detected language mix.
- **Speaker-labelled** — preserves provider speaker and timestamp evidence.
- **Compare And Verify** — retains multiple candidates, highlights agreement and
  disagreement, and creates a review queue.

Provider, model, region, diarization, rescue policy, and cost details remain
available in an Advanced panel and in final provenance.

### Main workflow

The interface should communicate one guided path:

`Add audio -> Inspect -> Choose mode -> Review estimate -> Process -> Review exceptions -> Export`

The professional review screen should have:

- an audio player synchronised with transcript regions;
- readable timestamps and speaker labels;
- language or dialect labels that distinguish provider-reported from
  human-reviewed status;
- Green, Amber, and Red review attention with plain-language explanations;
- side-by-side unchanged provider candidates for disputed regions;
- speaker rename, exact candidate selection, and manual correction;
- a permanent human-decision history; and
- one clear primary action at each stage.

The default transcript display should be comfortable for ordinary users.
Hashes, raw artifact paths, model versions, speaker-identity scope, and costs
belong in a collapsible **Evidence and trust** panel.

### Multilingual record shape

Where applicable, each reviewed segment should support separate fields for:

- provider-native candidate;
- reviewed source-language text;
- normalised display text;
- optional meaning translation in a selected review language;
- detected or declared language/variety and evidence source;
- timestamps and provider speaker evidence;
- uncertainty or unresolved tokens; and
- reviewer decision and correction history.

For the user's present Hokkien workflow, Simplified Chinese may be the preferred
review display where a qualified reviewer can support it. Script conversion is
not translation, and uncertain dialect words must not be silently replaced by
plausible Mandarin.

## Delivery Plan

### Milestone 0 — Product decisions and measurement baseline

Purpose: prevent visual polishing from outrunning the product claim.

Deliverables:

- one-sentence positioning and named initial buyer;
- public claim matrix using the language lifecycle above;
- current-dashboard usability baseline and issue list;
- threat-model boundary for public showcase versus hosted pilot;
- benchmark specification and minimum acceptance thresholds;
- event and outcome metrics; and
- a small decision record for every material scope change.

Acceptance criteria:

- the product contract, website copy, UI labels, and GitHub README use the same
  evidence boundary;
- no language is described simply as supported without a stage and use case;
- the team can state who buys, who operates, who reviews, who is represented in
  the data, and who accepts residual risk; and
- deferred features and reversal conditions are recorded.

Effort: small. Dependency: none. This is the first implementation tranche.

### Milestone 1 — Professional public-safe Yingfluence showcase

Purpose: demonstrate the product and thinking without accepting public audio.

Deliverables:

- a refined visual system with an APMA name lock-up, restrained colours,
  typography, spacing, buttons, empty states, and responsive layouts;
- a hero and problem statement written for buyers rather than developers;
- an interactive synthetic sample that requires no login, upload, API key, or
  live provider call;
- a guided mode selector with Advanced provider detail collapsed;
- a populated transcript-review example with player, timestamps, speaker
  evidence, disagreement, human correction, and export preview;
- an Evidence and trust panel showing source hash, duration coverage, provider
  runs, model versions, cost, uncertainty, and review status;
- a truthful language capability matrix and limitations section;
- architecture, tests, live-smoke evidence, privacy boundary, and a clear
  **Request an evaluation** call to action; and
- an acknowledgement of MERaLiON's time-limited research/evaluation support
  that does not imply endorsement.

Public location target:

- `yingfluence.com/labs/apma` for the public-safe evidence story; and
- the existing public GitHub repository for inspectable code, tests, and
  architecture.

Must-pass showcase gate:

- synthetic, licensed, or explicitly public-safe content only;
- no upload endpoint, runtime credential, live-provider invocation, customer
  data, private path, or confidential identifier in the public build;
- no unsupported quality or language claim;
- a first-time visitor can understand the problem, difference, evidence, and
  next action without knowing model codes;
- mobile and desktop layouts have no clipped or overlapping content;
- keyboard navigation, focus states, contrast, labels, and basic screen-reader
  semantics pass review;
- all costs use clear currency and sensible precision;
- all links, downloads, and sample interactions work;
- Docker dry-run tests, public-safe scans, link checks, and repository CI pass;
  and
- at least three first-time users can complete the sample review flow without
  operator instruction, with their confusion recorded for correction.

Effort: medium. Dependency: Milestone 0. This is the minimum professional public
showcase, not a hosted transcription service.

### Milestone 2 — Professional local and design-partner workflow

Purpose: make the real single-user product reliable and pleasant enough for
supervised demonstrations and bounded design-partner evaluation.

Deliverables:

- job history with stable statuses and resumable state;
- clear processing progress, elapsed audio coverage, cancellation, retry, and
  actionable errors;
- a production-quality review player and editor;
- review-queue filtering and visible outstanding Amber/Red regions;
- speaker rename and explicit chunk-scoped identity warnings;
- one-click validated exports with output status;
- language declaration, auto-detection suggestions, manual override, and
  unknown/uncertain handling;
- cost estimate, cap, newly incurred cost, and cache reuse explained in normal
  language; and
- structured feedback capture for correction effort and failure categories.

Acceptance criteria:

- interruption and restart do not create an unnoticed duplicate paid run;
- the interface accounts for the complete accepted source duration or explains
  exclusions;
- a reviewer can navigate from each flagged region to audio, candidates, and a
  retained decision;
- user edits never overwrite raw provider evidence;
- ordinary errors give recovery guidance without exposing secrets or internal
  stack traces; and
- representative use tests show the complete workflow is usable without direct
  database or filesystem manipulation.

Effort: medium to large. Dependency: Milestone 1 design decisions. Keep this
local or operator-supervised until Milestone 3 passes.

### Milestone 3 — Invite-only hosted pilot

Purpose: accept limited customer audio without treating the local dashboard as
an internet-ready service.

Required controls:

- authenticated invited users and role boundaries;
- per-customer job, object, log, and export isolation;
- production queue/workers rather than long work in a web request;
- server-side file type, size, duration, and malware controls;
- managed secret storage, rotation, least privilege, and provider-key scoping;
- encryption in transit and at rest;
- explicit consent, permitted-use, retention, deletion, backup, recovery, and
  cross-border processing rules;
- rate, concurrency, cost, retry, and abuse limits;
- audit events for access, processing, review, export, and deletion;
- redacted observability, service health, alerting, runbooks, incident response,
  and provider outage handling;
- dependency and container supply-chain checks; and
- reviewed provider terms for customer/commercial data use.

Hosted-pilot gate:

- threat model and privacy/data-flow record reviewed;
- authentication and tenant-isolation tests pass;
- no secret or audio content appears in application logs;
- deletion and retention behavior are demonstrated end to end;
- backup restoration and incident escalation are exercised;
- defined availability, recovery, support, and human-accountability boundaries
  are accepted by the owner; and
- each pilot has a bounded purpose, named data owner, named reviewer, cost cap,
  exit date, and success threshold.

Target location after this gate: invite-only `apma.yingfluence.com` or an
equivalent isolated service. Do not add real-audio upload to the public showcase.

Effort: large. Dependency: Milestone 2 and an explicit hosting architecture
decision.

### Milestone 4 — Language expansion and market proof

Purpose: turn regional ambition into repeatable evidence and commercial value.

For every new language or dialect, build at least three lawful test groups:

1. clean, mostly single-speaker speech;
2. natural code-switching; and
3. noisy, overlapping, or multi-speaker speech.

Synthetic fixtures validate plumbing only. Accuracy claims require licensed or
consented human speech, documented speaker context, annotation guidance, and
qualified review.

Scorecard:

- WER or CER where appropriate;
- meaning-unit preservation;
- names, numbers, dates, decisions, and action-item accuracy;
- code-switch boundary and language-preservation errors;
- omissions, repetitions, and unsupported content;
- timestamp coverage and speaker-attribution error;
- human correction minutes per audio hour;
- time to reviewed output and provider cost per reviewed audio hour; and
- reviewer acceptance, disagreement, and reason codes.

Promotion gate for a language:

- the fixture licence or consent and allowed publication/use are recorded;
- at least two qualified reviewers agree on annotation guidance and adjudicate
  disputed cases;
- thresholds are set before the final evaluation run;
- failures are published alongside successes;
- the provider/model/version and date are retained for drift comparison; and
- the product badge advances only to the stage actually demonstrated.

Expansion order:

1. complete Singapore Hokkien/Singlish/Mandarin/English evidence;
2. add Cantonese, Malay, Tamil, and Indonesian packs;
3. test Hakka/Kejia as the next provider-listed candidate; and
4. treat Teochew and Hainanese as research tracks until experiments and native
   review support a product decision.

Effort: ongoing evidence programme. It can run in parallel with a controlled
pilot, but not ahead of consent, reviewer, and benchmark readiness.

## Prioritised Backlog

### P0 — before the Yingfluence public showcase

- lock positioning, buyer, offer, and evidence-safe copy;
- create the language capability and claims matrix;
- replace model-code-first navigation with task modes;
- redesign the sample review flow and Evidence and trust panel;
- polish layout, states, cost formatting, accessibility, and responsiveness;
- produce one excellent synthetic end-to-end case;
- create the Yingfluence case-study page and evaluation CTA;
- verify public artefacts, licences, links, tests, and confidentiality scans; and
- test first-time comprehension with three users.

### P1 — before supervised design-partner use

- progress, cancellation, restart, job history, and recovery UX;
- professional audio/transcript review and speaker editing;
- language suggestion plus override and unknown handling;
- reliable export state and correction-effort measurement; and
- representative Hokkien evaluation pack and reviewer process.

### P2 — before hosted customer audio

- every security, privacy, isolation, operations, and provider-terms control in
  Milestone 3;
- pilot agreement, support boundary, success measures, and exit criteria; and
- evidence that processing and review economics are commercially workable.

### Explicitly deferred

- calendar, Zoom, Teams, or meeting-bot integrations;
- real-time transcription;
- native mobile applications;
- CRM automation and team conversation analytics;
- cross-meeting generative chat;
- automatic consequential decisions from transcripts;
- a proprietary speech foundation model;
- SSO, SCIM, or formal certification before the target buyer requires it; and
- broad language claims unsupported by APMA evaluation.

These items can return only when user demand, revenue, risk, or evaluation
evidence outweighs the cost and distraction.

## Product And Business Metrics

Public showcase:

- sample flow completion;
- time to understand APMA's difference;
- Evidence and trust panel engagement;
- evaluation enquiries and qualified-conversation conversion; and
- top confusion or abandonment points.

Pilot:

- upload-to-reviewed-transcript time;
- human correction minutes per audio hour;
- accepted versus unresolved segments;
- completion, repeat use, and reviewer return rate;
- cost and support time per reviewed audio hour;
- use by quality mode and language mix;
- security or privacy exceptions; and
- willingness to pay, gross margin, and renewal intent.

Correction velocity should also be measured: evidence detected to decision,
decision to working change, working change to verified outcome, and whether the
learning became a reusable test or control.

## Decision And Ownership Boundary

The product owner retains approval for public release, production hosting,
customer-data acceptance, provider expenditure, data policy, contractual
commitments, risk acceptance, and language-support claims.

Engineering may implement and test small reversible changes within an approved
milestone, preserving `DRY_RUN=true`, cost caps, evidence artifacts, private-data
exclusion, Docker reproducibility, and scoped commits.

Qualified language reviewers decide the reference transcription and
adjudication guidance. A provider response, model vote, or fluent translation
does not replace that authority.

## Immediate Next Tranche

Implement only Milestone 0 and the first design slice of Milestone 1:

1. freeze the public positioning and capability matrix;
2. specify the new information architecture and annotated page wireframes;
3. choose one synthetic multilingual story for the complete sample flow;
4. define the visual tokens, component states, and accessibility checklist; and
5. prepare a file-level implementation plan and acceptance tests before editing
   runtime behavior.

At that review point, decide whether to proceed with the showcase UI, change
the target buyer or message, or pause. Hosted processing remains out of scope.
