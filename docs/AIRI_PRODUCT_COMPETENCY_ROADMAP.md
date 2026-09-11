# APMA AI Product Competency And AIRI Roadmap

Status: public-safe evidence roadmap. This is not an AIRI certification or a
self-awarded maturity level.

## Positioning

APMA is a local-first, evidence-preserving transcription system for long,
code-switched Singapore conversations. It addresses the operational problem
that Hokkien, Singlish, Mandarin, and English can occur in one exchange while
models differ in dialect coverage, timing, diarization, price, and availability.

The project does not claim to have invented a Hokkien foundation model or
achieved perfect dialect recognition. Its contribution is the governed system
around emerging models:

- route specialist and general multilingual providers without silently mixing
  their outputs;
- preserve source media, hashes, duration coverage, raw responses, timestamps,
  speaker evidence, model identity, and cost;
- expose disagreement and uncertainty instead of polishing them away;
- limit paid rescue to bounded uncertain regions;
- preserve exact human decisions and corrections; and
- keep private audio, credentials, and runtime evidence outside the public
  source tree.

## Why the Hokkien problem is distinctive

Singapore Hokkien is a locally evolved Southern Min variety rather than a
single interchangeable label for every Hokkien-speaking region. Product and
evaluation design must therefore distinguish Singapore usage, Xiamen/Quanzhou/
Zhangzhou influences, Taiwanese variants, Mandarin, and local English or Malay
code-switching instead of treating every Chinese utterance as Mandarin.

Current provider capability is promising but does not remove the need for
evaluation. Qwen documents Hokkien support and file diarization; MERaLiON is
designed for Singapore and Southeast Asian speech, including Singlish and
English-Hokkien code-switching. APMA treats those statements as provider
capability claims to test, not as proof of accuracy on a user's recording.

References:

