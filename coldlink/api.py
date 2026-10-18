"""Minimal HTTP API over the Coldlink demo, for the hosted deployment.

Wraps the same code the CLI demo (coldlink.demo) and Lead agent
(coldlink.agents.lead) use -- no separate logic, so the hosted demo behaves
exactly like the verified local run.

Usage: uvicorn coldlink.api:app --host 0.0.0.0 --port 8080
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Literal

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from . import tools as T
from .agents import lead
from .db import DEFAULT_LAB, get_graph, rows
from .load import load as load_graph
from .load import load_case
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
    node_rows = rows(g, """
        MATCH (c:Case) WHERE c.id IN $cases
        OPTIONAL MATCH (c)-[:REPORTED_AT]->(s:Station)
        RETURN c.id AS id, c.status AS status, c.date AS date, s.name AS station
    """, {"cases": series["cases"]}, read_only=True)
    edges = rows(g, """
        MATCH (a:Case)-[l:LINKED]-(b:Case)
        WHERE a.id IN $cases AND b.id IN $cases AND id(a) < id(b) AND l.status IN ['proposed','accepted']
        RETURN a.id AS a, b.id AS b, l.score AS score, l.status AS status
    """, {"cases": series["cases"]}, read_only=True)
    return {"nodes": node_rows, "edges": edges}


@app.get("/graph/overview")
def graph_overview(limit: int = 60):
    """A sample of real cases and the active links between them, for a
    default network view before any single case is selected."""
    g = get_graph(DEFAULT_LAB)
    edges = rows(g, """
        MATCH (a:Case)-[l:LINKED]-(b:Case)
        WHERE id(a) < id(b) AND l.status IN ['proposed','accepted']
        RETURN a.id AS a, b.id AS b, l.score AS score, l.status AS status
        LIMIT $limit
    """, {"limit": limit}, read_only=True)
    ids = sorted({e["a"] for e in edges} | {e["b"] for e in edges})
    if not ids:
        return {"nodes": [], "edges": []}
    node_rows = rows(g, """
        MATCH (c:Case) WHERE c.id IN $ids
        OPTIONAL MATCH (c)-[:REPORTED_AT]->(s:Station)
        RETURN c.id AS id, c.status AS status, c.date AS date, s.name AS station
    """, {"ids": ids}, read_only=True)
    return {"nodes": node_rows, "edges": edges}


@app.get("/cases")
def list_cases(limit: int = 50, offset: int = 0):
    """Real cases from the loaded lab graph, for the case dashboard."""
    g = get_graph(DEFAULT_LAB)
    total = rows(g, "MATCH (c:Case) RETURN count(c) AS n", read_only=True)[0]["n"]
    case_rows = rows(g, """
        MATCH (c:Case)
        OPTIONAL MATCH (c)-[:REPORTED_AT]->(s:Station)
        OPTIONAL MATCH (c)-[:HAS_EXHIBIT]->(e:Exhibit)
        RETURN c.id AS id, c.date AS date, c.status AS status, c.type AS type, s.name AS station,
               count(DISTINCT e) AS exhibits
        ORDER BY c.id DESC SKIP $offset LIMIT $limit
    """, {"offset": offset, "limit": limit}, read_only=True)
    return {"total": total, "cases": case_rows}


@app.get("/cases/investigable")
def investigable_cases():
    """The cases with a scenario file on disk: these are the ones that can
    actually be handed to the Lead agent to investigate live."""
    out = []
    for p in sorted(Path("scenarios").glob("scenario_*.json")):
        sc = json.load(open(p))
        case = sc["case"]
        out.append({"key": p.stem.removeprefix("scenario_"), "case_id": case["id"],
                    "station": case.get("station"), "date": case.get("date"),
                    "type": case.get("type"), "location": case.get("location")})
    return {"cases": out}


@app.post("/demo/reset")
def demo_reset():
    """Reloads the graph from data/srfl.json, undoing any writes from a
    previous demo run (reopened decisions, held/disproved links, ...)."""
    write_all()
    stats = load_graph("data/srfl.json", DEFAULT_LAB)
    return {"status": "reloaded", **stats}


def _investigate_a(mode: str, data_path: Path, gt: dict) -> dict:
    ra = lead.run("scenarios/scenario_a.json", mode)
    series_a = set(T.series_candidates("SRFL-2026-0901")["series"]["cases"])
    kinds = {x["kind"]: x for x in ra["actions"]}
    results = [
        check("all seven planted series cases found", set(gt["series"]) <= series_a,
              f"{len(set(gt['series']) & series_a)}/7"),
        check("decoy rejected", gt["decoy"] not in series_a),
        check("2023 elimination reopened",
              kinds.get("reopen_decision", {}).get("target") == gt["alibi_decision"]),
        check("unaffected CAM-17 decision left standing",
              all(x["target"] != gt["unaffected_cam17_decision"] for x in ra["actions"])),
    ]
    retests = [x["target"] for x in ra["actions"] if x["kind"] == "request_retest"]
    results.append(check("ends in a re-test", bool(retests), ", ".join(retests)))
    retest_log = [complete_retest(x, str(data_path)) for x in retests]
    after = set(T.series_candidates("SRFL-2026-0901")["series"]["cases"])
    results.append(check("false link disproved and excluded after re-test",
                          gt["false_link_case"] in series_a and gt["false_link_case"] not in after))
    return {"run_id": ra["run_id"], "case_id": ra["case_id"], "report": ra["report"],
            "actions": ra["actions"], "trace": ra["trace"], "retest": retest_log,
            "checks": results, "passed": sum(1 for r in results if r["pass"]), "total": len(results)}


def _investigate_b(mode: str, gt: dict) -> dict:
    rb = lead.run("scenarios/scenario_b.json", mode)
    kb = [x["kind"] for x in rb["actions"]]
    results = [
        check("holds the link instead of re-testing",
              "hold_link" in kb and "request_retest" not in kb, ", ".join(kb)),
    ]
    held = T.case_links("SRFL-2026-0902", include_inactive=True)["links"]
    results.append(check("held link rests on the custody-gap exhibit's case",
                          any(l["status"] == "held" for l in held)))
    return {"run_id": rb["run_id"], "case_id": rb["case_id"], "report": rb["report"],
            "actions": rb["actions"], "trace": rb["trace"],
            "checks": results, "passed": sum(1 for r in results if r["pass"]), "total": len(results)}


@app.post("/investigate/{key}")
def investigate(key: str, mode: str = "offline"):
    """Hand one real case to the Lead agent and let it investigate live: the
    graph tool calls, their timing and the resulting actions all come from
    this one run, not a canned transcript. key is "a" or "b" (the two cases
    with a scenario file and known ground truth to check the result against);
    call /demo/reset first to undo a previous run's writes."""
    if mode not in ("offline", "claude", "groq"):
        raise HTTPException(400, "mode must be offline, claude or groq")
    data_path = Path("data/srfl.json")
    if not data_path.exists():
        raise HTTPException(503, "graph not loaded yet")
    gt = json.load(open(data_path))["ground_truth"]
    if key == "a":
        return {"mode": mode, **_investigate_a(mode, data_path, gt)}
    if key == "b":
        return {"mode": mode, **_investigate_b(mode, gt)}
    raise HTTPException(404, "unknown case key, use 'a' or 'b'")


