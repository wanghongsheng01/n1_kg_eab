"""Independent reference implementation (pure Python over in-memory tables) used to compute LEGO gold answers.

It shares no code with the semantic-layer compiler (semantic.py) or with any LLM prompt (prompts.py).
Semantics are those stated in prompts.RULES: current inventory = highest version; a named version applies to the
asked set only; nested sets and minifigs use their own current inventory; quantities multiply along nesting;
spares excluded unless asked.
"""
from collections import defaultdict
import duckdb


class World:
    def __init__(self, path):
        con = duckdb.connect(path, read_only=True)
        f = lambda q: con.execute(q).fetchall()
        self.sets = {r[0]: dict(name=r[1], year=r[2], theme=r[3], num_parts=r[4])
                     for r in f("SELECT set_num, name, year, theme_id, num_parts FROM sets")}
        self.themes = {r[0]: dict(name=r[1], parent=r[2]) for r in f("SELECT id, name, parent_id FROM themes")}
        self.figs = {r[0]: dict(name=r[1], num_parts=r[2]) for r in f("SELECT fig_num, name, num_parts FROM minifigs")}
        self.parts = {r[0]: dict(name=r[1], cat=r[2]) for r in f("SELECT part_num, name, part_cat_id FROM parts")}
        self.cats = dict(f("SELECT id, name FROM part_categories"))
        self.colors = dict(f("SELECT id, name FROM colors"))
        self.color_id = {v: k for k, v in self.colors.items()}
        self.inv = defaultdict(dict)                       # set_num/fig_num -> {version: inventory_id}
        for i, v, s in f("SELECT id, version, set_num FROM inventories"):
            self.inv[s][v] = i
        self.inv_parts = defaultdict(list)                 # inventory_id -> [(part_num, color_id, qty, is_spare)]
        for i, p, c, q, sp in f("SELECT inventory_id, part_num, color_id, quantity, is_spare FROM inventory_parts"):
            self.inv_parts[i].append((p, c, q, sp))
        self.inv_sets = defaultdict(list)                  # inventory_id -> [(set_num, qty)]
        for i, s, q in f("SELECT inventory_id, set_num, quantity FROM inventory_sets"):
            self.inv_sets[i].append((s, q))
        self.inv_figs = defaultdict(list)                  # inventory_id -> [(fig_num, qty)]
        for i, s, q in f("SELECT inventory_id, fig_num, quantity FROM inventory_minifigs"):
            self.inv_figs[i].append((s, q))
        con.close()
        self._memo = {}

    # inventories ---------------------------------------------------------
    def versions(self, num):
        return sorted(self.inv.get(num, {}))

    def inv_id(self, num, version=None):
        vs = self.inv.get(num)
        if not vs:
            return None
        return vs[max(vs)] if version is None else vs.get(version)

    # explosion -----------------------------------------------------------
    def explode(self, num, version=None, spares=False, _stack=()):
        """{(part_num, color_id): qty} for one unit of set/minifig `num` (all levels)."""
        key = ("x", num, version, spares)
        if key in self._memo:
            return self._memo[key]
        if num in _stack:
            raise RuntimeError(f"cycle at {num}")
        out = defaultdict(int)
        i = self.inv_id(num, version)
        if i is not None:
            for p, c, q, sp in self.inv_parts[i]:
                if spares or not sp:
                    out[(p, c)] += q
            for child, q in self.inv_sets[i] + self.inv_figs[i]:
                for k, qq in self.explode(child, None, spares, _stack + (num,)).items():
                    out[k] += q * qq
        self._memo[key] = dict(out)
        return self._memo[key]

    def own_parts(self, num, version=None, spares=False):
        """First-level parts of the inventory only (no nested sets, no minifigs)."""
        out = defaultdict(int)
        i = self.inv_id(num, version)
        if i is not None:
            for p, c, q, sp in self.inv_parts[i]:
                if spares or not sp:
                    out[(p, c)] += q
        return dict(out)

    def minifigs(self, num, version=None, _stack=()):
        """{fig_num: qty} for one unit of set `num`, through nested sets."""
        key = ("f", num, version)
        if key in self._memo:
            return self._memo[key]
        if num in _stack:
            raise RuntimeError(f"cycle at {num}")
        out = defaultdict(int)
        i = self.inv_id(num, version)
        if i is not None:
            for fg, q in self.inv_figs[i]:
                out[fg] += q
            for s, q in self.inv_sets[i]:
                for fg, qq in self.minifigs(s, None, _stack + (num,)).items():
                    out[fg] += q * qq
        self._memo[key] = dict(out)
        return self._memo[key]

    def nested_sets(self, num, version=None):
        """{set_num: qty} of all sets nested at any depth in one unit of `num`."""
        out = defaultdict(int)
        i = self.inv_id(num, version)
        if i is not None:
            for s, q in self.inv_sets[i]:
                out[s] += q
                for ss, qq in self.nested_sets(s).items():
                    out[ss] += q * qq
        return dict(out)

    def direct(self, num, version=None):
        i = self.inv_id(num, version)
        if i is None:
            return [], [], []
        return self.inv_parts[i], self.inv_sets[i], self.inv_figs[i]

    @staticmethod
    def by_part(ex):
        out = defaultdict(int)
        for (p, c), q in ex.items():
            out[p] += q
        return dict(out)

    # themes --------------------------------------------------------------
    def ancestors(self, tid):
        out = []
        while tid is not None:
            out.append(tid)
            tid = self.themes[tid]["parent"]
        return out

    def in_subtree(self, set_num, tid):
        return tid in self.ancestors(self.sets[set_num]["theme"])

    def root_theme(self, set_num):
        return self.ancestors(self.sets[set_num]["theme"])[-1]

    # where-used ----------------------------------------------------------
    def users_part(self, part, color=None, spares=False):
        """{set_num: qty} of sets whose current exploded contents include the part (optionally in one colour)."""
        out = {}
        for s in self.sets:
            ex = self.explode(s, None, spares)
            q = sum(v for (p, c), v in ex.items() if p == part and (color is None or c == color))
            if q:
                out[s] = q
        return out

    def users_part_direct(self, part, color=None, spares=False):
        out = {}
        for s in self.sets:
            ex = self.own_parts(s, None, spares)
            q = sum(v for (p, c), v in ex.items() if p == part and (color is None or c == color))
            if q:
                out[s] = q
        return out

    def users_fig(self, fig):
        return {s: self.minifigs(s)[fig] for s in self.sets if fig in self.minifigs(s)}