- [Qwen speech-to-text models](https://docs.qwencloud.com/developer-guides/speech/speech-to-text-models)
- [MERaLiON-3-3B-ASR model card](https://huggingface.co/MERaLiON/MERaLiON-3-3B-ASR)
- [Singapore Chinese Cultural Centre: The Hokkien dialect in Singapore](https://culturepaedia.singaporeccc.org.sg/language-education/the-hokkien-dialect-in-singapore/)

## AIRI boundary

AI Singapore describes AIRI as an industry-focused readiness framework and
directs the current openly licensed framework to the independently maintained
AIRI Foundation. As checked on 2026-09-11, the Foundation identifies its
framework as version 3.2 with separate personal (`pAIRI`) and organisational
(`oAIRI`) assessments, five pillars, fifteen dimensions, and five levels.

Level 4 is `AI Catalyst / Pioneer`. It requires evidence beyond one capable
application: individuals architect ecosystems and influence standards;
organisations demonstrate mature governance and continuous innovation. The
framework also caps overall maturity by the Ethics & Governance pillar.

APMA should therefore be described as evidence toward Builder-level product
orchestration and selected Catalyst behaviours, not as proof that its owner or
organisation is AIRI Level 4.

References:

- [AI Singapore AIRI](https://aisingapore.org/innovation/airi/)
- [Current AIRI framework](https://airi.foundation/)
- [Personal AIRI](https://airi.foundation/framework/pairi/)
- [Organisational AIRI](https://airi.foundation/framework/oairi/)

## Five-pillar evidence map

| AIRI pillar | Current APMA evidence | Next evidence required |
| --- | --- | --- |
| Leadership & Culture | Product ownership, explicit approval boundaries, rapid provider re-evaluation, and structured experimentation | Named responsibilities, learning cadence, team adoption, mentoring, and evidence of work redesign |
| Ethics & Governance | Dry-run defaults, cost gates, provenance, human judgment, confidential-release controls, and provider-scoped speaker identity | Versioned risk register, threat model, incident response, retention/deletion policy, independent testing, and recurring review |
| Business Value | A defined underserved workflow and configurable quality/cost modes | Baseline time and cost, review effort saved, repeat use, willingness to pay, revenue, and gross-margin evidence |
| Data Foundation | Source hashes, raw artifacts, canonical schemas, correction history, and synthetic regression fixtures | Licensed or consented local-language reference audio, annotation guidance, quality thresholds, and drift history |
| Infrastructure & Standards | Docker, CI, provider-neutral contracts, resumability, cache controls, tests, and local credential storage | Authentication, roles, observability, service-level objectives, supply-chain review, and production pilot evidence |

## Product decision spine

Every material product decision should answer these questions in one short,
versioned record:

1. **Why?** What user problem, baseline, and consequence justify action?
2. **What?** What outcome is required, and what is explicitly out of scope?
3. **Who and whom?** Who operates, buys, reviews, is represented in the data,
   accepts risk, and owns the result?
4. **Where?** Where do processing, storage, access, and jurisdictional
   boundaries sit?
5. **When?** What triggers action, review, retention, expiry, or reassessment?
6. **How?** Which model, architecture, process, controls, and tests are used?
7. **What next?** What is the next smallest experiment and success threshold?
8. **What if?** What happens under failure, misuse, drift, leakage, or provider
   withdrawal?
9. **What else?** What build, buy, manual, partner, or do-nothing alternatives
   were considered?
10. **So what?** What quality, adoption, financial, and risk outcomes changed?
11. **Then what?** Is the decision to keep, change, pause, scale, or stop?
12. **Why not and why not now?** What was rejected or sequenced later, and what
    new evidence would reverse that choice?

## Speed as controlled learning

The useful competitive metric is correction velocity, not code volume. Track:

- evidence detected to decision made;
- decision made to working change;
- working change to verified outcome;
- cost and reversibility of a wrong decision; and
- whether the learning became a reusable test, guardrail, or specification.

Fast development remains governed by explicit scope, dry-run defaults, bounded
live tests, cost caps, and human approval for consequential external actions.

## Next proof: Singapore code-switch evaluation pack

The highest-leverage public contribution is a lawful, non-private evaluation
pack for Singapore Hokkien/Singlish/Mandarin/English transcription. Start with
synthetic fixtures for plumbing, then add only licensed or explicitly consented
human recordings with documented provenance and review.

Measure more than WER:

- source-duration and timestamp coverage;
- code-switch preservation;
- Chinese character and meaning-unit accuracy;
- key name, number, decision, and action-item accuracy;
- speaker-attribution error;
- unsupported-content or omission rate;
- human correction minutes per audio hour;
- cost per reviewed audio hour; and
- reviewer acceptance and disagreement.

Publishing the evaluation method, schemas, consent boundary, and limitations
could turn APMA from one application into an ecosystem reference—direct evidence
toward Catalyst behaviour—without publishing private meetings.

## Adoption and commercial proof

Position APMA as review acceleration and accountable record creation, not
perfect transcription. A controlled pilot should record:

- upload-to-reviewed-transcript time;
- review effort saved per audio hour;
- repeat use and completion rate;
- provider cost and support time per job;
- quality-mode usage and correction density;
- willingness to pay and retention; and
- unit economics and gross margin by quality mode.

Start with a small number of design partners in research, oral history, or
internal multilingual meetings. Do not expand into regulated or consequential
decision-making merely because transcription is available. Authentication,
roles, data handling, and human accountability must grow before multi-user or
public-internet deployment.

## Portfolio presentation

Use five layers so reviewers can verify claims quickly:

1. a short synthetic-data demonstration;
2. a case study showing problem, decisions, corrections, and measured outcome;
3. architecture, relevant code, tests, and security controls;
4. this AIRI-aligned evidence map with honest gaps; and
5. external pilot, adoption, and unit-economics evidence when available.

Suggested contribution statement:

> I identified an underserved local-language problem, established the product
> and evidence boundaries, selected and re-evaluated providers as capabilities
> changed, directed implementation, enforced privacy and cost controls, and
> validated bounded live routes using non-private audio. AI agents accelerated
> implementation; I retained responsibility for product direction, risk
> acceptance, commercial priorities, and release decisions.

Suggested maturity statement:

> Building toward AIRI L4-aligned AI Catalyst capability. Current evidence
> demonstrates Builder-level product orchestration, responsible-AI controls,
> and customised solution development. Self-assessed, not certified.
