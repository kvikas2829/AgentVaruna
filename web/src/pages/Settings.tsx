import { useEffect, useState } from "react";
import { api, ago } from "../api";
import { useApp, useData } from "../state";
import { Chip, Empty, Panel } from "../ui";

const MODE_TEXT: Record<string, string> = {
  enforce: "Each action waits for Varuna's verdict and can be slowed, held for approval or blocked.",
  observe: "Varuna answers at once and never stops anything. It scores, alerts and records what it would have done, in the background.",
};

function Modes() {
  const { agents, refreshAgents, toast } = useApp();
  const { data: m, reload } = useData<any>("/v1/settings/mode");
  if (!m) return <Panel title="Observation or enforcement"><Empty>Loading</Empty></Panel>;
  const setDefault = async (v: string) => { await api("/v1/settings/mode", { method: "PUT", body: { default: v } }); toast(v === "observe" ? "Company default: observation only" : "Company default: enforcing"); reload(); refreshAgents(); };
  const setAgent = async (id: string, v: string) => { await api(`/v1/agents/${id}/mode`, { method: "PUT", body: { mode: v || null } }); reload(); refreshAgents(); };
  return (
    <Panel title="Observation or enforcement">
      <div className="seg" role="radiogroup" aria-label="Company default mode">
        {[["enforce", "Enforce"], ["observe", "Observe only"]].map(([k, l]) => <button key={k} role="radio" aria-checked={m.default === k} aria-pressed={m.default === k} onClick={() => setDefault(k)}>{l}</button>)}
      </div>
      <p className="muted small" style={{ marginTop: 8 }}>Company default. {MODE_TEXT[m.default]}</p>
      <div className="lab">Per agent</div>
      <div className="stack">{agents.map((a) => (
        <div key={a.id} className="row sp">
          <span>{a.name} <Chip kind={a.mode === "observe" ? "warn" : "ok"}>{a.mode === "observe" ? "Observing" : "Enforcing"}</Chip></span>
          <select className="select" value={m.agents[a.id] || ""} onChange={(e) => setAgent(a.id, e.target.value)} aria-label={`Mode for ${a.name}`}>
            <option value="">Company default ({m.default === "observe" ? "observe" : "enforce"})</option>
            <option value="enforce">Enforce</option>
            <option value="observe">Observe only</option>
          </select>
        </div>))}</div>
    </Panel>
  );
}

export default function Settings() {
  const { meta, toast, agentId } = useApp();
  const { data: ch } = useData<any>("/v1/settings/channels");
  const { data: notes } = useData<any[]>(`/v1/notifications?agent_id=${agentId}&limit=30`);
  const { data: m, reload: rm } = useData<any>("/v1/meta");
  const [f, setF] = useState<any>(null);
  useEffect(() => { if (ch && !f) setF(ch); }, [ch, f]);
  const save = async () => { await api("/v1/settings/channels", { method: "PUT", body: f }); toast("Channels saved"); };
  return (
    <>
      <div className="grid g11">
        <Modes />
        <Panel title="Judge">
          <div className="row"><Chip kind={meta?.judge === "heuristic" ? "warn" : "ok"}>{meta?.judge === "claude" ? `Claude (${meta.judge_model})` : meta?.judge === "local" ? `Local model (${meta.judge_model})` : "Offline heuristic"}</Chip></div>
          <p className="muted small" style={{ marginTop: 8 }}>{meta?.judge === "heuristic"
            ? "Varuna scores with its rules and a built-in phrase judge. Set ANTHROPIC_API_KEY or VARUNA_LOCAL_MODEL and restart to add a model judge."
            : `The model judges high-impact actions against the company policy and the agent's charter, with a ${meta?.judge_timeout_s}s limit, then falls back to the offline judge. Other actions use the rules and the offline judge.`}</p>
          <div className="lab">Sample data</div>
          <p className="muted small">{m?.has_sample ? "Generated sample history is present." : "No sample data is present."}</p>
          <div className="row">
            {m?.has_sample && <button className="btn" onClick={async () => { await api("/v1/sample-data", { method: "DELETE" }); toast("Sample data cleared"); rm(); }}>Clear sample data</button>}
            <button className="btn" onClick={async () => { await api("/v1/sample-data", { body: {} }); toast("Generating sample history…"); window.setTimeout(rm, 4000); }}>Regenerate sample data</button>
          </div>
        </Panel>
      </div>
      <div className="grid g11">
        <Panel title="Notification channels">
          <p className="muted small">In-app notifications are always recorded. Add channels to also send them out.</p>
          {f && <>
            <label className="f">Slack incoming-webhook URL<input className="input" value={f.slack_webhook_url} onChange={(e) => setF({ ...f, slack_webhook_url: e.target.value })} placeholder="https://hooks.slack.com/services/…" /></label>
            <label className="f">Generic webhook URL<input className="input" value={f.webhook_url} onChange={(e) => setF({ ...f, webhook_url: e.target.value })} /></label>
            <label className="f">Email address for alerts<input className="input" value={f.email_to} onChange={(e) => setF({ ...f, email_to: e.target.value })} /></label>
            <p className="muted small">{meta?.smtp_configured ? "SMTP is configured." : "SMTP is not configured on the server, so emails are logged instead of sent."}</p>
            <div className="row"><button className="btn p" onClick={save}>Save channels</button></div></>}
        </Panel>

      </div>
      <Panel title="Notification log">
        {notes && notes.length ? <div className="tbl"><table>
          <thead><tr><th>When</th><th>Agent</th><th>To</th><th>Channel</th><th>Message</th><th>Status</th></tr></thead>
          <tbody>{notes.map((n) => (
            <tr key={n.id}><td className="muted small">{ago(n.created_at)}</td><td>{n.agent_name}</td><td>{n.audience}</td><td>{n.channel}</td>
              <td><b>{n.title}</b><div className="muted small">{n.body}</div></td><td><Chip kind={n.status === "failed" ? "crit" : n.status === "sent" ? "ok" : "n"}>{n.status}</Chip></td></tr>))}</tbody>
        </table></div> : <Empty>No notifications yet</Empty>}
      </Panel>
    </>
  );
}
