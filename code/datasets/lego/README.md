# LEGO (Rebrickable) dataset for the N1 benchmark

Real-world multi-level composition data: sets contain parts (part x colour), nested sets and minifigs; minifigs contain
parts; sets have several inventory versions; themes form a hierarchy. Mirrors the interfaces of the synthetic `xb`
dataset in `code/` (build / gold / semantic / prompts / gen_questions / check_gold; `code/score.py` is reused unchanged).

Interpreter: `python` (duckdb 1.4.5, kuzu 0.11.3). Run from this folder:

```
python build.py          # ~4 s: data/lego_src.duckdb (+ .sha256), lego.duckdb, lego_u.duckdb, lego.kuzu, lego_u.kuzu
python gen_questions.py  # ~8 s: data/lego_questions.jsonl (190 questions, seed 20261105, deterministic)
python check_gold.py     # ~11 s: data/lego_s3_coverage.json; exit code 0 only if every check agrees
```

## Source and scope

* Source: `data/lego_src.duckdb`, built by `code/make_sources.py` (Rebrickable CSV export), copied to `data/lego_src.duckdb`,
  sha256 `8731df894ee3c276566aba783d3025b74921fb514854c87cdc992af72034ce54`. The `kg` schema of the snapshot is ignored.
* Scope: all sets with `year >= 2010` (18,936) plus everything they reach recursively through any inventory version
  (65 older nested sets, their inventories, 13,662 minifigs, 45,974 parts). All 496 themes, 275 colours and 76 part
  categories are kept.
* Inventory records with no content rows (no parts, nested sets or minifigs; mostly books/gear) are dropped. They carry
  no composition fact; keeping them would make "current = highest version" differ between the inventory-node model and
  the unified-edge model (one set in the full data has an empty latest version). Sets without any inventory remain in `sets`.
* Integrity (asserted by build.py): every inventory / nested-set / minifig reference resolves inside the snapshot,
  every minifig has exactly one inventory, and the unified "highest edge version" equals `max(inventories.version)`
  for every parent. Nesting depth is at most set -> set -> set -> minifig -> part; no cycles (gold.py raises on cycles).

## Substrates

| file | model | content |
|---|---|---|
| `lego.duckdb` (12.6 MB) | S1 relational (Rebrickable split) | sets 19,001; themes 496; inventories 27,200; inventory_parts 1,193,302; inventory_sets 3,301; inventory_minifigs 18,968; minifigs 13,662; parts 45,974; part_categories 76; colors 275 |
| `lego_u.duckdb` (14.7 MB) | S1u unified | `inventories` + `inventory_*` replaced by `u_composition(parent_kind, parent_num, version, child_kind, child_num, color_id, qty, is_spare)`, 1,215,571 rows (set->set, set->minifig, set->part, minifig->part) |
| `lego.kuzu` (64.5 MB) | S2 typed graph, Inventory as a node (faithful ERP-like migration) | nodes Set, Theme, Minifig, Part, PartCategory, Color, Inventory{id, version}; rels HAS_INVENTORY (Set/Minifig -> Inventory, rel-table group), CONTAINS_SET{qty}, CONTAINS_MINIFIG{qty}, HAS_PART{color_id, qty, is_spare}, IN_THEME, SUBTHEME_OF (child -> parent), IN_CATEGORY |
| `lego_u.kuzu` (62.0 MB) | S2u unified graph, no Inventory node | one rel-table group CONTAINS{version, qty, color_id, is_spare} over Set->Set, Set->Minifig, Set->Part, Minifig->Part (1,215,571 edges) + IN_THEME, SUBTHEME_OF, IN_CATEGORY |

Colour is an edge property (`color_id`) with a separate Color node table for names (Color names are unique).
Kuzu loads via Parquet staging (`COPY T FROM 'x.parquet'`; rel-table-group members via `COPY G FROM ... (from='A', to='B')`;
the `COPY G_A_B` form used for xb is rejected by kuzu 0.11.3 here).

## Semantics (stated once in `prompts.RULES`, baked into `semantic.SPEC`)

* Current inventory = highest version; a named version applies only to the asked set's own inventory, nested sets and
  minifigs always contribute their own current inventory.
* Explosion: own parts + for each nested set / minifig, qty x its (recursive) parts; quantities multiply along paths.
* Spares excluded unless the question says to include them (also for where-used).
* Part identity: part_num alone unless a colour is named; colour-qualified questions use (part_num, color).
* Theme "including sub-themes" = the subtree under `parent_id`. Minifigs are not sets. Multi-set sums explode each set on its own.
* `num_parts` is the catalogue's printed piece count and is not used by any gold answer.

## Semantic layer (`semantic.py`)

