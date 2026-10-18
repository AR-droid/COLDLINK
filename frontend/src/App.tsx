import { useState } from "react";
import "./theme.css";
import "./App.css";
import Dashboard from "./components/Dashboard";
import NetworkView from "./components/NetworkView";
import AgentWorkflow from "./components/AgentWorkflow";
import NewCase from "./components/NewCase";
import { api, type CaseIn, type InvestigateResult } from "./api";

type View =
  | { page: "dashboard" }
  | { page: "network" }
  | { page: "newcase" }
  | { page: "workflow"; runner: () => Promise<InvestigateResult>; autoRun: boolean };

export default function App() {
  const [view, setView] = useState<View>({ page: "dashboard" });

  function investigateScenario(key: string) {
    setView({ page: "workflow", runner: () => api.investigate(key), autoRun: false });
  }

  function investigateUpload(body: CaseIn) {
    setView({ page: "workflow", runner: () => api.uploadCase(body), autoRun: true });
  }

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
            className={view.page === "newcase" ? "navlink active" : "navlink"}
            onClick={() => setView({ page: "newcase" })}
          >
            New Case
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
        {view.page === "dashboard" && <Dashboard onInvestigate={investigateScenario} />}
        {view.page === "network" && <NetworkView />}
        {view.page === "newcase" && <NewCase onSubmit={investigateUpload} />}
        {view.page === "workflow" && (
          <AgentWorkflow
            runner={view.runner}
            autoRun={view.autoRun}
            onBack={() => setView({ page: "dashboard" })}
          />
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
