import { useEffect, useMemo, useState } from "react";
import { api } from "../api";
import { go, useApp, useData } from "../state";
import { AddTag, Chip, CopyBtn, Empty, Panel, Tags } from "../ui";

const TABS: [string, string][] = [["intent", "Intent"], ["risks", "Risks"], ["hooks", "Hooks"], ["responses", "Responses"]];

export default function AgentDetail({ id, tab }: { id: string; tab: string }) {
  const { refreshAgents, toast } = useApp();
  const { data: a, err, reload } = useData<any>(`/v1/agents/${id}`);
  if (err) return <div className="banner crit" role="alert">{err}</div>;
  if (!a) return <Empty>Loading</Empty>;
  const toggle = async () => { await api(`/v1/agents/${id}/${a.status === "paused" ? "resume" : "pause"}`, { body: {} }); toast(a.status === "paused" ? "Agent resumed" : "Agent paused"); await refreshAgents(); reload(); };
  return (
    <>
      <div className="row sp">
        <div className="row"><a href="#/agents" className="muted">← Agents</a><h2 style={{ margin: 0 }}>{a.name}</h2>
          {a.status === "paused" ? <Chip kind="crit">Paused</Chip> : a.status === "draft" ? <Chip kind="n">Draft</Chip> : <Chip kind="ok">Active</Chip>}</div>
        <button className="btn" onClick={toggle}>{a.status === "paused" ? "Resume agent" : "Pause agent"}</button>
      </div>
      {a.status === "paused" && <div className="banner crit" role="alert">{a.paused_reason || "Paused."} Guard calls from this agent are blocked until it is resumed.</div>}
      <div className="tabs" role="tablist">
        {TABS.map(([k, l]) => <a key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} href={`#/agents/${id}/${k}`}>{l}</a>)}
      </div>
      {tab === "intent" && <Intent agent={a} />}
      {tab === "risks" && <Risks />}
      {tab === "hooks" && <Hooks agent={a} />}
      {tab === "responses" && <Responses agent={a} />}
    </>
  );
}

/* ---------------------------------------------------------------- intent */
const LISTS: [string, string, "av" | "ok" | undefined][] = [
  ["objectives", "Objectives", undefined], ["key_results", "Key results", undefined], ["expected_actions", "Expected actions", "ok"],
  ["avoid", "Avoid", "av"], ["allowed_resources", "Allowed resources", "ok"], ["forbidden_resources", "Forbidden resources", "av"],
  ["high_impact_tools", "High-impact tools", undefined],
];

