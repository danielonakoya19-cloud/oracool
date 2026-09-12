"""OraCool AI — 1,000-core intelligence map (routing engine).

Loads cores.json (built from cores_part1..4.py) and routes a user query to the
most relevant core(s). Supports explicit invocation via @CORE or #CORE, bare
core-name mentions, and keyword scoring over role/example text.
"""
import json
import os
import re

BASE = os.path.dirname(os.path.abspath(__file__))

_CORES = None


def load():
    global _CORES
    if _CORES is None:
        with open(os.path.join(BASE, "cores.json"), encoding="utf-8") as f:
            _CORES = json.load(f)
    return _CORES


def normalize(s):
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def persona(core):
    name = core["name"].replace("_", " ")
    return ("You are now operating as the %s core of OraCool AI — %s. "
            "Example of what this core does: \"%s\". "
            "Adopt this specialized mindset while staying calm, precise and JARVIS-like."
            % (name, core["role"], core["example"]))


def route(query, max_results=3):
    q = (query or "").strip()
    cores = load()
    byid = {str(c["id"]): c for c in cores}
    bynorm = {}
    for c in cores:
        bynorm.setdefault(normalize(c["name"]), c)

    if not q:
        return {"query": q, "cores": [], "explicit": False,
                "hint": "Prefix a message with @CORE (e.g. @SOCRATES) to activate a core."}

    # 1) explicit @CORE / #CORE
    picked = []
    for tok in re.findall(r"[@#]([A-Za-z0-9_]+)", q):
        if tok.isdigit() and tok in byid:
            picked.append(byid[tok])
        else:
            c = bynorm.get(normalize(tok))
            if c:
                picked.append(c)
    # 2) bare core-name mentions
    qwords = set(re.findall(r"[A-Za-z0-9_]+", q.lower()))
    for c in cores:
        nm = normalize(c["name"])
        if len(nm) >= 3 and nm in qwords:
            picked.append(c)

    seen, seen_names, out = set(), set(), []
    for c in picked:
        if c["id"] not in seen and c["name"] not in seen_names:
            seen.add(c["id"]); seen_names.add(c["name"])
            out.append(c)
    if out:
        return {"query": q, "cores": out[:max_results], "explicit": True,
                "personas": [persona(c) for c in out[:max_results]]}

    # 3) keyword scoring
    qn = normalize(q)
    scored = []
    for c in cores:
        score = 0
        for kw in c.get("k", []):
            if kw and kw in qn:
                score += len(kw)
        for w in re.findall(r"[a-z0-9]+", qn):
            if len(w) >= 4 and (w in c["name"].lower() or w in c["role"].lower()):
                score += 3
        if score:
            scored.append((score, c))
    scored.sort(key=lambda t: -t[0])
    out, sn = [], set()
    for _s, c in scored:
        if c["name"] not in sn:
            sn.add(c["name"]); out.append(c)
        if len(out) >= max_results:
            break
    return {"query": q, "cores": out, "explicit": False,
            "personas": [persona(c) for c in out]}


def search(text, limit=50):
    q = normalize(text or "")
    cores = load()
    if not q:
        return cores[:limit]
    hits = []
    for c in cores:
        hay = normalize(c["name"] + " " + c["role"] + " " + c["example"])
        if q in hay:
            hits.append(c)
        if len(hits) >= limit:
            break
    return hits


def clusters():
    cores = load()
    out = {}
    for c in cores:
        out.setdefault(c["cluster"], {"cluster": c["cluster"], "count": 0})
        out[c["cluster"]]["count"] += 1
    return [out[k] for k in sorted(out)]


CLUSTER_NAMES = {
    1: "Core Intelligence",
    2: "Global & Cultural",
    3: "Advanced Science",
    4: "Business & Enterprise",
    5: "Advanced Security",
    6: "Advanced OSINT",
    7: "Autonomous Agents",
    8: "Technical & DevOps",
    9: "Creative & Design",
    10: "Future & Expansion",
}
