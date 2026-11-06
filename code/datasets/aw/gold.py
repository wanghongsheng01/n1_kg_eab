"""Independent reference implementation for AW (pure Python over in-memory tables) used to compute gold answers.

It shares no code with the semantic-layer compiler (semantic.py) or with any LLM prompt.
Definitions (identical to prompts.RULES):
- composition edges are bill_of_materials rows with assembly_id NOT NULL (header rows are ignored);
- an edge is valid on d iff start_date <= d and (end_date IS NULL or d <= end_date);
- quantities multiply along a path and sum over paths; a "part" is a purchased product (make_flag = false);
- revenue = sum(line_total); demand for a component = units sold directly + units contained at any level of the
  products sold, exploded with the BOM valid on the order date.
"""
import datetime as dt
from collections import defaultdict
import duckdb

TODAY = dt.date(2025, 6, 30)


class World:
    def __init__(self, path):
        con = duckdb.connect(path, read_only=True)
        f = lambda q: con.execute(q).fetchall()
        self.cat = dict(f("SELECT category_id, name FROM product_category"))
        self.sub = {r[0]: dict(name=r[2], cat=r[1]) for r in f("SELECT subcategory_id, category_id, name FROM product_subcategory")}
        self.prod = {}
        for pid, num, name, color, mk, fg, cost, price, line, sc in f(
                "SELECT product_id, product_number, name, color, make_flag, is_finished_good, standard_cost, list_price, "
                "product_line, subcategory_id FROM product"):
            self.prod[pid] = dict(num=num, name=name, color=color, make=mk, fg=fg, cost=cost, price=price, line=line,
                                  sub=self.sub[sc]["name"] if sc else None,
                                  cat=self.cat[self.sub[sc]["cat"]] if sc else None)
        self.by_num = {v["num"]: k for k, v in self.prod.items()}
        self.bom = defaultdict(list)          # assembly -> [(child, qty, vf, vt)]
        self.rows = []
        for a, c, vf, vt, q in f("SELECT assembly_id, component_id, start_date, end_date, per_assembly_qty FROM bill_of_materials"):
            if a is None:
                continue
            self.bom[a].append((c, float(q), vf, vt))
            self.rows.append((a, c, float(q), vf, vt))
        self.terr = {r[0]: dict(name=r[1], country=r[2], group=r[3]) for r in f("SELECT * FROM sales_territory")}
        self.orders = {r[0]: dict(date=r[1], online=r[2], terr=r[3], cust=r[4], due=r[5]) for r in
                       f("SELECT order_id, order_date, online_order, territory_id, customer_id, total_due FROM sales_order")}
        self.lines = [dict(order=r[0], line=r[1], prod=r[2], qty=r[3], price=r[4], disc=r[5], total=r[6]) for r in
                      f("SELECT order_id, line_id, product_id, order_qty, unit_price, unit_discount, line_total FROM sales_order_line")]
        con.close()
        self._memo = {}

    @staticmethod
    def valid(vf, vt, d):
        return vf <= d and (vt is None or d <= vt)

    # composition ----------------------------------------------------------------
    def explode(self, pid, d=TODAY, max_depth=None, ignore_validity=False):
        """{product_id: qty} for everything strictly below ONE unit of pid (all levels unless max_depth)."""
        key = (pid, d, max_depth, ignore_validity)
        if key in self._memo:
            return self._memo[key]
        out = defaultdict(float)
        for c, q, a, b in self.bom.get(pid, []):
            if ignore_validity or self.valid(a, b, d):
                out[c] += q
                if max_depth != 1:
                    for cc, qq in self.explode(c, d, None if max_depth is None else max_depth - 1, ignore_validity).items():
                        out[cc] += q * qq
        self._memo[key] = dict(out)
        return self._memo[key]

    def depth(self, pid, d=TODAY):
        ch = [c for c, q, a, b in self.bom.get(pid, []) if self.valid(a, b, d)]
        return 0 if not ch else 1 + max(self.depth(c, d) for c in ch)

    def parts(self, ex):
        return {c: q for c, q in ex.items() if not self.prod[c]["make"]}

    def purchased_cost(self, pid, d=TODAY, max_depth=None):
        return sum(q * self.prod[c]["cost"] for c, q in self.parts(self.explode(pid, d, max_depth)).items())

    def assemblies(self):
        return sorted(self.bom)

    def users(self, cid, d=TODAY, pred=lambda p: True):
        """{product_id: qty of cid per unit} over every product containing cid at any level on d."""
        out = {}
        for a in self.assemblies():
            q = self.explode(a, d).get(cid)
            if q and pred(self.prod[a]):
                out[a] = q
        return out

    # orders ---------------------------------------------------------------------
    def order_lines(self, pred=lambda o, ln: True):
        for ln in self.lines:
            o = self.orders[ln["order"]]
            if pred(o, ln):
                yield o, ln

    def demand(self, pred, ignore_validity=False, include_direct=True):
        """{product_id: units} required by the matching order lines (direct + embedded, BOM valid on order date)."""
        agg = defaultdict(float)
        for o, ln in self.order_lines(pred):
            agg[(ln["prod"], o["date"])] += ln["qty"]
        out = defaultdict(float)
        for (p, d), q in agg.items():
            if include_direct:
                out[p] += q
            for c, qq in self.explode(p, d, None, ignore_validity).items():
                out[c] += q * qq
        return dict(out)
