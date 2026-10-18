"""Lead agent: when a new case arrives, search the lab history, build a series
hypothesis, find its weakest link and choose the action that resolves it.

Two modes, same tools, same graph writes:

  --mode claude   Claude chooses which tools to call and writes the
                  explanation (needs ANTHROPIC_API_KEY).
  --mode offline  A rule policy chooses the next tool from what the graph
                  returned so far. Used for tests and when no API key is set.
                  It is deterministic, so it is also the benchmark baseline.

Either way, every tool call is checkpointed on an AgentRun node and every
action is an ActionRecord with its evidence path and rejected alternative.

Usage: python -m coldlink.agents.lead scenarios/scenario_a.json [--mode offline]
"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from .. import tools as T
from ..db import DEFAULT_LAB, get_graph
from ..load import load_case
from ..toolspec import spec

MODEL = os.environ.get("COLDLINK_MODEL", "claude-sonnet-5-5")

ACTION_TOOLS = [T.request_retest, T.reopen_decision, T.hold_link, T.notify_officer]
READ_TOOLS = T.CORE_TOOLS + T.SUPPORT_TOOLS

SYSTEM = """You are Coldlink's Lead agent in the Southern Regional Forensic Lab. A new case has
just been loaded into the lab graph. Your job:
1. Search the whole lab history for connections to it (marks, soles, vehicles, places, times, methods).
2. Build the series hypothesis the graph supports, scoring supporting and contradicting evidence separately,
   and explain why near-misses (similar but contradicted cases) are rejected.
3. Find the weakest link in the leading hypothesis and any decision that rests on an unreliable source.
4. Choose the action that resolves that weakness: request_retest, reopen_decision, notify_officer or hold_link.
   Choose from what the graph shows, not from a fixed routine. Every action must cite the evidence path
   (node ids) that justified it and name the alternative you rejected and why.
5. Call finish with a short report for the examiner. Every claim in it cites the exhibit, finding,
   procedure or decision ids behind it.
