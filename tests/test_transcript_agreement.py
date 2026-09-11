from copy import deepcopy
import json

from services.transcript_agreement import (
    AGREEMENT_DISCLAIMER,
    AgreementThresholds,
    classify_comparison,
    pairwise_similarity,
)
from services.transcript_alignment import comparison_html


PROVIDERS = ("M3ASR", "gptTr", "Gem35T")


def _candidate(provider: str, text: str | None, *, missing: bool = False) -> dict:
    if missing:
        return {"missing": True, "text": None, "provenance": None}
    return {
        "missing": False,
        "candidate_id": f"{provider}:fixture",
        "provider": provider,
        "global_start_sec": 10.0,
        "global_end_sec": 20.0,
        "text": text,
        "provenance": {"chunk_filename": "chunk.wav", "model": "fixture"},
    }


def _region(region_id: str, texts: dict[str, str | None]) -> dict:
    return {
        "region_id": region_id,
        "global_start_sec": 10.0,
        "global_end_sec": 20.0,
        "candidates": {
            provider: _candidate(
                provider,
                texts.get(provider),
                missing=provider not in texts,
            )
            for provider in PROVIDERS
        },
    }


def _comparison(regions: list[dict]) -> dict:
    return {
        "schema_version": "apma.transcript-comparison.v1",
        "provider_order": list(PROVIDERS),
        "statistics": {"source_segments": {provider: len(regions) for provider in PROVIDERS}},
        "regions": regions,
    }


def test_every_region_is_deterministic_and_raw_text_is_unchanged():
    source = _comparison(
        [
            _region(
                "green",
                {
                    "M3ASR": "Hello，世界!",
                    "gptTr": "hello 世界",
                    "Gem35T": "HELLO世界。",
                },
            ),
            _region(
                "amber",
                {
                    "M3ASR": "Saya pergi ke pasar hari ini",
                    "gptTr": "saya pergi pasar hari ini",
                    "Gem35T": "Saya pergi ke pasar besok",
                },
            ),
            _region(
                "red",
                {
                    "M3ASR": "有一个候选文本",
                    "gptTr": "有一个候选文本",
                },
            ),
        ]
    )
    raw_candidates = deepcopy([region["candidates"] for region in source["regions"]])

    first = classify_comparison(source)
    second = classify_comparison(first)

    assert first == second
    assert [region["agreement_status"] for region in first["regions"]] == [
        "GREEN",
        "AMBER",
        "RED",
    ]
    assert first["agreement_analysis"]["status_counts"] == {
        "GREEN": 1,
        "AMBER": 1,
        "RED": 1,
    }
    assert [region["candidates"] for region in first["regions"]] == raw_candidates
    assert first["agreement_analysis"]["label"] == AGREEMENT_DISCLAIMER
    assert "duration totals" in first["agreement_analysis"]["counting_basis"]
    assert "winner" not in first


def test_missing_empty_and_substantial_omission_are_red():
    source = _comparison(
        [
            _region("missing", {"M3ASR": "same", "gptTr": "same"}),
            _region(
                "empty",
                {"M3ASR": "", "gptTr": "text", "Gem35T": "text"},
            ),
            _region(
                "omission",
                {
                    "M3ASR": "a very long retained provider candidate passage",
                    "gptTr": "a very long retained provider candidate passage",
                    "Gem35T": "short",
                },
            ),
        ]
    )

    classified = classify_comparison(source)

    assert all(region["agreement_status"] == "RED" for region in classified["regions"])
    assert "Missing provider candidate" in classified["regions"][0]["agreement_reasons"][0]
    assert "Empty provider candidate" in classified["regions"][1]["agreement_reasons"][0]
    assert "text-length difference" in classified["regions"][2]["agreement_reasons"][0]
    missing_pairs = classified["regions"][0]["pairwise_similarities"]
    assert sum(pair["missing"] for pair in missing_pairs) == 2


def test_thresholds_are_explicit_and_configurable():
    source = _comparison(
        [
            _region(
                "configurable",
                {
                    "M3ASR": "alpha beta gamma",
                    "gptTr": "alpha beta delta",
                    "Gem35T": "alpha beta theta",
                },
            )
        ]
    )
    relaxed = AgreementThresholds(
        green_min_pairwise=0.40,
        green_mean_pairwise=0.40,
        red_min_pairwise=0.10,
        red_mean_pairwise=0.10,
        red_length_ratio=0.10,
    )

    classified = classify_comparison(source, relaxed)

    assert classified["regions"][0]["agreement_status"] == "GREEN"
    assert classified["agreement_analysis"]["thresholds"]["green_min_pairwise"] == 0.40


def test_mixed_language_similarity_is_normalized_for_calculation_only():
    similarity = pairwise_similarity(
        "Kita makan 福建面 at 8pm!",
        "kita makan 福建面 at 8PM",
    )

    assert similarity["character_similarity"] == 1.0
    assert similarity["token_similarity"] == 1.0
    assert similarity["derived_similarity"] == 1.0


def test_classified_html_shows_derived_status_and_all_raw_text():
    source = _comparison(
        [
            _region(
                "html",
                {
                    "M3ASR": "M3 raw <text>",
                    "gptTr": "gpt raw text",
                    "Gem35T": "Gem raw text",
                },
            )
        ]
    )
    classified = classify_comparison(source)

    html = comparison_html(classified)

    assert "<th>Derived agreement</th>" in html
    assert classified["regions"][0]["agreement_status"] in html
    assert "M3 raw &lt;text&gt;" in html
    assert "gpt raw text" in html
    assert "Gem raw text" in html
    assert "DERIVED ASR agreement/disagreement only" in html
    assert "not provider confidence" in html
    json.loads(json.dumps(classified, ensure_ascii=False))
