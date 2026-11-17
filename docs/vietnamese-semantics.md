# Corpus-wide Vietnamese semantics

This workflow adds reviewed phrase expansion to source retrieval, not generated
answers or a guarantee of evidence completeness. Agent authoring runs offline;
the API never calls an agent or hosted model. The initial corpus has 28 packages
and 524 chunks. Coverage means accounting for every source-backed unit, not
claiming recognition of every possible Vietnamese sentence.

### Explicit source reconciliation - 2026-11-16

The user-authorized reconciliation adds a separate Markdown conversation-decision
source under `assets/semantic-clarifications/`, with its own current package and
`supplements` relations to the four affected PDF revisions. It records scoped
interpretations, not an officially issued HR policy or new legal-validity claim.
Original PDF snapshots and transcription remain unchanged. Labor Article 43.1 is
restored from its real extraction span; the other PDF-backed chunks retain their
original wording. Their notes distinguish the separate clarification from literal
PDF evidence. Runtime retrieval does not implement automatic source precedence
or automatically attach related clarification chunks.

The resulting authoring snapshot contains **29 packages / 530 chunks**. The
private semantic proposal has 232 concepts and 636 evidence units, including the
restored inpatient unit and five attributed clarification units. Previous policy
replies remain recorded. The user subsequently approved all three source/alias
groups explicitly; private receipts bind that approval to the reviewed content
and prove a formatting-only line-ending carryover. A reviewed lexicon was exported
privately and passed the package-bound loader. Publication to a compatible target
and independent semantic evaluation remain required. The refreshed exposed
regression proposal is not a reviewed calibration/holdout dataset. No matching
accepted relevance profile or runtime release is supplied by this source step.
Age/time boundaries and document choices are evidence content, not global lexical
substitutions. Source approval never fills evaluation candidate labels.

### Reviewed lexical enrichment v2 - 2026-11-16

The user explicitly approved all three lexical review lots: 15 policy/HR,
16 administration and 25 software aliases. The resulting v2 at
`artifacts/semantics/lexicon.json` (previously stored under `semantics/v2`) has 232 concepts and 301 alias entries,
including 227 effective non-canonical rewrite rules. Fifty-three concepts gained
aliases; 66 concepts remain canonical-only. All v1 canonical phrases, old aliases,
styles and context guards are retained. No new policy rule, source package,
runtime algorithm, API, environment configuration or database change is included.

The private review packet records each proposal, complete client examples,
contrasts, source references and dispositions for all 232 concepts. Eleven
ambiguous or grammatically unsafe candidates were rejected. The user approval is
bound to exact proposal/inventory hashes. Source-unit review is inherited; no new
independent PDF-transcription review is claimed. The inventory contains both v1
and v2 lexicon/manifest pairs, preserving v1 for comparison and rollback planning.

Behavior checks preserve the exercised v1 rewrites within the two-alternate
budget, retain negation and numerical/role boundaries, and inspect overlapping
aliases. The v2 manifest still declares source-reviewed/provisioned with retrieval
evaluation pending and runtime release acceptance false. No compatible relevance
profile is emitted or inferred; deployment configuration remains unchanged.
These authored examples are exposed regression inputs, not independent holdout
families or candidate relevance labels. Private review and promotion receipts
remain outside tracked artifacts.

## Separate authoring, review and release

The inventory accounts for all packages/chunks, package JSON and source snapshot
hashes. An author reads chunk content and source references, separates business
units and records conditions. A second agent checks omissions, ambiguity,
duplicates and near-miss meanings. A responsible person resolves disagreements
and reviews evidence labels before acceptance. Automated validation checks
structure and declared coverage; it cannot establish transcription accuracy,
semantic equivalence, policy validity or independent human review.

Do not change chunks, extraction, source snapshots or publication as incidental
semantic cleanup. Record missing/ambiguous source evidence separately. Keep real
drafts, questions, source worksheets and reports under ignored `.tmp/`; fictional
test fixtures belong in source control. Do not infer review from agent-authored
labels, model scores or a passing shape validator.

