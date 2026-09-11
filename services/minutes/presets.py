from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class MinutesStylePreset:
    key: str
    label: str
    description: str
    prompt: str


STANDARD_PROMPT = """Convert this transcript into detailed English meeting minutes.

Read all languages used in the transcript, including mixed-language speech. Translate important non-English points into clear English, while preserving important original names, terms, phrases, and technical words where useful.

Rules:
- Do not invent facts.
- Do not over-compress.
- If something is unclear, write "unclear / needs verification."
- Separate confirmed facts, opinions, proposals, assumptions, unresolved issues, and AI-suggested next steps.
- Explain the what, why, impact, owner, timing, and dependency when the transcript supports it.
- Do not present AI-suggested next steps as confirmed meeting decisions.
- Keep enough detail that someone who missed the meeting can understand what happened and what must happen next.

Output:
1. Meeting Overview
2. Executive Summary
3. Detailed Discussion By Topic
4. Decisions Made
5. Confirmed Action Items
6. Questions And Answers
7. Commitments Or Verbal Assurances
8. Important Numbers, Dates, Names, And Facts
9. Risks, Concerns, And Open Issues
10. Documents Or Evidence Mentioned
11. Follow-Up Plan
12. AI-Suggested Next Steps

Clearly label AI-suggested next steps as suggestions, not confirmed meeting decisions."""


DEEP_EVIDENCE_PROMPT = """Convert this transcript into detailed, evidence-preserving English meeting minutes.

Purpose:
These minutes are for internal record, follow-up, accountability, and future reference. Preserve important details, decisions, concerns, reasoning, commitments, numbers, dates, names, risks, objections, and action items.

Language:
Read all languages used in the transcript, including mixed-language speech. Translate important non-English points into clear English. Preserve important original names, terms, phrases, and source wording where useful.

Rules:
- Do not invent facts.
- Do not over-compress.
- If unclear, write "unclear / needs verification" and explain what needs checking.
- Separate confirmed points from assumptions, opinions, proposals, unresolved issues, and AI-suggested next steps.
- Preserve the reasoning behind decisions, why each issue matters, likely impact, dependencies, and any caveats.
- Capture minor but specific facts when they may matter later.
- Include short exact phrases only when useful, but do not quote excessively.
- If speaker names are unclear, identify by role where possible.

Output:
A. Meeting Overview
B. Executive Summary
C. Key Outcomes
D. Detailed Discussion Notes By Topic
E. Decisions Made
F. Action Items
G. Questions And Answers
H. Commitments, Promises, And Verbal Assurances
I. Numbers, Dates, And Specific Facts
J. Risks, Concerns, And Issues Raised
K. Open Issues And Unresolved Questions
L. Documents, Evidence, And Data Mentioned
M. Follow-Up Plan
N. Clean Final Meeting Minutes

Final check:
Before finishing, review whether any important names, numbers, dates, commitments, risks, action items, documents, or unresolved questions were missed."""


ACTION_FOCUSED_PROMPT = """Convert this transcript into an action-focused English meeting summary.

Read all languages used in the transcript, including mixed-language speech. Translate important non-English points into clear English, while preserving important original names, terms, and technical phrases.

Rules:
- Do not invent facts.
- Mark unclear items as "unclear / needs verification."
- Separate confirmed action items from AI-suggested next steps.
- For every action, capture the why, expected output, owner, timing, dependency, blocker, and impact where available.
- Be practical, concise, and focused on what should happen next.

Output:
1. Short Meeting Summary
2. Main Decisions Or Agreements
3. Confirmed Action Items
   - Action
   - Owner
   - Deadline
   - Priority
   - Expected Output
   - Status
4. Risks Or Blockers
5. Missing Information / Needs Verification
6. Suggested Next Steps
7. Suggested Follow-Up Message Or Agenda

Clearly state which next steps were confirmed in the meeting and which are AI suggestions."""


_PRESETS: dict[str, MinutesStylePreset] = {
    "standard": MinutesStylePreset(
        key="standard",
        label="Standard Minutes",
        description="Balanced meeting minutes with decisions, actions, risks, facts, and suggested next steps.",
        prompt=STANDARD_PROMPT,
    ),
    "deep_evidence": MinutesStylePreset(
        key="deep_evidence",
        label="Deep Evidence Minutes",
        description="Most detailed record for accountability, evidence, unresolved issues, and later review.",
        prompt=DEEP_EVIDENCE_PROMPT,
    ),
    "action_focused": MinutesStylePreset(
        key="action_focused",
        label="Action-Focused Summary",
        description="Shorter output centered on decisions, owners, deadlines, blockers, and follow-up.",
        prompt=ACTION_FOCUSED_PROMPT,
    ),
}


def normalize_minutes_style(style: str | None) -> str:
    key = (style or "standard").strip().lower().replace("-", "_")
    if key not in _PRESETS:
        return "standard"
    return key


def get_minutes_style_preset(style: str | None = None) -> MinutesStylePreset:
    return _PRESETS[normalize_minutes_style(style)]


def list_minutes_style_presets() -> list[dict[str, str]]:
    return [
        {
            "key": preset.key,
            "label": preset.label,
            "description": preset.description,
        }
        for preset in _PRESETS.values()
    ]
