"""File-backed human exception review for a strict APMA final draft."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
from typing import Any

from services.transcript_alignment import PROVIDER_ORDER


DECISION_TYPES = {"provider_candidate", "manual_correction"}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _format_time(seconds: float) -> str:
    milliseconds = int(round(float(seconds) * 1000.0))
    hours, remainder = divmod(milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    secs, millis = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d}.{millis:03d}"


class ReviewWorkspace:
    """One local, single-user review workspace backed by JSON files."""

    def __init__(self, final_draft_path: Path, audio_dir: Path, output_dir: Path):
        self.final_draft_path = Path(final_draft_path).resolve()
        self.audio_dir = Path(audio_dir).resolve()
        self.output_dir = Path(output_dir).resolve()
        if not self.final_draft_path.is_file():
            raise FileNotFoundError(f"FINAL_DRAFT.json not found: {self.final_draft_path}")
        if not self.audio_dir.is_dir():
            raise FileNotFoundError(f"Review audio directory not found: {self.audio_dir}")
        self._source_bytes = self.final_draft_path.read_bytes()
        self.source_sha256 = _sha256_bytes(self._source_bytes)
        self.source = json.loads(self._source_bytes.decode("utf-8"))
        if self.source.get("schema_version") != "apma.final-draft.v1":
            raise ValueError("Review input must use apma.final-draft.v1")
        self.provider_order = tuple(self.source.get("provider_order") or PROVIDER_ORDER)
        if not self.provider_order or len(set(self.provider_order)) != len(
            self.provider_order
        ):
            raise ValueError("FINAL_DRAFT provider_order must contain unique provider codes")
        windows = self.source.get("windows")
        if not isinstance(windows, list):
            raise ValueError("FINAL_DRAFT windows must be a list")
        ids = [str(window.get("window_id") or "") for window in windows]
        if not all(ids) or len(ids) != len(set(ids)):
            raise ValueError("FINAL_DRAFT window_id values must be unique")
        self._windows = {window_id: window for window_id, window in zip(ids, windows)}

    @property
    def decisions_path(self) -> Path:
        return self.output_dir / "review_decisions.json"

    @property
    def reviewed_json_path(self) -> Path:
        return self.output_dir / "FINAL_REVIEWED.json"

    @property
    def reviewed_html_path(self) -> Path:
        return self.output_dir / "FINAL_REVIEWED.html"

    def _load_decisions(self) -> dict[str, dict[str, Any]]:
        if not self.decisions_path.is_file():
            return {}
        payload = json.loads(self.decisions_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "apma.review-decisions.v1":
            raise ValueError("Unsupported review decision schema")
        if payload.get("source_final_draft_sha256") != self.source_sha256:
            raise ValueError("Saved decisions do not match the current FINAL_DRAFT source")
        decisions = payload.get("decisions")
        if not isinstance(decisions, list):
            raise ValueError("Saved review decisions must be a list")
        result: dict[str, dict[str, Any]] = {}
        for decision in decisions:
            if not isinstance(decision, dict):
                raise ValueError("Saved review decision must be an object")
            window_id = str(decision.get("window_id") or "")
            if window_id not in self._windows or window_id in result:
                raise ValueError("Saved review decision has an invalid or duplicate window_id")
            result[window_id] = decision
        return result

    def _write_decisions(self, decisions: dict[str, dict[str, Any]]) -> None:
        ordered = [
            decisions[window["window_id"]]
            for window in self.source["windows"]
            if window["window_id"] in decisions
        ]
        _atomic_json(
            self.decisions_path,
            {
                "schema_version": "apma.review-decisions.v1",
                "source_final_draft_sha256": self.source_sha256,
                "decisions": ordered,
            },
        )

    def _audio_path(self, window_id: str) -> Path:
        if window_id not in self._windows:
            raise ValueError(f"Unknown review window: {window_id}")
        index = next(
            index
            for index, window in enumerate(self.source["windows"], start=1)
            if window["window_id"] == window_id
        )
        path = (self.audio_dir / f"chunk-{index:05d}.wav").resolve()
        try:
            path.relative_to(self.audio_dir)
        except ValueError as exc:
            raise ValueError("Review audio path escapes its configured directory") from exc
        if not path.is_file():
            raise FileNotFoundError(f"Exact review clip is missing for {window_id}")
        expected_sha = str(self._windows[window_id].get("clip_sha256") or "")
        actual_sha = _sha256_file(path)
        if not expected_sha or actual_sha != expected_sha:
            raise ValueError(f"Review audio SHA-256 does not match {window_id}")
        return path

    def audio(self, window_id: str) -> tuple[Path, dict[str, Any]]:
        window = self._windows.get(window_id)
        if window is None:
            raise ValueError(f"Unknown review window: {window_id}")
        path = self._audio_path(window_id)
        return path, {
            "window_id": window_id,
            "global_start_sec": window["global_start_sec"],
            "global_end_sec": window["global_end_sec"],
            "clip_sha256": window["clip_sha256"],
            "timing_authority": window.get("timing_authority"),
        }

    def save_decision(
        self,
        window_id: str,
        decision_type: str,
        *,
        selected_provider: str | None = None,
        final_text: str | None = None,
        reviewed_at: str | None = None,
    ) -> dict[str, Any]:
        window = self._windows.get(window_id)
        if window is None:
            raise ValueError(f"Unknown review window: {window_id}")
        if not window.get("review_required"):
            raise ValueError("GREEN auto-accepted windows do not require a Goal J decision")
        if decision_type not in DECISION_TYPES:
            raise ValueError("decision_type must be provider_candidate or manual_correction")
        candidates = window.get("provider_candidates")
        if not isinstance(candidates, dict):
            raise ValueError("Review window provider candidates are missing")

        if decision_type == "provider_candidate":
            if selected_provider not in self.provider_order:
                raise ValueError("selected_provider must identify a retained provider candidate")
            candidate = candidates.get(selected_provider)
            if not isinstance(candidate, dict) or not isinstance(candidate.get("text"), str):
                raise ValueError("Selected provider candidate is unavailable")
            accepted_text = candidate["text"]
            selected_provenance = deepcopy(
                {key: value for key, value in candidate.items() if key not in {"text", "missing"}}
            )
        else:
            if not isinstance(final_text, str) or not final_text.strip():
                raise ValueError("manual_correction final_text must contain text")
            accepted_text = final_text
            selected_provider = None
            selected_provenance = None

        decision = {
            "window_id": window_id,
            "global_start_sec": window["global_start_sec"],
            "global_end_sec": window["global_end_sec"],
            "decision_type": decision_type,
            "selected_provider": selected_provider,
            "final_text": accepted_text,
            "final_text_sha256": _sha256_bytes(accepted_text.encode("utf-8")),
            "reviewed_at": reviewed_at or _utc_now(),
            "selected_candidate_provenance": selected_provenance,
            "original_candidates": deepcopy(candidates),
        }
        decisions = self._load_decisions()
        decisions[window_id] = decision
        self._write_decisions(decisions)
        self.write_reviewed_outputs(decisions)
        return deepcopy(decision)

    def build_reviewed(self, decisions: dict[str, dict[str, Any]] | None = None) -> dict[str, Any]:
        decisions = decisions if decisions is not None else self._load_decisions()
        windows: list[dict[str, Any]] = []
        resolved = 0
        for source_window in self.source["windows"]:
            window = deepcopy(source_window)
            window_id = window["window_id"]
            if window.get("auto_accepted"):
                selection_method = str(window.get("selection_method") or "")
                assisted = selection_method == "gpt56_sol_assisted_reconciliation"
                window["review_resolution"] = {
                    "decision_type": (
                        "auto_accepted_derived_reconciliation"
                        if assisted
                        else "auto_accepted_provider_candidate"
                    ),
                    "selected_provider": window.get("selected_provider"),
                    "final_text": window.get("final_text"),
                    "reviewed_at": None,
                    "source": selection_method or "deterministic representative",
                    "correctness_claimed": False,
                }
                resolved += 1
            elif window_id in decisions:
                decision = deepcopy(decisions[window_id])
                window["final_text"] = decision["final_text"]
                window["review_required"] = False
                window["review_resolution"] = decision
                resolved += 1
            else:
                window["final_text"] = None
                window["review_resolution"] = None
            windows.append(window)
        return {
            "schema_version": "apma.final-reviewed.v1",
            "provider_order": list(self.provider_order),
            "source_final_draft": {
                "sha256": self.source_sha256,
                "modified": False,
            },
            "statistics": {
                "total_windows": len(windows),
                "resolved_windows": resolved,
                "unresolved_windows": len(windows) - resolved,
                "human_review_decisions": len(decisions),
            },
            "windows": windows,
        }

    def write_reviewed_outputs(
        self, decisions: dict[str, dict[str, Any]] | None = None
    ) -> dict[str, str]:
        payload = self.build_reviewed(decisions)
        _atomic_json(self.reviewed_json_path, payload)
        temporary = self.reviewed_html_path.with_suffix(".html.tmp")
        temporary.write_text(render_reviewed_html(payload), encoding="utf-8")
        temporary.replace(self.reviewed_html_path)
        return {
            "json": str(self.reviewed_json_path),
            "html": str(self.reviewed_html_path),
        }

    def view(self) -> dict[str, Any]:
        decisions = self._load_decisions()
        reviewed = self.build_reviewed(decisions)
        windows = []
        for window in reviewed["windows"]:
            item = deepcopy(window)
            item["audio_url"] = f"/api/review/audio/{window['window_id']}"
            windows.append(item)
        return {
            "source_final_draft_sha256": self.source_sha256,
            "provider_order": list(self.provider_order),
            "statistics": reviewed["statistics"],
            "windows": windows,
            "outputs": {
                "json": str(self.reviewed_json_path),
                "html": str(self.reviewed_html_path),
            },
        }


def render_reviewed_html(payload: dict[str, Any]) -> str:
    provider_order = tuple(payload.get("provider_order") or PROVIDER_ORDER)
    rows = []
    for window in payload["windows"]:
        resolution = window.get("review_resolution")
        final_text = resolution.get("final_text") if isinstance(resolution, dict) else None
        decision = resolution.get("decision_type") if isinstance(resolution, dict) else "review_required"
        candidates = "".join(
            f"<h4>{html.escape(provider)}</h4><pre>{html.escape(str(window['provider_candidates'][provider].get('text') or ''))}</pre>"
            for provider in provider_order
        )
        rows.append(
            f"<tr><td>{_format_time(window['global_start_sec'])}<br>to<br>{_format_time(window['global_end_sec'])}</td>"
            f"<td>{html.escape(str(window['derived_status']))}</td><td>{html.escape(str(decision))}"
            f"<pre>{html.escape(str(final_text or 'REVIEW REQUIRED'))}</pre></td><td>{candidates}</td></tr>"
        )
    return (
        "<!doctype html><html><head><meta charset=\"utf-8\"><title>APMA Final Reviewed</title>"
        "<style>body{font-family:Arial,sans-serif;margin:24px}table{border-collapse:collapse;width:100%;table-layout:fixed}th,td{border:1px solid #bbb;padding:10px;vertical-align:top}th{background:#1f4e78;color:#fff}pre{white-space:pre-wrap;word-break:break-word}</style>"
        "</head><body><h1>APMA FINAL REVIEWED</h1><p>Human decisions are recorded with unchanged source candidates and provenance.</p>"
        "<table><thead><tr><th>TIME</th><th>STATUS</th><th>FINAL TEXT / DECISION</th><th>ORIGINAL PROVIDER CANDIDATES</th></tr></thead><tbody>"
        + "".join(rows)
        + "</tbody></table></body></html>"
    )


REVIEW_HTML = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>APMA Human Exception Review</title>
  <style>
    :root{font-family:Arial,sans-serif;color:#17202a;background:#f5f7f9}*{box-sizing:border-box}
    body{margin:0}header,main{width:min(1180px,calc(100% - 32px));margin:auto}header{padding:24px 0 16px}
    h1{margin:0 0 6px}.notice{padding:12px;border-left:4px solid #1f6feb;background:#eaf2fd;line-height:1.45}
    #summary{margin:14px 0;font-weight:bold}.window,#speaker-mapping{background:#fff;border:1px solid #d8e0e6;border-radius:8px;margin:14px 0;padding:16px}
    .window-head{display:flex;justify-content:space-between;gap:12px;align-items:start}.status{font-weight:bold;padding:5px 8px;border-radius:5px}
    .GREEN{background:#c6efce}.AMBER{background:#ffeb9c}.RED{background:#ffc7ce}.state{margin:12px 0;padding:10px;background:#f6f8fa;border-left:4px solid #7f8c8d}
    .candidates{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px}.candidate{border:1px solid #d8e0e6;padding:10px;min-width:0}
    pre,textarea{white-space:pre-wrap;word-break:break-word;font:14px/1.45 Arial,sans-serif}pre{margin:6px 0}audio{width:100%;margin:10px 0}
    .actions{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0}button{border:0;border-radius:6px;padding:9px 12px;background:#1f6feb;color:#fff;font-weight:bold;cursor:pointer}
    button:disabled{background:#9aa4af;cursor:not-allowed}.reconcile{background:#fff;border:2px solid #1f6feb;border-radius:8px;margin:0 0 16px;padding:16px}.reconcile h2{margin-top:0}.cost{font-weight:bold;margin:10px 0}.approve{display:flex;gap:8px;align-items:flex-start;margin:12px 0}
    button.manual{background:#5b6570}textarea{width:100%;min-height:100px;border:1px solid #aeb8c2;border-radius:6px;padding:10px}.saved{color:#1a7f64;font-weight:bold}.error{color:#b42318;font-weight:bold}
    .mapping-row{display:grid;grid-template-columns:minmax(160px,1fr) minmax(160px,1fr) minmax(190px,1fr) auto;gap:8px;align-items:end;margin:10px 0}.mapping-row label{font-size:12px;font-weight:bold}.mapping-row input{display:block;width:100%;margin-top:4px;padding:8px;border:1px solid #aeb8c2;border-radius:5px}.scope{font-size:12px;color:#57606a;word-break:break-all}
    @media(max-width:800px){.candidates,.mapping-row{grid-template-columns:1fr}.window-head{display:block}.status{display:inline-block;margin-top:8px}}
  </style>
</head>
<body>
  <header><h1>APMA Human Exception Review</h1><p class="notice">Listen to the exact APMA-owned clip, compare the unchanged provider candidates, then select one or enter a manual correction. If GPT-5.6 Sol assistance is used, its proposed draft is clearly labelled and never replaces the original provider evidence.</p></header>
  <main><section id="reconciliation" class="reconcile" hidden><h2>Reduce this review with GPT-5.6 Sol</h2><p>Instead of reviewing over one thousand mismatched rows, APMA gives GPT-5.6 Sol the three transcripts for each <strong>same exact audio chunk</strong>. GPT-5.6 Sol reads text only; it does not hear the audio. It prepares a proposed draft and leaves uncertain chunks for you to hear and correct below.</p><div id="reconciliation-status">Calculating the estimate…</div><label class="approve"><input id="approve-reconciliation" type="checkbox"><span id="approve-reconciliation-label">I approve this one GPT-5.6 Sol run using the exact displayed estimate plus 15% reserve.</span></label><button id="run-reconciliation" disabled>Run GPT-5.6 Sol assisted comparison</button></section><div id="summary">Loading review windows…</div><div id="message" aria-live="polite"></div><section id="speaker-mapping" hidden><h2>Human speaker labels</h2><p>Enter a meeting label only when you know it. APMA always keeps the original provider ID in brackets. A meeting speaker ID may be reused manually across separately scoped runs; APMA does not infer that match.</p><div id="speaker-mapping-rows"></div></section><div id="windows"></div></main>
  <script>
    let providers = [];
    const windowsBox = document.getElementById("windows");
    const summary = document.getElementById("summary");
    const message = document.getElementById("message");
    const speakerMapping = document.getElementById("speaker-mapping");
    const speakerMappingRows = document.getElementById("speaker-mapping-rows");
    const reconciliation = document.getElementById("reconciliation");
    const reconciliationStatus = document.getElementById("reconciliation-status");
    const approveReconciliation = document.getElementById("approve-reconciliation");
    const runReconciliationButton = document.getElementById("run-reconciliation");
    const jobId = new URLSearchParams(window.location.search).get("job_id");
    const reviewQuery = jobId ? `?job_id=${encodeURIComponent(jobId)}` : "";
    let reconciliationEstimate = null;

    function timeLabel(value) {
      const total = Math.round(Number(value) * 1000);
      const h = Math.floor(total / 3600000);
      const m = Math.floor((total % 3600000) / 60000);
      const s = Math.floor((total % 60000) / 1000);
      const ms = total % 1000;
      return [h,m,s].map((item) => String(item).padStart(2,"0")).join(":") + "." + String(ms).padStart(3,"0");
    }

    function textElement(tag, text, className) {
      const node = document.createElement(tag);
      node.textContent = text;
      if (className) node.className = className;
      return node;
    }

    async function api(path, options={}) {
      const response = await fetch(path, options);
      const payload = await response.json();
      if (!response.ok) throw new Error(payload.error || "Review request failed");
      return payload;
    }

    async function saveDecision(windowId, decisionType, selectedProvider, finalText) {
      message.textContent = "Saving review decision…";
      message.className = "";
      try {
        await api("/api/review/decisions" + reviewQuery, {
          method: "POST",
          headers: {"Content-Type":"application/json"},
          body: JSON.stringify({job_id:jobId, window_id:windowId, decision_type:decisionType, selected_provider:selectedProvider, final_text:finalText})
        });
        message.textContent = "Decision saved. It will remain after refresh or reopen.";
        message.className = "saved";
        await loadReview();
      } catch (error) {
        message.textContent = error.message;
        message.className = "error";
      }
    }

    function dollars(value) {
      return "$" + Number(value || 0).toFixed(2);
    }

    async function loadReconciliationEstimate() {
      if (!jobId) return;
      try {
        reconciliationEstimate = await api("/api/quality/reconcile/estimate" + reviewQuery);
        reconciliation.hidden = false;
        if (reconciliationEstimate.already_completed) {
          const active = reconciliationEstimate.active_reconciliation || {};
          reconciliationStatus.textContent = `GPT-5.6 Sol assisted comparison is active: ${active.window_count || reconciliationEstimate.window_count} exact audio chunks, ${active.review_required || 0} requiring human review.`;
          approveReconciliation.hidden = true;
          runReconciliationButton.disabled = true;
          runReconciliationButton.textContent = "GPT-5.6 Sol comparison completed";
          return;
        }
        reconciliationStatus.textContent = `${reconciliationEstimate.window_count} exact audio chunks · estimated GPT-5.6 Sol cost ${dollars(reconciliationEstimate.estimated_cost_usd)} · approval limit including 15% reserve ${dollars(reconciliationEstimate.run_budget_cap_usd)} · configured maximum ${dollars(reconciliationEstimate.max_reconciliation_cost_per_job_usd)}. No audio is sent to GPT-5.6 Sol.`;
        approveReconciliation.hidden = false;
        runReconciliationButton.disabled = !reconciliationEstimate.within_cap || !approveReconciliation.checked;
      } catch (error) {
        reconciliation.hidden = false;
        reconciliationStatus.textContent = `GPT-5.6 Sol estimate is unavailable: ${error.message}`;
        reconciliationStatus.className = "error";
        approveReconciliation.hidden = true;
        runReconciliationButton.disabled = true;
      }
    }

    async function runReconciliation() {
      if (!reconciliationEstimate || !approveReconciliation.checked) return;
      runReconciliationButton.disabled = true;
      reconciliationStatus.textContent = "GPT-5.6 Sol is comparing the three retained transcripts chunk by chunk. Completed chunks are retained if a retry is needed…";
      try {
        await api("/api/quality/reconcile/run" + reviewQuery, {
          method: "POST",
          headers: {"Content-Type":"application/json"},
          body: JSON.stringify({job_id:jobId, confirm_live_reconciliation:true, run_budget_cap_usd:reconciliationEstimate.run_budget_cap_usd})
        });
        window.location.reload();
      } catch (error) {
        reconciliationStatus.textContent = error.message;
        reconciliationStatus.className = "error";
        runReconciliationButton.disabled = false;
      }
    }

    async function saveSpeakerMapping(localSpeakerKey, displayName, canonicalSpeakerId) {
      message.textContent = "Saving human speaker label…";
      message.className = "";
      try {
        await api("/api/review/speaker-mappings" + reviewQuery, {
          method: "POST",
          headers: {"Content-Type":"application/json"},
          body: JSON.stringify({job_id:jobId, local_speaker_key:localSpeakerKey, display_name:displayName, canonical_speaker_id:canonicalSpeakerId || null})
        });
        message.textContent = "Speaker label saved. The provider ID remains visible and the label will remain after refresh.";
        message.className = "saved";
        await loadReview();
      } catch (error) {
        message.textContent = error.message;
        message.className = "error";
      }
    }

    function renderSpeakerMappings(payload) {
      const speakers = payload.speaker_mappings && payload.speaker_mappings.local_speakers;
      if (!Array.isArray(speakers) || !speakers.length) {
        speakerMapping.hidden = true;
        speakerMappingRows.replaceChildren();
        return;
      }
      speakerMapping.hidden = false;
      const rows = speakers.map((speaker) => {
        const row = document.createElement("div");
        row.className = "mapping-row";
        const identity = document.createElement("div");
        identity.append(textElement("strong", speaker.display_label));
        identity.append(textElement("div", `${speaker.provider_code} / ${speaker.run_id || "no run"} / ${speaker.chunk_filename || "no chunk"}`, "scope"));
        const nameLabel = textElement("label", "Human display name");
        const name = document.createElement("input");
        name.value = speaker.mapping ? speaker.mapping.display_name : "";
        name.placeholder = "Example: John or Speaker B";
        nameLabel.append(name);
        const canonicalLabel = textElement("label", "Meeting speaker ID (optional)");
        const canonical = document.createElement("input");
        canonical.value = speaker.mapping ? speaker.mapping.canonical_speaker_id : "";
        canonical.placeholder = "Auto-created if blank";
        canonicalLabel.append(canonical);
        const button = textElement("button", "Save label");
        button.addEventListener("click", () => saveSpeakerMapping(speaker.local_speaker_key, name.value, canonical.value));
        row.append(identity, nameLabel, canonicalLabel, button);
        return row;
      });
      speakerMappingRows.replaceChildren(...rows);
    }

    function renderWindow(window) {
      const card = document.createElement("article");
      card.className = "window";
      const head = document.createElement("div");
      head.className = "window-head";
      head.append(textElement("h2", `${window.window_id} — ${timeLabel(window.global_start_sec)} to ${timeLabel(window.global_end_sec)}`));
      head.append(textElement("span", window.derived_status, `status ${window.derived_status}`));
      card.append(head);

      const resolution = window.review_resolution;
      const state = document.createElement("div");
      state.className = "state";
      if (resolution) {
        state.append(textElement("strong", `Current final state: ${resolution.decision_type}`));
        state.append(textElement("pre", resolution.final_text || ""));
        if (resolution.selected_provider) state.append(textElement("small", `Source provider: ${resolution.selected_provider}`));
      } else {
        state.append(textElement("strong", "Current final state: REVIEW REQUIRED"));
      }
      card.append(state);

      const speakerEvidence = window.speaker_evidence;
      if (speakerEvidence && Array.isArray(speakerEvidence.segments) && speakerEvidence.segments.length) {
        const evidence = document.createElement("div");
        evidence.className = "state";
        evidence.append(textElement("strong", "Gem35T provider-native speaker/timing evidence"));
        speakerEvidence.segments.forEach((segment) => {
          evidence.append(textElement("pre", `${timeLabel(segment.global_start_sec)}–${timeLabel(segment.global_end_sec)} ${segment.display_label || segment.speaker_id || "UNLABELLED"}: ${segment.text || ""}`));
        });
        card.append(evidence);
      }

      const candidates = document.createElement("div");
      candidates.className = "candidates";
      providers.forEach((provider) => {
        const box = document.createElement("section");
        box.className = "candidate";
        box.append(textElement("h3", provider));
        box.append(textElement("pre", window.provider_candidates[provider].text || ""));
        candidates.append(box);
      });
      card.append(candidates);

      if (window.review_required) {
        const audio = document.createElement("audio");
        audio.controls = true;
        audio.preload = "metadata";
        audio.src = window.audio_url;
        card.append(audio);

        const actions = document.createElement("div");
        actions.className = "actions";
        providers.forEach((provider) => {
          const button = textElement("button", `Use ${provider}`);
          button.addEventListener("click", () => saveDecision(window.window_id, "provider_candidate", provider, null));
          actions.append(button);
        });
        card.append(actions);

        const textarea = document.createElement("textarea");
        textarea.placeholder = "Type an exact manual correction here. No AI rewriting.";
        if (resolution && resolution.decision_type === "manual_correction") textarea.value = resolution.final_text;
        card.append(textarea);
        const manualButton = textElement("button", "Save manual correction", "manual");
        manualButton.addEventListener("click", () => saveDecision(window.window_id, "manual_correction", null, textarea.value));
        card.append(manualButton);
      }
      return card;
    }

    async function loadReview() {
      try {
        const payload = await api("/api/review" + reviewQuery);
        providers = Array.isArray(payload.provider_order) && payload.provider_order.length
          ? payload.provider_order
          : ["M3ASR", "gptTr", "Gem35T"];
        summary.textContent = `${payload.statistics.total_windows} windows — ${payload.statistics.resolved_windows} resolved, ${payload.statistics.unresolved_windows} still requiring review`;
        renderSpeakerMappings(payload);
        windowsBox.replaceChildren(...payload.windows.map(renderWindow));
      } catch (error) {
        message.textContent = error.message;
        message.className = "error";
      }
    }
    approveReconciliation.addEventListener("change", () => {
      runReconciliationButton.disabled = !reconciliationEstimate || !reconciliationEstimate.within_cap || !approveReconciliation.checked;
    });
    runReconciliationButton.addEventListener("click", runReconciliation);
    loadReconciliationEstimate();
    loadReview();
  </script>
</body>
</html>"""


__all__ = ["DECISION_TYPES", "REVIEW_HTML", "ReviewWorkspace", "render_reviewed_html"]
