import { useEffect, useRef, useState } from "react";
import { Network } from "vis-network";
import { DataSet } from "vis-data";
import { api, type GraphView } from "../api";

export default function NetworkView() {
  const [caseId, setCaseId] = useState("");
  const [loading, setLoading] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [view, setView] = useState<GraphView | null>(null);
  const containerRef = useRef<HTMLDivElement>(null);
  const networkRef = useRef<Network | null>(null);

  useEffect(() => {
    load();
  }, []);

  async function load(id?: string) {
    setLoading(true);
    setErr(null);
    try {
      const data = id ? await api.series(id) : await api.overview(80);
      setView(data);
      if (id && data.nodes.length === 0) setErr(`No active series found for ${id} yet.`);
    } catch (e) {
      setErr(String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!view || !containerRef.current) return;
    const nodes = new DataSet(
      view.nodes.map((n) => ({
        id: n.id,
        label: n.id.replace("SRFL-", ""),
        title: `${n.id}\n${n.station || ""}\n${n.status || ""}`,
        color: {
          background: n.status === "unsolved" ? "#a3262c" : n.status === "open" ? "#c99a3a" : "#5c7a52",
          border: "#2a2015",
        },
        font: { color: "#e9ddc0", face: "Courier Prime" },
        shape: "dot",
        size: 16,
      })),
    );
    const edges = new DataSet(
      view.edges.map((e, i) => ({
        id: i,
        from: e.a,
        to: e.b,
        label: e.score.toFixed(2),
        color: { color: "#8a7b50", highlight: "#a3262c" },
        font: { color: "#b3a271", size: 10, face: "Courier Prime", strokeWidth: 0 },
        width: Math.max(1, e.score * 4),
      })),
    );
    if (networkRef.current) networkRef.current.destroy();
    networkRef.current = new Network(
      containerRef.current,
      { nodes, edges },
      {
        physics: { stabilization: true, barnesHut: { gravitationalConstant: -4000, springLength: 140 } },
        interaction: { hover: true },
        nodes: { borderWidth: 2 },
      },
    );
    return () => {
      networkRef.current?.destroy();
      networkRef.current = null;
    };
  }, [view]);

  return (
    <div className="network-page">
      <div className="network-head">
        <h2>Evidence Network</h2>
        <p className="network-sub">
          Cases as nodes, active <code>LINKED</code> edges between them, scored by the agent's tools.
          Red = unsolved, amber = open, green = closed.
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
      <div className="network-canvas" ref={containerRef} />
      {view && (
        <div className="network-meta">
          {view.nodes.length} cases &middot; {view.edges.length} active links shown
        </div>
      )}
    </div>
  );
}
