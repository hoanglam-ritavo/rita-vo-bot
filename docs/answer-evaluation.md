# Template answer verification

## Semantic template answer API

`POST /api/v1/answer` uses deterministic task matching and source-bound templates.
It reads exact linked evidence from current eligible DB publications, independently
of retrieval top-K. Request/response fields remain compatible; generation and
verification seconds are zero. Unknown/ambiguous tasks do not receive arbitrary
nearest excerpts. Explicit document lookup can return labeled partial excerpts.
See [full contract](catalog-answering.md#runtime-contract).

### Configuration and validation

Keep `ANSWER_ENABLED=false` by default. Enabling document answers requires
`SEMANTIC_MODE=expand`, `SEMANTIC_ARTIFACT_PATH` and `ANSWER_CATALOG_PATH`
(default `artifacts/answers/catalog.json`). Deploy schema-v2 lexicon/catalog with
compatible code. `requirements-answer.txt` remains a retired empty dependency file.
No generator, verifier, classifier or relevance profile is loaded.

`python -m src.cli.check_answer_catalog` checks structure and lexical hash pairing,
not live DB compatibility or business correctness. `python -m src.cli.build_artifacts
impact` reports dependency-local package drift. See [update commands](catalog-answering.md#updating-chunks).

`/health/answer` returns 200 only when template initialization and model/database
readiness succeed; disabled/unavailable returns 503 with a fixed reason. Health
checks do not load a catalog again or evaluate questions. Busy/timeout/cancellation
behavior is shared with retrieval. Source errors do not become successful answers.

## Regression and acceptance

Run non-integration tests and the optional dedicated disposable PostgreSQL
integration suite. The retained `scripts.benchmark_template_answers` reads private
cases and records outcomes, citations, latency and artifact identity through the
real service without a listener. It never declares acceptance. An authored
regression set is distinct from independent source-reviewed quality evaluation.

Every declared supported task needs its evidence checked; incomplete templates
stay partial. Assess ambiguity, unsupported tasks, source/permission/revision drift,
conditions and exceptions. Source inventory coverage is not answer coverage.
Historical Qwen/verifier, intent and calibrated-scoring studies are retired;
Git and README historical receipts do not authorize the new runtime.
