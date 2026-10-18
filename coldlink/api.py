"""Minimal HTTP API over the Coldlink demo, for the hosted deployment.

Wraps the same code the CLI demo (coldlink.demo) and Lead agent
(coldlink.agents.lead) use -- no separate logic, so the hosted demo behaves
exactly like the verified local run.

Usage: uvicorn coldlink.api:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

from . import tools as T
from .agents import lead
from .db import DEFAULT_LAB, get_graph, rows
from .load import load as load_graph
from .scenarios import complete_retest, write_all

app = FastAPI(title="Coldlink API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def check(label: str, ok: bool, detail: str = "") -> dict:
    return {"label": label, "pass": bool(ok), "detail": detail}


@app.get("/health")
def health():
    g = get_graph(DEFAULT_LAB)
    n = rows(g, "MATCH (n) RETURN count(n) AS n", read_only=True)[0]["n"]
    return {"status": "ok", "nodes": n}


@app.get("/graph/stats")
def graph_stats():
    g = get_graph(DEFAULT_LAB)
    return {
        "nodes": rows(g, "MATCH (n) RETURN count(n) AS n", read_only=True)[0]["n"],
        "edges": rows(g, "MATCH ()-[r]->() RETURN count(r) AS n", read_only=True)[0]["n"],
        "cases": rows(g, "MATCH (c:Case) RETURN count(c) AS n", read_only=True)[0]["n"],
        "exhibits": rows(g, "MATCH (e:Exhibit) RETURN count(e) AS n", read_only=True)[0]["n"],
    }


@app.get("/graph/series/{case_id}")
def graph_series(case_id: str):
    """Cases and links in the series containing case_id, for visualization."""
    series = T.series_candidates(case_id)["series"]
    if not series:
        return {"nodes": [], "edges": []}
    g = get_graph(DEFAULT_LAB)
    edges = rows(g, """
        MATCH (a:Case)-[l:LINKED]-(b:Case)
        WHERE a.id IN $cases AND b.id IN $cases AND id(a) < id(b) AND l.status IN ['proposed','accepted']
        RETURN a.id AS a, b.id AS b, l.score AS score, l.status AS status
    """, {"cases": series["cases"]}, read_only=True)
    return {"nodes": series["cases"], "edges": edges}


@app.post("/demo/reset")
def demo_reset():
    """Reloads the graph from data/srfl.json, undoing any writes from a
    previous demo run (reopened decisions, held/disproved links, ...)."""
    write_all()
    stats = load_graph("data/srfl.json", DEFAULT_LAB)
    return {"status": "reloaded", **stats}


@app.post("/demo/run")
def demo_run(mode: str = "offline"):
    """Replays the two-scenario planted story end to end (same as `python -m
    coldlink.demo`) and returns the reports plus the 8 ground-truth checks.
    Call /demo/reset first if this graph already carries a previous run's
    writes (reopened decisions, held/disproved links)."""
    if mode not in ("offline", "claude", "groq"):
        raise HTTPException(400, "mode must be offline, claude or groq")
    data_path = Path("data/srfl.json")
    if not data_path.exists():
        raise HTTPException(503, "graph not loaded yet")
    gt = json.load(open(data_path))["ground_truth"]
    results = []

    ra = lead.run("scenarios/scenario_a.json", mode)
    series_a = set(T.series_candidates("SRFL-2026-0901")["series"]["cases"])
    kinds = {x["kind"]: x for x in ra["actions"]}
    results.append(check("all seven planted series cases found", set(gt["series"]) <= series_a,
                          f"{len(set(gt['series']) & series_a)}/7"))
    results.append(check("decoy rejected", gt["decoy"] not in series_a))
    results.append(check("2023 elimination reopened",
                          kinds.get("reopen_decision", {}).get("target") == gt["alibi_decision"]))
    results.append(check("unaffected CAM-17 decision left standing",
                          all(x["target"] != gt["unaffected_cam17_decision"] for x in ra["actions"])))
    retests = [x["target"] for x in ra["actions"] if x["kind"] == "request_retest"]
    results.append(check("scenario A ends in a re-test", bool(retests), ", ".join(retests)))

    retest_log = [complete_retest(x, str(data_path)) for x in retests]
    after = set(T.series_candidates("SRFL-2026-0901")["series"]["cases"])
    results.append(check("false link disproved and excluded later",
                          gt["false_link_case"] in series_a and gt["false_link_case"] not in after))

    rb = lead.run("scenarios/scenario_b.json", mode)
    kb = [x["kind"] for x in rb["actions"]]
    results.append(check("scenario B holds the link instead of re-testing",
                          "hold_link" in kb and "request_retest" not in kb, ", ".join(kb)))
    held = T.case_links("SRFL-2026-0902", include_inactive=True)["links"]
    results.append(check("held link rests on the custody-gap exhibit's case",
                          any(l["status"] == "held" for l in held)))

    return {
        "mode": mode,
        "passed": sum(1 for r in results if r["pass"]),
        "total": len(results),
        "checks": results,
        "scenario_a": {"run_id": ra["run_id"], "report": ra["report"], "actions": ra["actions"]},
        "retest": retest_log,
        "scenario_b": {"run_id": rb["run_id"], "report": rb["report"], "actions": rb["actions"]},
    }