function Intent({ agent }: { agent: any }) {
  const { toast, refreshAgents } = useApp();
  const { data: versions, reload } = useData<any[]>(`/v1/agents/${agent.id}/charters`);
  const [text, setText] = useState("");
  const [cur, setCur] = useState<any>(null);
  const [hover, setHover] = useState<{ key: string; i: number } | null>(null);
  const [busy, setBusy] = useState(false);
  const [dirty, setDirty] = useState(false);

  useEffect(() => {
    if (versions && !cur) {
      const v = versions.find((x) => x.status === "draft") || versions.find((x) => x.status === "active") || versions[0];
      if (v) { setCur(v); setText(v.source_text || ""); }
    }
  }, [versions, cur]);

  const data = cur?.data;
  const compile = async () => {
    setBusy(true);
    try { const c = await api(`/v1/agents/${agent.id}/charters/compile`, { body: { text } }); setCur(c); setDirty(false); await reload(); toast(`Compiled as draft v${c.version}. Review it, then activate.`); }
    catch (e: any) { toast(e.message); } finally { setBusy(false); }
  };
  const mutate = (key: string, fn: (l: string[]) => string[]) => { setCur({ ...cur, data: { ...data, [key]: fn([...(data[key] || [])]) } }); setDirty(true); };
  const save = async () => {
    const c = await api(`/v1/agents/${agent.id}/charters/${cur.version}`, { method: "PUT", body: { data: cur.data, source_text: text } });
    setCur(c); setDirty(false); await reload(); toast(`Saved as draft v${c.version}`); return c;
  };
  const activate = async () => {
    const c = dirty || cur.status !== "draft" ? await save() : cur;
    await api(`/v1/agents/${agent.id}/charters/${c.version}/activate`, { body: {} });
    toast(`Charter v${c.version} is now enforced`); await refreshAgents(); setCur(null); reload();
  };

  const hlSentences = useMemo(() => {
    const s = new Set<number>();
    if (hover && data?.refs?.[hover.key]?.[hover.i] !== undefined) s.add(data.refs[hover.key][hover.i]);
    return s;
  }, [hover, data]);

  const limits = data?.limits || {};
  return (
    <div className="grid g11">
      <Panel title="1 · Describe what the agent should do" note="Plain English">
        <textarea className="textarea" rows={12} value={text} onChange={(e) => setText(e.target.value)} aria-label="Charter text"
                  placeholder="This agent books business travel for employees. It should stay within policy. It must not export passenger data..." />
        <div className="row"><button className="btn p" onClick={compile} disabled={busy || text.trim().length < 10}>{busy ? "Compiling…" : "Compile to policy"}</button>
          <span className="muted small">Compiled by {cur?.compiled_by || "—"}. Recompiling creates a new draft; the active version keeps being enforced.</span></div>
        <div className="lab">Versions</div>
        <div className="pills">
          {(versions || []).map((v) => (
            <button key={v.version} className={`btn s ${cur?.version === v.version ? "p" : ""}`} onClick={() => { setCur(v); setText(v.source_text || ""); setDirty(false); }}>
              v{v.version} · {v.status}
            </button>
          ))}
        </div>
      </Panel>

      <Panel title="2 · Review the compiled policy" note={cur ? `v${cur.version} · ${cur.status}` : ""}>
        {!data ? <Empty>Write a description and compile it to see the structured policy here.</Empty> : (
          <>
            <div className="lab">Your text, with what each part became</div>
            <div className="annot">
              {(data.sentences || []).map((s: string, i: number) => <span key={i} className={hlSentences.has(i) ? "hl" : ""}>{s} </span>)}
            </div>
            {LISTS.map(([key, label, kind]) => (
              <div key={key}>
                <div className="lab">{label}</div>
                <Tags items={data[key] || []} kind={kind} hl={hover?.key === key ? hover.i : null} onHover={(i) => setHover(i === null ? null : { key, i })}
                      onRemove={(i) => mutate(key, (l) => l.filter((_, j) => j !== i))} />
                <AddTag placeholder={`Add to ${label.toLowerCase()}`} onAdd={(v) => mutate(key, (l) => [...l, v])} />
              </div>
            ))}
            <div className="lab">Limits</div>
            <div className="grid g11" style={{ gap: 8 }}>
              <label className="f">Max amount per action
                <input className="input" type="number" min={0} value={limits.max_amount_per_action ?? ""} onChange={(e) => { setCur({ ...cur, data: { ...data, limits: { ...limits, max_amount_per_action: e.target.value === "" ? null : Number(e.target.value) } } }); setDirty(true); }} /></label>
              <div><div className="lab" style={{ marginTop: 0 }}>Allowed domains</div>
                <Tags items={limits.allowed_domains || []} onRemove={(i) => { setCur({ ...cur, data: { ...data, limits: { ...limits, allowed_domains: limits.allowed_domains.filter((_: any, j: number) => j !== i) } } }); setDirty(true); }} />
                <AddTag placeholder="Add domain" onAdd={(v) => { setCur({ ...cur, data: { ...data, limits: { ...limits, allowed_domains: [...(limits.allowed_domains || []), v] } } }); setDirty(true); }} /></div>
            </div>
            <div className="lab">Protected targets <span className="muted small" style={{ textTransform: "none", letterSpacing: 0 }}>(changing these needs a person's approval)</span></div>
            <Tags items={limits.protected_targets || []} kind="av" onRemove={(i) => { setCur({ ...cur, data: { ...data, limits: { ...limits, protected_targets: limits.protected_targets.filter((_: any, j: number) => j !== i) } } }); setDirty(true); }} />
            <AddTag placeholder="Add protected target" onAdd={(v) => { setCur({ ...cur, data: { ...data, limits: { ...limits, protected_targets: [...(limits.protected_targets || []), v] } } }); setDirty(true); }} />
            <div className="row" style={{ marginTop: 12 }}>
              <button className="btn" onClick={save} disabled={!dirty}>Save draft</button>
              <button className="btn p" onClick={activate} disabled={cur.status === "active" && !dirty}>{cur.status === "active" && !dirty ? "Active" : "Activate and enforce"}</button>
            </div>
          </>
        )}
      </Panel>
    </div>
  );
}

/* ---------------------------------------------------------------- risks */
function Risks() {
  const { toast } = useApp();
  const { data: risks, reload } = useData<any[]>(`/v1/risks`);
  const [text, setText] = useState("");
  const [props, setProps] = useState<any[]>([]);
  const [by, setBy] = useState("");
  const compile = async () => { const r = await api("/v1/risks/compile", { body: { text } }); setProps(r.risks); setBy(r.compiled_by); if (!r.risks.length) toast("Nothing recognizable found. Try naming what could go wrong."); };
  const add = async (r: any, i: number) => { await api("/v1/risks", { body: r }); setProps(props.filter((_, j) => j !== i)); toast("Added to the register"); reload(); };
  const del = async (id: number) => { await api(`/v1/risks/${id}`, { method: "DELETE" }); reload(); };
  return (
    <>
      <p className="muted" style={{ maxWidth: 700 }}>The risk register is shared by every agent. Each risk has a category, a severity from 1 to 5, and how far the harm could spread.</p>
      <div className="grid g21">
        <Panel title="Risk register">
          {!risks ? <Empty>Loading</Empty> : (
            <div className="tbl"><table>
              <thead><tr><th>Risk</th><th>Category</th><th>Severity</th><th>Extent</th><th><span className="sr-only">Remove</span></th></tr></thead>
              <tbody>{risks.map((r) => (
                <tr key={r.id}><td><b>{r.name}</b><div className="muted small">{r.description}</div></td><td>{r.category}</td>
                  <td>{r.severity}/5</td><td className="muted small">{r.extent_text || `×${r.extent_factor}`}</td>
                  <td><button className="btn s d" aria-label={`Remove ${r.name}`} onClick={() => del(r.id)}>Remove</button></td></tr>
              ))}</tbody>
            </table></div>
          )}
        </Panel>
        <Panel title="Add risks from a description">
          <textarea className="textarea" rows={6} value={text} onChange={(e) => setText(e.target.value)} aria-label="Risk description"
                    placeholder="A leak of client records to an outside party would be very serious and could affect all clients." />
          <div className="row"><button className="btn p" onClick={compile} disabled={text.trim().length < 10}>Suggest risks</button>{by && <span className="muted small">Suggested by {by}</span>}</div>
          {props.map((r, i) => (
            <div className="pat" key={i}>
              <b>{r.name}</b><span>{r.category} · severity {r.severity}/5 · {r.extent_text}</span>
              <div className="row"><button className="btn s p" onClick={() => add(r, i)}>Add</button><button className="btn s" onClick={() => setProps(props.filter((_, j) => j !== i))}>Dismiss</button></div>
            </div>
          ))}
        </Panel>
      </div>
    </>
  );
}

/* ---------------------------------------------------------------- hooks */
function Hooks({ agent }: { agent: any }) {
  const base = location.origin;
  const py = `from varuna_sdk import VarunaClient, HttpTransport

varuna = VarunaClient(HttpTransport("${base}", "${agent.api_key}"))

@varuna.tool()          # checked before every call; blocked or held calls raise
def send_email(to: str, body: str): ...
`;
  const curl = `curl -s ${base}/v1/guard \\
  -H 'X-Varuna-Key: ${agent.api_key}' -H 'content-type: application/json' \\
  -d '{"session_id":"s1","tool":"send_email","args":{"to":"a@b.com"},"thought":"why I am doing this"}'`;
  const hook = `from varuna_sdk.claude_agent_hooks import pre_tool_use, post_tool_use
# Claude Agent SDK: register as PreToolUse / PostToolUse hooks (see README)`;
  return (
    <div className="grid g21">
      <Panel title="Connect your agent">
        <label className="f">API key<div className="row"><input className="input mono grow" readOnly value={agent.api_key} /><CopyBtn text={agent.api_key} /></div></label>
        {[["Python SDK", py], ["HTTP", curl], ["Claude Agent SDK", hook]].map(([t, c]) => (
          <div key={t}><div className="row sp"><div className="lab">{t}</div><CopyBtn text={c} /></div><pre className="json">{c}</pre></div>
        ))}
        <p className="muted small">The guard call waits for a verdict. Thoughts, tool results and messages are sent in the background and never delay the agent.</p>
      </Panel>
      <Panel title={`Tools Varuna knows about (${(agent.tools || []).length})`}>
        <div className="tbl"><table>
          <thead><tr><th>Tool</th><th>Kind</th><th>Impact</th></tr></thead>
          <tbody>{(agent.tools || []).map((t: any) => <tr key={t.name}><td className="mono">{t.name}</td><td>{t.kind}</td><td><Chip kind={t.impact === "high" ? "warn" : "n"}>{t.impact}</Chip></td></tr>)}</tbody>
        </table></div>
        <p className="muted small">Unknown tools are learned the first time they are called.</p>
      </Panel>
    </div>
  );
}

/* ---------------------------------------------------------------- responses */
const STEPS: [string, string, string][] = [["notify_owner", "Notify owner", "var(--info, #4b7bd8)"], ["notify_security", "Notify security", "var(--warn)"], ["slow", "Slow", "var(--warn)"], ["block", "Block", "var(--crit)"]];

function Responses({ agent }: { agent: any }) {
  const { toast } = useApp();
  const { data: pol, reload } = useData<any>(`/v1/agents/${agent.id}/responses`);
  const [p, setP] = useState<any>(null);
  const [prev, setPrev] = useState<any>(null);
  useEffect(() => { if (pol && !p) setP(pol); }, [pol, p]);
  useEffect(() => {
    if (!p) return;
    const h = window.setTimeout(() => { api(`/v1/agents/${agent.id}/responses/preview`, { body: p }).then(setPrev).catch(() => setPrev(null)); }, 350);
    return () => window.clearTimeout(h);
  }, [p, agent.id]);
  if (!p) return <Empty>Loading</Empty>;
  const th = p.thresholds;
  const setTh = (k: string, v: number) => setP({ ...p, thresholds: { ...th, [k]: v } });
  const valid = th.notify_owner < th.notify_security && th.notify_security < th.slow && th.slow < th.block && th.block <= 100 && th.notify_owner > 0;
  const save = async () => { await api(`/v1/agents/${agent.id}/responses`, { method: "PUT", body: p }); toast("Response policy saved"); reload(); };
  const stake = p.stakeholders || [];
  const setStake = (i: number, patch: any) => setP({ ...p, stakeholders: stake.map((s: any, j: number) => (j === i ? { ...s, ...patch } : s)) });
  return (
    <div className="grid g21">
      <Panel title="Response ladder" note="Scores run 0 to 100">
        <div className="ladder" aria-hidden>
          <div style={{ width: `${th.notify_owner}%`, background: "var(--ok-soft)", color: "var(--ok)" }}>Observe</div>
          <div style={{ width: `${th.notify_security - th.notify_owner}%`, background: "var(--accent-soft)" }}>Owner</div>
          <div style={{ width: `${th.slow - th.notify_security}%`, background: "var(--warn-soft)", color: "var(--warn)" }}>Security</div>
          <div style={{ width: `${th.block - th.slow}%`, background: "var(--warn)", color: "#fff" }}>Slow</div>
          <div style={{ width: `${100 - th.block}%`, background: "var(--crit)", color: "#fff" }}>Block</div>
        </div>
        <div className="grid g4" style={{ marginTop: 14 }}>
          {STEPS.map(([k, l]) => (
            <label className="f" key={k}>{l} at<input className="input" type="number" min={1} max={100} value={th[k]} onChange={(e) => setTh(k, Number(e.target.value))} /></label>
          ))}
        </div>
        {!valid && <div className="banner crit" role="alert">Thresholds must rise in order and stay at or below 100.</div>}
        <div className="grid g3" style={{ marginTop: 10 }}>
          <label className="f">When slowing an action<select className="input" value={p.slow_action} onChange={(e) => setP({ ...p, slow_action: e.target.value })}>
            <option value="hold">Hold for human approval</option><option value="delay">Delay, then continue</option></select></label>
          <label className="f">Pause the agent after this many blocks<input className="input" type="number" min={0} max={20} value={p.pause_after_blocks} onChange={(e) => setP({ ...p, pause_after_blocks: Number(e.target.value) })} /></label>
          <label className="f">If the judge is unavailable<select className="input" value={p.fail_mode} onChange={(e) => setP({ ...p, fail_mode: e.target.value })}>
            <option value="closed_high_impact">Hold high-impact tools</option><option value="open">Allow</option></select></label>
        </div>
        <div className="lab">Who gets notified</div>
        <div className="tbl"><table><thead><tr><th>Audience</th><th>Name</th><th>Email</th></tr></thead>
          <tbody>{stake.map((s: any, i: number) => (
            <tr key={i}><td>{s.audience || s.role}</td>
              <td><input className="input" aria-label="Name" value={s.name || ""} onChange={(e) => setStake(i, { name: e.target.value })} /></td>
              <td><input className="input" aria-label="Email" value={s.email || ""} onChange={(e) => setStake(i, { email: e.target.value })} /></td></tr>))}</tbody></table></div>
        <div className="row" style={{ marginTop: 12 }}><button className="btn p" disabled={!valid} onClick={save}>Save policy</button></div>
      </Panel>
      <Panel title="What this would have done" note="Last 24h of real traffic">
        {prev ? (
          <div className="stack">
            <p className="muted small">{prev.actions} scored actions replayed against these thresholds.</p>
            <div className="tbl"><table><thead><tr><th></th><th>Now</th><th>Proposed</th></tr></thead><tbody>
              {[["Alerts", "alerts"], ["Slowed", "slowed"], ["Blocked", "blocked"]].map(([l, k]) => <tr key={k}><td>{l}</td><td className="num">{prev.current[k]}</td><td className="num"><b>{prev.proposed[k]}</b></td></tr>)}
            </tbody></table></div>
            <p className="muted small">{prev.false_positives_marked} actions in this window were marked as false positives by reviewers.</p>
          </div>
        ) : <Empty>Adjust a threshold to preview its effect.</Empty>}
      </Panel>
    </div>
  );
}
