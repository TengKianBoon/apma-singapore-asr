# APMA Commercial Pilot Plan

Status: approved for local implementation planning on 2026-09-11. This plan
does not authorise live payments, public deployment, customer solicitation,
provider expenditure, use of private recordings, or publication of commercial
results.

## Outcome

Prepare APMA for a small, chargeable Singapore pilot without turning it into a
broad meeting-transcription SaaS. The offer is offline transcription assurance
for difficult Singapore and Southeast Asian recordings, beginning with
Hokkien/Singlish/Mandarin/English. Prices must be transparent, below comparable
pay-as-you-go assurance and human transcription, and protected by a
server-enforced cost floor targeting at least 15% after-tax operating margin.

## Product and pricing design

1. Launch pay-as-you-go. Do not launch a mandatory subscription, unlimited
   usage, annual commitment, or automatic renewal before repeat demand and real
   correction-effort data exist.
2. Quote by recorded duration, rounded up to the next audio minute. A minimum
   order covers fixed payment, storage, support, and exception-handling cost.
3. Keep three distinct offers:
   - **Dialect Draft**: one dialect-oriented ASR route and structured export;
     AI output, not human-verified.
   - **Compare & Flag**: two-model comparison with disagreement and review
     flags; AI output, not human-verified.
   - **Human Review Budget**: an optional, capped amount of active reviewer
     time. Extra review requires a new quote and approval; it is never unlimited.
4. Use published list prices as a ceiling comparison, not as the internal cost
   model. Temporary grants/free quotas—including the research/evaluation-only
   MERaLiON grant—have zero effect on the commercial price floor.
5. Prefer PayNow for domestic customers while allowing cards. Stripe Checkout
   should own payment details; APMA should retain only order/payment references.
6. Treat GST registration and tax treatment as operator-confirmed settings.
   Prices are pre-GST unless the configured entity is GST-registered. The 17%
   company-tax rate is a conservative pricing assumption, not tax advice or an
   estimate of actual tax payable after exemptions/rebates.
7. Measure the target margin as after-tax operating profit divided by pre-GST
   revenue. GST is a pass-through and is excluded from revenue and margin.
8. Add subscriptions only after at least two months of repeat-use evidence.
   Any later credit plan must be capped, have explicit expiry/rollover rules,
   and keep human review outside the included AI-hour allowance.

## Initial public price hypothesis

These are pilot list prices in Singapore dollars before GST, subject to the
server-calculated cost floor:

| Offer | Customer price | Included | Important limit |
| --- | ---: | --- | --- |
| Dialect Draft | S$5.90 per audio hour, S$5.90 minimum | One dialect-oriented transcript, timestamps/export where supported | No human accuracy guarantee |
| Compare & Flag | S$9.90 per audio hour, S$9.90 minimum | Two-model comparison, disagreements and review flags | No human accuracy guarantee |
| Human Review Budget | S$0.90 per active review minute, 30-minute minimum | Correction/verification against audio up to the purchased reviewer-time cap | Not priced per audio minute; extra work needs approval |

The quote engine may charge more than list price when configured costs would
otherwise miss the margin floor. It must never silently charge less than the
floor. Pilot discounts must be explicit line items funded from a separate
marketing budget, not hidden by lowering recorded costs.

## Cost and margin model

For pre-GST revenue `R`, non-payment delivery cost `C`, fixed payment fee `p`,
payment variable rate `f`, GST rate `g` (zero when not registered), income-tax
assumption `t`, and target after-tax margin `m`:

`R_floor = (C + p) / (1 - f * (1 + g) - m / (1 - t))`

The chosen pre-GST price is `max(list_price, R_floor)` rounded up to the nearest
cent. The quote must expose model cost, infrastructure/storage allocation,
support/exception reserve, reviewer cost when applicable, payment fee estimate,
tax assumption, target margin, GST, total payable, version, and expiry.

## Implementation scope

1. Add a Python-owned commercial pricing service with immutable product IDs,
   price-book versioning, bounded configuration, deterministic calculations,
   and a server-side margin-floor assertion.
2. Add privacy-minimised quote and order records. They may contain upload/job
   references, duration, pricing inputs, status, provider route, and payment
   reference; they must not contain audio, transcript text, customer names,
   emails, phone numbers, card/bank data, or API keys.
3. Add a local dashboard quote step after audio inspection. The customer-facing
   total and assumptions must be visible before any payment or live provider
   processing.
4. Keep `COMMERCIAL_MODE=false`, `PAYMENTS_ENABLED=false`, `STRIPE_MODE=test`,
   and `DRY_RUN=true` as defaults. Existing local development behavior remains
   unchanged when commercial mode is off.
5. If a Stripe adapter is added, create Checkout Sessions server-side, accept
   only server-calculated amounts, verify webhook signatures, process events
   idempotently, and store no payment credentials. Test doubles must cover the
   workflow without network calls.

## Acceptance criteria

- Every quote passes a recomputed minimum-margin assertion; manipulated client
  prices, durations, plan IDs, review minutes, or payment states are rejected.
- Free quotas, grants, promotional credits, and customer-facing list prices are
  not treated as provider cost evidence.
- Quotes are deterministic, versioned, expiring, currency-specific, and clear
  whether GST is included.
- Human-review cost is based on active reviewer minutes with a hard cap and a
  new-quote boundary for extra work.
- No route creates a live charge or billable provider call by default.
- Unit and dashboard integration tests cover plan selection, rounding, minimum
  order, both payment methods, GST on/off, margin floor, invalid configuration,
  privacy boundaries, and disabled/test payment modes.
- The complete Docker dry-run suite passes.

## Go/no-go boundary

Local quote calculation and a test-mode checkout flow can be built under this
plan. Live charging remains blocked until the owner confirms the contracting
entity, GST status, customer terms/refund policy, privacy/retention wording,
Stripe account readiness, success/cancel URLs, webhook endpoint, and test-mode
evidence. Public hosting, real customers, and commercial use of MERaLiON each
need separate approval and, where relevant, provider permission.
