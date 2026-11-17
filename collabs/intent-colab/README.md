# Standalone intent training kit

This Git-managed kit trains frozen-E5 intent heads on Google Colab without
cloning the repository. E5 embedding uses FP32 CUDA; sklearn logistic heads use
CPU. The exported trainer has no project imports, implicit installs/downloads,
database connection or dotenv loading. It produces a candidate, never acceptance.

## Run the notebook on Colab

Open [notebooks/train_intent.ipynb](notebooks/train_intent.ipynb) in Google Colab
(download and upload the notebook if opening it from the repository). It is also
included in the generated kit ZIP. No Git clone is required. The notebook is saved
with empty outputs and execution counts; do not commit private session output.

Upload `colab-upload.zip` through Colab Files, provision the unchanged E5 folder
at a local path, and edit the first configuration cell. Defaults are `/content`
workspace, `/content/intent-colab-kit` for extracted kit files, `/content/e5-model`,
CUDA and batch size 16. There is no automatic Drive connection or model download.
Run the cells in order to verify/extract the ZIP, install dependencies, check the
model/device, prepare/validate a private dataset, optionally upload a reviewed
replacement, preflight, train and download the selected batch.

An existing matching kit is retained; a differing kit stops extraction. Existing
working data is never overwritten by sample setup. Set
`UPLOAD_REVIEWED_DATASET=True` only for the explicit replacement cell; it accepts
one JSON and validates its review/structure before replacing the private copy.
It never marks data reviewed or changes the tracked sample. Train validates again
and refuses unreviewed data before starting the model. CLI failures stop the step,
with no automatic retries or CPU fallback.

The notebook calls the standalone CLI; it does not duplicate the model algorithm.
Only a completed matching run is selected. Export checks dataset/output hashes
and includes exactly that run's five output files plus its exact dataset. It
excludes all other runs, models, credentials and caches. A quality-failed run can
be exported for diagnostics and is never represented as accepted. Saved files
survive a cell rerun; the active run selection resets with configuration/runtime.
Live Colab/CUDA execution and real training remain separately unverified.

## Maintained files and private work

The selected synthetic sample is `datasets/draft-cases.json`; its authoring and
agent-review notes are in `datasets/dataset-review.json`. The generator produces
600 fictional questions with `reviewed: false`; tracked edits that diverge from
that source are rejected by source/review-binding checks before packaging.
Names, labels and review state do not certify
policy correctness, real employee facts, corpus coverage or live tool availability.
Human review is still required before training. Many Type B labels represent
prospective read-only needs beyond the currently bounded HTTP implementation.

Code/templates, requirements, this README, the sample and its authoring notes are
versioned. All working datasets, run reports, models, caches and ZIPs stay outside
this directory. Locally use repository `.tmp/intent-colab/`; on Colab use
`/content/.tmp/intent-colab/`. Existing local kits are not deleted or auto-imported.
Only upload content approved for cloud processing; no `.env`, employee records,
private source documents or database credentials are needed.

## Check, regenerate and package locally

Run from the repository root with the repository Python environment:

```powershell
python collabs/intent-colab/build_bundle.py --check
python collabs/intent-colab/author_dataset.py --check
python collabs/intent-colab/verify_bundle.py
python collabs/intent-colab/prepare_upload.py --verification-report .tmp/intent-colab/verification.json
```

The first two commands are read-only consistency checks. After deliberate source
changes, use their `--write` mode and review the resulting diff. The standalone
trainer is generated from selected upstream contract functions plus
`standalone_runtime.part`; do not edit the generated trainer directly. The dataset
generator preserves the unreviewed synthetic sample, not a private reviewed copy.
An upstream change requires explicit compatibility review, not blind regeneration.

`verify_bundle.py` runs the canonical suite in `src/tests/test_intent_colab.py`
without loading the full pytest conftest. It writes a source-bound receipt under
`.tmp/intent-colab/verification.json`. Standard pytest discovery also runs that
suite. Fixtures and cache remain under `.tmp`; fictional diagnostics are retained.
Unavailable numerical dependencies or actual symlink support produce explicit
skips. Synthetic numerical checks do not train E5 or establish acceptance.

Packaging outputs `.tmp/intent-colab/dist/colab-upload.zip` and its sibling
`bundle-manifest.json`. `--output-dir` permits another directory under repository
`.tmp` only. A supplied verification report must be successful and match the
current kit, canonical tests and upstream source hashes. Omitting
`--verification-report` creates a bundle explicitly marked without verification.
Counts/skips come from the receipt; no historical result is hard-coded.

The ZIP uses a fixed allowlist: trainer, requirements, README, sample, synthetic
review notes, notebook, manifest, and optional matching verification report. It never scans
the private workspace. Source snapshot hashes normalize UTF-8 line endings to LF
(`sha256-utf8-lf-v1`); model and archive payload fingerprints still use raw bytes.
Notebook source participates in verification identity; edits invalidate old receipts.
An older ZIP without the notebook is not compatible with the notebook's setup
cell. Generate a matching ZIP only after source/sample consistency is resolved;
an edited sample or stale receipt must not be bypassed to release a new archive.
Maintenance exits are 0 success, 1 consistency/test failure, 2 package input error.
Build/authoring require an explicit `--check` or `--write`; imports have no writes.

## Upload without Git

The cells above automate this workflow. The following commands are the manual
alternative; their extracted `/content` paths differ from the notebook's
`/content/intent-colab-kit` directory. Do not mix the two sets of paths.