@app.post("/demo/run")
def demo_run(mode: str = "offline"):
    """Runs both investigable cases back to back and combines their checks
    (same total as the old /demo/run). Prefer /investigate/a and
    /investigate/b individually for the dashboard, since those show each run on
    its own case."""
    if mode not in ("offline", "claude", "groq"):
        raise HTTPException(400, "mode must be offline, claude or groq")
    data_path = Path("data/srfl.json")
    if not data_path.exists():
        raise HTTPException(503, "graph not loaded yet")
    gt = json.load(open(data_path))["ground_truth"]
    a = _investigate_a(mode, data_path, gt)
    b = _investigate_b(mode, gt)
    return {
        "mode": mode,
        "passed": a["passed"] + b["passed"],
        "total": a["total"] + b["total"],
        "checks": a["checks"] + b["checks"],
        "scenario_a": a,
        "scenario_b": b,
    }


# ------------------------------------------------------------- user-submitted cases

TM_CLASSES = ["pry-bar", "screwdriver", "chisel", "bolt-cutter", "wrecking-bar", "tyre-lever"]
TM_PROFILES = ["flat-chamfered", "flat-square", "rounded", "serrated", "bevelled"]
FW_PATTERNS = ["zigzag", "hexagonal", "bar", "circular", "wavy", "block", "chevron", "lug",
               "herringbone", "diamond", "star", "target", "multi-bar", "dimple"]