Rules: you propose links between evidence; you never decide guilt. Never name or identify a suspect or
subject, not even by their P- reference; say "a subject eliminated in <year>" instead. A human examiner
makes the final call. Scores come from the tools; do not invent numbers."""

FINISH = {"name": "finish", "description": "End the run with the examiner report (markdown).",
          "input_schema": {"type": "object", "properties": {"report": {"type": "string"},
                                                            "series": {"type": "array", "items": {"type": "string"}}},
                           "required": ["report"]}}


class Toolbox:
    """Executes tools, checkpoints each call on the AgentRun, keeps a trace."""

    def __init__(self, run_id: str, lab: str):
        self.run_id, self.lab, self.step, self.trace = run_id, lab, 0, []
        self.fns = {f.__name__: f for f in READ_TOOLS + ACTION_TOOLS}

    def call(self, name: str, **args):
        import time as _time
        self.step += 1
        T.record_step(self.run_id, self.step, name, args, lab=self.lab)
        fn = self.fns[name]
        kwargs = dict(args)
        if "run_id" in fn.__code__.co_varnames:
            kwargs["run_id"] = self.run_id
        kwargs["lab"] = self.lab
        t0 = _time.monotonic()
        try:
            out = fn(**kwargs)
        except Exception as e:  # surface tool errors to the model rather than crash
            out = {"error": f"{type(e).__name__}: {e}"}
        elapsed_ms = round((_time.monotonic() - t0) * 1000, 1)
        kind = "action" if name in {f.__name__ for f in ACTION_TOOLS} else "read"
        self.trace.append({"step": self.step, "tool": name, "kind": kind, "args": args,
                            "result": out, "elapsed_ms": elapsed_ms})
        return out


# ------------------------------------------------------------------ offline

def offline_policy(case_id: str, tb: Toolbox) -> dict:
    """Pick each next tool from the previous results. Branches on the graph:
    no series -> stop; contradicted near-miss -> score and reject; weakness
    type decides the action (custody gap -> hold, superseded SOP -> re-test)."""
    notes, rejected = [], []
    sim = tb.call("find_similar_exhibits", case_id=case_id)
    series = tb.call("series_candidates", case_id=case_id)["series"]
    if not series or series["size"] < 3:
        if series:
            notes.append(f"Only {series['size'] - 1} linked case(s); not enough for a series.")
        else:
            notes.append("No active links to the lab history.")
    members = series["cases"] if series else [case_id]
    # Near-misses: similar exhibits in cases outside the series, or with contradictions.
    near = {m["other_case"] for m in sim["matches"] if m["other_case"] not in members or m["contra"] > 0}
    for oc in sorted(near):
        sp = tb.call("score_pair", case_a=case_id, case_b=oc)
        if sp.get("verdict") == "no link":
            reasons = [e["basis"] for e in sp["evidence"] if e["ct"] > 0]
            if sp["entry_a"] and sp["entry_b"] and sp["entry_a"] != sp["entry_b"]:
                reasons.append(f"entry {sp['entry_b']} vs {sp['entry_a']}")
            if abs(sp["hour_a"] - sp["hour_b"]) % 24 >= 6:
                reasons.append(f"time {sp['hour_b']:02d}:00 vs {sp['hour_a']:02d}:00")
            rejected.append({"case": oc, "score": sp["score"], "support": sp["support"], "contra": sp["contra"],
                             "reasons": reasons})
    actions, weakest = [], None
    if series and series["size"] >= 3:
        wl = tb.call("weakest_link", cases=members)
        weakest = wl["weakest"]
        # Decisions resting on unreliable sources used anywhere in the series.
        cams = tb.call("cameras_for_cases", cases=members)["cameras"]
        for cam in {c["camera"] for c in cams if c["reliability_finding"]}:
            dr = tb.call("decisions_resting_on", source_id=cam)
            rel = dr["reliability_findings"][0]
            for d in dr["decisions"]:
                if d["at_risk"] and d["status"] == "active":
                    actions.append(tb.call(
                        "reopen_decision", decision_id=d["decision"],
                        rationale=(f"{d['decision']} rests on {cam} ({' -> '.join(d['path'])}). {rel['finding']} found "
                                   f"the clock runs {rel['clock_offset_minutes']} min fast; {d['assessment']}."),
                        evidence=[rel["finding"], d["via_finding"]], path=d["path"] + [rel["finding"]],
                        rejected="leave decision standing",
                        rejected_reason="the time margin the decision relied on is smaller than the known clock error"))
                elif d["at_risk"] and d["status"] != "active":
                    notes.append(f"{d['decision']} rests on {cam} and is already {d['status']}.")
                elif d["at_risk"] is False:
                    notes.append(f"{d['decision']} also rests on {cam} but survives: {d['assessment']}.")
        gaps = tb.call("custody_gaps", case_ids=members)["gaps"]
        if weakest:
            e = weakest["edge"]
            kinds = {i["issue"] for i in e["issues"]}
            path = [e["a"], *[x for p in e["pairs"] for x in p.split("~")], e["b"]]
            if "custody_gap" in kinds:
                gap = next(i for i in e["issues"] if i["issue"] == "custody_gap")
                actions.append(tb.call(
                    "hold_link", case_a=e["a"], case_b=e["b"],
                    rationale=(f"The link {e['a']} ~ {e['b']} is the only link holding {weakest['member']} in the series "
                               f"and rests on {gap['exhibit']}, which has a {gap['detail']}."),
                    evidence=[gap["exhibit"]], path=path,
                    rejected="request_retest",
                    rejected_reason="a re-test re-measures the exhibit but cannot repair its chain of custody"))
                st = tb.call("case_summary", case_id=case_id)["station"]
                actions.append(tb.call(
                    "notify_officer", station=st,
                    message=(f"Possible link from {case_id} to {gap['case']} withheld: the supporting exhibit "
                             f"{gap['exhibit']} has a custody gap. Examiner review needed before any use."),
                    evidence=[gap["exhibit"], case_id], path=path))
            elif "superseded_sop" in kinds:
                iss = [i for i in e["issues"] if i["issue"] == "superseded_sop"]
                target = next((i for i in iss if i["case"] == weakest["member"]), iss[0])
                method = {"footwear": "M-FW", "toolmark": "M-TM", "vehicle": "M-VEH"}[target["type"]]
                be = tb.call("best_examiner", method=method)["ranking"][0]
                actions.append(tb.call(
                    "request_retest", exhibit_id=target["exhibit"], method_sop=target["current_sop"],
                    examiner=be["examiner"],
                    rationale=(f"{weakest['member']} is held in the series only by {target['type']} links "
                               f"(best effective score {weakest['member_attachment']}), all resting on "
                               f"{target['exhibit']}, {target['detail']}. A re-test under {target['current_sop']} "
                               f"compares the sub-type the old SOP did not record."),
                    evidence=[target["exhibit"]], path=path,
                    rejected="hold_link",
                    rejected_reason="custody of the exhibit is intact; the weakness is the measurement standard, which a re-test resolves"))
            else:
                notes.append(f"Weakest link {e['a']} ~ {e['b']} has no reliability issue; no action needed.")
        on_links = {x for le in wl["flagged_links"] for p in le["pairs"] for x in p.split("~")}
        for gp in gaps:
            if gp["exhibit"] not in on_links:
                notes.append(f"{gp['exhibit']} has a {gp['gap_days']}-day custody gap but no active link rests on it.")
        if any(a["kind"] == "hold_link" for a in actions):
            after = tb.call("series_candidates", case_id=case_id)["series"]
            members = after["cases"] if after else [case_id]
            notes.append(f"After the hold, {case_id} has " + (f"{len(members) - 1} active link(s) to the series."
                         if after else "no active link to the series; the link waits for examiner review."))
        hyp = (T.create_hypothesis(members, f"Series of {len(members)} cases around {case_id}", tb.run_id, lab=tb.lab)
               if len(members) >= 3 else None)
    else:
        hyp = None
    return {"series": series, "rejected": rejected, "weakest": weakest, "actions": actions, "notes": notes,
            "hypothesis": hyp["hypothesis"] if hyp else None}


def offline_report(case_id: str, r: dict) -> str:
    L = [f"# Coldlink report for {case_id}", ""]
    s = r["series"]
    if s:
        L += [f"**Series candidate:** {s['size']} cases, {s['links']} active links, mean link score "
              f"{s['mean_link_score']}, evidence types {', '.join(s['evidence_types'])}.",
              "", "Cases: " + ", ".join(s["cases"]), ""]
        if s["accepted_links"]:
            L.append("Previously linked by examiners: " + "; ".join(" ~ ".join(p) for p in s["accepted_links"]))
            L.append("")
    if r["rejected"]:
        L.append("## Rejected near-misses")
        for x in r["rejected"]:
            L.append(f"- {x['case']}: score {x['score']} (support {x['support']}, contra {x['contra']}). "
                     + "; ".join(x["reasons"]))
        L.append("")
    if r["weakest"]:
        e = r["weakest"]["edge"]
        L += ["## Weakest link", f"{e['a']} ~ {e['b']} (score {e['score']}, effective {e['effective']}): "
              + "; ".join(e["basis"]), "Issues: " + "; ".join(f"{i['exhibit']} {i['detail']}" for i in e["issues"]), ""]
    if r["actions"]:
        L.append("## Actions")
        for a in r["actions"]:
            L.append(f"- {a['kind']} on {a['target']} ({a['action']})")
        L.append("")
    if r["notes"]:
        L.append("## Notes")
        L += [f"- {n}" for n in r["notes"]]
    L += ["", "_Coldlink proposes links between evidence. It does not decide guilt; an examiner makes the final call._"]
    return "\n".join(L)


# ------------------------------------------------------------------- claude

def claude_loop(case_id: str, tb: Toolbox, max_turns: int = 30) -> dict:
    import anthropic

    client = anthropic.Anthropic()
    tools = [spec(f) for f in READ_TOOLS + ACTION_TOOLS] + [FINISH]
    messages = [{"role": "user", "content": f"New case {case_id} has been loaded. Investigate and act."}]
    for _ in range(max_turns):
        resp = client.messages.create(model=MODEL, max_tokens=4000, system=SYSTEM, tools=tools, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        uses = [b for b in resp.content if b.type == "tool_use"]
        if not uses:
            return {"report": "".join(b.text for b in resp.content if b.type == "text"), "series": None}
        results = []
        for u in uses:
            if u.name == "finish":
                return {"report": u.input["report"], "series": u.input.get("series")}
            out = tb.call(u.name, **u.input)
            results.append({"type": "tool_result", "tool_use_id": u.id, "content": json.dumps(out, default=str)[:20000]})
        messages.append({"role": "user", "content": results})
    return {"report": "Stopped: turn limit reached.", "series": None}


# ---------------------------------------------------------------------- groq

def _openai_tools() -> list[dict]:
    """Same tool specs as Claude mode, reshaped for OpenAI-style function calling."""
    specs = [spec(f) for f in READ_TOOLS + ACTION_TOOLS] + [FINISH]
    return [{"type": "function", "function": {"name": s["name"], "description": s["description"],
                                               "parameters": s["input_schema"]}} for s in specs]


def groq_loop(case_id: str, tb: Toolbox, max_turns: int = 50,
              model: str = "openai/gpt-oss-120b") -> dict:
    """Same agent loop as claude_loop, driven by Groq's OpenAI-compatible API
    instead of Anthropic's, since that is the live key available."""
    import os as _os
    import re as _re
    import time as _time
    import urllib.error
    import urllib.request

    api_key = _os.environ["GROQ_API_KEY"]
    url = "https://api.groq.com/openai/v1/chat/completions"
    tools = _openai_tools()
    messages = [{"role": "system", "content": SYSTEM},
                {"role": "user", "content": f"New case {case_id} has been loaded. Investigate and act."}]
    for _ in range(max_turns):
        body = json.dumps({"model": model, "messages": messages, "tools": tools, "tool_choice": "auto",
                            "max_tokens": 4000}).encode()
        req = urllib.request.Request(url, data=body, method="POST", headers={
            "Authorization": f"Bearer {api_key}", "Content-Type": "application/json",
            "User-Agent": "curl/8.7.1"})
        try:
            with urllib.request.urlopen(req) as resp:
                data = json.loads(resp.read())
        except urllib.error.HTTPError as e:
            err = e.read().decode()
            if e.code == 429:
                m = _re.search(r"try again in ([\d.]+)s", err)
                _time.sleep(float(m.group(1)) + 1 if m else 15)
                continue
            if e.code == 400 and "tool_use_failed" in err:
                # Groq rejects a malformed tool call before it reaches us; recover by
                # replaying the model's bad call and its validator message as a normal
                # tool error, so the model can see the mistake and retry.
                try:
                    detail = json.loads(err)["error"]
                    bad = json.loads(detail["failed_generation"])
                except Exception:
                    return {"report": f"Groq API error {e.code}: {err[:500]}", "series": None}
                cid = f"retry-{tb.step}"
                messages.append({"role": "assistant", "content": None,
                                  "tool_calls": [{"id": cid, "type": "function",
                                                   "function": {"name": bad["name"],
                                                                "arguments": json.dumps(bad["arguments"])}}]})
                messages.append({"role": "tool", "tool_call_id": cid,
                                  "content": json.dumps({"error": detail["message"]})})
                continue
            return {"report": f"Groq API error {e.code}: {err[:500]}", "series": None}
        msg = data["choices"][0]["message"]
        messages.append(msg)
        calls = msg.get("tool_calls") or []
        if not calls:
            return {"report": msg.get("content") or "Stopped: model returned no tool calls.", "series": None}
        for c in calls:
            name = c["function"]["name"]
            args = json.loads(c["function"]["arguments"] or "{}")
            if name == "finish":
                return {"report": args["report"], "series": args.get("series")}
            out = tb.call(name, **args)
            messages.append({"role": "tool", "tool_call_id": c["id"], "content": json.dumps(out, default=str)[:20000]})
    return {"report": "Stopped: turn limit reached.", "series": None}


