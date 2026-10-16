"""Load the generated dataset into FalkorDB and derive similarity and link edges.

Usage: python -m coldlink.load [--data data/srfl.json] [--lab srfl]

The graph name is coldlink_<lab>: one graph per lab (tenant) in one instance.
Ground truth in the data file is never loaded into the graph.
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import datetime

from . import cypher as Q
from .db import get_graph, rows

LABEL_FOR = {"toolmark": "ToolMark", "footwear": "Footwear", "vehicle": "Vehicle", "cctv": "CCTV",
             "dna": "DNA", "fingerprint": "Fingerprint", "fibre": "Fibre", "glass": "Glass"}
BATCH = 2000


def epoch(ts: str) -> int:
    return int(datetime.fromisoformat(ts).timestamp())


def batched(items, n=BATCH):
    for i in range(0, len(items), n):
        yield items[i:i + n]


def unwind(g, query: str, items: list[dict]):
    for chunk in batched(items):
        g.query(query, {"rows": chunk})


def custody_gaps(events: list[dict], max_transit_days: float = 3.0) -> dict[str, float]:
    """Precompute the largest dispatch-to-receipt gap per exhibit as a property
    (the custody_gaps tool still walks the NEXT chain in Cypher)."""
    by_ex: dict[str, list[dict]] = {}
    for e in events:
        by_ex.setdefault(e["exhibit"], []).append(e)
    gaps = {}
    for ex, evs in by_ex.items():
        evs.sort(key=lambda e: e["seq"])
        for a, b in zip(evs, evs[1:]):
            days = (epoch(b["ts"]) - epoch(a["ts"])) / 86400
            if a["action"] == "dispatched" and days > max_transit_days:
                gaps[ex] = max(gaps.get(ex, 0), round(days, 1))
    return gaps


def load_exhibits(g, exhibits: list[dict], gaps: dict[str, float]):
    by_label: dict[str, list[dict]] = {}
    for e in exhibits:
        props = {k: v for k, v in e.items() if v is not None}
        if "width_mm" in props:
            props["width_label"] = f"{props['width_mm']:.1f}"
        if e["id"] in gaps:
            props["custody_gap_days"] = gaps[e["id"]]
        by_label.setdefault(LABEL_FOR[e["type"]], []).append(props)
    for label, items in by_label.items():
        unwind(g, f"""UNWIND $rows AS r
            MATCH (c:Case {{id: r.case}})
            CREATE (c)-[:HAS_EXHIBIT]->(e:Exhibit:{label}) SET e = r""", items)
    g.query("""MATCH (e:CCTV) MATCH (cam:Camera {id: e.camera}) CREATE (e)-[:FROM_CAMERA]->(cam)""")


def load_case(g, case: dict):
    """Ingest one new case (used by the agent when new evidence arrives)."""
    c = {k: v for k, v in case["case"].items() if v is not None}
    g.query("""CREATE (c:Case) SET c = $c WITH c
               MATCH (s:Station {id: c.station}) CREATE (c)-[:REPORTED_AT]->(s)
               WITH c MATCH (l:Location {id: c.location}) CREATE (c)-[:AT]->(l)""", {"c": c})
    ex = [dict(e, case=c["id"]) for e in case["exhibits"]]
    load_exhibits(g, ex, custody_gaps(case.get("custody", [])))
    load_findings(g, case.get("findings", []))
    load_custody(g, case.get("custody", []))
    for st in case.get("statements", []):
        g.query("""MATCH (c:Case {id: $case}) CREATE (c)-[:HAS_STATEMENT]->(s:Statement) SET s = $s""",
                {"case": c["id"], "s": {k: v for k, v in st.items() if not isinstance(v, dict)}})
    derive(g, scope=c["id"])


def load_findings(g, findings: list[dict]):
    ex_f = [f for f in findings if f["about_kind"] == "Exhibit"]
    other = [f for f in findings if f["about_kind"] != "Exhibit"]
    q = """UNWIND $rows AS r
        MATCH (t:{label} {{id: r.about}})
        CREATE (f:Finding)-[:ABOUT]->(t) SET f = r
        WITH f, r
        MATCH (x:Examiner {{id: r.examiner}}), (m:Method {{id: r.method}}), (s:SOP {{id: r.sop}})
        CREATE (f)-[:EXAMINED_BY]->(x), (f)-[:USED_METHOD]->(m), (f)-[:USED_SOP]->(s)"""
    unwind(g, q.format(label="Exhibit"), ex_f)
    unwind(g, q.format(label="Camera"), other)


def load_custody(g, events: list[dict]):
    evs = [dict(e, ts_epoch=epoch(e["ts"])) for e in events]
    unwind(g, """UNWIND $rows AS r CREATE (ce:CustodyEvent) SET ce = r""", evs)
    firsts = [{"ex": e["exhibit"], "ce": e["id"]} for e in events if e["seq"] == 1]
    unwind(g, """UNWIND $rows AS r MATCH (e:Exhibit {id: r.ex}), (ce:CustodyEvent {id: r.ce})
                 CREATE (e)-[:FIRST_CUSTODY]->(ce)""", firsts)
    by_ex: dict[str, list[dict]] = {}
    for e in events:
        by_ex.setdefault(e["exhibit"], []).append(e)
    pairs = []
    for evs_ in by_ex.values():
        evs_.sort(key=lambda e: e["seq"])
        pairs += [{"a": a["id"], "b": b["id"]} for a, b in zip(evs_, evs_[1:])]
    unwind(g, """UNWIND $rows AS r MATCH (a:CustodyEvent {id: r.a}), (b:CustodyEvent {id: r.b})
                 CREATE (a)-[:NEXT]->(b)""", pairs)


def derive(g, scope: str | None = None) -> dict:
    out = {}
    for name, q in [("toolmark", Q.SIMILAR_TOOLMARK), ("footwear", Q.SIMILAR_FOOTWEAR), ("vehicle", Q.SIMILAR_VEHICLE)]:
        out[f"similar_{name}"] = rows(g, q, {"scope": scope})[0]["created"]
    out["links"] = rows(g, Q.DERIVE_LINKS, {"scope": scope})[0]["created"]
    return out


def load(path: str, lab: str) -> dict:
    data = json.load(open(path))
    g = get_graph(lab)
    try:
        g.delete()
    except Exception:
        pass
    g = get_graph(lab)
    t0 = time.time()
    for q in Q.INDEXES:
        g.query(q)
    g.query("CREATE (:Lab {id: $id, name: $name})", data["lab"])
    unwind(g, "UNWIND $rows AS r CREATE (s:Station) SET s = r", data["stations"])
    unwind(g, """UNWIND $rows AS r MATCH (s:Station {id: r.station})
                 CREATE (l:Location)-[:IN_STATION]->(s) SET l = r""", data["locations"])
    unwind(g, """UNWIND $rows AS r MATCH (s:Station {id: r.station})
                 CREATE (c:Camera)-[:IN_STATION]->(s) SET c = r""", data["cameras"])
    unwind(g, "UNWIND $rows AS r CREATE (m:Method) SET m = r", data["methods"])
    unwind(g, """UNWIND $rows AS r MATCH (m:Method {id: r.method})
                 CREATE (s:SOP)-[:FOR_METHOD]->(m) SET s = r""",
           [{k: v for k, v in s.items() if v is not None} for s in data["sops"]])
    unwind(g, """UNWIND $rows AS r MATCH (a:SOP {id: r.newer}), (b:SOP {id: r.older})
                 CREATE (a)-[:SUPERSEDES]->(b)""", data["supersedes"])
    unwind(g, """UNWIND $rows AS r CREATE (x:Examiner) SET x = r
                 WITH x, r UNWIND r.methods AS mid MATCH (m:Method {id: mid}) CREATE (x)-[:SKILLED_IN]->(m)""",
           [{k: v for k, v in e.items() if v is not None} for e in data["examiners"]])
    cases = [{k: v for k, v in c.items() if v is not None and k != "planted"} for c in data["cases"]]
    unwind(g, """UNWIND $rows AS r CREATE (c:Case) SET c = r
                 WITH c MATCH (s:Station {id: c.station}) CREATE (c)-[:REPORTED_AT]->(s)
                 WITH c MATCH (l:Location {id: c.location}) CREATE (c)-[:AT]->(l)""", cases)
    gaps = custody_gaps(data["custody"])
    load_exhibits(g, data["exhibits"], gaps)
    load_findings(g, data["findings"])
    decisions = []
    for d in data["decisions"]:
        decisions.append({k: v for k, v in d.items() if v is not None})
    unwind(g, """UNWIND $rows AS r MATCH (c:Case {id: r.case})
                 CREATE (d:Decision)-[:FOR_CASE]->(c) SET d = r
                 WITH d, r UNWIND r.based_on AS fid MATCH (f:Finding {id: fid}) CREATE (d)-[:BASED_ON]->(f)""",
           decisions)
    g.query("""MATCH (d:Decision) WHERE d.linked_case IS NOT NULL
               MATCH (c:Case {id: d.linked_case}) CREATE (d)-[:LINKS]->(c)""")
    unwind(g, """UNWIND $rows AS r MATCH (c:Case {id: r.case})
                 CREATE (c)-[:HAS_STATEMENT]->(s:Statement) SET s = r""",
           [{k: v for k, v in s.items() if not isinstance(v, dict)} | {f"mentions_{k}": v for k, v in s["mentions"].items()}
            for s in data["statements"]])
    load_custody(g, data["custody"])
    t1 = time.time()
    stats = derive(g)
    # Human decisions that linked cases become accepted links.
    g.query("""MATCH (d:Decision {kind: 'link'})-[:FOR_CASE]->(a:Case), (d)-[:LINKS]->(b:Case)
               MATCH (a)-[l:LINKED]-(b) SET l.status = 'accepted', l.decision = d.id, l.origin = 'human'""")
    t2 = time.time()
    stats.update({"load_s": round(t1 - t0, 1), "derive_s": round(t2 - t1, 1)})
    stats["nodes"] = rows(g, "MATCH (n) RETURN count(n) AS n")[0]["n"]
    stats["edges"] = rows(g, "MATCH ()-[r]->() RETURN count(r) AS n")[0]["n"]
    return stats


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--data", default="data/srfl.json")
    ap.add_argument("--lab", default="srfl")
    a = ap.parse_args()
    print(json.dumps(load(a.data, a.lab), indent=1))


if __name__ == "__main__":
    main()
