"""Cached OpenAI-compatible client for the N1 pilot (copied from ICDE2027/code/llm.py and extended).

Extensions: every call records latency, tokens and list-price dollars; the cache key includes a repeat index `rep`;
a per-model daily spend guard (this session's share of the shared $20/model/day cap). Keys are read from the shared
file at run time and never copied. Only synthetic/public benchmark text is sent to the vendors.
"""
import hashlib, json, os, time, threading, datetime
import requests

ROOT = os.environ.get("N1_KEYS_ROOT", ".")
HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
KEYS = json.load(open(os.path.join(ROOT, ".secrets", "llm_keys.json")))
CACHE_DIR = os.path.join(HERE, "cache")
LOG = os.path.join(CACHE_DIR, "usage.log")
os.makedirs(CACHE_DIR, exist_ok=True)

MODELS = {"ds-flash": ("deepseek", "deepseek-flash"), "ds-pro": ("deepseek", "deepseek-v4-pro"),
          "glm-flash": ("glm", "glm-4.5-flash"), "glm-air": ("glm2", "glm-4.5-air"), "kimi-k2.6": ("kimi", "kimi-k2.6"),
          "gpt-6-sol": ("llmhub", "gpt-6-sol"), "claude-opus-5.5": ("llmhub", "claude-opus-5-5"),
          "qwen3.8-27b": ("llmhub", "Qwen3.8-27B"),
          "ds-flash-think": ("deepseek", "deepseek-flash"),  # review round: DS-Flash with thinking enabled
          "ds-pro-think": ("deepseek", "deepseek-v4-pro"), "kimi-think": ("kimi", "kimi-k2.6"),
          "qwen-think": ("llmhub", "Qwen3.8-27B"), "glm-think": ("llmhub", "glm-5.3"),
          "kimi3-think": ("ark", "kimi-k3-1-pro-261024")}  # Kimi K3.1 Pro on Volcengine Ark (a newer model, not Kimi-K2.6)  # thinking switched on through the gateway's extra_body
OFF = {"thinking": {"type": "disabled"}}
FIXED = {"ds-flash": OFF, "ds-pro": OFF, "glm-flash": OFF, "glm-air": OFF, "kimi-k2.6": {**OFF, "temperature": 0.6},
         "claude-opus-5.5": {"temperature": None}, "ds-flash-think": {"thinking": {"type": "enabled"}},
         "ds-pro-think": {"thinking": {"type": "enabled"}}, "kimi-think": {"thinking": {"type": "enabled"}, "temperature": 1.0},
         "qwen-think": {"extra_body": {"chat_template_kwargs": {"enable_thinking": True}}, "temperature": 0.6},
         "glm-think": {"thinking": {"type": "enabled"}, "temperature": 0.6},
         "kimi3-think": {"thinking": {"type": "enabled"}, "temperature": 1.0}}  # the gateway's Claude endpoint rejects the temperature parameter
# USD per 1M tokens (input, output); list prices used by the sibling sessions on 2026-11-03
PRICES = {"glm-flash": (0.0, 0.0), "glm-air": (0.20, 1.10), "ds-flash": (0.30, 1.20), "ds-pro": (1.32, 3.96),
          "kimi-k2.6": (0.95, 4.00), "gpt-6-sol": (0.0, 0.0), "claude-opus-5.5": (0.0, 0.0),
          "qwen3.8-27b": (0.0, 0.0), "ds-flash-think": (0.30, 1.20), "ds-pro-think": (1.32, 3.96), "kimi-think": (0.95, 4.00), "qwen-think": (0.0, 0.0), "glm-think": (0.0, 0.0), "kimi3-think": (0.0, 0.0)}
# gpt-6-sol and claude-opus-5.5 are served through the organisation's OpenAI-compatible gateway (no list price recorded;
# tokens are reported instead); its endpoint and key come only from LLMHUB_BASE_URL / LLMHUB_API_KEY, never from disk
DAILY_CAP = float(os.environ.get("DAILY_CAP", 6.0))   # this session's share
SHARED_CAP = float(os.environ.get("SHARED_CAP", 20.0))  # per model per day, shared by all sessions
CAP_MARGIN = float(os.environ.get("CAP_MARGIN", 1.0))   # head-room for requests already in flight
_lock = threading.Lock()
_spent = {}


def cost(model, usage):
    pi, po = PRICES[model]
    return (usage.get("prompt_tokens", 0) * pi + usage.get("completion_tokens", 0) * po) / 1e6


