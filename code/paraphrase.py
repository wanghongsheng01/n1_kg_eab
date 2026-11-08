"""Paraphrase the benchmark questions (robustness variant of the question set).

For every question in data/{xb,aw,lego}_questions.jsonl:
  1. generator gpt-6-sol (temperature 0.7, rep = attempt index) rewrites the question in a business-user style
     (style assigned deterministically per question); it never sees the gold answer;
  2. a code check requires every identifier / number / date / quoted name / colour / territory etc. of the original
     to appear verbatim in the paraphrase;
  3. an independent verifier claude-opus-5.5 judges whether meaning and answer are exactly the same.
Up to 3 attempts (rep 0, 1, 2); a retry is told why the previous attempt was rejected. If all attempts fail the
original text is kept (para_model = null, verifier_ok = false).

Outputs data/{ds}_questions_para.jsonl and results/paraphrase_stats.json. Resumable: finished questions are stored in
cache/paraphrase_progress.jsonl and all LLM calls are cached by code/llm.py.

Run (endpoint and key only via environment):
  LLMHUB_BASE_URL=... LLMHUB_API_KEY=... python -W ignore code/paraphrase.py [--workers 6] [--limit N]
"""
import argparse, hashlib, json, os, random, re, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import llm  # noqa: E402

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA, RES = os.path.join(HERE, "data"), os.path.join(HERE, "results")
PROG = os.path.join(HERE, "cache", "paraphrase_progress.jsonl")
DATASETS = ["xb", "aw", "lego"]
GEN, VER = "gpt-6-sol", "claude-opus-5.5"
MAX_ATTEMPTS = 3

DOMAIN = {
    "xb": "an office-furniture manufacturer's ERP: sales SKUs (incl. bundle SKUs) map to company models, company models "
          "have a bill of materials (BOM) of components (parts and sub-assemblies), orders are placed on platforms/stores",
    "aw": "the AdventureWorks bicycle company: products (bikes, sub-assemblies, purchased parts) with a bill of materials "
          "(BOM), subcategories, standard costs, list prices, and sales orders in sales territories",
    "lego": "the LEGO catalogue (Rebrickable): sets with versioned inventories that list parts (with colours), minifigs "
            "and nested sets; sets belong to themes with sub-themes",
}
STYLES = [
    "an imperative request (e.g. starting with 'Give me', 'Tell me', 'Find', 'List', 'Show')",
    "a direct question with a different sentence structure from the original",
    "informal, conversational phrasing such as a busy business user would type into a chat box",
    "a question that moves the conditions (time, scope, BOM version, location) to the front of the sentence",
    "a polite request ('Could you ...', 'Can you ...', 'I need to know ...')",
    "a short, terse request a user might type quickly, still a complete and unambiguous sentence",
]

GEN_PROMPT = """You rewrite questions for a business-data question-answering benchmark. Domain: {domain}.

Rewrite the ORIGINAL question as a paraphrase that a business user might type. Required style: {style}.

Rules:
- Use different wording and sentence structure from the original (synonyms are welcome where they are exact, e.g. "bill of materials" / "BOM", "made of", "goes into", "how many" / "what is the number of").
- The meaning and the correct answer must be EXACTLY the same: keep every condition and every scope qualifier (e.g. current vs. dated BOM/inventory version, directly/first level only vs. at any level, included or excluded items, completed orders, colour constraints, time period, counting distinct items vs. summing quantities, what to return, rounding).
- Copy every code / identifier, number, date, version number, year, quarter, colour name, quoted name, platform/store/country/territory name and unit VERBATIM (same characters, same case; write numbers as digits exactly as in the original, e.g. keep "2 decimals" as the digit 2, keep "3" as 3).
- Do not add any constraint, do not drop any constraint, do not add hints about how to compute the answer, do not answer the question.
- Output only JSON: {{"paraphrase": "..."}}

ORIGINAL: {text}"""

RETRY_NOTE = "\n\nA previous rewrite was rejected: {why}\nRejected rewrite: {prev}\nWrite a new rewrite that fixes this."

VER_PROMPT = """You are a strict reviewer for a question-answering benchmark over {domain}.

Decide whether the PARAPHRASE has EXACTLY the same meaning as the ORIGINAL, i.e. any correct answer to one is a correct answer to the other, with the same expected output (what to return, rounding). Answer false if the paraphrase adds, drops, weakens or changes any condition (identifiers, numbers, dates, versions, time periods, current vs. dated BOM/inventory, direct/first level vs. any level, included/excluded items, colour, counting distinct vs. summing, order status, location), if it is ambiguous where the original is not, or if it gives hints on how to compute the answer that the original does not give. Differences in wording, tone and sentence structure are fine.

ORIGINAL: {orig}
PARAPHRASE: {para}

Output only JSON: {{"same_meaning": true or false, "reason": "<one sentence>"}}"""

