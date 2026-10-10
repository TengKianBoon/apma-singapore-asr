# APMA AI Product Delivery Roadmap

Status: public-safe product, governance, adoption, and value roadmap.

## Product position

APMA is a local-first transcription assurance system for long, code-switched
Singapore and Southeast Asian recordings. It addresses a practical gap: model
quality, dialect coverage, diarization, price, and availability can vary within
the same recording, while users still need one accountable outcome.

The contribution is the governed workflow around the models:

- route specialist and general providers without silently mixing outputs;
- preserve source, duration, model, cost, timing, speaker and raw-response evidence;
- expose disagreement and uncertainty instead of polishing them away;
- send only selected unclear or speaker-sensitive clips for human verification;
- retain exact corrections and unresolved decisions; and
- keep private audio, credentials and runtime evidence outside public source.

## Why the language problem is distinctive

Singapore Hokkien is a locally evolved Southern Min variety, not a generic
label for every Hokkien-speaking region. Evaluation must distinguish Singapore
usage, Xiamen/Quanzhou/Zhangzhou influences, Taiwanese variants, Mandarin, and
local English or Malay code-switching rather than treating every Chinese
utterance as Mandarin.

The [Hokkien domain-discovery decision record](HOKKIEN_DOMAIN_DISCOVERY.md)
documents how historical, regional, provider, and unresolved evidence was
converted into requirements without describing context-grounded model use as
weight fine-tuning.

Provider capability is therefore a hypothesis to test, not proof of accuracy on
a user's recording. The evaluation design includes duration and timestamp
coverage, code-switch preservation, meaning-unit accuracy, names and numbers,
speaker attribution, unsupported content, omissions, human correction effort,
cost, and reviewer acceptance.

## Product decision spine

Every material decision should answer these questions in a short, versioned
record:

1. **Why?** What user problem, baseline and consequence justify action?
2. **What?** What outcome is required, and what is explicitly out of scope?
3. **Who?** Who operates, buys, reviews, appears in the data and owns the result?
4. **Where?** Where do processing, storage, access and jurisdiction boundaries sit?
5. **When?** What triggers action, review, retention, expiry or reassessment?
6. **How?** Which model, architecture, process, controls and tests are used?
7. **What next?** What is the smallest useful experiment and success threshold?
8. **What if?** What happens under failure, misuse, drift, leakage or provider withdrawal?
9. **What else?** Which build, buy, manual, partner or do-nothing options were considered?
10. **So what?** Which quality, adoption, financial or risk outcomes changed?
11. **Then what?** Is the decision to keep, change, pause, scale or stop?
12. **Why not now?** What is sequenced later, and what evidence would reverse that choice?

## Controlled learning

The useful speed measure is correction velocity, not code volume:

- evidence detected to decision made;
- decision made to working change;
- working change to verified outcome;
- cost and reversibility of a wrong decision; and
- whether the learning became a reusable test, guardrail or specification.

Fast iteration remains bounded by scope, dry-run defaults, explicit paid-run
approval, cost caps, source retention, and human acceptance for consequential
outputs.

## Maturity sequence

| Stage | Capability | Evidence required to advance |
| --- | --- | --- |
| Local working product | Ingest, media QC, multi-provider routing, comparison, targeted verification, export, cost preflight and deterministic tests | Complete Docker CI, synthetic browser evidence and public-safe release review |
| Consented external pilot | Real workflow observation with privacy-minimised quality, effort, adoption, trust and cost outcomes | Clear consent, lawful audio handling, participant feedback, denominators, incident log and keep/change/stop decision |
| Hosted commercial pilot | Authenticated job submission, worker isolation, secure retention, support process and bounded charging | Threat model, access roles, deletion proof, observability, recovery exercise, payment controls and support economics |
| Repeatable service | Stable target segment, repeat usage, measured quality thresholds and sustainable unit economics | Retention, renewal, referral or expansion evidence plus service objectives and recurring governance review |

## Trust and operating controls

| Area | Current evidence | Next control |
| --- | --- | --- |
| Human accountability | Exact review decisions, unresolved states and release labels | Independent sample review and reviewer-agreement measure |
| Data | Source hashes, raw artifacts, canonical schemas and synthetic fixtures | Licensed or consented dialect evaluation data and drift history |
| Security | Local credentials, ignored private artifacts, redaction and clean-history publication | Authentication, roles, threat model, retention/deletion tests and supply-chain review |
| Reliability | Docker, CI, bounded retries, resumability and fail-closed readiness | Hosted observability, service objectives, load evidence and incident exercises |
| Business value | Defined niche, outcome modes, cost caps and quote floor | Time saved, willingness to pay, support cost, repeat use and gross margin |

## Next public contribution

The highest-leverage addition is a lawful Singapore code-switch evaluation pack.
Start with synthetic fixtures for plumbing; add human recordings only when they
are licensed or explicitly consented, documented, and suitable for publication.

Public claims should remain specific: working software, tests and bounded
provider checks are inspectable; perfect dialect accuracy, production scale,
customer adoption and commercial traction are not yet claimed.
