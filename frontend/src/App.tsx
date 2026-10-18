import { useState } from "react";
import "./theme.css";
import "./App.css";
import Dashboard from "./components/Dashboard";
import NetworkView from "./components/NetworkView";
import AgentWorkflow from "./components/AgentWorkflow";

type View = { page: "dashboard" } | { page: "network" } | { page: "workflow"; key: string };

export default function App() {
  const [view, setView] = useState<View>({ page: "dashboard" });

  return (
    <div className="shell">
      <nav className="topnav">
        <div className="brand" onClick={() => setView({ page: "dashboard" })}>
          <span className="brand-mark">&#9679;</span> COLDLINK
        </div>
        <div className="navlinks">
          <button
            className={view.page === "dashboard" ? "navlink active" : "navlink"}
            onClick={() => setView({ page: "dashboard" })}
          >
            Case Dashboard
          </button>
          <button
            className={view.page === "network" ? "navlink active" : "navlink"}
            onClick={() => setView({ page: "network" })}
          >
            Network
          </button>
        </div>
        <a className="source-link" href="https://github.com/AR-droid/COLDLINK" target="_blank" rel="noreferrer">
          Source
        </a>
      </nav>

      <main className="main">
        {view.page === "dashboard" && (
          <Dashboard onInvestigate={(key) => setView({ page: "workflow", key })} />
        )}
        {view.page === "network" && <NetworkView />}
        {view.page === "workflow" && (
          <AgentWorkflow caseKey={view.key} onBack={() => setView({ page: "dashboard" })} />
        )}
      </main>

      <footer className="sitefoot">
        Coldlink proposes links between evidence. It does not decide guilt; an examiner makes the
        final call. All case data is synthetic &mdash; built for Graph Hacks: Context for AI Agents
        (WeMakeDevs &times; FalkorDB).
      </footer>
    </div>
  );
}
