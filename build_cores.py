#!/usr/bin/env python3
"""Build cores.json from cores_part1..4.py (run from the oracool dir)."""
import json
import re
import sys

sys.path.insert(0, ".")

from cores_part1 import PART1
from cores_part2 import PART2
from cores_part3 import PART3
from cores_part4 import PART4

STOP = set("""the a an of in on at to for and or but with by from as is are was were
this that these those it its be been being have has had do does did will would can
could should may might must what what's how why when where who which your you i we
he she they them me my our us out up down over under into about between during
above below through before after while give takes some any all""".split())


def keywords(name, role, example):
    words = set()
    for w in re.findall(r"[a-zA-Z0-9]+", (name + " " + role + " " + example).lower()):
        if len(w) >= 3 and w not in STOP:
            words.add(w)
    return sorted(words)


cores = []
for num, name, role, example in PART1 + PART2 + PART3 + PART4:
    cores.append({
        "id": num,
        "name": name,
        "role": role,
        "example": example,
        "cluster": (num - 1) // 100 + 1,
        "k": keywords(name, role, example),
    })

ids = [c["id"] for c in cores]
assert len(ids) == 1000, f"expected 1000 cores, got {len(ids)}"
assert len(set(ids)) == 1000, "duplicate ids found"

with open("cores.json", "w", encoding="utf-8") as f:
    json.dump(cores, f, ensure_ascii=False)

print("cores.json written:", len(cores), "cores")
