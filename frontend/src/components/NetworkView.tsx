import { useEffect, useRef, useState } from "react";
import { Network } from "vis-network";
import { DataSet } from "vis-data";
import { api, type GraphView } from "../api";

const STATUS_COLOR: Record<string, string> = {
  unsolved: "#a3262c",
  open: "#c99a3a",
  closed: "#5c7a52",
};

export default function NetworkView() {
  const [caseId, setCaseId] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [view, setView] = useState<GraphView | null>(null);
  const [shownFor, setShownFor] = useState<string | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<Network | null>(null);

  useEffect(() => {
    // Default to a real series instead of the full sparse sample, which is
    // too scattered to read at a glance.
    api
      .investigable()
      .then((r) => {
        const first = r.cases[0]?.case_id;
        if (first) {
          setCaseId(first);
          load(first);
        } else {
          load();
        }
      })
      .catch(() => load());
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function load(id?: string) {
    setLoading(true);
    setErr(null);
    try {
      const data = id ? await api.series(id) : await api.overview(60);
      setView(data);
      setShownFor(id || null);
      if (id && data.nodes.length === 0) setErr(`No active series found for ${id} yet.`);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!view || !containerRef.current) return;
    const dense = view.edges.length > 40;
    const nodes = new DataSet(
      view.nodes.map((n) => ({
        id: n.id,
        label: n.id.replace("SRFL-", ""),
        title: `${n.id}\n${n.station || ""}\n${n.status || ""}`,
        color: { background: STATUS_COLOR[n.status] || "#8a7b50", border: "#1a140c" },
        font: { color: "#f3ead2", face: "Courier Prime", size: 13, strokeWidth: 3, strokeColor: "#1a140c" },
        shape: "dot",
        size: dense ? 14 : 22,
      })),
    );
    const edges = new DataSet(
      view.edges.map((e, i) => ({
        id: i,
        from: e.a,
        to: e.b,
        title: `${e.a} ↔ ${e.b}\nlink score ${e.score.toFixed(2)} · ${e.status}`,
        color: { color: "#8a7b50", highlight: "#a3262c", opacity: dense ? 0.5 : 0.85 },
        width: Math.max(1, e.score * 4),
      })),
    );
    if (networkRef.current) networkRef.current.destroy();
    const net = new Network(
      containerRef.current,
      { nodes, edges },
      {
        physics: {
          stabilization: { iterations: 150 },
          barnesHut: { gravitationalConstant: dense ? -6000 : -3000, springLength: dense ? 100 : 160 },
        },
        interaction: { hover: true, zoomView: true },
        nodes: { borderWidth: 2 },
      },
    );
    net.once("stabilizationIterationsDone", () => {
      net.setOptions({ physics: false });
      net.fit({ animation: { duration: 400, easingFunction: "easeInOutQuad" } });
    });
    networkRef.current = net;
    return () => {
      net.destroy();
      networkRef.current = null;
    };
  }, [view]);

  return (
    <div className="network-page">
      <div className="network-head">
        <h2>Evidence Network</h2>
        <p className="network-sub">
          Each node is a real case from the lab graph; each edge is an active link the agent's
          tools scored between two cases. Pick any case id to see the series it belongs to.
        </p>
        <div className="network-controls">
          <input
            value={caseId}
            onChange={(e) => setCaseId(e.target.value)}
            placeholder="SRFL-2026-0901"
            onKeyDown={(e) => e.key === "Enter" && load(caseId.trim() || undefined)}
          />
          <button onClick={() => load(caseId.trim() || undefined)} disabled={loading}>
            {loading ? "Loading…" : "Load series"}
          </button>
          <button className="ghost" onClick={() => { setCaseId(""); load(); }} disabled={loading}>
            Overview sample
          </button>
        </div>
        {err && <div className="network-err">{err}</div>}
      </div>
      <div className="network-canvas-wrap">
        <div className="network-canvas" ref={containerRef} />
        {view && view.nodes.length > 0 && (
          <div className="network-overlay">
            <div className="network-overlay-title">
              {shownFor ? `Case series around ${shownFor}` : "Sample of active case links"}
            </div>
            <div className="network-overlay-sub">
              {view.nodes.length} cases &middot; {view.edges.length} <code>LINKED</code> edges &middot;
              line thickness = link score &middot; hover an edge for its score
            </div>
            <div className="network-overlay-legend">
              <span className="legend-dot" style={{ background: STATUS_COLOR.unsolved }} /> unsolved
              <span className="legend-dot" style={{ background: STATUS_COLOR.open }} /> open
              <span className="legend-dot" style={{ background: STATUS_COLOR.closed }} /> closed
            </div>
          </div>
        )}
      </div>
    </div>
  );
}
