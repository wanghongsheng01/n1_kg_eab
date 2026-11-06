"""Independent reference implementation (pure Python over in-memory tables) used to compute gold answers.

It shares no code with the semantic-layer compiler or with any LLM prompt, so a bug in either cannot leak into gold.
"""
import datetime as dt
from collections import defaultdict
import duckdb

TODAY = dt.date(2026, 10, 1)


class World:
    def __init__(self, path):
        con = duckdb.connect(path, read_only=True)
        f = lambda q: con.execute(q).fetchall()
        self.comp = {r[0]: dict(code=r[1], name=r[2], cat=r[3], asm=r[4], cost=r[5]) for r in f("SELECT * FROM component")}
        self.comp_by_code = {v["code"]: k for k, v in self.comp.items()}
        self.model = {r[0]: dict(code=r[1], name=r[2], line=r[3]) for r in f("SELECT * FROM company_model")}
        self.model_by_code = {v["code"]: k for k, v in self.model.items()}
        self.sku = {r[0]: dict(code=r[1], title=r[2], market=r[3], bundle=r[4]) for r in f("SELECT * FROM sales_sku")}
        self.sku_by_code = {v["code"]: k for k, v in self.sku.items()}
        self.store = {r[0]: dict(name=r[1], platform=r[2], country=r[3]) for r in f("SELECT * FROM store")}
        self.cbom = defaultdict(list)   # parent -> [(child, qty, vf, vt)]
        for p, c, q, a, b in f("SELECT * FROM component_bom"):
            self.cbom[p].append((c, q, a, b))
        self.mbom = defaultdict(list)   # model -> [(comp, qty, vf, vt)]
        for m, c, q, a, b in f("SELECT * FROM model_bom"):
            self.mbom[m].append((c, q, a, b))
        self.sku_model = defaultdict(list)
        for s, m, q in f("SELECT * FROM sku_model"):
            self.sku_model[s].append((m, q))
        self.bundle = defaultdict(list)
        for b, s, q in f("SELECT * FROM bundle_item"):
            self.bundle[b].append((s, q))
        self.listing = {r[0]: dict(store=r[1], sku=r[2], ext=r[3]) for r in f("SELECT * FROM listing")}
        self.orders = {r[0]: dict(store=r[1], date=r[2], status=r[3]) for r in f("SELECT * FROM orders")}
        self.lines = [dict(order=r[0], line=r[1], listing=r[2], qty=r[3], price=r[4]) for r in f("SELECT * FROM order_line")]
        con.close()
        self._memo = {}

    @staticmethod
    def valid(vf, vt, d):
        return vf <= d and (vt is None or d <= vt)

    # composition ---------------------------------------------------------
    def explode_component(self, cid, d):
        """{component_id: qty} for everything strictly below one unit of component cid on date d."""
        key = (cid, d)
        if key in self._memo:
            return self._memo[key]
        out = defaultdict(float)
        for c, q, a, b in self.cbom.get(cid, []):
            if self.valid(a, b, d):
                out[c] += q
                for cc, qq in self.explode_component(c, d).items():
                    out[cc] += q * qq
        self._memo[key] = dict(out)
        return self._memo[key]

    def explode_model(self, mid, d=TODAY, max_depth=None):
        out = defaultdict(float)
        for c, q, a, b in self.mbom.get(mid, []):
            if self.valid(a, b, d):
                out[c] += q
                if max_depth != 1:
                    for cc, qq in self.explode_component(c, d).items():
                        out[cc] += q * qq
        return dict(out)

    def sku_models(self, sid):
        """{model_id: model units per one unit of sku sid}; bundles are expanded."""
        out = defaultdict(float)
        if self.sku[sid]["bundle"]:
            for s, q in self.bundle[sid]:
                for m, qq in self.sku_model[s]:
                    out[m] += q * qq
        else:
            for m, q in self.sku_model[sid]:
                out[m] += q
        return dict(out)

    def explode_sku(self, sid, d=TODAY):
        out = defaultdict(float)
        for m, q in self.sku_models(sid).items():
            for c, qq in self.explode_model(m, d).items():
                out[c] += q * qq
        return dict(out)

    def parts(self, ex):
        return {c: q for c, q in ex.items() if not self.comp[c]["asm"]}

    # orders --------------------------------------------------------------
    def completed_lines(self, pred=lambda o, st, ln: True):
        for ln in self.lines:
            o = self.orders[ln["order"]]
            if o["status"] != "completed":
                continue
            st = self.store[o["store"]]
            if pred(o, st, ln):
                yield o, st, ln
