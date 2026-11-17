# Independent Colab kits

Each child directory is a separately packaged kit. Do not put credentials,
private datasets, model downloads or execution outputs here.

| Kit | Purpose | Release boundary |
| --- | --- | --- |
| [Intent training](intent-colab/README.md) | Frozen-E5 intent heads with an unreviewed fictional sample; standalone GPU embedding and CPU logistic training. | Human-reviewed working data and internal paired benchmark are required before runtime acceptance. |
| [Relevance scoring V3](relevance-scoring-v3/README.md) | Fit reviewed numeric development features without cloning the repository; optional GPU scoring diagnostics. | Local CPU acceptance and existing promotion remain mandatory. |
| [Intent classifier](intent-colab/README.md) | Separate standalone intent-classifier workflow. | Follow that kit's own verification and acceptance boundaries. |

Other kits can coexist in their own child directories. A directory's presence
does not establish its validation or acceptance status.
