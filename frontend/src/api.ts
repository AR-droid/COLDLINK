export const API = import.meta.env.VITE_API_URL || "https://coldlink.onrender.com";

export type Stats = { nodes: number; edges: number; cases: number; exhibits: number };
export type CaseRow = {
  id: string;
  date: string;
  status: string;
  type: string;
  station: string;
  exhibits: number;
};
export type InvestigableCase = {
  key: string;
  case_id: string;
  station: string;
  date: string;
  type: string;
  location: string;
};
export type Check = { label: string; pass: boolean; detail: string };
export type Action = { kind: string; target: string; action: string; rejected: string };
export type TraceStep = {
  step: number;
  tool: string;
  kind: "read" | "action";
  args: Record<string, unknown>;
  result: unknown;
  elapsed_ms: number;
};
export type InvestigateResult = {
  mode: string;
  run_id: string;
  case_id: string;
  report: string;
  actions: Action[];
  trace: TraceStep[];
  checks: Check[];
  passed: number;
  total: number;
  retest?: unknown[];
};
export type GraphNode = { id: string; status: string; date: string; station: string };
export type GraphEdge = { a: string; b: string; score: number; status: string };
export type GraphView = { nodes: GraphNode[]; edges: GraphEdge[] };

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${API}${path}`);
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}`);
  return res.json();
}

async function post<T>(path: string): Promise<T> {
  const res = await fetch(`${API}${path}`, { method: "POST" });
  if (!res.ok) throw new Error(`${path} -> HTTP ${res.status}: ${await res.text()}`);
  return res.json();
}

export const api = {
  health: () => get<{ status: string; nodes: number }>("/health"),
  stats: () => get<Stats>("/graph/stats"),
  cases: (limit = 50, offset = 0) => get<{ total: number; cases: CaseRow[] }>(`/cases?limit=${limit}&offset=${offset}`),
  investigable: () => get<{ cases: InvestigableCase[] }>("/cases/investigable"),
  overview: (limit = 60) => get<GraphView>(`/graph/overview?limit=${limit}`),
  series: (caseId: string) => get<GraphView>(`/graph/series/${caseId}`),
  investigate: (key: string, mode = "offline") => post<InvestigateResult>(`/investigate/${key}?mode=${mode}`),
  reset: () => post<{ status: string }>("/demo/reset"),
};
