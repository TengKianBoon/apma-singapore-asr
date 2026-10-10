# APMA Commercial Pilot Pricing

Research and price-book date: 11 September 2026. Currency: Singapore dollars
unless stated otherwise. This is a pilot pricing decision, not tax or legal
advice and not authorisation to take a live payment.

## Recommendation

Launch with pay-as-you-go pricing. Do not require a subscription and do not
offer unlimited transcription.

APMA's initial buyers are expected to have uneven batches of interviews,
heritage recordings, field audio, or difficult archives. A compulsory monthly
plan would add commitment before APMA has measured repeat demand. It would also
encourage high-volume commodity use, where APMA has little reason to compete.

The product should be sold as **offline transcription assurance for difficult
Singapore and Southeast Asian recorded conversations**, not as another meeting
notetaker. The customer is buying a dialect-oriented route, preserved evidence,
comparison and review boundaries—not merely cheap ASR minutes.

## Launch price book

Prices are before GST. GST is added only if the contracting entity is
GST-registered. Audio duration is rounded up to the next recorded minute.

| Offer | Pilot list price | What the buyer receives | Boundary |
| --- | ---: | --- | --- |
| Dialect Draft | **S$5.90/audio hour**, S$5.90 minimum | One Hokkien/SEA-oriented ASR route, source/duration accounting and structured transcript export | AI draft; no human accuracy guarantee |
| Compare & Flag | **S$9.90/audio hour**, S$9.90 minimum | Qwen dialect-oriented candidate plus a general multilingual candidate, disagreement/review flags and structured export | AI comparison; no human accuracy guarantee |
| Human Review Budget | **S$0.90/active reviewer minute**, 30-minute minimum | A capped amount of listen-and-correct work against the recording | Time budget, not an all-inclusive price per audio minute; extra work needs a revised quote |

PayNow should be presented first for Singapore buyers; cards remain available.
The price is calculated from recorded audio time. Human review is calculated
from active reviewer time because one difficult hour of code-switched audio may
take substantially more than one hour to verify.

## Why this is lower-priced without being loss-making

The benchmark must compare equivalent offers:

| Market reference | Published price | What it means for APMA |
| --- | ---: | --- |
| Sonix pay-as-you-go | US$10/audio hour | APMA Draft and Compare can sit below a mainstream one-off purchase while remaining dialect-focused. |
| Sonix Core | US$25/month for 5 hours | Subscription-effective prices can be much lower than pay-as-you-go; APMA should not claim to beat every bundle. |
| Happy Scribe additional AI credits | US$0.20/minute (US$12/hour) | A useful comparison for irregular uploaded-file use. |
| Happy Scribe human proofreading | from US$2.00/minute | Human verification is a distinct labour product and should never be hidden inside cheap AI minutes. |
| Rev AI pay-per-minute | US$0.25/minute (US$15/hour) | APMA is materially lower than a comparable one-off AI order. |
| Rev human transcription | US$1.99/minute | APMA's capped review budget is far lower, but it does not promise Rev's service level or 99% guarantee. |
| Rev Basic subscription | US$14.99/month for 20 AI hours | At full utilisation this is cheaper per hour than APMA; it proves that “cheapest anywhere” would be an unsafe claim. |

