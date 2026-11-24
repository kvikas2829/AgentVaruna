import { useEffect, useState } from "react";
import { ago, api } from "../api";
import { useApp, useData } from "../state";
import { Chip, Empty, Panel } from "../ui";

const CATS = ["financial", "data_leakage", "legal", "reputational", "morale", "operational"];
const catLabel = (c: string) => c.replace("_", " ");

function Compiled({ data }: { data: any }) {
  if (!data) return null;
  const lim = data.limits || {};
  return (
    <div className="stack">
      {!!lim.amount_rules?.length && <div className="why"><b>Spending limits</b><span>{lim.amount_rules.map((r: any) =>
        `$${Number(r.max).toLocaleString()} ${r.topic ? `on ${r.topic} (${r.scope.join(", ")})` : "on any single payment"}`).join(" · ")}</span></div>}
      {!!data.forbidden_resources?.length && <div className="why"><b>Off limits to every agent</b><span>{data.forbidden_resources.join(", ")}</span></div>}
      {!!lim.protected_targets?.length && <div className="why"><b>Critical systems (changes need approval)</b><span>{lim.protected_targets.join(", ")}</span></div>}
      {!!lim.allowed_domains?.length && <div className="why"><b>Internal domains</b><span>{lim.allowed_domains.join(", ")}</span></div>}
      <div className="why"><b>Rules the judge weighs</b><span>{(data.rules || []).length ? <ul style={{ margin: 0, paddingLeft: 18 }}>{data.rules.map((r: string, i: number) => <li key={i}>{r}</li>)}</ul> : "None found"}</span></div>
    </div>
  );
}

function PolicyEditor() {
  const { toast } = useApp();
  const { data, reload } = useData<any>("/v1/company/policy");
  const [text, setText] = useState<string | null>(null);
  const [preview, setPreview] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const active = data?.active;
  useEffect(() => { if (active && text === null) setText(active.source_text); }, [active, text]);
  const dirty = text !== null && active && text !== active.source_text;
  const doPreview = async () => { const r = await api("/v1/company/policy/compile", { body: { text } }); setPreview(r.data); };
  const save = async () => {
    setBusy(true);
    try { const r = await api("/v1/company/policy", { method: "PUT", body: { text } }); toast(`Company policy v${r.version} is now active`); setPreview(null); await reload(); }
    catch (e: any) { toast(e.message); } finally { setBusy(false); }
  };
  const activate = async (v: number) => { const r = await api(`/v1/company/policy/${v}/activate`, { body: {} }); setText(r.source_text); setPreview(null); toast(`Version ${v} is active again`); reload(); };
  if (!data) return <Empty>Loading</Empty>;
  return (
    <div className="grid g11">
      <Panel title="Company policy" note={active ? `v${active.version} active · saved ${ago(active.created_at)}` : "None yet"}>
        <p className="muted small">Plain English. Every agent is judged against this as well as its own charter, and the stricter of the two wins, so a charter that allows more cannot switch these rules off.</p>
        <textarea className="textarea" rows={14} value={text ?? ""} onChange={(e) => { setText(e.target.value); setPreview(null); }} aria-label="Company policy text" />
        <div className="row" style={{ marginTop: 8 }}>
          <button className="btn" onClick={doPreview} disabled={!text || text.trim().length < 10}>Preview rules</button>
          <button className="btn p" onClick={save} disabled={busy || !dirty}>{busy ? "Saving" : "Save as new version"}</button>
          {dirty && <span className="muted small">Unsaved changes</span>}
        </div>
        {data.history?.length > 1 && (<>
          <div className="lab" style={{ marginTop: 14 }}>Versions</div>
          <div className="stack">{data.history.map((h: any) => (
            <div key={h.version} className="row sp small"><span>v{h.version} · {ago(h.created_at)}{h.created_by ? ` · ${h.created_by}` : ""}</span>
              {h.status === "active" ? <Chip kind="ok">Active</Chip> : <button className="btn s" onClick={() => activate(h.version)}>Make active</button>}</div>))}</div>
        </>)}
      </Panel>
      <Panel title={preview ? "Preview: what Varuna will check" : "What Varuna checks"} note={preview ? "Not saved yet" : undefined}>
        <Compiled data={preview || active?.data} />
      </Panel>
    </div>
  );
}

