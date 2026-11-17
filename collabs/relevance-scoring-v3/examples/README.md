# Fictional round-trip

From the extracted kit root (after installing requirements):

```sh
python -m relevance_kit example --output-dir work/fictional-v1
python -m relevance_kit fit --bundle work/fictional-v1/input.zip --output outputs/fictional-result-v1.zip
```

The generator creates exactly 300 fictional cases, ten families, baseline and
semantic arms and 1,200 numeric rows. `reviewed=true` describes fictional labels
for software testing only. The bundle manifest says `data_kind=synthetic` and
import receipts say `synthetic_candidate_only`. It cannot establish real
relevance, quality acceptance, holdout independence or a deployable profile.
Never relabel this fixture as a reviewed real dataset.
