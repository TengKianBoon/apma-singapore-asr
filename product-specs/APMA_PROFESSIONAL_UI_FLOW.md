# APMA Professional UI Flow

Status: product decision and implementation boundary for the next UI tranche.
This document does not authorise public audio upload, production hosting, or
live provider expenditure.

Implementation snapshot (2026-10-10): the lean local tranche now includes the
guided import/outcome/cost/review shell, upload progress, recent retained jobs,
timestamp-linked playback, focused exception-review filters, human speaker
labels, structured exports, and trust evidence. Durable background processing
and multi-user features remain deferred as described below. The local Pilot
outcome tab now operationalises consent-gated, pseudonymous outcome measurement
without turning APMA into a general analytics platform. Compare & Verify also
offers optional targeted human verification: exact flagged clips, separate
content and speaker decisions, honest completion labels, and measured review
coverage without an unsupported accuracy claim.

## Product Experience Objective

APMA should feel as straightforward as a professional transcription product
while remaining visibly more rigorous about multilingual evidence:

> Add a recording, choose the outcome, understand time and cost, review only
> what needs judgment, and export an accountable record.

The interface must not make an ordinary user understand provider codes,
container paths, chunk policy, model versions, or reconciliation internals
before starting. Those details remain available as evidence and advanced
controls.

## Lean Professional Boundary

Professional does not mean copying the full feature inventory of an enterprise
meeting platform. For the single-user, cost-plus-low-price product, the first
release earns trust through a small number of polished jobs:

- one obvious file-import action with real local upload progress;
- four outcome choices expressed in user language rather than model codes;
- a duration, chunk, cost, and approval checkpoint before any paid call;
- a recent-job list with honest ready, review-required, processing, and failed
  states;
- timestamp-linked audio playback, exact exception correction, and speaker
  relabelling without overwriting provider evidence; and
- readable transcript, subtitle, local-project, and trust/evidence exports.

The first release does **not** require a durable distributed queue, background
workers, live collaboration, comments, CRM/calendar integrations, mobile apps,
an unrestricted rich-text editor, or waveform rendering. Those are deferred
until observed users cannot complete the core workflow without them or paying
demand justifies their operating and maintenance cost.

The current local implementation may remain request-bound while it clearly
shows the active stage and preserves recoverable job artifacts. True
leave-the-page-and-return processing is a later production-hosting requirement,
not a claim made by this UI tranche.

## Chosen Niche And Anti-Positioning

APMA is a **transcription-assurance workspace for existing Southeast Asian
audio files**. It performs asynchronous, post-recording work on difficult,
long, multilingual, code-switched, or legacy recordings.

It is strongest when the user needs to:

- account for the complete duration and integrity of an existing recording;
- compare specialist and general speech models without mixing their evidence;
- see where models agree, disagree, omit, or assign speakers differently;
- focus human listening and correction on the exceptions;
- preserve raw results, timestamps, speaker scope, cost, and human decisions;
  and
- export an accountable transcript or derived record.

APMA is not positioned as:

- a live meeting recorder or streaming-caption service;
- a calendar, Zoom, or Teams bot;
- a sales-call coach or CRM analytics platform;
- a general team collaboration and workplace-search suite; or
- a system that automatically beautifies uncertain speech into a confident
  transcript.

The customer-facing category is **Southeast Asia multilingual transcription
assurance**, not AI meeting assistant. Market-leader interfaces are references
for import, progress, playback, editing, and export ergonomics only.

## Current Flow Observed

The local dashboard currently combines most controls on one screen:

`Choose file -> Upload and estimate -> Choose model or fixed quality -> Approve -> Run -> Open separate review page -> Return for minutes -> Export`

It is technically functional and unusually strong in guarded provider routing,
cost caps, retained artifacts, reconciliation, and exact human decisions. The
main professional gap is not the absence of processing logic. It is that the
interface presents implementation structure instead of guiding the user's job.

Observed friction:

- **Technical first impression.** `APMA Local Dashboard`, provider readiness,
  model identifiers, chunk counts, and route descriptions dominate the start.
- **Competing primary actions.** Upload, simple run, fixed-quality run, minutes
  estimation, minutes generation, and two approval checkboxes share one form.
- **Premature choices.** Minutes style and model are presented before a
  transcript exists.
- **Weak progress model.** Long processing is represented mainly by status
  text; there is no persistent stage, duration-coverage, queue, or completion
  view.