function RiskRow({ r, onSave, onDelete }: { r: any; onSave: (r: any) => void; onDelete?: () => void }) {
  const [f, setF] = useState<any>(r);
  useEffect(() => setF(r), [r]);
  const dirty = JSON.stringify(f) !== JSON.stringify(r);
  const set = (k: string, v: any) => setF({ ...f, [k]: v });
  return (
    <tr>
      <td><input className="input" value={f.name} onChange={(e) => set("name", e.target.value)} aria-label="Risk name" style={{ minWidth: 220 }} />
        <input className="input small" value={f.description || ""} onChange={(e) => set("description", e.target.value)} aria-label="Description" placeholder="What could go wrong" style={{ marginTop: 4 }} /></td>
      <td><select className="select" value={f.category} onChange={(e) => set("category", e.target.value)} aria-label="Category">{CATS.map((c) => <option key={c} value={c}>{catLabel(c)}</option>)}</select></td>
      <td><select className="select" value={f.severity} onChange={(e) => set("severity", Number(e.target.value))} aria-label="Severity">{[1, 2, 3, 4, 5].map((n) => <option key={n} value={n}>{n}</option>)}</select></td>
      <td><input className="input" value={f.extent_text || ""} onChange={(e) => set("extent_text", e.target.value)} aria-label="Extent" />
        <label className="row small muted" style={{ marginTop: 4 }}>Spread
          <input className="input" type="number" min={0.1} max={1} step={0.05} value={f.extent_factor} onChange={(e) => set("extent_factor", Number(e.target.value))} aria-label="Extent factor" style={{ width: 70 }} /></label></td>
      <td className="row">
        <button className="btn s p" disabled={!dirty || f.name.trim().length < 3} onClick={() => onSave(f)}>{r.id ? "Save" : "Add"}</button>
        {onDelete && <button className="btn s" onClick={onDelete} aria-label={`Remove ${r.name}`}>Remove</button>}
      </td>
    </tr>
  );
}

const BLANK = { name: "", description: "", category: "legal", severity: 3, extent_text: "", extent_factor: 0.8, applies_to: [] };

function RiskRegister() {
  const { toast } = useApp();
  const { data: risks, reload } = useData<any[]>("/v1/risks");
  const { data: agents } = useData<any[]>("/v1/agents");
  const company = (risks || []).filter((r) => r.scope === "company");
  const agentRisks = (risks || []).filter((r) => r.scope === "agent");
  const name = (id: string) => agents?.find((a) => a.id === id)?.name || id;
  const save = async (r: any) => {
    const body = { name: r.name, description: r.description || "", category: r.category, severity: r.severity, extent_text: r.extent_text || "", extent_factor: r.extent_factor, applies_to: [] };
    if (r.id) await api(`/v1/risks/${r.id}`, { method: "PUT", body }); else await api("/v1/risks", { body });
    toast("Risk register saved"); reload();
  };
  const del = async (id: number) => { await api(`/v1/risks/${id}`, { method: "DELETE" }); reload(); };
  return (
    <>
      <Panel title="Company risk register" note="Applies to every agent">
        <p className="muted small">Risk is scored against these for every agent, together with each agent's own risks. Within a category the most serious risk counts. Severity 1 to 5; spread from 0.1 (one person) to 1 (company-wide).</p>
        {!risks ? <Empty>Loading</Empty> : (
          <div className="tbl"><table>
            <thead><tr><th>Risk</th><th>Category</th><th>Severity</th><th>Extent</th><th><span className="sr-only">Actions</span></th></tr></thead>
            <tbody>
              {company.map((r) => <RiskRow key={r.id} r={r} onSave={save} onDelete={() => del(r.id)} />)}
              <RiskRow key={`new-${company.length}`} r={BLANK} onSave={save} />
            </tbody>
          </table></div>)}
      </Panel>
      <Panel title="Agent risks" note="Edit these on each agent's Risks tab">
        {agentRisks.length ? <div className="tbl"><table>
          <thead><tr><th>Risk</th><th>Agent</th><th>Category</th><th>Severity</th></tr></thead>
          <tbody>{agentRisks.map((r) => <tr key={r.id}><td>{r.name}</td><td className="muted">{r.applies_to.map(name).join(", ")}</td><td>{catLabel(r.category)}</td><td>{r.severity}</td></tr>)}</tbody>
        </table></div> : <Empty>No agent-specific risks</Empty>}
      </Panel>
    </>
  );
}

function KnownResources() {
  const { data } = useData<any[]>("/v1/company/resources");
  const [q, setQ] = useState("");
  const rows = (data || []).filter((r) => !q || `${r.key} ${r.text}`.toLowerCase().includes(q.toLowerCase()));
  return (
    <Panel title="What Varuna knows about your systems" note={data ? `${data.length} identifiers` : undefined}>
      <p className="muted small">Imported from the asset inventory, document classification and supplier catalogs. When an agent names only an ID (say, isolate WMS-APP-01), Varuna checks the action as if it named what the ID is, so company rules still apply. It also learns IDs from what tools return during a conversation.</p>
      <input className="input" value={q} onChange={(e) => setQ(e.target.value)} placeholder="Filter, e.g. HR or critical" aria-label="Filter identifiers" />
      <div className="tbl" style={{ maxHeight: 280, overflow: "auto", marginTop: 8 }}><table>
        <thead><tr><th>Identifier</th><th>What it is</th></tr></thead>
        <tbody>{rows.map((r) => <tr key={r.key}><td className="mono">{r.key}</td><td className="small">{r.text}</td></tr>)}</tbody>
      </table></div>
    </Panel>
  );
}

export default function Company() {
  const { meta } = useApp();
  return (
    <>
      <p className="muted" style={{ maxWidth: 760 }}>{meta?.company || "The company"}'s policy and risk register sit above every agent. An agent's charter can only narrow what is allowed: if a charter is looser than company policy, Varuna applies the company rule and the reason names it.</p>
      <PolicyEditor />
      <RiskRegister />
      <KnownResources />
    </>
  );
}
