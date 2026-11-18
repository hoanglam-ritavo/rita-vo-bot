# Runtime artifact operations

## Git LFS checkout and verification

Provision local E5 and pinned ONNX/tokenizer bytes before startup. Keep one current
artifact/manifest pair directly under each `artifacts/<group>/`; Git stores history.
Do not overwrite a running service checkout or automatically discover versions.

```powershell
git lfs pull
python -m scripts.verify_runtime_assets
```

The inventory binds exact model/artifact/manifest byte hashes and sizes. The verifier
uses local Git and standard-library Python, never `.env`, DB connections or downloads.
LFS pointers, missing/corrupt files, unlisted tracked assets and undeclared authoring
files fail verification. Model fingerprints must remain byte-identical across hosts.
Do not edit models or remove installed transitive dependencies as incidental cleanup.

## Git policy for questions and retrieval

Both directories are eligible for ordinary Git tracking through a narrow
`.gitignore` allowlist. Retrieval permits `catalog.json`, `manifest.json`,
`source/index.json` and direct JSON children of the declared `families`, `systems`,
`subtypes`, `operations`, `bindings` and `routes` collections. Its generated pair
remains subject to the existing runtime inventory and source build/check rules.

Questions permits `README.md`, `index.json` and direct JSON children of `policy`,
`software`, `employee`, `mixed`, `conversation`, `boundaries` and `oracles`. The inventory
checker accepts only the README/index and group/oracle files declared in the question
index. It checks index schema version 1, the files list, safe unique paths and
regular-file presence; linked/reparse paths, malformed indexes, missing files and
tracked undeclared files fail with fixed diagnostics. A checkout without Questions
is supported; deleting a tracked question tree does not bypass its checks.

Question files are authored nonruntime sources: they do not belong in the runtime
manifest, change model/catalog fingerprints or enter the artifact builder. This
path/index check does not validate case content, coverage, behavior or acceptance.
See [question format and maintenance](../artifacts/questions/README.md).

Root-level reports, logs, backup extensions and unknown/nested paths remain
ignored. Direct group/collection JSON files still require declaration by their
owning index; the filename allowlist alone does not authorize a report as source.
Keep private evaluation material under `.tmp/`; do not force-add it. Git
eligibility does not stage files, commit them or push a release.

## Split source authoring

Author concepts in `artifacts/semantics/source/` and tasks/evidence/coverage in
`artifacts/answers/source/`; optional cross-domain ordering metadata lives under `artifacts/retrieval/source/`. The builder supports two or three groups. The source index envelope is schema 1; selected runtime metadata
is schema 2. Declared small JSON inputs assemble deterministically into the selected
lexicon/catalog; the catalog binds the generated lexical SHA-256.

```powershell
python -m src.cli.build_artifacts impact
python -m src.cli.build_artifacts refresh
python -m src.cli.build_artifacts build
python -m src.cli.build_artifacts check
python -m src.cli.check_answer_catalog
```

`impact` is readonly. `refresh` updates only safe mechanical links and coverage;
content/context/revision changes remain review-required. Build changes runtime JSON,
check compares bytes, and neither updates acceptance or publishes DB data.
`import --lexicon <path> --catalog <path> [--retrieval <path>] --output .tmp/<new-directory>` writes a
new private authoring tree, never promotes it. See [full reconciliation rules](catalog-answering.md#updating-chunks).

## Release, recovery and rollback

Release code, lexicon/answer catalog, optional retrieval catalog, provenance manifests and runtime inventory together
from an isolated checkout. Update hashes/sizes after an intentional build and keep
review/quality acceptance truthful. Store private cases/reports and backups outside
tracked runtime assets. The local `.tmp` workspace is disposable research storage,
not a runtime dependency. Do not delete it or private reports as incidental cleanup.

Publication is separate: verify target DB revision/content/access against bound
references, then run HTTP smoke tests at the agreed deployment time. No schema
change, application publication or service restart is part of this implementation.
Rollback requires a compatible code/artifact set and current DB evidence; Git does
not roll back publications. Interrupted authoring/builds require inspection and a
successful impact/build/check before release; files are replaced individually.

## Profile promotion

Retired. No intent artifact or relevance profile is provisioned or trained. Retired
env keys inherited from launchers are ignored with name-only warnings. Use
[catalog verification](catalog-answering.md#verification-and-release) instead.

See [business topics](business-topics.md) for the selected schema-2 retrieval
artifact, bounded supplementation and question runner; [leave ranking](leave-retrieval.md)
retains the leave-specific regression and readonly comparison utility.