Download the source files directly from the repository, or use the ZIP above.
Upload the ZIP through Colab Files. Extract this trusted generated bundle:

```python
%cd /content
import zipfile
with zipfile.ZipFile('/content/colab-upload.zip') as archive:
    archive.extractall('/content')
```

The trainer, `requirements-colab.txt`, `draft-cases.json`, and
`dataset-review.json` are then at `/content/`. Build/generator/test source is not
required on Colab. Select a GPU runtime and provision an exact local copy of the
selected E5 directory at `/content/e5-model`. Include hydrated Git LFS bytes, not
pointer files. Whole-directory fingerprint must match the internal runtime model;
do not place notebooks or reports inside the model directory.

```python
%pip install -r /content/requirements-colab.txt
```

The minimal requirements retain the repository's selected versions. NumPy,
PyTorch and tokenizer dependencies resolve transitively and their versions are
recorded in run manifests. Resolve compatibility normally and restart the Colab
runtime when required; do not bypass dependency resolution. This kit does not
claim that live Colab, CUDA or dependency installation has already been verified.

## Prepare a private working dataset

After extraction, copy the sample once. Refuse to replace an existing working
copy, which may already contain review edits:

```python
from pathlib import Path
import shutil

work = Path('/content/.tmp/intent-colab')
work.mkdir(parents=True, exist_ok=True)
target = work / 'reviewed-cases.json'
if target.exists():
    raise FileExistsError('Review the existing working dataset before replacing it.')
shutil.copyfile('/content/draft-cases.json', target)
```

With a repository checkout, copy `datasets/draft-cases.json` from this kit to a
fresh repository `.tmp/intent-colab/reviewed-cases.json` instead. The working name
does not establish review. Never train directly from the tracked sample:
the trainer enforces dataset/output paths under `.tmp` of its working directory.

```python
!python /content/train_intent_standalone.py validate --dataset .tmp/intent-colab/reviewed-cases.json
```

Expected for the sample: no structural errors, `review_required: true`,
`training_allowed: false`. Review every label, critical case, scope and family
split, using the synthetic notes as an aid. Check undeclared paraphrase/template
leakage; add realistic linguistic diversity. The sample has only two-style
A/mixed requests and composed mixed questions, so it is not representative traffic.
Do not use holdout predictions to choose labels or tune the classifier.

Only after actual human review, set `reviewed: true` in the private copy and write
truthful `review_notes`. Re-run validate and freeze that exact dataset. Keep
the tracked sample unreviewed. A changed review flag changes the dataset hash;
the original agent notes do not attest the changed working dataset.

## Preflight and train

Substitute the actual supplied E5 revision if different. The original normalized
rerank query is fixed: no typo correction, expansion or truncation. Batch size
defaults to 16. CUDA absence/OOM is explicit; no automatic CPU fallback/retry.
Use `--device cpu` deliberately, or reduce `--batch-size` and start a new run.

```python
!python /content/train_intent_standalone.py preflight --dataset .tmp/intent-colab/reviewed-cases.json --model-path /content/e5-model --model-revision 614241f622f53c4eeff9890bdc4f31cfecc418b3 --device cuda
!python /content/train_intent_standalone.py train --dataset .tmp/intent-colab/reviewed-cases.json --model-path /content/e5-model --model-revision 614241f622f53c4eeff9890bdc4f31cfecc418b3 --device cuda --output-root .tmp/intent-colab/runs
```

Preflight loads E5 and checks all dataset token lengths without embedding those
questions. On CUDA it additionally measures CPU/GPU differences on three fixed
fictional questions. That diagnostic is not a quality gate or bitwise guarantee.
Draft preflight is allowed; unreviewed training is rejected before ML loading.

Each run owns a UTC timestamp/UUID directory containing validation, preflight,
training report, candidate and manifest. Embeddings remain in RAM; no resume or
persistent embedding cache exists. Stdout is one JSON result; stderr shows safe
stages, never submitted questions/vectors. Exit 0 means a successful requested
step or quality-passed candidate; 1 structural/token/quality rejection;
2 operational/review failure; 130 interruption.

Only `complete: true` plus `candidate_ready_for_benchmark` establishes completed
offline gates. `quality_failed` is a complete diagnostic run, not approval.
Incomplete manifests or leftover outputs after interruption are not successful
runs. Preserve all reports and the exact final dataset before the Colab session ends.

## Download and benchmark internally

```python
import shutil
from google.colab import files
archive = shutil.make_archive('/content/intent-results', 'zip', '/content/.tmp/intent-colab')
files.download(archive)
```

Extract results to a fresh directory under the internal repository `.tmp`, then
use the existing benchmark with a matching E5, provisioned corpus/database,
reranker and valid relevance profile:

```powershell
python -m scripts.benchmark_intent --dataset .tmp/<import>/reviewed-cases.json --candidate .tmp/<import>/runs/<run-id>/candidate.json --training-report .tmp/<import>/runs/<run-id>/training-report.json --model-path <unchanged-local-E5> --model-revision <same-40-hex-revision> --rerank --rerank-profile <matching-profile.json> --requests 200 --output-prefix .tmp/intent/bench-colab-01
```

Only benchmark success writes `*-accepted.json`. Renaming candidate files or
passing synthetic tests is not promotion. Provision accepted output explicitly,
start with observe and restart in an authorized rollout. Assist requires separate
semantic evaluation. See the repository [intent contract](../../docs/intent-classifier.md)
when reading this README in the checkout; the linked document is not included in the ZIP.