# ---------------------------------------------------------------- verbatim-token extraction
_NAMED = re.compile(r"\b(?:platform|country|territory|store|colour|color)\s+([A-Z][\w-]*(?:\s+[A-Z][\w-]*)*)")
_QUOTED = re.compile(r"'([^']+)'")
_TOKEN = re.compile(r"[A-Za-z0-9_]+(?:[-.][A-Za-z0-9_]+)*")


def required_tokens(text):
    """Tokens of the original that must appear verbatim in the paraphrase (each must occur at least once)."""
    toks = [m.group(1) for m in _QUOTED.finditer(text)]                    # quoted names: 'Road-650 Red, 44'
    toks += [m.group(1) for m in _NAMED.finditer(text)                    # colours, territories, platforms, stores
             if not re.search(r"\d", m.group(1))]                          # (names with digits come from the codes)
    rest = _QUOTED.sub(" ", text)
    for m in _TOKEN.finditer(rest):                                       # codes, numbers, dates, Q4, versions
        t = m.group(0)
        if re.search(r"\d", t):
            toks.append(t)
        elif t == "make_flag":
            toks.append(t)
    return toks


def _count(tok, text):
    return len(re.findall(r"(?<![\w-])" + re.escape(tok) + r"(?![\w]|-[\w])", text))


def missing_tokens(orig, para):
    return [t for t in dict.fromkeys(required_tokens(orig)) if _count(t, para) < 1]


