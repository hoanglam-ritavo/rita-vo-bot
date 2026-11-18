# Vietnamese task semantics

The schema-v2 runtime lexicon contains guarded concepts, canonical phrases,
aliases and optional style labels. It expands bounded phrase alternatives and
supports deterministic task selection; it never grants document/personnel access.
There is no learned intent assistance or corpus-bound relevance profile.

The current source inventory contains 29 packages / 530 chunks. The lexicon has
252 concepts: 232 inherited concepts plus 20 source-checked procedure concepts
for Bravo, Workit and Office 365. Prior reviewed language content is retained.
The new concepts were checked against the complete source chunks by the
implementation agent; no human approval or live quality acceptance is inferred.

Author concepts in `artifacts/semantics/source/concepts/` and declare them in the
source index. Keep contexts, beneficiaries, negation, numbers and exclusions.
Aliases do not certify policy equivalence, completeness or applicability.
The runtime lexicon has no chunk routing IDs; task catalog references own evidence.

## Separate authoring, review and release

Use `python -m src.cli.prepare_semantics inventory|validate|export` for private
source-authoring inventories. The inventory envelope remains schema 1; export
produces runtime schema 2. Export checks declared source review but cannot certify
business accuracy. Import a compatible candidate pair into a new `.tmp` tree
before deliberate source reconciliation. Build runtime JSON from declared sources,
never by hand. See [artifact operations](runtime-artifacts.md#split-source-authoring).

Package fingerprints record provenance; unrelated package changes do not disable
lexical expansion. Changed task evidence is checked against the DB independently.
Keep source precedence/conflicts explicit: newer filenames and conversation
clarifications do not automatically override issued policy. No memory, automatic
aggregation or personal entitlement decisions are introduced.

Follow [catalog source updates](catalog-answering.md#updating-chunks) and test
paraphrases, abbreviations, negation, different subjects, ambiguous overlaps,
out-of-scope questions and content/revision drift. Keep private cases under `.tmp`.
