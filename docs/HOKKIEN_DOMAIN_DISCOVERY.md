# Hokkien Domain Discovery: From Research To Product Architecture

Status: public-safe research and decision record, checked 10 October 2026.

## Why research preceded programming

APMA began with a domain problem rather than a model choice. A long Singapore
recording can contain Hokkien, Singlish, Mandarin, English, Malay-derived words,
speaker overlap, family names, numbers, and locally understood expressions in
one exchange. A provider checkbox labelled `Hokkien` does not establish that
the model understands that recording, that another regional variety is an
adequate substitute, or that a polished transcript is correct.

Before implementation, the project owner researched the language's history,
cultural use, regional relationships, model coverage, and transcription gaps.
The useful outcome was not a claim of linguistic authority. It was a set of
testable product requirements, explicit unknowns, and controls for working
responsibly where model capability remains uneven.

Research notes and permitted reference material were used internally. This
public record publishes the source-backed synthesis and design consequences,
not copies of third-party theses, private recordings, or confidential reports.

## Research method

1. **Clarify the target.** Break the broad label `Hokkien` into the Singapore
   usage being served, relevant Southern Min relationships, code-switching,
   speaker conditions, and output conventions.
2. **Trace historical and cultural context.** Study how migration and contact
   with other Singapore languages shaped local usage.
3. **Map regional deltas.** Record similarities and differences among
   Singapore usage, Xiamen, Quanzhou, Zhangzhou, and Taiwanese Minnan instead
   of treating one variety as an automatic ground truth for another.
4. **Separate evidence states.** Keep published linguistic sources, provider
   capability claims, bounded APMA tests, human decisions, and unresolved
   hypotheses distinct.
5. **Turn gaps into requirements.** Convert each material uncertainty into a
   routing, provenance, comparison, review, evaluation, privacy, or cost
   requirement before coding the corresponding workflow.
6. **Recheck provider progress.** Model names, versions, supported-language
   labels, pricing, and availability can change. A new model listing triggers
   evaluation; it does not silently replace a tested route.

## What the public sources establish

The Singapore Chinese Cultural Centre's Culturepaedia explains that `Hokkien`
is the customary Singapore label for Minnan, while Fujian contains multiple
language varieties. It traces local Minnan mainly to migration from Zhangzhou,
Quanzhou, and Xiamen, describes Singapore pronunciation as a flexible synthesis
of those influences, and records vocabulary contact with Malay, English, and
Cantonese. This supports treating Singapore Hokkien as a specific usage context
rather than a generic Mandarin setting or one uniform acoustic target.

The provider sources show promising but differently scoped capability:

- QwenCloud's speech-to-text documentation lists offline Filetrans routes,
  prompt context, hot words, and speaker diarization. Its public 3.0 entry
  explicitly lists Hokkien; the current recommendation has advanced to the 3.1
  family and uses a different dialect taxonomy. APMA therefore retains the
  tested 3.0 route until a bounded 3.1 evaluation is designed and completed.
- The MERaLiON-3 ASR model card describes a Singapore- and Southeast-Asia-
  focused system covering Hokkien, Singlish, and natural English code-switching.
  It also publishes provider-controlled benchmarks and examples. Those are
  useful capability evidence, but they are not proof of accuracy on an APMA
  user's recording or across every Singapore Hokkien condition.

## Research-to-product decision trace

| Research or model uncertainty | Product consequence in APMA | Evidence boundary |
| --- | --- | --- |
| `Hokkien` is a broad customary label; Singapore usage has a particular history and contact environment | Treat Singapore Hokkien and code-switching as explicit product and evaluation requirements | Cultural and linguistic sources guide requirements; they do not measure ASR accuracy |
| Singapore usage reflects Xiamen, Quanzhou, and Zhangzhou influences with local variation | Do not make Taiwanese Minnan, one Fujian variety, or Mandarin an automatic transcription ground truth | Regional similarity can generate hypotheses, not silent normalisation |
| Malay, English, Mandarin, Cantonese, and Singlish may appear naturally within an exchange | Preserve mixed-language output and evaluate code-switch boundaries, names, numbers, and meaning units | Language labels alone do not establish semantic correctness |
| A Chinese-character rendering may hide pronunciation, regional wording, or uncertainty | Retain provider wording, allow `unclear`, and record exact human correction without overwriting source evidence | Script conversion is not dialect translation or semantic verification |
| Providers document different language, timing, diarization, and file capabilities | Route providers behind a neutral contract and retain model/version provenance | A provider statement is capability evidence, not a permanent ranking |
| Multiple model outputs may share training sources or make the same mistake | Expose agreement and disagreement, but do not treat agreement as independent corroboration | Green means exact normalised agreement under APMA rules, not guaranteed truth |
| Provider speaker labels are local machine outputs | Keep speaker labels provider/chunk scoped until a human makes a reviewed mapping | Diarization is not identity verification |
| Representative Singapore Hokkien evaluation data remain limited | Require a licensed or consented evaluation pack, reviewer method, denominators, and drift history before broad accuracy claims | Synthetic fixtures verify plumbing; they do not prove dialect quality |

