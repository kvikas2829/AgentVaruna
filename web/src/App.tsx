import { FormEvent, useEffect, useState } from "react";
import { api, getToken, setToken } from "./api";
import { AppProvider, go, useApp, useRoute } from "./state";
import Overview from "./pages/Overview";
import Agents from "./pages/Agents";
import AgentDetail from "./pages/AgentDetail";
import Trace from "./pages/Trace";
import Approvals from "./pages/Approvals";
import Demo from "./pages/Demo";
import Settings from "./pages/Settings";
import Company from "./pages/Company";

const Eye = ({ size = 22 }: { size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 36 36" aria-hidden="true">
    <path d="M3 18c4-6 9-10 15-10s11 4 15 10c-4 6-9 10-15 10S7 24 3 18z" fill="none" stroke="var(--accent)" strokeWidth="2.6" />
    <circle cx="18" cy="18" r="5" fill="var(--accent)" />
  </svg>
);

function Login({ onDone }: { onDone: () => void }) {
  const [u, setU] = useState("admin");
  const [p, setP] = useState("");
  const [err, setErr] = useState("");
  const [busy, setBusy] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    try {
      const r = await api("/v1/auth/login", { body: { username: u, password: p } });
      setToken(r.token);
      onDone();
    } catch (x: any) { setErr(x.message); } finally { setBusy(false); }
  };
  return (
    <div className="login">
      <form onSubmit={submit} className="panel" style={{ padding: 24 }}>
        <div className="row"><Eye size={30} /><h1 style={{ fontSize: 24 }}>Agent Varuna</h1></div>
        <p className="muted">Sign in to watch your agents.</p>
        <label className="f">Username<input className="input" value={u} onChange={(e) => setU(e.target.value)} autoComplete="username" /></label>
        <label className="f">Password<input className="input" type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" autoFocus /></label>
        {err && <div className="banner crit" role="alert">{err}</div>}
        <button className="btn p" disabled={busy || !p}>{busy ? "Signing in" : "Sign in"}</button>
      </form>
    </div>
  );
}

const NAV: [string, string][] = [["overview", "Overview"], ["agents", "Agents"], ["trace", "Live trace"], ["approvals", "Approvals"], ["demo", "Demo sandbox"]];
const TITLES: Record<string, string> = { overview: "Overview", agents: "Agents", trace: "Live trace", approvals: "Approvals", demo: "Demo sandbox", settings: "Settings", company: "Company policy" };

function modeLabel(meta: any, agents: any[]) {
  const modes = new Set(agents.map((a: any) => a.mode).filter(Boolean));
  if (modes.size > 1) return "Mixed modes";
  const m = [...modes][0] || meta?.mode?.default;
  return m === "observe" ? "Observation only" : "Enforcing";
}

function Shell({ onLogout }: { onLogout: () => void }) {
  const { agents, agentId, setAgentId, connected, pending, meta } = useApp();
  const { parts } = useRoute();
  const page = parts[0] || "overview";
  useEffect(() => { if (!location.hash) go("/overview"); }, []);
  const scoped = page === "overview" || page === "trace" || page === "approvals";
  return (
    <div className="shell">
      <aside className="side">
        <div className="brand"><Eye />Varuna</div>
        {NAV.map(([k, label]) => (
          <a key={k} className={`nav ${page === k ? "on" : ""}`} href={`#/${k}`}>
            {label}{k === "approvals" && pending > 0 && <span className="badge">{pending}</span>}
          </a>
        ))}
        <div className="sep">Admin</div>
        <a className={`nav ${page === "company" ? "on" : ""}`} href="#/company">Company policy</a>
        <a className={`nav ${page === "settings" ? "on" : ""}`} href="#/settings">Settings</a>
        <div className="foot">
          <a href="#/settings" className={`chip ${modeLabel(meta, agents) === "Enforcing" ? "ok" : "warn"}`} style={{ textDecoration: "none" }} title="Observation or enforcement mode">{modeLabel(meta, agents)}</a>
          <span>Judge: {meta?.judge === "claude" ? `Claude (${meta.judge_model})` : meta?.judge === "local" ? `local model (${meta.judge_model})` : "offline heuristic"}</span>
          <button className="btn s" onClick={onLogout}>Sign out</button>
        </div>
      </aside>
      <div className="main">
        <header className="top">
          <h1>{TITLES[page] || "Agent Varuna"}</h1>
          <div className="row">
            {scoped && (
              <label className="row small muted">Agent
                <select className="select" value={agentId} onChange={(e) => setAgentId(e.target.value)} aria-label="Agent filter">
                  <option value="all">All agents</option>
                  {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                </select>
              </label>
            )}
            <span className={`live ${connected ? "" : "off"}`}>{connected ? "Live" : "Reconnecting"}</span>
          </div>
        </header>
        <main className="content">
          {page === "overview" && <Overview />}
          {page === "agents" && (parts[1] ? <AgentDetail id={parts[1]} tab={parts[2] || "intent"} /> : <Agents />)}
          {page === "trace" && <Trace sessionId={parts[1]} />}
          {page === "approvals" && <Approvals />}
          {page === "demo" && <Demo agentKey={parts[1]} />}
          {page === "settings" && <Settings />}
          {page === "company" && <Company />}
        </main>
      </div>
    </div>
  );
}

export default function App() {
  const [authed, setAuthed] = useState(!!getToken());
  useEffect(() => {
    const f = () => setAuthed(false);
    window.addEventListener("varuna-logout", f);
    return () => window.removeEventListener("varuna-logout", f);
  }, []);
  if (!authed) return <Login onDone={() => setAuthed(true)} />;
  return <AppProvider><Shell onLogout={() => { setToken(null); setAuthed(false); }} /></AppProvider>;
}
