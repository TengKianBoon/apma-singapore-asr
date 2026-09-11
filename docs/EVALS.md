# ASR Evaluation Guide

## Purpose

APMA uses deterministic offline checks to detect text-metric and transcript
contract regressions without an API key or network call. These checks are a
harness baseline, not evidence that one provider is best for real meetings.

## Run In Docker

PowerShell:

```powershell
docker run --rm -w /app -e DRY_RUN=true -v "${PWD}:/app" apma-v5:dev python scripts/run_asr_evals.py
```

Write a local report under ignored job output:

```powershell
docker run --rm -w /app -e DRY_RUN=true -v "${PWD}:/app" apma-v5:dev python scripts/run_asr_evals.py --output jobs/evals/asr-golden.json
```

## Current Golden Set

`tests/fixtures/asr/golden_cases.json` contains synthetic English, Simplified
Chinese, Bahasa Indonesia, and mixed-language text pairs. No private audio or
provider call is used.

The report includes per-case and aggregate word error rate (WER), character
error rate (CER), provider/model scorecards, cost-per-audio-minute evidence when
supplied, and a deterministic release-gate result. The harness also validates
that diarized canonical transcript segments retain speaker labels and numeric
timestamps.

The versioned default scorecard thresholds are deliberately visible in the
report. `pass`, `warn`, and `fail` are regression-harness qualifications, not a
claim that a provider is best. A provider winner must never be declared without
representative, like-for-like evidence.

## Adding Real Provider Evidence Later

Use only licensed, synthetic, or explicitly approved evaluation recordings.
Keep private audio and generated provider artifacts outside Git. Record fixture
provenance, language mix, audio conditions, expected transcript, model/run
settings, cost, and reviewer decision. Compare providers on the same evidence
and do not hard-code a winner before representative results exist.