- **Split review experience.** Human review opens as a separate page and is not
  integrated with the job, transcript, outputs, and remaining-review count.
- **Engineering-style results.** Quality runs can show JSON stage and artifact
  details where a user expects the reviewed transcript and exceptions.
- **Insufficient return path.** There is no durable recent-job list, search,
  resume inbox, or clear distinction among processing, review required, ready,
  and failed jobs.
- **Review density.** All candidates and controls are repeated in long cards;
  there is no Amber/Red filter, next-unresolved navigation, sticky player, or
  keyboard review flow.
- **Formatting and hierarchy.** Six-decimal cost values, duplicate file-input
  affordances, long explanatory copy, a large empty result panel, and mixed
  button emphasis reduce perceived finish.

## Market Workflow Patterns

Current market leaders differ in breadth, but their professional flows repeat a
small set of useful patterns:

- Otter makes file import asynchronous, shows upload/processing progress, and
  lets the user leave while processing continues.
- Fireflies leads from a meeting list into a two-panel meeting workspace with
  notes and transcript, then keeps editing, playback, comments, and actions in
  that context.
- Notta centres the record detail page on transcript blocks, speakers,
  timestamps, synchronised playback, editing, and a separate tool area.
- Sonix emphasises the professional edit loop: click transcript text to hear the
  matching audio, correct speakers or wording, and then export.

References:

- [Otter file import workflow](https://help.otter.ai/hc/en-us/articles/360047733574-Import-an-audio-or-video-file)
- [Fireflies meeting workspace](https://guide.fireflies.ai/articles/6653885315-learn-about-the-fireflies-notepad)
- [Notta record detail workflow](https://support.notta.ai/hc/en-us/articles/37437047511195-Records-detail-page-screen-layout-and-functions-Notta-Web)
- [Sonix professional transcription editor](https://sonix.ai/features/automated-transcription)

APMA should adopt their low-friction workflow patterns, not their complete
feature inventories.

## Adopt, Adapt, Defer, Or Reject

| Market pattern | APMA decision | Reason |
| --- | --- | --- |
| Simple file import with persistent progress | **Adopt now** | Basic professional expectation for long recordings |
| Recent recordings/jobs with clear states | **Adopt now** | Essential for resume, retry, and review work |
| Synchronized audio and transcript | **Adopt now** | Core to evidence-based correction |
| Edit text and rename speakers in context | **Adapt now** | Retain raw provider evidence and record every human decision |
| Search, filter, and next-unresolved navigation | **Adopt now** | Reduces review effort without changing evidence |
| Summary, decisions, and actions beside transcript | **Adapt now** | Keep them derived and visibly separate from transcript authority |
| Confidence heatmap | **Adapt carefully** | Show APMA review attention and disagreement, not uncalibrated confidence |
| Translation beside source text | **Adapt later** | Useful regionally, but must preserve source, script conversion, translation, and reviewer status separately |
| Custom vocabulary | **Consider after evals** | Valuable for names and domains only if provider paths support it consistently |
| Sharing and comments | **Pilot later** | Requires identity, permissions, isolation, and audit controls |
| AI chat over meetings | **Defer** | Not the current differentiated problem and can obscure source evidence |
| Calendar or meeting bots | **Defer** | High integration cost; not needed for file-based evaluation and review |
| CRM and task-system integrations | **Defer** | Export and webhook boundaries are sufficient initially |
| Real-time transcription | **Defer** | Adds latency, partial-result, and speaker-continuity complexity |
| Native mobile application | **Defer** | Responsive review and reliable file intake come first |
| Automatic transcript polishing | **Reject as default** | Fluency must never silently replace what providers actually returned |
| One generic accuracy percentage | **Reject** | Quality varies by language mix, audio condition, speaker evidence, model, and use case |

## Target Information Architecture

### 1. Home and job inbox

Purpose: start work or resume the next required decision.

Show:

- **New transcription** as the primary action;
- recent jobs with title, duration, language mix, mode, stage, review count,
  time, and cost;
- state filters: Processing, Review required, Ready, Failed, and Archived;
- safe resume and retry actions; and
- one public-safe sample job when the product is in showcase mode.

Do not show model codes in the default list.

### 2. New transcription

Use one short staged flow rather than one long technical form.

#### Step 1 — Add recording

- one drag-and-drop or file-choice surface;
- accepted formats and maximum size in secondary text;
- visible filename, duration, format, channel count, and quality warnings after
  inspection; and
- explicit private/local processing boundary.

#### Step 2 — Conversation

- optional title;
- language mix: Auto-suggest, English/Singlish, Mandarin, Hokkien, Cantonese,
  Malay, Tamil, Indonesian, Hakka/Kejia, or Other/Unknown;
- allow more than one selection for code-switching;
- auto-suggestion must identify its source and remain editable; and
- output preference, such as source-preserving or reviewed Simplified Chinese
  display where appropriate.

Teochew and Hainanese may appear only as experimental/research choices until
their evidence stage advances.

#### Step 3 — Outcome

Present user-facing modes:

- **Fast Draft** — quickest first pass;
- **Southeast Asia Multilingual** — specialist route for code-switching;
- **Speaker-labelled** — emphasises speaker and timestamp evidence; and
- **Compare and Verify** — multiple candidates and exception review.

Each mode card should state expected output, review effort, approximate time,
and price range. The exact provider plan sits under **Advanced**.

#### Step 4 — Review and start

- source duration and chunk coverage;
- provider plan and data-processing regions;
- estimated newly incurred cost, reserve, and hard cap;
- what may be cached or reused;
- retention choice where implemented;
- one precise approval control; and
- one **Start transcription** action.

Minutes style and minutes model do not belong here. They become available after
the transcript reaches a usable state.

### 3. Processing

The job page remains useful while work continues.

Show:

- a stage stepper: Inspecting, Preparing audio, Transcribing, Comparing,
  Building review, and Ready;
- accepted duration versus processed duration;
- completed versus pending provider runs;
- elapsed time and a conservative status, not a fabricated countdown;
- newly incurred cost and current cap;
- safe cancellation or leave-and-return behavior;
- plain-language failure and recovery guidance; and
- evidence detail collapsed by default.

### 4. Review workspace

Use one job-centred workspace:

- top bar: job title, state, outstanding review count, cost, and Export;
- main area: speaker-labelled transcript blocks with timestamps;
- sticky audio player: play/pause, short rewind/forward, speed, timeline, and
  current segment;
- review rail: All, Amber, Red, Unresolved, and Resolved;
- evidence drawer: unchanged provider candidates, model/run identity, raw
  timing and speaker scope, and source hashes; and
- optional side panel: minutes, decisions, actions, or translation.

For each exception, the reviewer can:

1. hear the exact bounded audio;
2. compare unchanged candidates;
3. select a retained candidate or enter an exact correction;
4. rename a speaker without hiding the provider label;
5. mark unresolved when the audio is insufficient; and
6. move to the next outstanding item.

The targeted-verification option records intent before the run, then reports
the actual selected clip count, selected-audio duration and source-audio share.
Completion is labelled as automated, selected-window human confirmed, or
selected-window human reviewed with unresolved items. It never implies full-
audio review. See
[the targeted verification decision](../docs/TARGETED_HUMAN_VERIFICATION.md).

Save status and correction history must be visible. Undo should reverse the
human edit while leaving original provider evidence unchanged.

### 5. Finalise and export

Finalisation is a visible gate, not an automatic implication that every word is
correct.

Show:

- resolved and unresolved counts;
- duration coverage and excluded intervals;
- selected display language and translation boundary;
- source/provider/human contribution summary;
- export choices: JSON, HTML, TXT, DOCX, PDF, SRT, and VTT when valid;
- speaker and timestamp inclusion options; and
- **Finalise with unresolved items** only as a deliberate, recorded decision.

Minutes, decisions, and actions can be generated or refreshed here from the
reviewed transcript. They remain derived outputs.

### 6. Advanced and evidence settings

Keep provider selection, model/version, route readiness, region, pricing,
chunking, rescue policy, cache identity, raw artifact paths, and detailed caps
available for operators and evaluators. They should not control the visual
hierarchy of the ordinary workflow.

## Two Product Surfaces

### Public Yingfluence showcase

- uses a precomputed synthetic or licensed sample;
- lets visitors explore upload inspection, comparison, review, and provenance
  without submitting audio;
- has no credentials, live provider call, public upload, or private filesystem
  path; and
- ends with **Request a Southeast Asia speech AI evaluation**.

### Local or invite-only application

- accepts actual jobs only inside its approved security boundary;
- exposes cost approval and operational controls to authorised users;
- preserves the same review interaction demonstrated publicly; and
- adds identity, isolation, retention, deletion, and audit controls before any
  hosted customer-data pilot.

## Competitive Gap And Priority

| Experience area | Current APMA | Professional target | Priority |
| --- | --- | --- | --- |
| First-run comprehension | Engineering dashboard | Outcome-led start and sample | P0 |
| Navigation | Single page plus separate review page | Job inbox and one job-centred workspace | P0 |
| Provider selection | Prominent model dropdown and fixed route button | Mode first; provider plan under Advanced | P0 |
| Cost approval | Strong control, dense presentation | Preserve control with plain-language review step | P0 |
| Processing | Blocking/status-message feel | Persistent staged progress and safe return | P1 |
| Transcript display | Plain text or JSON-oriented quality output | Structured speaker/timestamp blocks | P0 |
| Review | Strong evidence logic, high interaction cost | Filtered, synchronized, keyboard-efficient exception review | P0 |
| Speaker correction | Available in separate mapping area | Rename in transcript with provenance visible | P1 |
| Outputs | Tabs and links after completion | Clear finalisation and export centre | P1 |
| Job history | Absent | Recent jobs, state, resume, retry, archive | P1 |
| Trust evidence | Strong artifacts, weak presentation | Collapsible Evidence and trust drawer | P0 |
| Collaboration | Not supported | Invite-only roles/comments only after hosting controls | P2 |
| Integrations | Minimal | Export/webhook first; meeting/CRM integrations deferred | Deferred |

## Implementation Decisions

1. **Do not start with a framework rewrite.** Professionalism comes from the
   flow, state model, copy, accessibility, and review ergonomics. Extract the
   embedded dashboard and review HTML/CSS/JavaScript into maintainable static
   assets or templates first; keep the tested Python service contracts.
2. **Use one job-state vocabulary.** API, manifest, dashboard, review, and
   exports must agree on queued, inspecting, processing, review required,
   ready, failed, cancelled, and archived states.
3. **Keep canonical JSON authoritative.** The UI is a view and decision surface,
   not a second transcript authority.
4. **Keep provider outputs immutable.** Inline editing creates human decisions
   and regenerated views; it never mutates raw provider artifacts.
5. **Add frontend structure incrementally.** A hosted multi-user architecture
   decision may later justify a separate frontend application, but that is not
   required for the public-safe showcase.

## Delivery Tranches

### UI-0 — Flow specification and design states

- approve this target flow and terminology;
- create desktop and narrow-width wireframes for Empty, Inspected, Processing,
  Review required, Ready, and Failed;
- define visual tokens and accessibility acceptance criteria; and
- select one synthetic multilingual job for every state.

No runtime behavior change.

### UI-1 — Public-safe professional sample

- implement the job-centred shell and precomputed sample;
- implement the outcome modes and Advanced disclosure;
- implement review attention, candidate comparison, and trust drawer;
- implement finalisation/export preview; and
- complete confidentiality, responsive, keyboard, and comprehension testing.

No live upload or provider call.

### UI-2 — Connect the local workflow

- map existing status, estimates, run, review, and export APIs into the new
  states;
- add durable recent jobs and resume/retry;
- add persistent processing progress and duration coverage;
- integrate speaker rename and review decisions; and
- preserve existing dry-run, cost-cap, and evidence tests while adding UI-flow
  integration tests.

### UI-3 — Hosted pilot controls

- add authentication, roles, isolation, retention/deletion, audit, monitoring,
  rate limits, and incident operations; and
- run security and design-partner acceptance before customer audio is accepted.

## Acceptance Criteria For A Professional Showcase

- A first-time user can state APMA's purpose after viewing the initial screen.
- A first-time user can start and complete the sample flow without model-code
  knowledge or operator guidance.
- At every stage there is one clear primary action.
- The current job stage, cost boundary, and next required human action are
  visible.
- Transcript, audio, speaker labels, uncertainty, candidate evidence, human
  correction, and export are experienced as one coherent workflow.
- No fluent-looking output is presented as verified merely because processing
  completed.
- Keyboard navigation, visible focus, form labels, colour-independent status,
  contrast, and 320-pixel responsive layout pass review.
- No private audio, transcript, credential, confidential identifier, absolute
  private path, or live-provider ability exists in the public build.
- Existing Docker dry-run tests, new UI-flow tests, public-safe scans, and link
  checks pass.

## Immediate Decision

Proceed with **UI-0**, followed by **UI-1** if its wireframes and copy clearly
express APMA's evidence-review difference. Do not begin hosted infrastructure,
meeting integrations, or a frontend-framework rewrite in this tranche.
