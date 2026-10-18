import { useEffect, useMemo, useState } from "react";
import { marked } from "marked";
import { api, type InvestigateResult, type TraceStep } from "../api";

const TOOL_LABEL: Record<string, string> = {
  find_similar_exhibits: "Find Similar Exhibits",
  series_candidates: "Series Candidates",
  score_pair: "Score Pair",
  link_path: "Link Path",
  weakest_link: "Weakest Link",
  cameras_for_cases: "Cameras For Cases",
  decisions_resting_on: "Decisions Resting On",
  custody_gaps: "Custody Gaps",
  best_examiner: "Best Examiner",
  case_summary: "Case Summary",
  case_links: "Case Links",
  findings_under_superseded_sop: "Findings Under Superseded SOP",
  reopen_decision: "Reopen Decision",
  request_retest: "Request Re-test",
  hold_link: "Hold Link",
  notify_officer: "Notify Officer",
};

export default function AgentWorkflow({ caseKey, onBack }: { caseKey: string; onBack: () => void }) {
  const [result, setResult] = useState<InvestigateResult | null>(null);
  const [running, setRunning] = useState(false);
  const [revealed, setRevealed] = useState(0);
  const [err, setErr] = useState<string | null>(null);

  async function run() {
    setRunning(true);
    setErr(null);
    setResult(null);
    setRevealed(0);
    try {
      const r = await api.investigate(caseKey);
      setResult(r);
    } catch (e) {
      setErr(String(e));
    } finally {
      setRunning(false);
    }
  }

  // Reveal trace steps one at a time once the (real) result has arrived, so the
  // pipeline reads as a sequence rather than dumping everything at once.
  useEffect(() => {
    if (!result) return;
    if (revealed >= result.trace.length) return;
    const t = setTimeout(() => setRevealed((r) => r + 1), 180);
    return () => clearTimeout(t);
  }, [result, revealed]);

  const grouped = useMemo(() => {
    if (!result) return [];
    return result.trace.slice(0, revealed);
  }, [result, revealed]);

  return (
    <div className="workflow">
      <div className="workflow-head">
        <button className="back-link" onClick={onBack}>&larr; Dashboard</button>
        <h2>Agent Workflow</h2>
        {!result && !running && (
          <button className="primary" onClick={run}>Run investigation on this case</button>
        )}
        {running && <span className="muted">Lead agent is querying the graph&hellip;</span>}
        {result && (
          <button className="ghost" onClick={run} disabled={running}>Re-run</button>
        )}
      </div>

      {err && <div className="network-err">{err}</div>}

      {result && (
        <>
          <div className={`score-banner ${result.passed === result.total ? "ok" : "warn"}`}>
            {result.case_id} &middot; {result.passed}/{result.total} outcome checks passed &middot; run{" "}
            {result.run_id}
          </div>

          <section className="pipeline">
            {grouped.map((s) => (
              <PipelineCard key={s.step} step={s} />
            ))}
            {revealed < result.trace.length && <div className="pipeline-loading">running next tool call&hellip;</div>}
          </section>

          {revealed >= result.trace.length && (
            <>
              <section className="panel">
                <div className="panel-head"><span>Outcome Checks</span></div>
                <div className="checks">
                  {result.checks.map((c, i) => (
                    <div key={i} className={`check ${c.pass ? "pass" : "fail"}`}>
                      <span className="dot" />
                      <span>{c.label}</span>
                      {c.detail && <span className="check-detail">{c.detail}</span>}
                    </div>
                  ))}
                </div>
              </section>

              {result.actions.length > 0 && (
                <section className="panel">
                  <div className="panel-head"><span>Actions Taken</span></div>
                  <div className="actions">
                    {result.actions.map((a, i) => (
                      <div className="action" key={i}>
                        <span className="action-kind">{a.kind}</span>
                        <span className="action-target">{a.target}</span>
                        <span className="action-rejected">rejected: {a.rejected || "—"}</span>
                      </div>
                    ))}
                  </div>
                </section>
              )}

              <section className="panel">
                <div className="panel-head"><span>Examiner Report</span></div>
                <div
                  className="report"
                  dangerouslySetInnerHTML={{ __html: marked.parse(result.report) as string }}
                />
              </section>

              <section className="panel">
                <div className="panel-head">
                  <span>Agent Audit Log</span>
                  <span className="panel-note">{result.trace.length} graph tool calls, timed live</span>
                </div>
                <table className="audit-table">
                  <thead>
                    <tr>
                      <th>#</th>
                      <th>Tool</th>
                      <th>Kind</th>
                      <th>Elapsed</th>
                    </tr>
                  </thead>
                  <tbody>
                    {result.trace.map((s) => (
                      <tr key={s.step}>
                        <td>{s.step}</td>
                        <td>{TOOL_LABEL[s.tool] || s.tool}</td>
                        <td><span className={`kind-tag kind-${s.kind}`}>{s.kind}</span></td>
                        <td>{s.elapsed_ms} ms</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </section>
            </>
          )}
        </>
      )}
    </div>
  );
}

function PipelineCard({ step }: { step: TraceStep }) {
  const ok = !(step.result && typeof step.result === "object" && "error" in (step.result as object));
  return (
    <div className={`pipe-card ${step.kind}`}>
      <div className="pipe-tag">{step.kind === "action" ? "ACTION" : "GRAPH TOOL"}</div>
      <div className="pipe-title">{TOOL_LABEL[step.tool] || step.tool}</div>
      <div className="pipe-status">
        <span className={ok ? "dot-ok" : "dot-fail"} />
        {ok ? "complete" : "error"} &middot; {step.elapsed_ms} ms
      </div>
    </div>
  );
}