# --------------------------------------------------------------------- main

def investigate_case(case_id: str, mode: str = "auto", lab: str = DEFAULT_LAB) -> dict:
    """Run the Lead agent on a case already present in the graph (ingestion
    is the caller's job -- see run() for the scenario-file path, or ingest a
    case dict directly with load.load_case)."""
    if mode == "auto":
        mode = ("claude" if os.environ.get("ANTHROPIC_API_KEY")
                 else "groq" if os.environ.get("GROQ_API_KEY") else "offline")
    run_id = T.start_run("lead", f"investigate {case_id}", lab=lab)
    tb = Toolbox(run_id, lab)
    if mode == "claude":
        out = claude_loop(case_id, tb)
        report = out["report"]
    elif mode == "groq":
        out = groq_loop(case_id, tb)
        report = out["report"]
    else:
        out = offline_policy(case_id, tb)
        report = offline_report(case_id, out)
    T.finish_run(run_id, report[:2000], lab=lab)
    Path("runs").mkdir(exist_ok=True)
    Path(f"runs/{run_id}.md").write_text(report)
    Path(f"runs/{run_id}.trace.json").write_text(json.dumps(tb.trace, indent=1, default=str))
    return {"run_id": run_id, "mode": mode, "case_id": case_id, "report": report, "result": out,
            "actions": T.action_log(run_id, lab=lab), "steps": tb.step, "trace": tb.trace}


def run(scenario_path: str, mode: str = "auto", lab: str = DEFAULT_LAB, ingest: bool = True) -> dict:
    sc = json.load(open(scenario_path))
    case_id = sc["case"]["id"]
    g = get_graph(lab)
    if ingest and not g.query("MATCH (c:Case {id: $id}) RETURN c", {"id": case_id}).result_set:
        load_case(g, sc)
    return investigate_case(case_id, mode, lab)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("scenario")
    ap.add_argument("--mode", choices=["auto", "claude", "groq", "offline"], default="auto")
    ap.add_argument("--lab", default=DEFAULT_LAB)
    a = ap.parse_args()
    r = run(a.scenario, a.mode, a.lab)
    print(r["report"])
    print(f"\n[{r['mode']}] run {r['run_id']}: {r['steps']} tool calls, {len(r['actions'])} actions")
    for x in r["actions"]:
        print(f"  {x['kind']:16} {x['target']:32} rejected: {x['rejected']}")


if __name__ == "__main__":
    main()
