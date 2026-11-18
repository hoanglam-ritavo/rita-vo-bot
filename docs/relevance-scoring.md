# Retired learned relevance scoring

Corpus-bound relevance profiles, learned scoring, calibration, fitters and profile
promotion have been removed. Raw local ONNX reranking is retained for ordering
unassessed document candidates. It never authorizes a policy answer.

See [catalog answering](catalog-answering.md) for exact evidence checks and source
updates without training or profile calibration. Historical implementation and
receipts remain in Git; private workspaces are untouched.

## Features and runtime contract

No learned feature/scoring artifact is loaded by the current runtime.

## Independent evaluation and release

Use task/evidence regression and a compatible code/catalog/lexicon release.