## Evidence ladder

APMA uses an evidence ladder so a persuasive result cannot outrun its support:

1. **Published domain source** — supports language, cultural, or historical
   context.
2. **Provider capability claim** — supports that a route is worth testing.
3. **Contract or dry-run test** — supports integration behaviour without
   proving a live provider result.
4. **Bounded live check on non-private audio** — supports connectivity and
   returned artifact behaviour within the recorded conditions.
5. **Representative consented evaluation** — can support a scoped quality
   statement when the dataset, language mix, annotation method, denominators,
   and reviewer agreement are disclosed.
6. **Human-reviewed decision** — is authoritative only for the clip and
   decision actually reviewed; it is not automatically a full-audio guarantee.

## Delta register

| Delta or unknown | Current treatment | What would close the gap |
| --- | --- | --- |
| Singapore Hokkien versus generic provider `Hokkien` or `Fujianese` labels | Record the provider's exact claim and model version; do not infer equivalence | Representative Singapore recordings with lawful provenance and expert-reviewed references |
| Xiamen/Quanzhou/Zhangzhou and Taiwanese Minnan transfer | Use as research context and error-analysis dimensions | Region-labelled comparison with a documented annotation protocol |
| Code-switch boundaries and borrowed vocabulary | Preserve mixed-language tokens and flag omissions or forced normalisation | Meaning-unit and critical-term evaluation across realistic conversations |
| Character choice, romanisation, and Simplified/Traditional output | Preserve raw provider text; treat script conversion separately from translation | Published output policy plus competent linguistic review |
| Meeting-global speaker continuity | Preserve provider-local labels and human speaker decisions separately | Reviewed cross-chunk mapping and speaker-attribution evaluation |
| Provider/version drift | Keep requested and resolved model identity, dated checks, and no silent route upgrade | Repeatable regression pack and explicit promotion gate |

## How LLMs supported the research

Permitted papers, reports, reference summaries, and working notes were supplied
as prompt or retrieval context to help compare terminology, expose gaps, form
hypotheses, and draft evaluation questions. The outputs were treated as
analytical assistance, not independent evidence.

This is **context-grounded model-assisted analysis**, not APMA fine-tuning:

- no claim is made that APMA changed a provider model's weights;
- no prompt session is described as persistent model learning;
- source attribution and human judgment remain necessary;
- model-generated explanations do not become facts because they are fluent;
- third-party research is cited or summarised, not republished without rights.

Provider organisations may describe their own models as fine-tuned. That is a
provider fact and must not be presented as work performed by APMA or its owner.

## What this demonstrates—and what it does not

The inspectable contribution is the ability to:

- investigate an ambiguous domain before selecting technology;
- convert cultural, linguistic, model, operational, and governance uncertainty
  into explicit requirements;
- keep evidence states and claim boundaries visible;
- revise routing and evaluation choices as provider capabilities change;
- design human review where consequences justify it; and
- carry a research insight through architecture, implementation, testing, and
  the next consented pilot.

This document does not establish that the owner is a professional linguist,
that APMA created or fine-tuned a Hokkien foundation model, that Taiwanese
Minnan is interchangeable with Singapore Hokkien, or that APMA has achieved a
representative dialect-accuracy threshold.

## Public references

- [Singapore Chinese Cultural Centre, “The Hokkien dialect in Singapore”](https://culturepaedia.singaporeccc.org.sg/language-education/the-hokkien-dialect-in-singapore/)
- [QwenCloud speech-to-text model documentation](https://docs.qwencloud.com/developer-guides/speech/speech-to-text-models)
- [A*STAR I2R MERaLiON-3-3B-ASR model card](https://huggingface.co/MERaLiON/MERaLiON-3-3B-ASR)
- [APMA transcription architecture and current gaps](TRANSCRIPTION_ARCHITECTURE_4OFX.md)
- [APMA targeted human-verification boundary](TARGETED_HUMAN_VERIFICATION.md)
- [APMA consented pilot runbook](CONSENTED_PILOT_RUNBOOK.md)