Keep three artifacts separate:

- The coverage inventory links semantic units and conditions to source chunks.
- The runtime lexicon contains concepts, canonical phrases, aliases and context
  guards. It contains no document/chunk routing or ranking IDs.
- The independent evaluation dataset contains questions, family splits,
  source-backed required evidence groups and exhaustive candidate labels.

Read all corpus topics, including labor rules, attendance, benefits, travel,
administration, security, facilities, assets, Workit and Office/ERP instructions.
Administrative text, duplicates, ambiguous evidence and unsupported units need
explicit dispositions. Do not label every chunk as a distinct concept or use
titles alone as coverage evidence.

Review regional wording, ordinary spoken questions, supported abbreviations,
short/long questions, contextual ambiguity, controlled typos/unaccented aliases,
negation and combined requests. Preserve quantities, time, employment category,
family relationship and approval conditions. A personal forgetting event and a
broken attendance machine, or overtime and arriving late, are different concepts.
Money, leave entitlement and procedures are different evidence needs even when
they concern one event.

## Runtime contract

`SEMANTIC_MODE=off` is the default and preserves the previous pipeline and
fingerprint. `expand` requires the pinned reranker and an explicitly configured
`SEMANTIC_ARTIFACT_PATH`. Relative paths resolve against the repository root.
Missing, malformed, unreviewed or package-mismatched configured artifacts fail
startup with a safe error. No downloads, database writes or publication occur.

The schema-v1 lexicon has `reviewed`, `review_notes`, `package_fingerprint` and
`concepts`. Each concept has `id`, `canonical`, `aliases`, `requires_any`,
`excludes_any`, and optional existing intent `styles`. Concept IDs identify
language concepts only; they are not retrieval targets. `reviewed` is a review
declaration, not a cryptographic certification or permission to bypass quality
gates. Runtime release independently needs a valid relevance profile.

Expansion replaces bounded, unambiguous aliases while retaining surrounding
question text. At most two semantic alternates supplement the original and
existing typo variant: at most four distinct queries. No alias match or ambiguous
meaning keeps the original path. An over-token alternate is omitted, never
truncated; original-question limits retain their previous error behavior.
Controlled aliases must be authored explicitly; there is no automatic global
accent stripping or unconstrained rewriting.

All queries use the existing access/eligibility filters. RRF60 combines candidates
into the existing 30-candidate budget. Cosine distances remain relative to the
original query. Reranking uses the original query with existing normalization and
typo corrections, never the last semantic alternate. No semantic document
boosting or fuzzy content deduplication is introduced.

The effective reranker fingerprint binds the lexicon hash, expansion version,
query policy, budget and optional classifier version. The independent profile
also binds the eligible publication corpus. A baseline profile cannot release
semantic candidates. Changing these artifacts requires recalibration; reverting
only a mode flag without its compatible profile is not a valid rollback.

`POST /api/v1/retrieve` and its response remain unchanged. Concepts, rewritten
questions and intent predictions are not public response fields. Existing
`RELEVANCE_FILTER_ENABLED=false` behavior remains an explicit unassessed mode,
not semantic acceptance. See [filter semantics](online-reranking.md).

## Intent assistance

`INTENT_MODE=off|observe|assist` retains the existing source/style taxonomy.
`observe` must retain exact non-timing result parity. `assist` requires semantic
expansion and uses accepted style predictions solely to prioritize already-valid
semantic alternatives. It never filters documents by source label, calls Type B
tools or infers missing conditions. Unavailable/uncertain predictions fall back
to unassisted alternatives within the same assistance pipeline identity.

Classification reuses the original E5 vector and adds no transformer or second
embedding. Only the existing accepted artifact contract can supply hints; invalid
classifier artifacts remain unavailable. Assistance fingerprinting includes its
mode and classifier digest, including the unavailable state, to prevent reuse
of a semantic-only profile. Classifier quality/acceptance requirements remain
owned by [intent classification](intent-classifier.md).

