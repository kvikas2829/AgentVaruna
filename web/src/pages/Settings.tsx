import { useEffect, useState } from "react";
import { api, ago } from "../api";
import { useApp, useData } from "../state";
import { Chip, Empty, Panel } from "../ui";

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
        <Panel title="Notification channels">
          <p className="muted small">In-app notifications are always recorded. Add channels to also send them out.</p>
          {f && <>
            <label className="f">Slack incoming-webhook URL<input className="input" value={f.slack_webhook_url} onChange={(e) => setF({ ...f, slack_webhook_url: e.target.value })} placeholder="https://hooks.slack.com/services/…" /></label>
            <label className="f">Generic webhook URL<input className="input" value={f.webhook_url} onChange={(e) => setF({ ...f, webhook_url: e.target.value })} /></label>
            <label className="f">Email address for alerts<input className="input" value={f.email_to} onChange={(e) => setF({ ...f, email_to: e.target.value })} /></label>
            <p className="muted small">{meta?.smtp_configured ? "SMTP is configured." : "SMTP is not configured on the server, so emails are logged instead of sent."}</p>
            <div className="row"><button className="btn p" onClick={save}>Save channels</button></div></>}
        </Panel>
        <Panel title="Judge">
          <div className="row"><Chip kind={meta?.judge === "claude" ? "ok" : "warn"}>{meta?.judge === "claude" ? `Claude (${meta.judge_model})` : "Offline heuristic"}</Chip></div>
          <p className="muted small" style={{ marginTop: 8 }}>{meta?.judge === "claude"
            ? `Claude judges high-impact actions before they run, with a ${meta.judge_timeout_s}s limit, then falls back to the heuristic judge.`
            : "No ANTHROPIC_API_KEY is set, so Varuna scores with its rules and a built-in lexicon judge. Set the key and restart to add Claude."}</p>
          <div className="lab">Sample data</div>
          <p className="muted small">{m?.has_sample ? "Generated sample history is present." : "No sample data is present."}</p>
          <div className="row">
            {m?.has_sample && <button className="btn" onClick={async () => { await api("/v1/sample-data", { method: "DELETE" }); toast("Sample data cleared"); rm(); }}>Clear sample data</button>}
            <button className="btn" onClick={async () => { await api("/v1/sample-data", { body: {} }); toast("Generating sample history…"); window.setTimeout(rm, 4000); }}>Regenerate sample data</button>
          </div>
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
