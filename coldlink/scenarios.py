"""Incoming-case scenarios and a simulated lab that returns re-test results.

Scenario A: a night burglary at Harbour with a 14 mm pry-bar mark and a zigzag
sole. It joins the window series. The series' weakest link is a footwear
match made under a superseded SOP, so the right action is a re-test.

Scenario B: a night burglary at Northgate with only a size-10 zigzag sole. Its
one link into the series runs through an exhibit with a 9-day custody gap, so
the right action is to hold the link back; a re-test cannot repair custody.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta
from pathlib import Path

from . import cypher as Q
from .db import DEFAULT_LAB, get_graph, rows
from .tools import now


def _custody(ex_id: str, collected: datetime) -> list[dict]:
    steps = [("collected", "scene", 0), ("sealed", "scene", 2), ("stored", "station store", 10),
             ("dispatched", "station store", 30), ("received", "lab intake", 34), ("examined", "lab", 52)]
    return [{"id": f"CE-{ex_id[3:]}-{i + 1}", "exhibit": ex_id, "seq": i + 1, "action": a, "place": p,
             "ts": (collected + timedelta(hours=h)).strftime("%Y-%m-%dT%H:%M:%S")} for i, (a, p, h) in enumerate(steps)]


def _finding(fid, ex, when, method, sop, text):
    return {"id": fid, "about": ex, "about_kind": "Exhibit", "date": when, "examiner": "EXM-08", "method": method,
            "sop": sop, "kind": "examination", "text": text}


def scenario_a() -> dict:
    t = datetime(2026, 10, 14, 2, 40)
    cid = "SRFL-2026-0901"
    return {
        "name": "A", "description": "New night burglary at Harbour: pry-bar mark and zigzag sole",
        "case": {"id": cid, "year": 2026, "date": t.isoformat(), "station": "ST-HB", "type": "burglary",
                 "entry_point": "rear first-floor window", "hour": 2, "location": "LOC-HB-03", "status": "open",
                 "sealed": False},
        "exhibits": [
            {"id": "EX-90001", "type": "toolmark", "collected": "2026-10-14T07:00:00", "tm_class": "pry-bar",
             "width_mm": 14.0, "edge_profile": "flat-chamfered"},
            {"id": "EX-90002", "type": "footwear", "collected": "2026-10-14T07:30:00", "pattern": "zigzag",
             "size": 9, "subtype": "b", "wear_code": "W-07"},
        ],
        "findings": [
            _finding("F-90001", "EX-90001", "2026-10-15T10:00:00", "M-TM", "SOP-TM-1",
                     "pry-bar mark, blade width 14.0 mm, flat-chamfered edge"),
            _finding("F-90002", "EX-90002", "2026-10-15T11:00:00", "M-FW", "SOP-FW-3",
                     "zigzag sole pattern, size 9, sub-type b, wear W-07"),
        ],
        "custody": _custody("EX-90001", datetime(2026, 10, 14, 7)) + _custody("EX-90002", datetime(2026, 10, 14, 7, 30)),
        "statements": [{"id": "STM-90001", "case": cid, "date": t.isoformat(), "witness": "W-9001",
                        "text": "Occupier asleep; rear upstairs window forced, found at 06:30."}],
    }


def scenario_b() -> dict:
    t = datetime(2026, 10, 15, 1, 50)
    cid = "SRFL-2026-0902"
    return {
        "name": "B", "description": "New night burglary at Northgate: size-10 zigzag sole only",
        "case": {"id": cid, "year": 2026, "date": t.isoformat(), "station": "ST-NG", "type": "burglary",
                 "entry_point": "rear first-floor window", "hour": 1, "location": "LOC-NG-02", "status": "open",
                 "sealed": False},
        "exhibits": [
            {"id": "EX-90011", "type": "footwear", "collected": "2026-10-15T08:00:00", "pattern": "zigzag",
             "size": 10, "subtype": "a", "wear_code": "W-17"},
        ],
        "findings": [
            _finding("F-90011", "EX-90011", "2026-10-15T15:00:00", "M-FW", "SOP-FW-3",
                     "zigzag sole pattern, size 10, sub-type a, wear W-17"),
        ],
        "custody": _custody("EX-90011", datetime(2026, 10, 15, 8)),
        "statements": [{"id": "STM-90011", "case": cid, "date": t.isoformat(), "witness": "W-9011",
                        "text": "Neighbour heard a window go at about 2 AM; no vehicle seen."}],
    }


SCENARIOS = {"A": scenario_a, "B": scenario_b}


def write_all(out_dir: str = "scenarios"):
    Path(out_dir).mkdir(exist_ok=True)
    for k, f in SCENARIOS.items():
        Path(out_dir, f"scenario_{k.lower()}.json").write_text(json.dumps(f(), indent=1))


# ---------------------------------------------------------------- lab sim

def complete_retest(exhibit_id: str, data_path: str = "data/srfl.json", lab: str | None = None) -> dict:
    """Simulate the lab returning a re-test under the current footwear SOP.
    The true sub-type comes from the generator's hidden ground truth. Links
    that no longer pass the threshold are marked disproved with the reason
    kept; disproved links drop out of the series projection."""
    g = get_graph(lab or DEFAULT_LAB)
    truth = json.load(open(data_path))["ground_truth"]["retest_outcomes"].get(exhibit_id)
    ex = rows(g, "MATCH (e:Exhibit {id: $x}) RETURN e.type AS type, e.case AS case_id, e.retest_examiner AS exm, "
                 "e.subtype AS subtype", {"x": exhibit_id})[0]
    if ex["type"] != "footwear" or truth is None:
        return {"exhibit": exhibit_id, "result": "re-test confirms the original finding", "changed_links": []}
    sub = truth["subtype"]
    fid = f"F-RT-{exhibit_id[3:]}"
    g.query("""MATCH (e:Exhibit {id: $x}) SET e.subtype = $sub, e.retest_status = 'done'
               WITH e MATCH (old:Finding)-[:ABOUT]->(e) WHERE NOT old.id STARTS WITH 'F-RT-'
               MATCH (x:Examiner {id: coalesce(e.retest_examiner, 'EXM-08')}), (m:Method {id: 'M-FW'}), (s:SOP {id: 'SOP-FW-3'})
               CREATE (f:Finding {id: $fid, about: $x, about_kind: 'Exhibit', date: $ts, method: 'M-FW', sop: 'SOP-FW-3',
                       kind: 'retest', text: 'Re-test under SOP-FW-3: sub-type ' + $sub})
               CREATE (f)-[:ABOUT]->(e), (f)-[:REVISES]->(old), (f)-[:EXAMINED_BY]->(x), (f)-[:USED_METHOD]->(m), (f)-[:USED_SOP]->(s)""",
            {"x": exhibit_id, "sub": sub, "fid": fid, "ts": now()})
    # Remove footwear similarity that the sub-type now contradicts.
    removed = rows(g, """MATCH (e:Exhibit {id: $x})-[s:SIMILAR_TO {etype: 'footwear'}]-(o:Footwear)
                         WHERE o.subtype IS NOT NULL AND o.subtype <> $sub
                         WITH s, o.id AS oid DELETE s RETURN collect(oid) AS removed""", {"x": exhibit_id, "sub": sub})[0]["removed"]
    changed = []
    for l in rows(g, """MATCH (c:Case {id: $c})-[l:LINKED]-(o:Case) WHERE 'footwear' IN l.types
                        AND l.status IN ['proposed', 'accepted'] RETURN o.id AS other""", {"c": ex["case_id"]}):
        sc = rows(g, Q.SCORE_PAIR, {"a": ex["case_id"], "b": l["other"]})[0]
        if sc["score"] < Q.LINK_THRESHOLD:
            reason = (f"Re-test {fid} under SOP-FW-3 found sub-type {sub}; the footwear match no longer holds "
                      f"and the rescored link is {sc['score']} (< {Q.LINK_THRESHOLD}).")
            rows(g, Q.SET_LINK_STATUS, {"a": ex["case_id"], "b": l["other"], "status": "disproved",
                                        "reason": reason, "ts": now(), "by": "labsim"})
            changed.append({"link": [ex["case_id"], l["other"]], "status": "disproved", "rescored": sc["score"]})
    return {"exhibit": exhibit_id, "finding": fid, "subtype": sub, "removed_similarity_to": removed,
            "changed_links": changed}


if __name__ == "__main__":
    write_all()
    print("wrote scenarios/scenario_a.json and scenario_b.json")
