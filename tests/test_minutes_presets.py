from services.minutes.presets import get_minutes_style_preset, list_minutes_style_presets, normalize_minutes_style


def test_minutes_style_presets_include_required_styles():
    styles = list_minutes_style_presets()
    keys = {style["key"] for style in styles}

    assert keys == {"standard", "deep_evidence", "action_focused"}
    assert all(style["label"] for style in styles)
    assert all(style["description"] for style in styles)


def test_minutes_style_prompts_preserve_evidence_and_language_rules():
    for key in ("standard", "deep_evidence", "action_focused"):
        preset = get_minutes_style_preset(key)

        assert "Do not invent facts" in preset.prompt
        assert "Translate important non-English points into clear English" in preset.prompt
        assert "AI-suggested" in preset.prompt or "Suggested Next Steps" in preset.prompt


def test_unknown_minutes_style_falls_back_to_standard():
    assert normalize_minutes_style("not-a-style") == "standard"
    assert get_minutes_style_preset("not-a-style").key == "standard"
    assert normalize_minutes_style("deep-evidence") == "deep_evidence"
