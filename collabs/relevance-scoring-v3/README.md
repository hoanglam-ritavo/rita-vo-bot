# Relevance scoring V3: independent Colab development kit

Kit version **1.0.0** targets `passage-logistic-v1`, baseline + semantic paired
features and intent off. Python 3.12+ is required by the pinned NumPy build. The supported numerical
dependency is pinned in `requirements.txt`; validation records the actual Python
and NumPy environment. This directory can be zipped and run outside this repo.
No database, source clone, E5, ONNX or credentials are needed on Colab.

**This kit never creates an accepted relevance profile.** CPU fitting emits a
development candidate, not a threshold or `holdout_passed`. The final
`artifacts/rerank/relevance-profile.json` is emitted only by a successful local
CPU acceptance run and installed through the existing reviewed promotion flow.

## Files and boundaries

- `notebooks/build_v3.ipynb`: upload, verify, fit, inspect diagnostics and download.
- `relevance_kit/`: independent runner and UTF-8/LF scoring/fitter snapshots.
- `contracts/data.md`: input/output, numerical compatibility and release contracts.
- `kit-manifest.json`: kit version, upstream revision, upstream module hashes and
  packaged-file hashes. Revision is provenance; hashes enforce module parity.
- `examples/README.md`: fictional smoke recipe; no reviewed internal data shipped.
- `config/run.json`: reference settings, optional GPU role and numerical tolerances
  (documentation of fixed defaults, not a runtime override file).

Keep local exports/results under ignored `.tmp/`. Colab `work/` and `outputs/`
are ignored too. Never commit private cases, execution outputs or downloaded ZIPs.
Record hashes and retain private reviewed inputs separately before upload.

## 1. Prepare and export locally