Sources: [Sonix pricing](https://sonix.ai/pricing/detailed-pricing-and-features),
[Happy Scribe pricing](https://www.happyscribe.com/pricing),
[Rev pricing](https://www.rev.com/pricing?tab=transcripts), and
[Rev pay-per-minute guidance](https://support.rev.com/hc/en-us/articles/18893487380365-Pricing).

The safe market claim is:

> Lower-cost than comparable pay-as-you-go transcription assurance and far
> below traditional human transcription, with dialect-focused comparison and
> transparent review pricing.

Do not claim “cheapest transcription” or “lower than every competitor.” Free
plans, annual bundles, heavy-use subscriptions and promotional allowances can
produce a lower apparent price per hour, but they are not equivalent to APMA's
specialist workflow.

## Direct provider economics

Published Singapore list prices make the automated routes affordable:

- Qwen Audio 3.0 ASR Flash Filetrans is US$0.000035/second, or **US$0.126 per
  audio hour**, before any APMA contingency. Its official model information
  explicitly lists Hokkien and supports offline file transcription, timestamps,
  context/hotwords and speaker diarization.
- GPT-Transcribe is **US$0.0045/minute**, or **US$0.27 per audio hour**, and
  supports language hints and code-switching.
- Running those two candidates costs about **US$0.396/audio hour** in direct
  model fees before exchange rate, retries, storage, processing and support.

Sources: [Alibaba Cloud model pricing](https://www.alibabacloud.com/help/en/model-studio/model-pricing),
[Qwen speech-to-text capabilities](https://docs.modelstudio.console.alibabacloud.com/en/model-studio/asr-model),
and [OpenAI GPT-Transcribe](https://developers.openai.com/api/docs/models/gpt-transcribe).

Direct model cost is therefore not the main commercial risk. Reviewer labour,
support, failed jobs, refunds, retention/storage, payment fees, maintenance and
customer acquisition can be larger than the ASR bill. APMA's price floor
includes explicit reserves rather than treating free quotas as profit.

MERaLiON is strategically valuable because its current model card covers
Singapore English, Mandarin, Malay, Tamil, Indonesian, Cantonese, Hokkien and
natural Singlish/code-switching. However, the current user grant is for
research, evaluation and testing. It is excluded from commercial cost and from
the initial paid route until commercial permission is confirmed. See the
[MERaLiON-3 ASR model card](https://huggingface.co/MERaLiON/MERaLiON-3-3B-ASR).

## Cost-floor policy

The price engine in `services/commercial_pricing.py` never trusts a browser-
supplied price. It calculates the minimum pre-GST revenue from:

- paid provider cost at published/configured rates;
- a 25% provider contingency;
- infrastructure and storage allocation;
- customer-support and exception reserve;
- product-operations and administration reserve;
- reviewer labour for a review budget;
- the selected payment method's processing fee;
- a conservative 17% company-income-tax assumption; and
- a target 15% after-tax operating margin on pre-GST revenue.

For delivery cost `C`, fixed payment fee `p`, variable payment rate `f`, GST
rate `g`, income-tax assumption `t`, and target after-tax margin `m`, the
pre-GST floor is:

`R_floor = (C + p) / (1 - f * (1 + g) - m / (1 - t))`

The customer quote is the higher of list price and this floor, rounded up to
the next cent. Cent-level Stripe/GST rounding is then recomputed and the quote
is raised by cents if necessary. GST is pass-through and is excluded from
revenue and margin.

Stripe's current Singapore standard pricing is 1.3% for PayNow and 3.4% plus
S$0.50 for a successful domestic card transaction. International-card and
currency-conversion surcharges may also apply. See
[Stripe Singapore pricing](https://stripe.com/en-sg/pricing).

IRAS states that Singapore GST is 9% for GST-registered businesses and that the
headline corporate income-tax rate is 17%. Actual tax can differ because of
entity type, exemptions, rebates and allowable expenses. See
[IRAS GST rates](https://www.iras.gov.sg/taxes/goods-services-tax-%28gst%29/basics-of-gst/current-gst-rates)
and [IRAS corporate tax rates](https://www.iras.gov.sg/quick-links/tax-rates/corporate-income-tax-rates).

## One-hour pilot examples

Using the v1 defaults, no GST registration, S$1.40/US$ and published provider
rates:

| Quote | Price | Model/provider cost | Total delivery cost including reserves | Stripe estimate | After-tax operating margin |
| --- | ---: | ---: | ---: | ---: | ---: |
| Dialect Draft via card | S$5.90 | S$0.18 | S$3.57 | S$0.70 | about 22.9% |
| Compare & Flag via card | S$9.90 | S$0.55 | S$6.09 | S$0.84 | about 24.9% |
| 30 active review minutes via card | S$27.00 | — | S$20.00 | S$1.42 | about 17.2% |

These are planning examples, not guaranteed realised margins. Every pilot job
must keep its actual provider, support and review costs so the price book can be
recalibrated after the first 10 paid orders.

## Subscription decision

Defer subscriptions until APMA has at least two months of repeat-use data. Add
a plan only if customers return often enough that prepayment genuinely helps
them. The first candidate should be a capped monthly credit pack, not unlimited
usage:

- **Researcher 10 (candidate, not launched): S$49/month** for up to 10 Dialect
  Draft hours or 5 Compare & Flag hours, with one-month rollover.
- Human review is always separate.
- No annual lock-in during the pilot.
- Do not launch unless measured costs still clear the 15% after-tax floor and
  at least five pilot users show repeat demand.

This candidate is deliberately documented but not implemented. Pay-as-you-go
is the recommended launch model.

## Commercial go-live gates

Before any real charge:

1. Confirm the contracting entity and GST status with the owner/accountant.
2. Approve customer terms, cancellation/refund rules, privacy notice, provider
   processing disclosure and retention/deletion policy.
3. Map each paid product to the exact tested provider workflow; a displayed
   plan must never silently execute a different provider set.
4. Use server-created Stripe Checkout Sessions, PayNow plus cards, signed
   webhooks and idempotent order state. Store no card or bank details in APMA.
5. Pass Stripe test-mode order, payment, failure, retry, refund and webhook
   replay tests.
6. Keep `PAYMENTS_ENABLED=false`, `STRIPE_MODE=test`, and `DRY_RUN=true` until
   those gates are explicitly approved.
