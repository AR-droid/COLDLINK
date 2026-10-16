"""Coldlink agent tools. Each one is a graph operation over FalkorDB.

The seven core tools from the brief:
    find_similar_exhibits   property match over SIMILAR_TO
    link_path               weighted shortest path (algo.SPpaths)
    series_candidates       connected components (algo.WCC)
    weakest_link            traversal and scoring
    decisions_resting_on    reverse traversal of BASED_ON
    custody_gaps            walk along NEXT
    best_examiner           weighted counts

Supporting tools let the agent read a case, score a pair, and act. Every
action is written to the graph as an ActionRecord with the evidence path that
justified it and the alternative it rejected.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone

from . import cypher as Q
from .db import DEFAULT_LAB, get_graph, rows

MAX_TRANSIT_DAYS = 3.0
SUPERSEDED_FACTOR = 0.75   # a finding under a superseded SOP counts for less
EXAMINER_LEFT_NOTE = True


def now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _g(lab: str | None):
    return get_graph(lab or DEFAULT_LAB)


# =========================================================== core seven tools

def find_similar_exhibits(case_id: str, etype: str | None = None, min_score: float = 0.0,
                          limit: int = 30, lab: str | None = None) -> dict:
    """Exhibits elsewhere in the lab history that match this case's exhibits by
    class and measurements. Includes weak and contradicted matches so the agent
    can explain rejections."""
    res = rows(_g(lab), Q.FIND_SIMILAR_EXHIBITS,
               {"case_id": case_id, "etype": etype, "min_score": min_score, "limit": limit}, read_only=True)
    return {"case_id": case_id, "matches": res}


def link_path(case_a: str, case_b: str, max_len: int = 6, lab: str | None = None) -> dict:
    """The strongest evidence chain between two cases over active links, with
    the evidence behind every hop."""
    g = _g(lab)
    res = rows(g, Q.LINK_PATH, {"a": case_a, "b": case_b, "max_len": max_len}, read_only=True)
    if not res:
        return {"case_a": case_a, "case_b": case_b, "path": None, "note": "no active evidence chain"}
    cases = res[0]["cases"]
    hops = []
    for x, y in zip(cases, cases[1:]):
        d = rows(g, Q.LINK_DETAIL, {"a": x, "b": y}, read_only=True)
        hops.append({"from": x, "to": y, **(d[0] if d else {})})
    return {"case_a": case_a, "case_b": case_b, "cases": cases, "hops": hops,
            "chain_strength": round(min(h.get("score", 0) for h in hops), 3)}


def _cohesion(cases: list[str], g) -> dict:
    edges = rows(g, Q.SERIES_EDGES_AMONG, {"cases": cases}, read_only=True)
    n = len(cases)
    possible = n * (n - 1) / 2 or 1
    types = sorted({t for e in edges for t in e["types"]})
    mean = sum(e["score"] for e in edges) / len(edges) if edges else 0
    multi = sum(1 for e in edges if len(e["types"]) > 1)
    density = len(edges) / possible
    return {"cases": cases, "size": n, "links": len(edges), "density": round(density, 3),
            "mean_link_score": round(mean, 3), "multi_type_links": multi, "evidence_types": types,
            "cohesion": round(mean * (0.5 + 0.5 * density) * (1 + 0.1 * len(types)), 3),
            "accepted_links": [[e["a"], e["b"]] for e in edges if e["status"] == "accepted"]}


def series_candidates(case_id: str | None = None, min_size: int = 3, limit: int = 5,
                      lab: str | None = None) -> dict:
    """Group cases into likely series with connected components over active
    links (disproved and held links are excluded). With case_id, returns the
    component that case belongs to."""
    g = _g(lab)
    comps = rows(g, Q.SERIES_COMPONENTS, {"min_size": 2 if case_id else min_size, "limit": 10000 if case_id else 200},
                 read_only=True)
    if case_id:
        comps = [c for c in comps if case_id in c["cases"]]
        if not comps:
            return {"case_id": case_id, "series": None, "note": "case has no active links"}
        return {"case_id": case_id, "series": _cohesion(comps[0]["cases"], g)}
    scored = [_cohesion(c["cases"], g) for c in comps]
    scored.sort(key=lambda s: s["cohesion"] * s["size"], reverse=True)
    return {"candidates": scored[:limit]}


def weakest_link(cases: list[str], lab: str | None = None) -> dict:
    """Find the link a series hypothesis depends on most and how reliable it
    is. Each link's score is discounted by the reliability of the exhibits
    behind it (superseded SOP, custody gap). A link is load-bearing when it is
    the only active link holding one member in the series above threshold."""
    g = _g(lab)
    edges = rows(g, Q.SERIES_EDGES_AMONG, {"cases": cases}, read_only=True)
    exhibits = sorted({x for e in edges for p in e["pairs"] for x in p.split("~")})
    rel = {r["exhibit"]: r for r in rows(g, Q.EXHIBIT_RELIABILITY, {"exhibits": exhibits}, read_only=True)}
    scored = []
    for e in edges:
        issues, factor = [], 1.0
        for pair, t in zip(e["pairs"], e["types"]):
            for x in pair.split("~"):
                r = rel.get(x, {})
                if r.get("custody_gap_days", 0) > MAX_TRANSIT_DAYS:
                    issues.append({"exhibit": x, "case": r["case_id"], "issue": "custody_gap",
                                   "detail": f"{r['custody_gap_days']}-day gap between dispatch and lab receipt"})
                    factor = 0.0
                if r.get("superseded_by"):
                    issues.append({"exhibit": x, "case": r["case_id"], "issue": "superseded_sop",
                                   "detail": f"examined under {r['sop']}, superseded by {r['superseded_by']}",
                                   "current_sop": r["superseded_by"], "type": t})
                    factor = min(factor, SUPERSEDED_FACTOR)
                if r.get("examiner_left"):
                    issues.append({"exhibit": x, "case": r["case_id"], "issue": "examiner_left",
                                   "detail": f"original examiner {r['examiner']} has left the lab"})
        scored.append({**e, "effective": round(e["score"] * factor, 3), "issues": issues})
    # Members' attachments: which edges each member depends on.
    by_member: dict[str, list[dict]] = {c: [] for c in cases}
    for e in scored:
        by_member[e["a"]].append(e)
        by_member[e["b"]].append(e)
    load_bearing = []
    for m, es in by_member.items():
        good = sorted(es, key=lambda e: e["effective"], reverse=True)
        best = good[0] if good else None
        second = good[1]["effective"] if len(good) > 1 else 0.0
        if best is None:
            continue
        # The member stays in only through its best link if nothing else is reliable,
        # or if all its links share the same weakness.
        shared_issue = all(any(i["issue"] in ("custody_gap", "superseded_sop") and i["case"] == m for i in e["issues"])
                           for e in es)
        if second < Q.LINK_THRESHOLD or shared_issue:
            load_bearing.append({"member": m, "edge": best, "member_attachment": best["effective"],
                                 "all_links_share_weakness": shared_issue, "links": len(es)})
    load_bearing.sort(key=lambda x: x["member_attachment"])
    weakest = load_bearing[0] if load_bearing else None
    return {"cases": cases, "weakest": weakest,
            "load_bearing": load_bearing[:6],
            "flagged_links": [e for e in scored if e["issues"]][:12],
            "note": ("effective = score x reliability; custody gap -> 0, superseded SOP -> x"
                     f"{SUPERSEDED_FACTOR}; a member is load-bearing on its best link when no second link "
                     f"reaches {Q.LINK_THRESHOLD} or all its links share the same weakness")}


def decisions_resting_on(source_id: str, lab: str | None = None) -> dict:
    """Every decision built on a source (camera, exhibit or finding), by
    reverse traversal of BASED_ON, with whether a known reliability problem
    with the source overturns each decision's margin."""
    g = _g(lab)
    decs = rows(g, Q.DECISIONS_RESTING_ON, {"source_id": source_id}, read_only=True)
    rel = rows(g, Q.SOURCE_RELIABILITY_FINDINGS, {"source_id": source_id}, read_only=True)
    offset = max((abs(r["clock_offset_minutes"]) for r in rel), default=None)
    for d in decs:
        if offset is not None and d.get("margin_minutes") is not None:
            d["at_risk"] = offset >= d["margin_minutes"]
            d["assessment"] = (f"clock offset {offset} min >= decision margin {d['margin_minutes']} min: conclusion no longer holds"
                               if d["at_risk"] else
                               f"clock offset {offset} min < margin {d['margin_minutes']} min: conclusion survives")
        else:
            d["at_risk"] = None
    return {"source_id": source_id, "reliability_findings": rel, "decisions": decs}


