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

## Split source authoring

Author concepts in `artifacts/semantics/source/` and tasks/evidence/coverage in
`artifacts/answers/source/`. The source index envelope is schema 1; runtime metadata
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
`import --lexicon <path> --catalog <path> --output .tmp/<new-directory>` writes a
new private authoring tree, never promotes it. See [full reconciliation rules](catalog-answering.md#updating-chunks).

## Release, recovery and rollback

Release code, lexicon/catalog, provenance manifests and runtime inventory together
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
