import { useState } from "react";
import { api, ago } from "../api";
import { go, useApp, useData } from "../state";
import { Bars, Chip, Empty, KPI, Panel, RiskChart, Scores, Spark, Verdict } from "../ui";

export default function Overview() {
  const { agentId, setAgentId, toast } = useApp();
  const [hours, setHours] = useState(24);
  const { data: o, err, reload } = useData<any>(`/v1/overview?agent_id=${agentId}&hours=${hours}`);
  const { data: pol } = useData<any>(agentId !== "all" ? `/v1/agents/${agentId}/responses` : null);
  if (err) return <div className="banner crit" role="alert">{err}</div>;
  if (!o) return <Empty>Loading</Empty>;
  const t = o.totals;
  const delta = t.prev_actions ? Math.round((100 * (t.actions - t.prev_actions)) / t.prev_actions) : null;
  const healthKind: any = { healthy: "ok", watch: "warn", at_risk: "crit" };
  const healthLabel: any = { healthy: "Healthy", watch: "Watch", at_risk: "At risk" };
  const catMax = Math.max(1, ...o.categories.map((c: any) => c.events));

  return (
    <>
      {o.has_sample && (
        <div className="banner">
          <span>This install includes generated sample history so the charts have something to show. It is labeled as sample data and can be removed.</span>
          <button className="btn s" onClick={async () => { await api("/v1/sample-data", { method: "DELETE" }); toast("Sample data cleared"); reload(); }}>Clear sample data</button>
        </div>
      )}
      <div className="row sp">
        <span className="muted small">Showing the last {hours} hours</span>
        <div className="seg" role="group" aria-label="Time window">
          {[6, 24].map((h) => <button key={h} aria-pressed={hours === h} onClick={() => setHours(h)}>{h}h</button>)}
        </div>
      </div>

      <div className="grid g4">
        <KPI label="Actions monitored" value={t.actions.toLocaleString()} sub={delta === null ? "No earlier window to compare" : `${delta >= 0 ? "+" : ""}${delta}% vs previous ${hours}h`} />
        <KPI label="Deviated from intent" value={`${t.deviation_rate}%`} sub={`${t.deviated} of ${t.actions} actions scored 40 or more`} tone={t.deviation_rate >= 3 ? "up" : "dn"} />
        <KPI label="Average risk score" value={t.avg_risk} sub={`${t.alerts} alerts raised`} />
        <KPI label="Held or blocked" value={t.held + t.blocked} sub={`${t.held} held · ${t.blocked} blocked${t.slowed ? ` · ${t.slowed} slowed` : ""}`} tone={t.blocked ? "up" : undefined} />
      </div>

      <div className="grid g21">
        <Panel title="Highest risk score per hour" note={pol ? `Thresholds from this agent's responses` : "Default thresholds"}>
          <RiskChart series={o.series} slow={pol?.thresholds?.slow} block={pol?.thresholds?.block} />
        </Panel>
        <Panel title="Risk events by category" note={`${hours}h`}>
          {o.categories.length ? <Bars items={o.categories.map((c: any) => ({ label: c.label, value: c.events }))} max={catMax} /> : <Empty>No risk events in this window</Empty>}
        </Panel>
      </div>

      <div className="grid g21">
        <Panel title="Deviation by agent">
          <div className="tbl"><table>
            <thead><tr><th>Agent</th><th>Actions</th><th>Deviation rate</th><th>Trend</th><th>Status</th></tr></thead>
            <tbody>
              {o.agents.map((a: any) => (
                <tr key={a.id} className="click" onClick={() => { setAgentId(a.id); go(`/agents/${a.id}`); }}>
                  <td>{a.name}</td><td className="num">{a.actions}</td><td className="num">{a.deviation_rate}%</td>
                  <td><Spark data={a.trend} color={`var(--${a.health === "at_risk" ? "crit" : a.health === "watch" ? "warn" : "ok"})`} /></td>
                  <td>{a.status === "paused" ? <Chip kind="crit">Paused</Chip> : <Chip kind={healthKind[a.health]}>{healthLabel[a.health]}</Chip>}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        </Panel>
        <Panel title="Patterns detected">
          {o.patterns.length ? o.patterns.map((p: any) => (
            <div className="pat" key={p.agent_id + p.kind}>
              <b>{p.title}</b>
              <span>{p.agent_name}: {p.detail}{p.sessions > 1 ? ` Seen in ${p.sessions} sessions.` : ""}</span>
            </div>
          )) : <Empty>No patterns yet</Empty>}
        </Panel>
      </div>

      <Panel title="Needs attention" note="Select a row to open its trace">
        {o.attention.length ? (
          <div className="tbl"><table>
            <thead><tr><th>Time</th><th>Agent</th><th>Action</th><th>Deviation / risk</th><th>Verdict</th><th>Seen</th></tr></thead>
            <tbody>
              {o.attention.map((a: any) => (
                <tr key={a.event_id} className="click" onClick={() => go(`/trace/${a.session_id}?event=${a.event_id}`)}>
                  <td className="num">{new Date(a.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", hour12: false })}</td>
                  <td>{a.agent_name}</td><td className="mono" style={{ maxWidth: 420, overflowWrap: "anywhere" }}>{a.summary}</td>
                  <td><Scores dev={a.deviation} risk={a.risk} /></td><td><Verdict verdict={a.verdict} band={a.band} /></td>
                  <td className="muted small">{ago(a.ts)}</td>
                </tr>
              ))}
            </tbody>
          </table></div>
        ) : <Empty>Nothing needs attention in this window</Empty>}
      </Panel>
    </>
  );
}