## Independent evaluation and promotion

Use the application command to create an inventory, author it offline, validate
it, then export only after review:

```powershell
python -m src.cli.prepare_semantics inventory --output .tmp/semantics/inventory-01.json --json
python -m src.cli.prepare_semantics validate --manifest .tmp/semantics/authored.json --json
python -m src.cli.prepare_semantics export --manifest .tmp/semantics/reviewed.json --output .tmp/semantics/lexicon-01.json --json
```

The paired evaluation interface supports cross-document evidence rather than
restricting every question to one package:

```powershell
python -m scripts.benchmark_semantic_retrieval --dataset .tmp/semantics/evaluation.json --model-path <local-E5> --model-revision <40-hex> --semantic-artifact .tmp/semantics/lexicon-01.json --output .tmp/semantics/collection-01.json --rerank --collect-only
```

After source review and exhaustive union labeling, use a fresh output and omit
`--collect-only` to calibrate/compare. Supply `--intent-artifact <accepted-JSON>`
only for the third arm. `--rerank-profile` is rejected because the tool derives
each arm's threshold independently; shared model/batch flags remain available.
Collection never writes a profile. A completed collection is not a successful
quality run, and normal runs do not mark a lexicon/classifier accepted.

Evaluation schema v1 declares `reviewed`, `review_notes`, all package pins
(`path`, `sha256`), `required_units` and `cases`. A case declares its `id`,
`question`, `domain`, `intent_group`, split, relevant/critical/top1 flags,
`unit_ids`, wording category, `required_groups` and `candidate_labels`.
Required groups are ANDed; references inside each group are alternatives,
identified by document/revision UUID and chunk index. Candidate labels map the
exact union of returned UUIDs to booleans, never inferred from scores. Direct and
paraphrased authoring questions are proposals, not independent holdout evidence.

Compare baseline, semantics, and optionally semantics-plus-assistance using the
offline benchmark. Keep at least 300 reviewed retrieval cases, increasing the
dataset to cover all clear business units. Every such unit needs a direct and a
paraphrased case somewhere in the evaluation corpus. Guard distinctions need
near-miss cases; ambiguous and unsupported units need explicit dispositions.
Inventory coverage and behavioral coverage are separate review tasks.

Keep entire semantic/template families in one split, including cross-document
variants. Previously inspected questions and their families belong in regression.
Lexicon authors may inspect the source corpus but must not tune against holdout
wording or scores. Reviewer labels must be fixed independently of rankings.
Changing a pipeline changes candidates: label the union of all returned top-30
UUIDs, without assuming candidates absent from baseline are irrelevant.

Measure actual pre-rerank candidate completeness, post-rerank recall, filtered
complete evidence@5, nDCG@5, top-1, paraphrase consistency, duplicates and latency
by domain and overall. Do not use the legacy evaluator's empty `before_rerank`
field as candidate recall. Cross-document evidence groups must keep their own
document/revision identities.

Retain the existing absolute gates: split-balanced calibration/holdout controls,
at least 90% supported complete evidence@5, no irrelevant released passages
through top-K 20, no unsupported acceptances, all critical/required-top-1 cases,
and warmed public-path p95 <=5 seconds across at least 100 requests. A new arm
also needs higher macro domain nDCG@5 on holdout without reduced complete evidence
overall or in any domain. Failed gates produce a diagnostic report, not an
accepted profile; never relax thresholds to fit the five known examples.

Deploy semantics before considering assistance. Provision immutable lexicon,
matching profile and reviewed provenance under selectively tracked versioned
`artifacts/` directories; keep reports and frozen private data in private durable
storage with independent backups. Restart explicitly. Log only
fixed codes, labels, hashes, counts and timings, never questions or excerpts.
Rollback restores the previous compatible pipeline/profile pair. Source/package
drift requires review and recalibration, not automatic alias updates.

See [artifact operations](runtime-artifacts.md) and the command inventories in
[application commands](../src/cli/README.md) and
[evaluation utilities](../scripts/README.md) for executable interfaces.