def custody_gaps(exhibit_ids: list[str] | None = None, case_ids: list[str] | None = None,
                 lab: str | None = None) -> dict:
    """Walk each exhibit's custody chain along NEXT and report dispatch-to-
    receipt gaps longer than allowed."""
    g = _g(lab)
    ids = list(exhibit_ids or [])
    if case_ids:
        ids += [r["id"] for r in rows(g, "MATCH (e:Exhibit) WHERE e.case IN $c RETURN e.id AS id", {"c": case_ids},
                                      read_only=True)]
    gaps = []
    for x in sorted(set(ids)):
        gaps += rows(g, Q.CUSTODY_GAPS, {"exhibit_id": x, "max_transit_days": MAX_TRANSIT_DAYS}, read_only=True)
    return {"checked": len(set(ids)), "max_transit_days": MAX_TRANSIT_DAYS, "gaps": gaps}


def best_examiner(method: str, exclude: list[str] | None = None, limit: int = 3, lab: str | None = None) -> dict:
    """Rank active examiners by hands-on history with a method, weighting work
    under the current SOP and recent work."""
    res = rows(_g(lab), Q.BEST_EXAMINER, {"method": method, "exclude": exclude or [], "limit": limit,
                                          "recent_from": "2024-01-01"}, read_only=True)
    return {"method": method, "ranking": res,
            "weighting": "total + 2 x findings under current SOP + 3 x findings since 2024; active examiners only"}


