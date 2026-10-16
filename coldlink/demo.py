"""End-to-end demo of the core flow, with checks against the planted ground truth.

  1. Reload the lab graph from data/srfl.json.
  2. Scenario A arrives; the Lead agent finds the series, rejects the decoy,
     reopens the 2023 elimination and requests a re-test of the weakest link.
  3. The simulated lab returns the re-test; the false link is disproved and
     drops out of later series searches (memory revision).
  4. Scenario B arrives; its only link rests on an exhibit with a custody gap,
     so the agent holds the link back instead of requesting a re-test.

Usage: python -m coldlink.demo [--mode offline|claude|auto]
"""
from __future__ import annotations

import argparse
import json

from . import tools as T
from .agents import lead
from .load import load
from .scenarios import complete_retest, write_all


def check(label: str, ok: bool, detail: str = ""):
    print(f"  [{'PASS' if ok else 'FAIL'}] {label}" + (f": {detail}" if detail else ""))
    return ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", default="auto", choices=["auto", "offline", "claude"])
    ap.add_argument("--data", default="data/srfl.json")
    a = ap.parse_args()
    gt = json.load(open(a.data))["ground_truth"]
    write_all()
    print("Loading graph ...", json.dumps(load(a.data, "srfl")))
    results = []

    print("\n== Scenario A ==")
    ra = lead.run("scenarios/scenario_a.json", a.mode)
    print(ra["report"])
    series_a = set(T.series_candidates("SRFL-2026-0901")["series"]["cases"])
    kinds = {x["kind"]: x for x in ra["actions"]}
    results.append(check("all seven planted series cases found", set(gt["series"]) <= series_a,
                         f"{len(set(gt['series']) & series_a)}/7"))
    results.append(check("decoy rejected", gt["decoy"] not in series_a))
    results.append(check("2023 elimination reopened", kinds.get("reopen_decision", {}).get("target") == gt["alibi_decision"]))
    results.append(check("unaffected CAM-17 decision left standing",
                         all(x["target"] != gt["unaffected_cam17_decision"] for x in ra["actions"])))
    retests = [x["target"] for x in ra["actions"] if x["kind"] == "request_retest"]
    results.append(check("scenario A ends in a re-test", bool(retests), ", ".join(retests)))

    print("\n== Lab returns re-test ==")
    before = len(series_a)
    for x in retests:
        print(" ", json.dumps(complete_retest(x, a.data)))
    after = set(T.series_candidates("SRFL-2026-0901")["series"]["cases"])
    print(f"  series size before {before}, after {len(after)}")
    results.append(check("false link disproved and excluded later", gt["false_link_case"] in series_a
                         and gt["false_link_case"] not in after))

    print("\n== Scenario B ==")
    rb = lead.run("scenarios/scenario_b.json", a.mode)
    print(rb["report"])
    kb = [x["kind"] for x in rb["actions"]]
    results.append(check("scenario B holds the link instead of re-testing", "hold_link" in kb and "request_retest" not in kb,
                         ", ".join(kb)))
    held = T.case_links("SRFL-2026-0902", include_inactive=True)["links"]
    results.append(check("held link rests on the custody-gap exhibit's case",
                         any(l["status"] == "held" for l in held)))
    print(f"\n{sum(results)}/{len(results)} checks passed")


if __name__ == "__main__":
    main()
