"""File-backed speaker overlay and human mapping for APMA quality review."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any

from services import job as job_mod
from services.speaker_timeline import attach_speaker_evidence_to_final


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    temporary.replace(path)


def _speaker_scope(segment: dict[str, Any]) -> dict[str, str]:
    return {
        "provider_code": str(segment.get("provider_code") or "Gem35T"),
        "run_id": str(segment.get("run_id") or ""),
        "chunk_filename": str(segment.get("chunk_filename") or ""),
        "provider_speaker_id": str(segment.get("speaker_id") or ""),
    }


def _local_speaker_key(scope: dict[str, str]) -> str:
    canonical = json.dumps(scope, sort_keys=True, separators=(",", ":"))
    return "local-speaker-" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]


def _text_snapshot(payload: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        {
            "window_id": window.get("window_id"),
            "final_text": window.get("final_text"),
            "selected_text": window.get("selected_text"),
            "provider_candidates": deepcopy(window.get("provider_candidates")),
        }
        for window in payload.get("windows") or []
    ]


def _provider_evidence(manifest: dict[str, Any], job_dir: Path) -> list[dict[str, Any]]:
    quality = manifest.get("quality") if isinstance(manifest.get("quality"), dict) else {}
    outputs = quality.get("outputs") if isinstance(quality.get("outputs"), dict) else {}
    providers = outputs.get("providers") if isinstance(outputs.get("providers"), dict) else {}
    evidence = []
    for provider_code in ("M3ASR", "gptTr", "Gem35T"):
        item = providers.get(provider_code)
        if not isinstance(item, dict) or not item.get("json"):
            continue
        path = Path(str(item["json"]))
        if not path.is_file():
            path = job_dir / "quality" / "provider-evidence" / provider_code / f"{provider_code}.json"
        if not path.is_file():
            raise FileNotFoundError(f"Quality provider evidence not found: {path}")
        evidence.append(
            {
                "provider_code": provider_code,
                "path": str(path.resolve()),
                "sha256": _sha256(path),
                "modified": False,
            }
        )
    return evidence


def attach_speaker_overlay(
    job_dir: Path,
    speaker_timeline_json: Path,
    *,
    speaker_timeline_html: Path | None = None,
) -> dict[str, Any]:
    """Attach an immutable timeline copy as a companion to quality FINAL windows."""

    job_dir = Path(job_dir).resolve()
    timeline_path = Path(speaker_timeline_json).resolve()
    if not timeline_path.is_file():
        raise FileNotFoundError(f"Speaker timeline not found: {timeline_path}")
    source_timeline_bytes = timeline_path.read_bytes()
    timeline = json.loads(source_timeline_bytes.decode("utf-8"))
    if timeline.get("schema_version") != "apma.speaker-timeline.v1":
        raise ValueError("Speaker overlay input must use apma.speaker-timeline.v1")

    manifest = job_mod.read_manifest(job_dir)
    quality = manifest.get("quality") if isinstance(manifest.get("quality"), dict) else {}
    outputs = quality.get("outputs") if isinstance(quality.get("outputs"), dict) else {}
    final_item = outputs.get("final_draft") if isinstance(outputs.get("final_draft"), dict) else {}
    final_path = Path(str(final_item.get("json") or ""))
    if not final_path.is_file():
        final_path = job_dir / "quality" / "final-draft" / "FINAL_DRAFT.json"
    if not final_path.is_file():
        raise FileNotFoundError(f"Quality FINAL_DRAFT not found: {final_path}")
    final_bytes = final_path.read_bytes()
    final_payload = json.loads(final_bytes.decode("utf-8"))
    if final_payload.get("schema_version") != "apma.final-draft.v1":
        raise ValueError("Speaker overlay requires apma.final-draft.v1")

    derived = attach_speaker_evidence_to_final(final_payload, timeline)
    if _text_snapshot(derived) != _text_snapshot(final_payload):
        raise ValueError("Speaker overlay must not change quality transcript wording")

    local_speakers: dict[str, dict[str, Any]] = {}
    overlay_windows = []
    for window in derived.get("windows") or []:
        evidence = deepcopy(window.get("speaker_evidence") or {})
        for segment in evidence.get("segments") or []:
            scope = _speaker_scope(segment)
            key = _local_speaker_key(scope)
            segment["local_speaker_key"] = key
            segment["speaker_scope"] = deepcopy(scope)
            if scope["provider_speaker_id"]:
                local_speakers.setdefault(
                    key,
                    {
                        "local_speaker_key": key,
                        **scope,
                        "identity_inferred": False,
                    },
                )
        overlay_windows.append(
            {
                "window_id": window["window_id"],
                "global_start_sec": window["global_start_sec"],
                "global_end_sec": window["global_end_sec"],
                "speaker_evidence": evidence,
            }
        )

    overlay_dir = job_dir / "quality" / "speaker-overlay"
    overlay_dir.mkdir(parents=True, exist_ok=True)
    copied_timeline = overlay_dir / "source_speaker_timeline.json"
    copied_timeline.write_bytes(source_timeline_bytes)
    copied_html: Path | None = None
    if speaker_timeline_html is not None:
        source_html = Path(speaker_timeline_html).resolve()
        if not source_html.is_file():
            raise FileNotFoundError(f"Speaker timeline HTML not found: {source_html}")
        copied_html = overlay_dir / "source_speaker_timeline.html"
        shutil.copyfile(source_html, copied_html)

    source_timeline_sha = hashlib.sha256(source_timeline_bytes).hexdigest()
    overlay = {
        "schema_version": "apma.speaker-overlay.v1",
        "purpose": "Provider speaker/timing evidence beside unchanged APMA quality text.",
        "source_speaker_timeline": {
            "original_path": str(timeline_path),
            "copied_path": str(copied_timeline),
            "sha256": source_timeline_sha,
            "copied_byte_for_byte": _sha256(copied_timeline) == source_timeline_sha,
            "modified": False,
        },
        "source_final_draft": {
            "path": str(final_path.resolve()),
            "sha256": hashlib.sha256(final_bytes).hexdigest(),
            "modified": False,
        },
        "quality_provider_evidence": _provider_evidence(manifest, job_dir),
        "policy": {
            "speaker_evidence_only": True,
            "quality_text_overwritten": False,
            "automatic_cross_chunk_matching": False,
            "speaker_names_invented": False,
        },
        "statistics": {
            "window_count": len(overlay_windows),
            "local_speaker_count": len(local_speakers),
            "multiple_speaker_window_count": sum(
                1
                for item in overlay_windows
                if len(item["speaker_evidence"].get("speaker_ids") or []) > 1
            ),
        },
        "local_speakers": list(local_speakers.values()),
        "windows": overlay_windows,
    }
    overlay_path = overlay_dir / "speaker_overlay.json"
    _atomic_json(overlay_path, overlay)

    quality.setdefault("outputs", {})["speaker_overlay"] = {
        "json": str(overlay_path),
        "source_timeline_json": str(copied_timeline),
        "source_timeline_html": str(copied_html) if copied_html else None,
        "speaker_mappings_json": str(job_dir / "quality" / "review" / "speaker_mappings.json"),
        "source_timeline_sha256": source_timeline_sha,
        "quality_text_overwritten": False,
    }
    manifest["quality"] = quality
    job_mod.write_manifest(job_dir, manifest)
    return overlay


class SpeakerMappingStore:
    """Single-user human mappings for explicitly scoped provider speaker IDs."""

    def __init__(self, overlay_path: Path, mapping_path: Path):
        self.overlay_path = Path(overlay_path).resolve()
        self.mapping_path = Path(mapping_path).resolve()
        if not self.overlay_path.is_file():
            raise FileNotFoundError(f"Speaker overlay not found: {self.overlay_path}")
        self.overlay_bytes = self.overlay_path.read_bytes()
        self.overlay_sha256 = hashlib.sha256(self.overlay_bytes).hexdigest()
        self.overlay = json.loads(self.overlay_bytes.decode("utf-8"))
        if self.overlay.get("schema_version") != "apma.speaker-overlay.v1":
            raise ValueError("Unsupported speaker overlay schema")
        self.local_speakers = {
            str(item["local_speaker_key"]): deepcopy(item)
            for item in self.overlay.get("local_speakers") or []
        }

    def _load(self) -> dict[str, dict[str, Any]]:
        if not self.mapping_path.is_file():
            return {}
        payload = json.loads(self.mapping_path.read_text(encoding="utf-8"))
        if payload.get("schema_version") != "apma.speaker-mappings.v1":
            raise ValueError("Unsupported speaker mapping schema")
        if payload.get("source_speaker_overlay_sha256") != self.overlay_sha256:
            raise ValueError("Saved speaker mappings do not match this speaker overlay")
        mappings: dict[str, dict[str, Any]] = {}
        for item in payload.get("mappings") or []:
            key = str(item.get("local_speaker_key") or "")
            if key not in self.local_speakers or key in mappings:
                raise ValueError("Saved speaker mapping has an invalid or duplicate scope")
            mappings[key] = item
        return mappings

    def _next_canonical_id(self, mappings: dict[str, dict[str, Any]]) -> str:
        existing = {str(item.get("canonical_speaker_id") or "") for item in mappings.values()}
        index = 1
        while f"canonical-speaker-{index:03d}" in existing:
            index += 1
        return f"canonical-speaker-{index:03d}"

    def save_mapping(
        self,
        local_speaker_key: str,
        display_name: str,
        *,
        canonical_speaker_id: str | None = None,
        mapped_at: str | None = None,
    ) -> dict[str, Any]:
        if local_speaker_key not in self.local_speakers:
            raise ValueError("Unknown local provider speaker scope")
        cleaned_name = str(display_name or "").strip()
        if not cleaned_name:
            raise ValueError("display_name must contain a human-entered label")
        mappings = self._load()
        previous = mappings.get(local_speaker_key)
        canonical = str(canonical_speaker_id or "").strip()
        if not canonical and previous:
            canonical = str(previous["canonical_speaker_id"])
        if not canonical:
            canonical = self._next_canonical_id(mappings)
        if len(canonical) > 128 or not all(
            character.isalnum() or character in "._-" for character in canonical
        ):
            raise ValueError("canonical_speaker_id must use letters, numbers, '.', '_', or '-'")

        scope = self.local_speakers[local_speaker_key]
        mapping = {
            "local_speaker_key": local_speaker_key,
            "provider_code": scope["provider_code"],
            "run_id": scope["run_id"],
            "chunk_filename": scope["chunk_filename"],
            "provider_speaker_id": scope["provider_speaker_id"],
            "canonical_speaker_id": canonical,
            "display_name": cleaned_name,
            "mapping_source": "human",
            "mapped_at": mapped_at or _utc_now(),
        }
        mappings[local_speaker_key] = mapping
        ordered = [mappings[key] for key in self.local_speakers if key in mappings]
        _atomic_json(
            self.mapping_path,
            {
                "schema_version": "apma.speaker-mappings.v1",
                "source_speaker_overlay_sha256": self.overlay_sha256,
                "automatic_identity_inference": False,
                "mappings": ordered,
            },
        )
        return deepcopy(mapping)

    def view(self) -> dict[str, Any]:
        mappings = self._load()
        speakers = []
        for key, speaker in self.local_speakers.items():
            item = deepcopy(speaker)
            mapping = mappings.get(key)
            item["mapping"] = deepcopy(mapping)
            item["display_label"] = (
                f"{mapping['display_name']} [{speaker['provider_speaker_id']}]"
                if mapping
                else speaker["provider_speaker_id"]
            )
            speakers.append(item)
        return {
            "schema_version": "apma.speaker-mappings-view.v1",
            "local_speakers": speakers,
            "mapped_count": len(mappings),
            "mapping_path": str(self.mapping_path),
        }

    def apply_to_review_view(self, review_view: dict[str, Any]) -> dict[str, Any]:
        result = deepcopy(review_view)
        before = _text_snapshot(result)
        mapping_view = self.view()
        mappings = {
            item["local_speaker_key"]: item
            for item in mapping_view["local_speakers"]
        }
        overlay_windows = {
            str(item["window_id"]): item for item in self.overlay.get("windows") or []
        }
        for window in result.get("windows") or []:
            overlay_window = overlay_windows.get(str(window.get("window_id")))
            if not overlay_window:
                continue
            evidence = deepcopy(overlay_window["speaker_evidence"])
            for segment in evidence.get("segments") or []:
                speaker = mappings.get(str(segment.get("local_speaker_key") or ""))
                segment["display_label"] = (
                    speaker["display_label"]
                    if speaker
                    else str(segment.get("speaker_id") or "UNLABELLED")
                )
                segment["human_mapping"] = deepcopy(speaker.get("mapping")) if speaker else None
            window["speaker_evidence"] = evidence
        if _text_snapshot(result) != before:
            raise ValueError("Speaker mapping must not change quality transcript wording")
        result["speaker_mappings"] = mapping_view
        result["speaker_overlay"] = {
            "path": str(self.overlay_path),
            "sha256": self.overlay_sha256,
            "evidence_only": True,
            "quality_text_overwritten": False,
        }
        return result


__all__ = ["SpeakerMappingStore", "attach_speaker_overlay"]