Same JSON grammar and compiler contract as xb (`metric, params, group_by, filters, order_by, limit, aggregate`).
Metrics: `part_quantity`, `minifig_quantity`, `set_contents` (first level), `where_used` (part [+colour] / minifig / set,
any depth), `catalog_parts`, `catalog_minifigs` (roll-ups over sets), `set_attr`, `inventory_versions`.
Set dims: `set.set_num, set.name, set.year, theme.name, theme.root, theme.rollup` (`theme.rollup = T` selects T and all
its sub-themes). Compiled SQL runs on `lego.duckdb`.

## Questions (`data/lego_questions.jsonl`)

190 questions; record fields as in xb (`qid` = `LG-<cls><nn>`, `cls`, `tpl`, `text`, `gold_type` scalar|set, `gold`)
plus `s3_covered` (bool) and `args` (template parameters for check_gold.py only; never show to a model).
163 scalar, 27 set answers.

| class | templates (count) | non-triviality filter |
|---|---|---|
| L 12 | L1 year, L2 #versions, L3 first-level nested sets (list), L4 first-level #minifigs (3 each) | control |
| E 54 | E1 part qty any colour (8), E2 part x colour qty (7), E3 total parts (7), E4 distinct part_nums (6), E5 distinct part x colour (6), E6 total minifigs via nested sets (6), E7 total incl. spares (5), E8 parts of a category (5), X4 (4, OOC) | answer changes when nested sets/minifigs are ignored; E2 also differs from the any-colour answer; E3 sets have spares; E5 differs from E4 |
| W 40 | W1 #sets with part (8), W2 #sets with part x colour (8), W3 sets with minifig (list, 8), W4 W2 restricted to a year (6), W5 sets with part x colour (list, 6), X3 (4, OOC) | some users reached only through nesting/minifigs; colour answers differ from any-colour answers |
| A 46 | A1 #sets in theme subtree x year (8), A2 minifigs (7), A3 parts (7), A4 top-3 top-level themes by #sets (6), A5 part x colour qty over a year (7), A6 distinct colours in theme subtree x year (7), X2 (4, OOC) | answer changes when sub-themes are ignored (A1-A3, A6) / when nesting is ignored (A5); A4 top-3 untied and different from the top-3 direct themes |
| V 38 | V1 part qty in v1 (8), V2 total parts in v1 (8), V3 distinct parts in v1 (7), V4 minifigs in v1 (6), V5 total parts in an intermediate version k (5), X1 (4, OOC) | version answer differs from the current-version answer |

Out of S3 coverage (16 = 8.4%, `s3_covered = false`): X1 set difference between versions, X2 average per set,
X3 conjunction of two where-used conditions, X4 percentage from nested contents.
Candidate pools are small for V4 (7 sets whose minifig count differs between v1 and current) and V5 (8 sets with
an intermediate version whose total differs from current); every other template draws from hundreds of candidates.

## Gold verification (`check_gold.py`)

* S3 oracle (hand-written semantic-layer JSON per template, compiled by `semantic.py`, run on `lego.duckdb`):
  **174/174 covered questions agree** with gold.py; coverage 174/190 (91.6%).
* Independent hand-written SQL on `lego_u.duckdb` (templates A1, E3, E6, E7, V2, V4, V5, W3): **53/53 agree**.
* Independent hand-written Cypher on `lego_u.kuzu` (templates A1, E3, E6, V2, V4, V5, W3; current version expressed as a
  correlated `NOT EXISTS` on a higher-version edge, four fixed-depth patterns summed): **48/48 agree**.
* Matching uses `code/score.py` unchanged.

## Plugging into a runner

`prompts.py` exports the xb interface (`RULES, SQL_DOC, SQL_U_DOC, CYPHER_DOC, CYPHER_U_DOC, TASK, messages, repair`)
and loads `semantic.py` by file path (module name `lego_semantic`), so it never picks up `code/semantic.py`.
A runner should load both modules by path (e.g. `importlib.util.spec_from_file_location`) rather than via `sys.path`,
because the module names collide with the xb modules; then point the DuckDB connections at `lego.duckdb` / `lego_u.duckdb`
(the S3 compiler targets `lego.duckdb`) and the Kuzu paths at `lego.kuzu` / `lego_u.kuzu`, and read
`data/lego_questions.jsonl`. Only `text` may be shown to a model. `code/run_pilot.py` currently hard-codes the xb
paths and `from prompts import ...`; it needs a `--dataset` switch (not done here: files outside this folder were not edited).

## License note

The data are Rebrickable's public database downloads (rebrickable.com/downloads). **Rebrickable's terms of use could
not be verified automatically: the site returned HTTP 403 to automated requests.** Before redistributing the derived
databases or questions with the paper, check the current Rebrickable terms by hand and cite Rebrickable as the source.
LEGO is a trademark of the LEGO Group, which does not sponsor or endorse this work.
