import { FormEvent, useState } from "react";
import { api, ago } from "../api";
import { go, useApp } from "../state";
import { Chip, Panel } from "../ui";

export default function Agents() {
  const { agents, refreshAgents, toast } = useApp();
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [desc, setDesc] = useState("");
  const [tools, setTools] = useState("");
  const [domains, setDomains] = useState("");
  const [err, setErr] = useState("");

  const create = async (e: FormEvent) => {
    e.preventDefault();
    try {
      const a = await api("/v1/agents", { body: {
        name, description: desc, tools: tools.split(/[\n,]+/).map((s) => s.trim()).filter(Boolean),
        internal_domains: domains.split(/[\n,\s]+/).map((s) => s.trim()).filter(Boolean) } });
      await refreshAgents();
      toast("Agent connected. Describe what it should do next.");
      go(`/agents/${a.id}/intent`);
    } catch (x: any) { setErr(x.message); }
  };

  return (
    <>
      <div className="row sp">
        <p className="muted" style={{ maxWidth: 640 }}>Each agent has a charter (what it should do), a share of the risk register, hooks that report to Varuna, and a response ladder.</p>
        <button className="btn p" onClick={() => setOpen(!open)}>{open ? "Cancel" : "Connect an agent"}</button>
      </div>
      {open && (
        <form className="panel" onSubmit={create}>
          <h3>Connect an agent</h3>
          <div className="grid g11">
            <label className="f">Name<input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Invoice Processing Agent" required minLength={2} /></label>
            <label className="f">What it does, in a line<input className="input" value={desc} onChange={(e) => setDesc(e.target.value)} /></label>
            <label className="f">Tool names it can call, one per line (optional)<textarea className="textarea" rows={4} value={tools} onChange={(e) => setTools(e.target.value)} placeholder={"read_invoice\nsend_email\npay_invoice"} /></label>
            <label className="f">Your organization's email domains (optional)<textarea className="textarea" rows={4} value={domains} onChange={(e) => setDomains(e.target.value)} placeholder="example.com" /></label>
          </div>
          {err && <div className="banner crit" role="alert">{err}</div>}
          <div className="row"><button className="btn p">Create and continue</button><span className="muted small">You get an API key on the next screen. Tool kinds are guessed from names and can be corrected later.</span></div>
        </form>
      )}
      <div className="grid g3">
        {agents.map((a) => (
          <section className="panel" key={a.id}>
            <div className="row sp"><h3>{a.name}</h3>
              {a.status === "paused" ? <Chip kind="crit">Paused</Chip> : a.status === "draft" ? <Chip kind="n">Draft</Chip> : <Chip kind="ok">Active</Chip>}</div>
            <p className="muted small">{a.description || "No description"}</p>
            <div className="row small muted">
              {a.is_demo && <Chip kind="info">Demo agent</Chip>}
              <span>Charter {a.charter_version ? `v${a.charter_version}` : "not set"}</span><span>Seen {ago(a.last_seen)}</span>
            </div>
            <div className="grid g3" style={{ gap: 8 }}>
              <div><div className="num" style={{ fontSize: 18 }}>{a.actions_24h ?? 0}</div><div className="muted small">actions 24h</div></div>
              <div><div className="num" style={{ fontSize: 18 }}>{a.deviation_rate ?? 0}%</div><div className="muted small">deviation</div></div>
              <div><div className="num" style={{ fontSize: 18 }}>{(a.blocked_24h ?? 0) + (a.held_24h ?? 0)}</div><div className="muted small">held or blocked</div></div>
            </div>
            <div className="row"><a className="btn p" href={`#/agents/${a.id}/intent`}>Open setup</a>
              <a className="btn" href={`#/trace`} onClick={() => localStorage.setItem("varuna_agent", a.id)}>Traces</a></div>
          </section>
        ))}
      </div>
      {!agents.length && <Panel><p className="muted">No agents yet.</p></Panel>}
    </>
  );
}
