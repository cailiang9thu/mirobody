# mirobody-rare

Rare-disease coding layer for mirobody (阶段一 MVP, `docs/rare-mvp-plan.md` D1a / D1b / D8).

```
uv pip install -e plugins/mirobody-rare          # registers the `rare` MCP tools
mirobody-rare-serve --port 8765                   # OpenAI-compatible coding shim for haenv
```

| Module | Plan item | What it does |
| --- | --- | --- |
| `assertion/rules.py` | D1a §14.2 | text → assertions with `subject` / `polarity` / `asserted_by` / `onset_text` / `char_span` |
| `assertion/llm.py` | D1a §14.3 ④ | optional Gemini extractor, same schema, off by default |
| `hpo/bundle.py`, `hpo/adapter.py` | D1b §3.1–3.4 | `hpo_bundle.tar.gz` from hp.obo + zh babelon; `HpoAdapter.resolve/resolve_many` |
| `disease/` | D8 | Orphanet product1/6 + phenotype.hpoa; name resolution and IC phenotype ranking |
| `gene/` | D8 | HGNC symbol / alias / prev table |
| `coding.py` | — | the pipeline; solver-contract JSON |
| `serve_coding.py` | 决策 #5 | `/v1/chat/completions` shim haenv talks to |
| `tools.py` | D5 (layer 1) | `resolve_hpo`, `code_phenotypes`, `rank_rare_diseases` |
| `../../mirobody/schema/32_phenotype.sql` | §3.2 | `th_phenotype`, `th_disease_code` DDL (main package, append-only) |

Configuration is `mirobody_rare/config.yaml`; `MIROBODY_RARE_ONTOLOGY_DIR` / `MIROBODY_RARE_CACHE_DIR` override
the two paths. Tables are built on first use into `cache_dir` from the ontology drop.

No guessing: an assertion with no vocabulary hit goes to `abstained`, an ambiguous hit is coded with
`review=true`, and a disorder with several causal genes yields `gene.symbol=null` with the candidates listed.
