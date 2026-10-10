# APMA Local Dashboard

The local dashboard is a browser UI for the existing APMA pipeline.

It can:

- upload common audio and video-audio files from your computer to the local service
- show duration, file size, provider/model options, and estimated transcription cost
- lock the displayed estimate as the maximum budget for that one transcription run
- run guarded live transcription only when the selected provider has the required environment variables and the user confirms the run
- choose a meeting-minutes style preset before running the job
- estimate and generate live OpenAI minutes as a separate confirmed step after transcription
- show transcript, minutes, action items, and summary outputs
- open the self-contained transcript HTML or download SRT/VTT when those exports exist
- show the absolute subproject, original-audio, MP3, and MP3-chunks folder addresses
- record structured, pseudonymous pilot outcomes and download a privacy-minimised
  cohort summary

The dashboard route does not translate transcript wording or directly invoke
DOCX/PDF export. The meeting-archive service can generate DOCX and PDF derived
views while keeping final JSON authoritative.

`full_transcript.json` remains the canonical transcript authority. The dashboard's HTML, SRT,
and VTT links expose deterministic derivatives of that JSON. SRT/VTT omit untimed transcript
segments rather than assigning guessed timestamps.

## Pilot Outcome Evidence

After opening a retained job, use **Pilot outcome** only for synthetic QA or a
separately governed consented pilot. The form records pseudonymous identifiers,
structured governance attestations, review/cost inputs, human-reference counts,
and trust/adoption answers. It derives duration, provider cost, segments, and
corrections from the job.

`POST /api/pilot/evidence` appends a hash-linked event and updates the current
job view. `GET /api/pilot/summary?cohort_code=<code>` returns a cohort aggregate.
The aggregate excludes synthetic records and suppresses participant codes,
consent-record IDs, source hashes, transcript text, and audio.

Do not enter names, emails, phone numbers, addresses, notes, excerpts, or other
free text. For real participants, keep the consent record and code mapping
outside the job folder and Git. Follow
`docs/CONSENTED_PILOT_RUNBOOK.md` before any external pilot use.

## Transcription Models And Speaker Labels

The dashboard keeps `gpt-4o-mini-transcribe` as the default low-cost transcription model.

It also lists:

- `gpt-transcribe` as the recommended multilingual option.
- `gpt-4o-transcribe` for higher-quality plain transcription.
- `gpt-4o-transcribe-diarize` when you need speaker-labelled output.
- `M3ASR` for hosted `MERaLiON-3-3B-ASR-Consortium`, a Singapore-focused
  multilingual and code-switching route.
- `Gem35T` for Gemini `gemini-3.5-transcribe` with verbatim audio transcription,
  diarization, and word timestamps.
- `QwenA3FT` for Alibaba Cloud Qwen long-file transcription when its guarded
  staging route is configured. `data_uri` needs only a region-matched Model
  Studio key and is restricted to bounded chunks; private OSS remains the
  larger-input production path.

`gemini-3.7-flash` is retired from the selectable application catalog.
Historical artifacts remain readable, but that retired route cannot be
selected or submitted through the dashboard.

External provider routes are disabled by default. They require an explicit
provider gate, runtime credentials, verified pricing, and a successful
preflight cost cap before any billable request. The selected provider records
the requested model and, when returned, the resolved model/version in
transcript provenance.

Plain transcription models return continuous text. They do not identify speakers.

Choose `gpt-4o-transcribe-diarize + speaker labels` when you need `Speaker 1:`, `Speaker 2:` style output. The app requests `diarized_json`, preserves speaker turns and timestamps in the canonical transcript JSON, and formats labels into the text export. Provider speaker identities are scoped to each chunk; the app does not infer real names or claim that a label is the same person across chunks.

## Minutes Style Presets

The dashboard includes three minutes styles:

- **Standard Minutes**: balanced meeting minutes with decisions, actions, risks, facts, and suggested next steps.
- **Deep Evidence Minutes**: the most detailed record for accountability, evidence, unresolved issues, and later review.
- **Action-Focused Summary**: a shorter output centered on decisions, owners, deadlines, blockers, and follow-up.

The transcription step still writes deterministic placeholder minutes first so every job has complete outputs. After the transcript is ready, use the dashboard's **Estimate Minutes** and **Generate Minutes** buttons to replace that placeholder with live OpenAI minutes.

The preset prompts require mixed-language transcripts to be read across languages, with important non-English points translated into clear English while preserving useful original names, terms, and phrases. They also require unclear points to be marked as `unclear / needs verification` and keep AI-suggested next steps separate from confirmed meeting decisions.

Live minutes use a text-generation model, separate from the transcription model:

- **Default**: `gpt-5.4-mini`, recommended starting point for good detail and lower cost.
- **Premium**: `gpt-5.4`, for stronger reasoning and richer minutes.
- **Best Quality**: `gpt-5.5`, for the most demanding evidence-preserving minutes when budget allows.

Model names and pricing are configurable through environment variables because model access and pricing can change.

## Start The Dashboard

