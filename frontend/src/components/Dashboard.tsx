import { useEffect, useState } from "react";
import { api, type CaseRow, type InvestigableCase, type Stats } from "../api";

export default function Dashboard({ onInvestigate }: { onInvestigate: (key: string) => void }) {
  const [stats, setStats] = useState<Stats | null>(null);
  const [cases, setCases] = useState<CaseRow[]>([]);
  const [total, setTotal] = useState(0);
  const [investigable, setInvestigable] = useState<InvestigableCase[]>([]);
  const [page, setPage] = useState(0);
  const [err, setErr] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const pageSize = 15;

  useEffect(() => {
    void refresh();
  }, [page]);

  async function refresh() {
    setErr(null);
    setLoading(true);
    try {
      const [s, c, inv] = await Promise.all([
        api.stats(),
        api.cases(pageSize, page * pageSize),
        api.investigable(),
      ]);
      setStats(s);
      setCases(c.cases);
      setTotal(c.total);
      setInvestigable(inv.cases);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="dash">
      <div className="dash-head">
        <h1>Case Dashboard</h1>
        <p className="dash-sub">Southern Regional Forensic Lab &mdash; live from the loaded graph.</p>
      </div>

      {err && (
        <div className="network-err">
          Backend unreachable or waking from idle (free tier can take ~60s): {err}
          <button className="link-btn" onClick={refresh}>retry</button>
        </div>
      )}

      {stats && (
        <div className="stat-row">
          <Stat label="nodes" value={stats.nodes} />
          <Stat label="edges" value={stats.edges} />
          <Stat label="cases" value={stats.cases} />
          <Stat label="exhibits" value={stats.exhibits} />
        </div>
      )}

      <section className="panel">
        <div className="panel-head">
          <span>Investigable Cases</span>
          <span className="panel-note">new cases the Lead agent can investigate live</span>
        </div>
        <table className="case-table">
          <thead>
            <tr>
              <th>Case ID</th>
              <th>Station</th>
              <th>Type</th>
              <th>Date</th>
              <th />
            </tr>
          </thead>
          <tbody>
            {investigable.map((c) => (
              <tr key={c.key}>
                <td className="mono">{c.case_id}</td>
                <td>{c.station}</td>
                <td>{c.type}</td>
                <td>{c.date?.slice(0, 10)}</td>
                <td>
                  <button className="investigate-btn" onClick={() => onInvestigate(c.key)}>
                    Investigate
                  </button>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>

      <section className="panel">
        <div className="panel-head">
          <span>Lab History</span>
          <span className="panel-note">{total.toLocaleString()} cases on file</span>
        </div>
        <table className="case-table">
          <thead>
            <tr>
              <th>Case ID</th>
              <th>Station</th>
              <th>Type</th>
              <th>Date</th>
              <th>Status</th>
              <th>Exhibits</th>
            </tr>
          </thead>
          <tbody>
            {loading && cases.length === 0 ? (
              <tr><td colSpan={6} className="muted">Loading&hellip;</td></tr>
            ) : (
              cases.map((c) => (
                <tr key={c.id}>
                  <td className="mono">{c.id}</td>
                  <td>{c.station}</td>
                  <td>{c.type}</td>
                  <td>{c.date?.slice(0, 10)}</td>
                  <td><StatusTag status={c.status} /></td>
                  <td>{c.exhibits}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
        <div className="pager">
          <button disabled={page === 0} onClick={() => setPage((p) => Math.max(0, p - 1))}>&larr; prev</button>
          <span>
            {page * pageSize + 1}&ndash;{Math.min(total, (page + 1) * pageSize)} of {total.toLocaleString()}
          </span>
          <button disabled={(page + 1) * pageSize >= total} onClick={() => setPage((p) => p + 1)}>next &rarr;</button>
        </div>
      </section>
    </div>
  );
}

function Stat({ label, value }: { label: string; value: number }) {
  return (
    <div className="stat">
      <div className="stat-value">{value.toLocaleString()}</div>
      <div className="stat-label">{label}</div>
    </div>
  );
}

function StatusTag({ status }: { status: string }) {
  return <span className={`status-tag status-${status}`}>{status}</span>;
}