# ============================================================ supporting tools

def case_summary(case_id: str, lab: str | None = None) -> dict:
    res = rows(_g(lab), Q.CASE_SUMMARY, {"case_id": case_id}, read_only=True)
    return res[0] if res else {"error": f"no case {case_id}"}


def case_links(case_id: str, include_inactive: bool = False, lab: str | None = None) -> dict:
    return {"case_id": case_id, "links": rows(_g(lab), Q.CASE_LINKS,
                                               {"case_id": case_id, "include_inactive": include_inactive}, read_only=True)}


def score_pair(case_a: str, case_b: str, lab: str | None = None) -> dict:
    """Score two cases from independent evidence types, supporting and
    contradicting separately. Works for any pair, linked or not."""
    res = rows(_g(lab), Q.SCORE_PAIR, {"a": case_a, "b": case_b}, read_only=True)
    if not res:
        return {"error": "case not found"}
    r = res[0]
    r["verdict"] = "link" if r["score"] >= Q.LINK_THRESHOLD else "no link"
    r["threshold"] = Q.LINK_THRESHOLD
    return r


def cameras_for_cases(cases: list[str], lab: str | None = None) -> dict:
    """Cameras whose recordings were used in these cases, with any finding
    about the camera's reliability."""
    return {"cameras": rows(_g(lab), Q.CAMERAS_FOR_CASES, {"cases": cases}, read_only=True)}


def findings_under_superseded_sop(cases: list[str] | None = None, lab: str | None = None) -> dict:
    return {"findings": rows(_g(lab), Q.FINDINGS_UNDER_SUPERSEDED, {"cases": cases}, read_only=True)}


# ================================================================ memory/actions

def start_run(agent: str, goal: str, lab: str | None = None, run_id: str | None = None) -> str:
    run_id = run_id or f"RUN-{uuid.uuid4().hex[:8]}"
    _g(lab).query("MERGE (r:AgentRun {id: $id}) ON CREATE SET r.agent = $agent, r.goal = $goal, r.started = $ts, "
                  "r.status = 'running'", {"id": run_id, "agent": agent, "goal": goal, "ts": now()})
    return run_id


def finish_run(run_id: str, summary: str, lab: str | None = None):
    _g(lab).query("MATCH (r:AgentRun {id: $id}) SET r.status = 'done', r.finished = $ts, r.summary = $s",
                  {"id": run_id, "ts": now(), "s": summary})


def record_step(run_id: str, step: int, tool: str, args: dict, lab: str | None = None):
    """Checkpoint each tool call on the run so another agent can resume it."""
    _g(lab).query("MATCH (r:AgentRun {id: $id}) SET r.step = $step, r.last_tool = $tool, r.last_args = $args",
                  {"id": run_id, "step": step, "tool": tool, "args": json.dumps(args)})


def create_hypothesis(cases: list[str], summary: str, run_id: str, lab: str | None = None) -> dict:
    g = _g(lab)
    coh = _cohesion(cases, g)
    hid = f"H-{uuid.uuid4().hex[:6]}"
    rows(g, Q.CREATE_HYPOTHESIS, {"id": hid, "ts": now(), "by": run_id, "cases": cases, "summary": summary,
                                  "support": coh["mean_link_score"], "contra": 0.0})
    return {"hypothesis": hid, **coh}