The dashboard supports explicitly gated live provider runs. They may create a
billable charge. The default remains dry-run and does not call external APIs.

Open PowerShell, then go to the repo folder:

```powershell
cd "C:\path\to\apma-singapore-asr"
```

Before starting a live provider route, set that provider's API key in your
terminal session only. Do not commit it, save it in the repo, or paste it into
screenshots. The dashboard does not display the key.

PowerShell hidden input:

```powershell
$sec = Read-Host "Paste OpenAI API key" -AsSecureString
$bstr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($sec)
$env:OPENAI_API_KEY = [Runtime.InteropServices.Marshal]::PtrToStringAuto($bstr)
```

Check that the key is present without printing it:

```powershell
if ($env:OPENAI_API_KEY) { "key is set" } else { "key is missing" }
```

Then start the dashboard with live gates enabled.

PowerShell:

```powershell
docker run --rm -w /app -p 8000:8000 -e DRY_RUN=true -e ENABLE_LIVE_OPENAI_TRANSCRIPTION=true -e ENABLE_LIVE_OPENAI_MINUTES=true -e MAX_COST_PER_JOB_USD=2.00 -e MAX_MINUTES_COST_PER_JOB_USD=2.00 -e "APMA_HOST_JOBS_PATH=$(Join-Path (Get-Location) 'jobs')" -e OPENAI_API_KEY="$env:OPENAI_API_KEY" -v "${PWD}:/app" apma-v5:dev python scripts/local_dashboard.py
```

Open:

```text
http://127.0.0.1:8000
```

The UI still requires explicit confirmation before running transcription and another explicit confirmation before generating live minutes.

## Budget Lock

After you upload an audio file, the dashboard estimates cost for configured OpenAI transcription models before the run button is used.

The estimate uses:

- audio duration
- estimated normalized WAV chunk count and chunk overlap
- configured model price per minute
- `MAX_COST_PER_JOB_USD`

For the selected model, the dashboard sends the displayed estimate back to the server as the maximum budget for that one run. If the estimate changes or exceeds `MAX_COST_PER_JOB_USD`, the server refuses to run.

This is an application-side preflight cap, not an OpenAI account billing limit. For stronger protection, also set project or account budgets in OpenAI before doing larger tests.

Pricing can change. Check the official OpenAI pricing page before relying on estimates for production budgeting.

## Minutes Budget Lock

Live minutes are a second billable step after transcription.

After transcription completes:

1. Select the minutes style.
2. Select the minutes model.
3. Click **Estimate Minutes**.
4. Review the estimated token count and locked minutes budget.
5. Tick the minutes approval checkbox.
6. Click **Generate Minutes**.

The server refuses live minutes unless all are true:

- `ENABLE_LIVE_OPENAI_MINUTES=true`
- `OPENAI_API_KEY` exists in the server environment
- the transcript already exists
- the UI approval checkbox is ticked
- the sent budget exactly matches the latest estimate
- the estimate is within `MAX_MINUTES_COST_PER_JOB_USD`

The default local command above sets `MAX_MINUTES_COST_PER_JOB_USD=2.00`. Lower it for stricter testing, or raise it only intentionally.

## Supported Audio

Current dashboard transcription supports these formats through FFmpeg normalization:

- WAV
- MP3
- M4A
- MP4
- MPEG
- MPGA
- WebM
- OGG
- FLAC
- AAC
- 3GP
- AMR
- WMA

Support means the file is inspected, normalized to WAV when needed, chunked, transcribed by chunks, and then aggregated into the same transcript/minutes outputs.

Legacy phone formats such as 3GP, AMR, and WMA depend on the FFmpeg build being able to decode that exact file. If FFmpeg cannot read a file, the dashboard refuses before transcription.

Keep real/private meeting audio outside Git tracking. Runtime dashboard uploads are written under `jobs/`, which is ignored by Git.

## Per-audio Subproject Folders

Every completed APMA job is its own subproject under `jobs/<job-id>/`. The
service retains this media layout after temporary transcription WAV cleanup:

```text
jobs/<job-id>/
  audio/
    original/<original filename>
    mp3/<original stem>.mp3
    mp3/chunks/<original stem> - part-NNN start-end.mp3
    audio_project.json
```

The original is byte-verified against the ingested source. The full MP3 is
encoded at 128 kbps and 48 kHz. The nested chunks are 10-minute, range-labelled
MP3 stream copies whose aggregate duration is checked against the full MP3.
The dashboard shows all four absolute folder addresses and provides a copy
button after a job finishes. `Start_APMA.ps1` passes the Windows jobs address
into Docker so the displayed value opens directly in File Explorer rather than
showing Docker's internal `/app/jobs` path.

## Return To Safe Defaults

After live testing:

```powershell
Remove-Item Env:\OPENAI_API_KEY -ErrorAction SilentlyContinue
$env:DRY_RUN="true"
$env:TRANSCRIPTION_ENGINE="mock"
$env:ENABLE_LIVE_OPENAI_TRANSCRIPTION="false"
$env:ENABLE_LIVE_OPENAI_MINUTES="false"
```