From the repository root, activate its existing Python environment. First use
the existing collection/review bridge described in
[learned scoring](../../docs/relevance-scoring.md) and
[utility commands](../../scripts/README.md#evaluation-and-experiments): collect
development features, review all candidate labels without scores, then export
reviewed development rows. Use regression/development families, not acceptance
calibration/holdout. The export must include the bridge's dataset, collection,
review, source-package and corpus hashes. Declarations cannot prove a human
actually reviewed the data; that remains an operational responsibility.

```powershell
python -m scripts.relevance_colab package --output .tmp/colab-kit-v1.zip
python -m scripts.relevance_colab export --dataset .tmp/scoring/reviewed-development.json --output .tmp/colab-development-v1.zip
```

Both commands refuse overwrite. Real export requires >=300 reviewed cases,
exactly baseline/semantic arms and the existing source-review provenance.
Free-text review notes, source paths and unknown fields are stripped; only
fixed review wording, feature vectors, labels, identity/group strings, evidence
groups and provenance hashes travel. Inspect these IDs/group names for private
content before upload. Hashes bind bytes, not review authenticity.

## 2. Run on Colab

Open `notebooks/build_v3.ipynb` in Colab; upload the kit ZIP and development ZIP
when prompted. The notebook installs only pinned NumPy, loads the kit into a new
directory and runs its self-check. It does not install model libraries or mount
Drive. Choose a GPU runtime only if you want the optional diagnostics and CUDA
PyTorch is already available. PyTorch is not a fitting dependency; its actual
version is recorded if used. GPU numerical checks are optional, so no separate
PyTorch installation or version change is needed.

The CPU path is authoritative. GPU uses FP64 to score the already fitted numeric
vectors, records absolute/relative error and rank-position changes, and never
selects weights or thresholds. Missing CUDA/PyTorch or unavailable FP64 execution
is explicitly skipped. A completed diagnostic outside tolerance is reported as
such; it never silently replaces CPU scores or authorizes release.

Equivalent commands from the extracted kit root:

```sh
python -m pip install -r requirements.txt
python -m relevance_kit check
python -m relevance_kit fit --bundle work/input.zip --output outputs/candidate-v1.zip
# Optional diagnostics; use a new output name:
python -m relevance_kit fit --bundle work/input.zip --output outputs/candidate-gpu-v1.zip --gpu
```

Download the result ZIP immediately after fit. If the runtime disconnects, retain
the downloaded result, original input and kit together. To continue, reupload
those same files into a fresh runtime and rerun checks; do not overwrite old
outputs. No intermediate optimizer checkpoint or unattended resume is provided.
`check` verifies integrity, not model quality. `fit` exit 0 means only that a
candidate was produced; exit 2 rejects inputs/fit/output. Errors use fixed codes.

## 3. Import and re-fit on local CPU

```powershell
python -m scripts.relevance_colab import --bundle .tmp/colab-development-v1.zip --result .tmp/downloaded-candidate-v1.zip --output-dir .tmp/scoring/colab-import-v1
```

Import requires the exact input manifest, compatible kit and upstream hashes.
It independently re-fits on CPU, compares the complete report and coefficients
(FP64 rtol 1e-9, atol 1e-10), validates the scoring digest and records an import
receipt. Coefficient digests themselves remain exact strings; reported numerical
fields use the tolerance. Outputs go only into a new private directory; no
`.env`, runtime inventory, profile or service is changed. A failed/partial write
is not a valid import: use a new directory after diagnosing it, never reuse it.

The original ZIP manifest retains the original development-file SHA-256 and
review provenance. The training report binds the **sanitized** development JSON
inside the ZIP; preserve both for the original-to-uploaded provenance chain.

## 4. Acceptance and promotion stay local

Run the existing paired semantic benchmark with the imported `candidate.json`
as `--scoring-candidate` and `training-report.json` as `--scoring-training-report`.
Use a separately reviewed >=300-case acceptance dataset with fresh, disjoint
calibration/holdout families, the exact CPU runtime/model/batch/lexicon and target
database publication identities. Do not use the fictional demo as acceptance.

```powershell
python -m scripts.benchmark_semantic_retrieval --dataset .tmp/scoring/frozen-acceptance.json --model-path models/embeddings/multilingual-e5-small/614241f622f53c4eeff9890bdc4f31cfecc418b3 --model-revision 614241f622f53c4eeff9890bdc4f31cfecc418b3 --semantic-artifact artifacts/semantics/lexicon.json --rerank --scoring-candidate .tmp/scoring/colab-import-v1/candidate.json --scoring-training-report .tmp/scoring/colab-import-v1/training-report.json --output .tmp/scoring/acceptance-v1.json
```

The benchmark must pass >=90% complete evidence@5 on calibration and holdout,
zero irrelevant passages through top-K20, no unsupported acceptances, required
critical/top-1 checks, semantic comparison gates and >=100 warmed public requests
with p95 <=5 seconds. Colab timings cannot establish the local latency gate.
All runtime flags must match the intended deployment; the example assumes the
current defaults, batch 4 and intent off.

Only a passing run emits `acceptance-v1.semantic.profile.json`. Follow
[independent release](../../docs/relevance-scoring.md#independent-evaluation-and-release)
and [artifact operations](../../docs/runtime-artifacts.md#promote-and-verify)
for actual human acceptance, the gated promotion command, inventory checks,
backup and matching-corpus HTTP smoke. Provision in a release checkout, not by
overwriting a live service. Enable filtering/restart only as a separate reviewed
deployment. If gates fail, preserve diagnostics; never invent a threshold,
change fingerprints or set `holdout_passed=true` yourself.

## Maintenance

Any changes to the canonical scoring/fitter invalidate local export/import.
Refresh their UTF-8/LF snapshots and upstream hashes only with review, update kit
version/provenance, re-run parity and round-trip tests, and regenerate the
packaged-file hashes. Do not make an old candidate appear to belong to a new kit.
Standalone users retain their matching kit/input/result together; the repository
import command rejects stale kits. No automatic source synchronization occurs.