CASE_TYPES = ["burglary", "theft", "criminal_damage", "vehicle_crime", "robbery", "assault"]
ENTRY_POINTS = ["rear first-floor window", "rear ground-floor window", "rear ground-floor door",
                "front door", "side door", "garage door", "front ground-floor window"]


@app.get("/reference")
def reference():
    """Vocabulary for the case-upload form: real stations/locations from the
    loaded graph, plus the exhibit property choices the generator itself uses
    (so a user-built case speaks the same language as the planted data)."""
    g = get_graph(DEFAULT_LAB)
    stations = rows(g, "MATCH (s:Station) RETURN s.id AS id, s.name AS name ORDER BY s.name", read_only=True)
    locations = rows(g, "MATCH (l:Location) RETURN l.id AS id, l.station AS station ORDER BY l.id", read_only=True)
    return {
        "stations": stations,
        "locations": locations,
        "case_types": CASE_TYPES,
        "entry_points": ENTRY_POINTS,
        "exhibit_types": {
            "toolmark": {"tm_class": TM_CLASSES, "edge_profile": TM_PROFILES},
            "footwear": {"pattern": FW_PATTERNS, "subtype": ["a", "b", "c"]},
            "vehicle": {},
        },
    }


class ExhibitIn(BaseModel):
    type: Literal["toolmark", "footwear", "vehicle"]
    tm_class: str | None = None
    width_mm: float | None = None
    edge_profile: str | None = None
    pattern: str | None = None
    size: int | None = None
    subtype: str | None = None
    wear_code: str | None = None
    make_model: str | None = None
    colour: str | None = None
    plate_partial: str | None = None


class CaseIn(BaseModel):
    station: str
    location: str | None = None
    date: str
    type: str
    entry_point: str | None = None
    exhibits: list[ExhibitIn]


@app.post("/cases/upload")
def upload_case(body: CaseIn, mode: str = "offline"):
    """Ingest a real, user-submitted case and let the Lead agent investigate
    it live -- same tools, same graph, same Toolbox trace as the two planted
    scenarios, just with no authored ground truth to check against."""
    if mode not in ("offline", "claude", "groq"):
        raise HTTPException(400, "mode must be offline, claude or groq")
    if not body.exhibits:
        raise HTTPException(400, "at least one exhibit is required")
    try:
        when = datetime.fromisoformat(body.date)
    except ValueError:
        raise HTTPException(400, "date must be ISO 8601, e.g. 2026-10-18T02:30:00")

    g = get_graph(DEFAULT_LAB)
    station_row = rows(g, "MATCH (s:Station {id: $id}) RETURN s.id AS id", {"id": body.station}, read_only=True)
    if not station_row:
        raise HTTPException(404, f"unknown station {body.station}")
    location = body.location
    if location:
        loc_row = rows(g, "MATCH (l:Location {id: $id, station: $s}) RETURN l.id",
                        {"id": location, "s": body.station}, read_only=True)
        if not loc_row:
            raise HTTPException(404, f"unknown location {location} for station {body.station}")
    else:
        loc_row = rows(g, "MATCH (l:Location {station: $s}) RETURN l.id AS id LIMIT 1",
                        {"s": body.station}, read_only=True)
        if not loc_row:
            raise HTTPException(404, f"station {body.station} has no locations")
        location = loc_row[0]["id"]

    case_id = f"SRFL-{when.year}-U{uuid.uuid4().hex[:4].upper()}"
    case = {"id": case_id, "year": when.year, "date": body.date, "station": body.station,
            "type": body.type, "entry_point": body.entry_point, "hour": when.hour,
            "location": location, "status": "open", "sealed": False, "planted": None}
    exhibits = []
    for ex in body.exhibits:
        e = {"id": f"EX-U{uuid.uuid4().hex[:6].upper()}", "case": case_id, "type": ex.type,
             "collected": body.date}
        if ex.type == "toolmark":
            e.update(tm_class=ex.tm_class, width_mm=ex.width_mm, edge_profile=ex.edge_profile)
        elif ex.type == "footwear":
            e.update(pattern=ex.pattern, size=ex.size, subtype=ex.subtype, wear_code=ex.wear_code)
        elif ex.type == "vehicle":
            e.update(make_model=ex.make_model, colour=ex.colour, plate_partial=ex.plate_partial)
        exhibits.append(e)

    load_case(g, {"case": case, "exhibits": exhibits})
    result = lead.investigate_case(case_id, mode, DEFAULT_LAB)
    return {"mode": mode, "case_id": case_id, **result}