def _action(kind: str, target: str, rationale: str, evidence: list[str], path: list[str],
            rejected: str, rejected_reason: str, run_id: str, lab: str | None, agent: str = "lead") -> dict:
    aid = f"ACT-{uuid.uuid4().hex[:6]}"
    res = rows(_g(lab), Q.RECORD_ACTION, {"run_id": run_id, "id": aid, "kind": kind, "target": target,
                                          "rationale": rationale, "path": json.dumps(path), "rejected": rejected,
                                          "rejected_reason": rejected_reason, "ts": now(), "agent": agent,
                                          "evidence": evidence})
    return {"action": aid, "kind": kind, "target": target, "evidence_links": res[0]["evidence_links"] if res else 0}


def request_retest(exhibit_id: str, method_sop: str, examiner: str, rationale: str, evidence: list[str],
                   path: list[str], rejected: str, rejected_reason: str, run_id: str, lab: str | None = None) -> dict:
    """Ask the lab to re-examine an exhibit under the current SOP by a named
    examiner. Use when a link is weak because of how a finding was made, and
    the exhibit itself is sound. Cite the evidence path and the rejected
    alternative."""
    g = _g(lab)
    g.query("MATCH (e:Exhibit {id: $x}) SET e.retest_status = 'requested', e.retest_sop = $sop, e.retest_examiner = $exm",
            {"x": exhibit_id, "sop": method_sop, "exm": examiner})
    return _action("request_retest", exhibit_id, rationale, evidence + [exhibit_id, examiner], path, rejected,
                   rejected_reason, run_id, lab)


def reopen_decision(decision_id: str, rationale: str, evidence: list[str], path: list[str], rejected: str,
                    rejected_reason: str, run_id: str, lab: str | None = None) -> dict:
    """Reopen a past decision whose basis no longer holds, e.g. an
    elimination that rests on a source later found unreliable."""
    _g(lab).query("MATCH (d:Decision {id: $d}) SET d.status = 'reopened', d.reopened = $ts, d.reopen_reason = $r",
                  {"d": decision_id, "ts": now(), "r": rationale})
    return _action("reopen_decision", decision_id, rationale, evidence + [decision_id], path, rejected,
                   rejected_reason, run_id, lab)


def hold_link(case_a: str, case_b: str, rationale: str, evidence: list[str], path: list[str], rejected: str,
              rejected_reason: str, run_id: str, lab: str | None = None) -> dict:
    """Withhold a link from the series because evidence on its path cannot be
    relied on (e.g. a custody gap). The link is kept with its reason but drops
    out of series searches until an examiner releases it."""
    rows(_g(lab), Q.SET_LINK_STATUS, {"a": case_a, "b": case_b, "status": "held", "reason": rationale,
                                      "ts": now(), "by": run_id})
    return _action("hold_link", f"{case_a}~{case_b}", rationale, evidence + [case_a, case_b], path, rejected,
                   rejected_reason, run_id, lab)


def notify_officer(station: str, message: str, evidence: list[str], path: list[str], run_id: str,
                   rejected: str = "", rejected_reason: str = "", lab: str | None = None) -> dict:
    """Records a notification for the station's officer in charge. Nothing is
    sent outside the lab graph; a human examiner releases it."""
    return _action("notify_officer", station, message, evidence + [station], path, rejected, rejected_reason,
                   run_id, lab)


def set_link_status(case_a: str, case_b: str, status: str, reason: str, run_id: str, lab: str | None = None) -> dict:
    """Mark a link accepted, rejected or disproved. Reasoning is kept on the
    edge; disproved and held links are removed from the series projection, so
    later searches exclude them."""
    assert status in ("proposed", "accepted", "rejected", "disproved", "held")
    return rows(_g(lab), Q.SET_LINK_STATUS, {"a": case_a, "b": case_b, "status": status, "reason": reason,
                                             "ts": now(), "by": run_id})[0]


def action_log(run_id: str | None = None, lab: str | None = None) -> list[dict]:
    return rows(_g(lab), """MATCH (r:AgentRun)-[:TOOK]->(a:ActionRecord) WHERE $id IS NULL OR r.id = $id
        OPTIONAL MATCH (a)-[:JUSTIFIED_BY]->(n)
        RETURN r.id AS run, a.id AS action, a.kind AS kind, a.target AS target, a.rationale AS rationale,
               a.rejected_alternative AS rejected, a.rejected_reason AS rejected_reason, a.path AS path,
               collect(n.id) AS evidence, a.ts AS ts ORDER BY ts""", {"id": run_id}, read_only=True)


CORE_TOOLS = [find_similar_exhibits, link_path, series_candidates, weakest_link,
              decisions_resting_on, custody_gaps, best_examiner]
SUPPORT_TOOLS = [case_summary, case_links, score_pair, cameras_for_cases, findings_under_superseded_sop]
