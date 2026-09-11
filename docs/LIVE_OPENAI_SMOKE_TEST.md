# Live OpenAI Smoke Test

This smoke test is optional and deferred until explicit user approval. Normal development and CI stay dry-run only.

The live path can create billable OpenAI API usage. Keep the sample tiny, non-private, and local. Never commit `.env`, API keys, generated audio, or real meeting audio.

## Current Pricing Note

Pricing is configurable in environment variables and may change. Before running a live test, check the official OpenAI pricing page (`https://openai.com/api/pricing/`) and audio transcription API docs (`https://platform.openai.com/docs/api-reference/audio/createTranscription`). The repository defaults are:

- `gpt-4o-mini-transcribe`: `OPENAI_PRICE_GPT4O_MINI_TRANSCRIBE=0.003`
- `gpt-transcribe`: `OPENAI_PRICE_GPT_TRANSCRIBE=0.006`
- `gpt-4o-transcribe`: `OPENAI_PRICE_GPT4O_TRANSCRIBE=0.006`
- `gpt-4o-transcribe-diarize`: `OPENAI_PRICE_GPT4O_TRANSCRIBE_DIARIZE=0.006`

## Required Gates

The live smoke script refuses to run unless all are true:

- `DRY_RUN=false`
- `ENABLE_LIVE_OPENAI_TRANSCRIPTION=true`
- `TRANSCRIPTION_ENGINE=openai`
- `OPENAI_API_KEY` is set in the environment
- `--confirm-live-api` is passed
- input WAV exists
- estimated cost is below `MAX_COST_PER_JOB_USD`
- input file size is below `OPENAI_FILE_SIZE_LIMIT_BYTES`

## Manual Command

DO NOT RUN UNLESS APPROVED.

Command Prompt:

```cmd
set DRY_RUN=false
set ENABLE_LIVE_OPENAI_TRANSCRIPTION=true
set TRANSCRIPTION_ENGINE=openai
set MAX_COST_PER_JOB_USD=0.05
set OPENAI_API_KEY=your_api_key_from_environment_only
docker run --rm -e DRY_RUN=false -e ENABLE_LIVE_OPENAI_TRANSCRIPTION=true -e TRANSCRIPTION_ENGINE=openai -e MAX_COST_PER_JOB_USD=0.05 -e OPENAI_API_KEY -v "%CD%:/app" apma-v5:dev python scripts/run_live_openai_smoke.py --input-wav sample_audio/live_smoke.wav --generate-synthetic-wav --job-id live-smoke-demo --confirm-live-api
```

PowerShell:

```powershell
$env:DRY_RUN="false"
$env:ENABLE_LIVE_OPENAI_TRANSCRIPTION="true"
$env:TRANSCRIPTION_ENGINE="openai"
$env:MAX_COST_PER_JOB_USD="0.05"
$env:OPENAI_API_KEY="your_api_key_from_environment_only"
docker run --rm -e DRY_RUN=false -e ENABLE_LIVE_OPENAI_TRANSCRIPTION=true -e TRANSCRIPTION_ENGINE=openai -e MAX_COST_PER_JOB_USD=0.05 -e OPENAI_API_KEY -v "${PWD}:/app" apma-v5:dev python scripts/run_live_openai_smoke.py --input-wav sample_audio/live_smoke.wav --generate-synthetic-wav --job-id live-smoke-demo --confirm-live-api
```

## Return To Safe Dry-Run

Command Prompt:

```cmd
set DRY_RUN=true
set TRANSCRIPTION_ENGINE=mock
set ENABLE_LIVE_OPENAI_TRANSCRIPTION=false
set OPENAI_API_KEY=
```

PowerShell:

```powershell
$env:DRY_RUN="true"
$env:TRANSCRIPTION_ENGINE="mock"
$env:ENABLE_LIVE_OPENAI_TRANSCRIPTION="false"
Remove-Item Env:\OPENAI_API_KEY -ErrorAction SilentlyContinue
```

Run normal tests after returning to dry-run:

```cmd
docker run --rm -e DRY_RUN=true -v "%CD%:/app" apma-v5:dev pytest -q
```
