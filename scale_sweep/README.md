# Scale and depth sweep of the materialized total-requirements relation (sql_x, XB)

A model-free measurement (no LLM or API calls) of how the closure relation `u_explosion` (the Gozinto total-requirements
matrix T with validity intervals, built by `code/build_closure.py`) grows with the size and the depth of the XB product
structure, and how lookups on it compare with the equivalent recursive query over `u_composition`.

## What is measured

For XB with seed 20261102 at scale 1, 3, 6 and 10 (three assembly tiers, the generator as shipped), plus a deeper
variant at scale 1 with five assembly tiers:

1. **Source.** Items = distinct nodes of `u_composition`; edges = rows of `u_composition`; depth = longest
   root-to-leaf path of the composition DAG in edges (identical with and without the as-of-date filter at every setting).
2. **Closure.** Rows of `u_explosion`, rows per composition edge, build time of `build_closure.build_sql`
   (`closure_s` = Python closure computation; `total_build_s` = closure + copy + insert + checkpoint), and the DuckDB file
   size of `xb_u.duckdb` (without T) and `xb_x.duckdb` (the same database plus `u_explosion`).
3. **Queries (DuckDB 1.4.5, warm).** As of 2026-10-01:
   - *explode*: total quantity of every leaf (non-assembly component) under one root. Recursive: `WITH RECURSIVE` over
     `u_composition`, quantities multiplied along each path, `valid_from <= d AND (valid_to IS NULL OR d <= valid_to)` on
     every edge, summed per leaf. Lookup: the same filter on `u_explosion` for the root. 100 roots drawn uniformly from
     all nodes with children.
   - *where-used*: every node that contains one leaf (all ancestors). Recursive: upward `WITH RECURSIVE ... UNION` over
     valid edges. Lookup: `u_explosion` filtered on the item. 100 leaves drawn uniformly from leaves used in some edge.
   - Per key: one warm-up run of each query, then 5 alternating timed runs; the key's time is their median. p50 and p95
     are taken over the 100 keys. Answers of the recursive and the lookup query are compared for **every** key (same
     rows; quantities equal up to 1e-9 relative).

The exact SQL is in `sweep.py` (`Q_REC`, `Q_LOOK`, `Q_WU_REC`, `Q_WU_LOOK`).

## Results

Machine: Apple M4, 16 GiB RAM (`sysctl -n machdep.cpu.brand_string hw.memsize` -> `Apple M4`, `17179869184`), macOS 15
(Darwin 24.0.0), Python 3.9.6, DuckDB 1.4.5. The machine was shared with other jobs (1-minute load average 16-25 during
the reported pass, up to 61 earlier), so absolute times are upper bounds; single runs, not repeated across days.

| scale | tiers | items | edges | depth | T rows | rows/edge | closure build s (compute / total) | DB MB without T | DB MB with T | explode recursive p50 / p95 ms | explode lookup p50 / p95 ms | mismatches | where-used recursive p50 / p95 ms | where-used lookup p50 / p95 ms | mismatches |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 3 | 7,269 | 15,831 | 6 | 277,415 | 17.52 | 1.42 / 1.70 | 3.3 | 6.3 | 4.9 / 7.7 | 1.7 / 2.9 | 0 | 2.4 / 6.9 | 1.0 / 1.8 | 0 |
| 3 | 3 | 21,776 | 46,856 | 6 | 815,433 | 17.40 | 5.45 / 6.52 | 5.0 | 14.8 | 11.6 / 15.2 | 3.2 / 4.1 | 0 | 7.2 / 17.8 | 3.3 / 4.5 | 0 |
| 6 | 3 | 43,663 | 94,340 | 6 | 1,652,957 | 17.52 | 16.02 / 20.23 | 9.8 | 30.3 | 11.9 / 20.4 | 3.4 / 4.2 | 0 | 13.5 / 32.6 | 5.2 / 8.2 | 0 |
| 10 | 3 | 72,646 | 156,907 | 6 | 2,740,306 | 17.46 | 42.19 / 65.31 | 14.8 | 49.5 | 10.1 / 17.0 | 2.8 / 3.9 | 0 | 12.2 / 31.5 | 7.2 / 9.6 | 0 |
| 1 | **5** | 7,271 | 15,795 | **8** | 456,599 | 28.91 | 4.07 / 4.79 | 3.3 | 8.3 | 7.2 / 13.4 | 2.6 / 3.5 | 0 | 3.8 / 11.2 | 1.8 / 2.8 | 0 |

Observations:

- **Size of T is linear in the number of composition edges at fixed depth**: 17.4-17.5 rows per edge from scale 1 to
  scale 10 (10x edges, 9.9x rows). Rows with a closed validity interval stay at about one third (97,909 of 277,415 at
  scale 1; 900,869 of 2,740,306 at scale 10); at most 6-7 validity intervals per (root, item) pair.
- **Depth is the driver of T's size**: at the same scale, five assembly tiers (depth 8) instead of three (depth 6) give
  1.65x the rows per edge (28.9 vs 17.5) and 11 instead of 7 intervals per pair at most.
- **Storage**: `u_explosion` adds 3.0 MB at scale 1 and 34.8 MB at scale 10 to the DuckDB file (1.9x to 3.3x the
  database without it).
- **Build**: the reference closure builder (single-threaded Python) takes 1.4 s at scale 1 and 42 s at scale 10 for the
  closure computation (65 s including the DuckDB insert); repeated sequential runs: scale 6 12.4 / 16.0 s, scale 10
  42.7 / 42.2 s. Growth is mildly super-linear (about edges^1.5 between scale 1 and 10).
- **Queries**: the lookup on T returns the recursive query's answer for all 100 roots and all 100 leaves at every
  setting (0 mismatches in 1,000 comparisons). Explode lookups are 2.7-3.7x faster at the median and stay at 1.7-3.4 ms
  p50 up to scale 10; where-used lookups are 1.7-2.6x faster and grow with |T| (1.0 ms -> 7.2 ms p50), because
  `u_explosion` is stored in root order, so an item lookup scans the table (zone maps prune root lookups only).
  ART indexes on `(item_kind, item_code)` and `(root_kind, root_code)` do not change this in DuckDB 1.4.5 (EXPLAIN still
  shows a sequential scan; measured with `index_variant.py`, kept in `results.json` under
  `scale10_tiers3.index_variant`).

Reproducibility checks (`check_repro.py`, in `results.json` under `repro_checks`): scale 1 of the sweep reproduces the
benchmark exactly (`u_composition` identical to `data/xb_u.duckdb`, 15,831 rows; `u_explosion` 277,415 rows, as in
`results/closure_build.json`), and the local generator copy with `tiers=3` produces the same `u_composition` as the
original generator.

## How the settings are built

- Scale knob: `code/make_sources.build_xb(path, seed, scale)` multiplies leaves (3000), assemblies (800), models (800),
  SKUs (3000), bundles (400) and orders by `scale`; this sweep calls it unchanged.
- Deeper variant: `make_xb_tiers.py` is a copy of `build_xb` whose only change is that the number of assembly tiers is a
  parameter (`tier = j % tiers`; a tier-t assembly may contain tier-(t+1) assemblies), so depth = 3 + tiers edges
  (bundle -> SKU -> model -> tiers assembly levels -> leaf). The temporary order-line CSV is written next to the output
  database instead of `data/`.
- `xb_u.duckdb` is built with the SQL of `code/build_substrates.build_unified` (DuckDB half, verbatim); `xb_x.duckdb` is
  built by calling `code/build_closure.build_sql("xb", ...)` unchanged. `sweep.redirect()` points every path constant of
  the imported modules to `scale_sweep/work/<tag>/` (asserted), so nothing in `data/`, `results/` or `code/` is written.
- XB at scale 10 takes about 20 minutes to generate on this machine (the order tables dominate; the BOM part is
  seconds), so scale 10 was run as planned; scale 6 is an extra point.

## Commands

```sh
PY=python
cd scale_sweep
# pass 1: generate, build, measure (these were run concurrently next to other jobs)
$PY sweep.py --scale 1
$PY sweep.py --scale 3
$PY sweep.py --scale 6
$PY sweep.py --scale 10
$PY sweep.py --scale 1 --tiers 5
$PY check_repro.py
# pass 2 (reported numbers): one process at a time, rebuild u_explosion from the existing xb_u.duckdb and re-measure
./rerun_sequential.sh
$PY sweep.py --scale 6 --closure-only     # repeat of the two largest builds (reported values are these runs)
$PY sweep.py --scale 10 --closure-only
$PY index_variant.py --tag scale10_tiers3 # ART-index variant (no effect, see above)
$PY make_table.py                         # prints the table above from results.json
```

Files: `sweep.py` (driver), `make_xb_tiers.py` (tier-parameterised generator copy), `check_repro.py`,
`rerun_sequential.sh`, `index_variant.py`, `make_table.py`, `results.json` (all measurements, machine, load averages,
first-pass build times under `closure_build_first_run`), `log_*.txt`, `work/` (generated databases, ~0.45 GB; not needed
in the artifact; `work/results_first_pass.json` and `work/results_second_pass.json` are snapshots of earlier passes).