# ---------------------------------------------------------------- LLM helpers
def parse_json(s):
    s = s.strip()
    m = re.search(r"\{.*\}", s, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except ValueError:
        return None


def style_for(qid):
    return STYLES[int(hashlib.md5(qid.encode()).hexdigest(), 16) % len(STYLES)]


def process(ds, q):
    orig, attempts, why, prev = q["text"], [], None, None
    for k in range(MAX_ATTEMPTS):
        p = GEN_PROMPT.format(domain=DOMAIN[ds], style=style_for(q["qid"]), text=orig)
        if why:
            p += RETRY_NOTE.format(why=why, prev=prev)
        r = llm.chat(GEN, [{"role": "user", "content": p}], temperature=0.7, max_tokens=400, rep=k)
        js = parse_json(r["text"]) or {}
        para = " ".join(str(js.get("paraphrase", "")).split())
        rec = {"rep": k, "paraphrase": para}
        if not para:
            why, prev = "the output was not valid JSON with a non-empty \"paraphrase\" field", r["text"][:300]
            rec["reject"] = "bad_json"
            attempts.append(rec)
            continue
        miss = missing_tokens(orig, para)
        if miss:
            why, prev = ("these items of the original are missing or not copied verbatim: "
                         + ", ".join(miss)), para
            rec["reject"] = "tokens:" + "|".join(miss)
            attempts.append(rec)
            continue
        v = llm.chat(VER, [{"role": "user", "content": VER_PROMPT.format(domain=DOMAIN[ds], orig=orig, para=para)}],
                     max_tokens=300, rep=k)
        vj = parse_json(v["text"]) or {}
        rec["verifier"] = vj
        if vj.get("same_meaning") is True:
            attempts.append(rec)
            return {"qid": q["qid"], "ds": ds, "para": para, "ok": True, "n": k + 1, "attempts": attempts}
        why, prev = "a reviewer found the meaning differs: " + str(vj.get("reason", "unparseable verdict")), para
        rec["reject"] = "verifier"
        attempts.append(rec)
    return {"qid": q["qid"], "ds": ds, "para": orig, "ok": False, "n": MAX_ATTEMPTS, "attempts": attempts}


# ---------------------------------------------------------------- statistics
def lev(a, b):
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def words(s):
    return re.findall(r"[a-z0-9]+(?:[-'][a-z0-9]+)*", s.lower())


def pair_stats(o, p):
    wo, wp = set(words(o)), set(words(p))
    return {"char_lev": lev(o, p), "char_lev_norm": lev(o, p) / max(len(o), len(p)),
            "word_lev_norm": lev(words(o), words(p)) / max(len(words(o)), len(words(p))),
            "jaccard_dist": 1 - len(wo & wp) / len(wo | wp)}


def mean(xs):
    return round(sum(xs) / len(xs), 4) if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="per dataset, for a smoke test (no outputs written)")
    a = ap.parse_args()
    qs = {ds: [json.loads(l) for l in open(os.path.join(DATA, f"{ds}_questions.jsonl"))] for ds in DATASETS}
    done = {}
    if os.path.exists(PROG):
        for l in open(PROG):
            d = json.loads(l)
            done[(d["ds"], d["qid"])] = d
    todo = [(ds, q) for ds in DATASETS for q in (qs[ds][:a.limit] if a.limit else qs[ds])
            if (ds, q["qid"]) not in done]
    print(f"{len(done)} done, {len(todo)} to do", flush=True)
    lock, t0, n = threading.Lock(), time.time(), 0
    with ThreadPoolExecutor(a.workers) as ex, open(PROG, "a") as fp:
        futs = {ex.submit(process, ds, q): (ds, q["qid"]) for ds, q in todo}
        for f in as_completed(futs):
            try:
                d = f.result()
            except Exception as e:  # leave it for the next run
                print("FAIL", futs[f], str(e)[:200], flush=True)
                continue
            with lock:
                fp.write(json.dumps(d) + "\n")
                fp.flush()
                done[(d["ds"], d["qid"])] = d
                n += 1
                if n % 25 == 0:
                    print(f"{n}/{len(todo)} {time.time() - t0:.0f}s", flush=True)
    if a.limit:
        for ds, q in todo:
            d = done.get((ds, q["qid"]))
            if d:
                print(ds, q["qid"], d["ok"], d["n"], "\n  O:", q["text"], "\n  P:", d["para"])
        return
    missing = [(ds, q["qid"]) for ds in DATASETS for q in qs[ds] if (ds, q["qid"]) not in done]
    if missing:
        print(f"{len(missing)} questions unfinished; rerun to resume", flush=True)
        return
    stats, rng = {"generator": GEN, "generator_temperature": 0.7, "verifier": VER, "max_attempts": MAX_ATTEMPTS,
                  "edit_distance_note": "char_lev_norm = character Levenshtein / max length; word_lev_norm = word "
                                        "Levenshtein / max word count; jaccard_dist = 1 - Jaccard of lower-cased word "
                                        "sets; 'accepted' excludes fallbacks (identical to the original)"}, \
        random.Random(2027)
    for ds in DATASETS:
        out, ps, acc = [], [], []
        c = {"n": len(qs[ds]), "accepted_first_try": 0, "accepted_after_retries": 0, "fallback_original": 0,
             "rejections": {"bad_json": 0, "tokens": 0, "verifier": 0}}
        for q in qs[ds]:
            d = done[(ds, q["qid"])]
            for at in d["attempts"]:
                if "reject" in at:
                    c["rejections"][at["reject"].split(":")[0]] += 1
            if not d["ok"]:
                c["fallback_original"] += 1
            elif d["n"] == 1:
                c["accepted_first_try"] += 1
            else:
                c["accepted_after_retries"] += 1
            row = {**q, "text": d["para"], "text_orig": q["text"], "para_model": GEN if d["ok"] else None,
                   "para_attempts": d["n"], "verifier_ok": d["ok"]}
            out.append(row)
            st = pair_stats(q["text"], d["para"])
            ps.append(st)
            if d["ok"]:
                acc.append(st)
        with open(os.path.join(DATA, f"{ds}_questions_para.jsonl"), "w") as f:
            for row in out:
                f.write(json.dumps(row, ensure_ascii=False) + "\n")
        for name, sub in [("accepted", acc), ("all", ps)]:
            c[f"mean_{name}"] = {k: mean([s[k] for s in sub]) for k in ["char_lev", "char_lev_norm", "word_lev_norm",
                                                                        "jaccard_dist"]}
        okrows = [r for r in out if r["verifier_ok"]]
        c["examples"] = [{"qid": r["qid"], "tpl": r["tpl"], "original": r["text_orig"], "paraphrase": r["text"],
                          "attempts": r["para_attempts"]} for r in rng.sample(okrows, min(10, len(okrows)))]
        c["fallback_qids"] = [r["qid"] for r in out if not r["verifier_ok"]]
        stats[ds] = c
    tot = {k: sum(stats[ds][k] for ds in DATASETS) for k in ["n", "accepted_first_try", "accepted_after_retries",
                                                             "fallback_original"]}
    stats["total"] = tot
    with open(os.path.join(RES, "paraphrase_stats.json"), "w") as f:
        json.dump(stats, f, indent=1, ensure_ascii=False)
    print(json.dumps({ds: {k: v for k, v in stats[ds].items() if k not in ("examples",)} for ds in DATASETS},
                     indent=1), "\n", tot)


if __name__ == "__main__":
    main()
