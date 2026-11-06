# Knowledge Graph, Semantic Layer, or Plain SQL? — Replication Package

This package accompanies the ICDE EAB paper *"Knowledge Graph, Semantic Layer, or Plain SQL? An Experimental Study of LLM Question Answering over Multi-Level Product Composition Data"*.

It contains everything needed to rebuild the three benchmark datasets, regenerate every question and gold answer, re-run every substrate with any OpenAI-compatible LLM endpoint, and regenerate all tables, figures and numbers in the paper from the released raw results.

## Layout

| Path | Content |
|---|---|
| `code/build_substrates.py`, `gold.py`, `gen_questions.py`, `check_gold.py`, `semantic.py`, `prompts.py` | The XB synthetic dataset: four substrates (SQL, SQL-U, Graph-T, Graph-U), the metric layer, an independent gold implementation, the question generator and the gold cross-check |
| `code/datasets/aw/`, `code/datasets/lego/` | The same components for AdventureWorks and the LEGO catalog |
| `code/run_pilot.py` | The runner for the single-shot substrates (SQL, SQL-U, Graph-T, Graph-U, Metric, Metric-R, and the pilot variants) |
| `code/run_agent.py` | The runner for the Agent substrate (governed metric tools plus raw SQL) |
| `code/perturb.py` | The perturbed-instance re-check |
| `code/classify_errors.py` | The LLM-assisted error taxonomy |
| `code/analyze_main.py`, `code/make_figs_main.py` | Generate every number, table and figure of the paper |
| `data/*_questions.jsonl`, `data/*_s3_coverage.json` | Questions, gold answers, and metric-layer coverage flags |
| `results/main_*.jsonl` | One record per execution: prompt outcome, generated query or tool trace, result rows, tokens, list-price cost, latency |
| `results/runs*.jsonl` | Pilot runs: repeated runs and the refusal, fallback, registry and agent variants |
| `results/main_*_m2.jsonl` | Metric and Metric-R re-executed after the compiler started rejecting `aggregate: count` without `group_by`; these supersede the Metric and Metric-R records in `main_*.jsonl`, which are kept for transparency |
| `results/main_*_abl.jsonl`, `results/main_*_neo.jsonl` | Ablations (documentation symmetry, optional refusal) and the Neo4j replication |
| `results/tax_main_*.jsonl`, `results/robust_main_xb.jsonl` | Error taxonomy sample and perturbed-instance re-checks |

## Reproducing

Use Python 3.9+ with `duckdb==1.4.5` and `kuzu==0.11.3`; matplotlib is needed for the figures. The second graph engine is Neo4j 5.26 Community with JDK 21. All model calls were made between 2026-11-03 and 2026-11-06 through the vendors' OpenAI-compatible endpoints (`deepseek-flash`, `deepseek-v4-pro`, `kimi-k2.6`; `glm-4.5-air` in the pilot).

1. **Build the source snapshots.** Run `python code/make_sources.py --raw raw --out data`. It writes `data/{aw,lego,xb}_src.duckdb`, and the result is table-for-table identical to the snapshots used in the paper.
   - XB is produced by a seeded generator.
   - AdventureWorks is built from the CSV exports in `raw/aw/`, which are taken from [microsoft/sql-server-samples](https://github.com/microsoft/sql-server-samples) under the MIT license.
   - LEGO needs the Rebrickable catalog downloads (`*.csv.gz` from rebrickable.com/downloads) in `raw/rebrickable/`. They are not redistributed here; please respect Rebrickable's terms of use.
2. **Build the substrates.**
   - XB: `python code/build_substrates.py`.
   - AdventureWorks and LEGO: `python code/datasets/aw/build.py` and `python code/datasets/lego/build.py`.
3. **Regenerate and check the questions.** Run `gen_questions.py` and `check_gold.py` for each dataset. The generators are seeded, so reruns are byte-identical. The gold answers agree with the metric-layer oracle on every covered question.
4. **Run the substrates.**
   ```
   N1_DATASET=aw python code/run_pilot.py --models <m> --substrates sql,sql_u,cypher,cypher_u,semantic,classify --out main_aw.jsonl
   ```
   Set `N1_KEYS_ROOT` to a directory that contains `.secrets/llm_keys.json` with `{provider: {api_key, base_url}}`. `run_agent.py` takes the same arguments. The other substrates are selected with `--substrates`:
   - `cypher_h`: Graph-T with the multi-type pattern note (documentation ablation).
   - `semantic_abs`: Metric with an optional `UNSUPPORTED` answer.
   - `neo_t` and `neo_u`: the two graphs on Neo4j 5.
   - `classify`: Metric-R.
   
   For Neo4j, `code/neo4j_build.py <kuzu_db> <ds>_<t|u> <port_offset>` copies a Kùzu graph into a dedicated local Neo4j instance with identical labels, types and properties. `code/run_neo4j_all.sh` runs the replication; it expects a JDK 21 and a Neo4j 5.26 Community tarball unpacked under `.tools/`.
5. **Regenerate the paper's numbers and figures.** Run `python code/analyze_main.py`, which writes `paper/numbers.tex` and the tables, then `python code/make_figs_main.py`.

## Notes

- Every Kùzu query runs in its own process (`kuzu_exec.py`), because a generated query can crash the engine.
- DuckDB spill space is capped at 300 MB per connection.
- The enterprise case study reported in the paper uses de-identified proprietary data. It is not part of this package; only aggregate results are reported.
