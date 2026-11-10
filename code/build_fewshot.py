"""Few-shot condition (review): worked examples for SQL, SQL-U, Graph-T and Graph-U, drawn from the frozen fresh question set.

A candidate is a fresh question for which every base substrate has a run whose answer is correct; its query is the final
attempt of that run (DS-V4-Pro first, then DS-Flash, then Kimi). Candidates are shuffled with a fixed seed and picked
greedily with distinct templates and distinct question classes. run_pilot.py (substrates <base>_fs) shows each test
question the first three examples whose template differs from its own, so no example shares the test template and every
substrate sees the same three questions.

Usage: python build_fewshot.py [xb aw lego]  -> data/<ds>_fewshot.json
"""
import json, os, random, re, sys

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASES = {"sql": "sql", "sql_u": "sql", "cypher": "cypher", "cypher_u": "cypher"}
ORDER = ["ds-pro", "ds-flash", "kimi-k2.6"]


def extract(text, lang):
    m = re.findall(r"```(?:" + lang + r")?\s*\n(.*?)```", text, re.S | re.I)
    if m:
        return m[-1].strip()
    m = re.findall(r"```\w*\s*\n(.*?)```", text, re.S)
    return (m[-1] if m else text).strip()


def build(ds, n=4, seed=20261109):
    qs = {json.loads(l)["qid"]: json.loads(l) for l in open(os.path.join(HERE, "data", f"{ds}_questions_fresh.jsonl"))}
    best = {}
    for f in (f"main_{ds}_fresh.jsonl", f"main_{ds}_fresh2.jsonl"):
        for l in open(os.path.join(HERE, "results", f)):
            r = json.loads(l)
            if r["substrate"] in BASES and r["correct"] and r.get("rep", 0) == 0:
                k = (r["qid"], r["substrate"])
                if k not in best or ORDER.index(r["model"]) < ORDER.index(best[k]["model"]):
                    best[k] = r
    cand = sorted(q for q in qs if all((q, s) in best for s in BASES))
    random.Random(seed).shuffle(cand)
    picked, tpls, cls = [], set(), set()
    for q in cand:
        if qs[q]["tpl"] not in tpls and qs[q]["cls"] not in cls:
            picked.append(q); tpls.add(qs[q]["tpl"]); cls.add(qs[q]["cls"])
        if len(picked) == n:
            break
    demos = [{"qid": q, "tpl": qs[q]["tpl"], "cls": qs[q]["cls"], "text": qs[q]["text"],
              "queries": {s: extract(best[(q, s)]["attempts"][-1]["text"], BASES[s]) for s in BASES},
              "from_model": {s: best[(q, s)]["model"] for s in BASES}} for q in picked]
    json.dump({"seed": seed, "candidates": len(cand), "demos": demos},
              open(os.path.join(HERE, "data", f"{ds}_fewshot.json"), "w"), indent=1)
    print(ds, "candidates", len(cand), "picked", [(d["qid"], d["cls"]) for d in demos])


if __name__ == "__main__":
    for ds in sys.argv[1:] or ["xb", "aw", "lego"]:
        build(ds)