_others = {}  # today's spend of the other sessions' logs, read once at import
_today0 = datetime.date.today().isoformat()
for _f in __import__("glob").glob(os.path.join(ROOT, "**", "usage.log"), recursive=True):
    if os.path.abspath(_f) == os.path.abspath(LOG):
        continue
    try:
        for _l in open(_f):
            try:
                _d = json.loads(_l)
            except ValueError:
                continue
            if _d.get("day") == _today0:
                _others[_d.get("m")] = _others.get(_d.get("m"), 0) + _d.get("usd", 0)
    except OSError:
        pass

if os.path.exists(LOG):
    _today = datetime.date.today().isoformat()
    for _l in open(LOG):
        _d = json.loads(_l)
        if _d.get("day") == _today:
            _spent[_d["m"]] = _spent.get(_d["m"], 0) + _d.get("usd", 0)


def chat(model, messages, temperature=0.0, max_tokens=1500, rep=0, retries=8):
    """Returns dict(text, usage, usd, latency, status, cached)."""
    prov, name = MODELS[model]
    # truncation retry (retry_truncated.py): raises the output cap of every call in the re-run
    max_tokens = max(max_tokens, int(os.environ.get("N1_MIN_MAX_TOKENS", 0)))
    kw = {k: v for k, v in FIXED.get(model, {}).items() if k != "temperature"}
    temperature = FIXED.get(model, {}).get("temperature", temperature)
    body = {"model": name, "messages": messages, "temperature": temperature, "max_tokens": max_tokens, **kw}
    if temperature is None:
        del body["temperature"]
    key = hashlib.sha256(json.dumps([prov, body, rep], sort_keys=True).encode()).hexdigest()
    path = os.path.join(CACHE_DIR, key[:2], key + ".json")
    if os.path.exists(path):
        return {**json.load(open(path)), "cached": True}
    if (_spent.get(model, 0) > DAILY_CAP - CAP_MARGIN
            or _spent.get(model, 0) + _others.get(model, 0) > SHARED_CAP - CAP_MARGIN):
        raise RuntimeError(f"daily cap reached for {model}")
    send = body
    if prov == "llmhub":
        cfg, err = {"base_url": os.environ["LLMHUB_BASE_URL"], "api_key": os.environ["LLMHUB_API_KEY"]}, None
    elif prov == "ark":   # Volcengine Ark; endpoint and key only from ARK_BASE_URL / ARK_API_KEY (read from the Keychain at launch)
        cfg, err = {"base_url": os.environ["ARK_BASE_URL"], "api_key": os.environ["ARK_API_KEY"]}, None
    elif prov == "kimi" and os.environ.get("N1_KIMI_GATEWAY") == "1":
        # the same Kimi-K2.6 model served through the organisation's gateway (used after the vendor account ran out of
        # balance on 2026-11-10); the request is unchanged except for the gateway's model id, so the cache key is the same
        cfg, err = {"base_url": os.environ["LLMHUB_BASE_URL"], "api_key": os.environ["LLMHUB_API_KEY"]}, None
        send = {**body, "model": "Kimi-K2.6"}
    else:
        cfg, err = KEYS[prov], None
    for i in range(retries):
        try:
            t0 = time.time()
            r = requests.post(cfg["base_url"].rstrip("/") + "/chat/completions",
                              headers={"Authorization": "Bearer " + cfg["api_key"]}, json=send, timeout=float(os.environ.get("N1_HTTP_TIMEOUT", 180)))
            lat = time.time() - t0
            if r.status_code == 200:
                d = r.json()
                u = d.get("usage") or {}
                out = {"text": d["choices"][0]["message"]["content"] or "", "usage": u, "usd": cost(model, u),
                       "latency": lat, "status": "ok", "finish": d["choices"][0].get("finish_reason")}
                if send is not body:
                    out["endpoint"] = "gateway"
            else:
                err = f"{r.status_code} {r.text[:200]}"
                time.sleep(min(60, 2 ** i) + (5 if r.status_code == 429 else 0))
                continue
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with _lock:
                json.dump(out, open(path, "w"))
                _spent[model] = _spent.get(model, 0) + out["usd"]
                with open(LOG, "a") as f:
                    f.write(json.dumps({"day": datetime.date.today().isoformat(), "t": time.time(), "m": model,
                                        "usd": out["usd"], "lat": lat, **u}) + "\n")
            return {**out, "cached": False}
        except requests.RequestException as e:
            err = repr(e)
            time.sleep(min(60, 2 ** i))
    raise RuntimeError(err)
