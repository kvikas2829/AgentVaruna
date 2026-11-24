import { useEffect, useState } from "react";
import { api, ago, fmtTime } from "../api";
import { go, useApp, useData, useRoute } from "../state";
import { Chip, Empty, Panel, Scores, Verdict } from "../ui";

const TYPE_LABEL: Record<string, string> = { thought: "Thought", tool_call: "Action", tool_result: "Result", message: "Message", user_message: "User" };

export default function Trace({ sessionId }: { sessionId?: string }) {
  const { agentId, toast, refreshAgents } = useApp();
  const { query } = useRoute();
  const [flagged, setFlagged] = useState(false);
  const { data: sessions } = useData<any[]>(`/v1/sessions?agent_id=${agentId}&only_flagged=${flagged}`);
  const sid = sessionId || sessions?.[0]?.id;
  const { data: d, reload } = useData<any>(sid ? `/v1/sessions/${sid}` : null, [sid]);
  const [sel, setSel] = useState<number | null>(null);
  const [tab, setTab] = useState("why");

  useEffect(() => { setSel(null); }, [sid]);
  useEffect(() => {
    if (!d) return;
    const q = Number(query.get("event"));
    if (q && sel === null && d.events.some((e: any) => e.id === q)) { setSel(q); return; }
    if (sel === null) {
      const worst = [...d.events].filter((e: any) => e.assessment?.enforceable).sort((a: any, b: any) => (b.assessment.decision_score - a.assessment.decision_score))[0];
      if (worst) setSel(worst.id);
    }
  }, [d, sel, query]);

  const ev = d?.events.find((e: any) => e.id === sel);
  const fb = async (kind: string) => { await api(`/v1/events/${sel}/feedback`, { body: { kind } }); toast(kind === "false_positive" ? "Marked as a false positive" : kind === "allow_once" ? "Allowed once. The agent can retry this exact action." : "Marked as confirmed"); reload(); };
  const setInc = async (status: string) => { await api(`/v1/incidents/${d.incident.id}`, { method: "PATCH", body: { status } }); toast(`Incident ${status.replace("_", " ")}`); reload(); };
  const resume = async () => { await api(`/v1/agents/${d.agent.id}/resume`, { body: {} }); toast("Agent resumed"); await refreshAgents(); reload(); };

  return (
    <div className="trace">
      <Panel title="Sessions" right={<label className="small muted"><input type="checkbox" checked={flagged} onChange={(e) => setFlagged(e.target.checked)} /> Flagged only</label>}>
        <div className="sess">
          {sessions?.length ? sessions.map((s) => (
            <button key={s.id} className={s.id === sid ? "on" : ""} onClick={() => go(`/trace/${s.id}`)}>
              <b>{s.label || s.id}</b><span>{s.agent_name} · {ago(s.last_event_at)}</span>
              <span><Scores dev={s.max_deviation} risk={s.max_risk} /> {s.blocked_count ? <Chip kind="crit">{s.blocked_count} blocked</Chip> : null}</span>
            </button>
          )) : <Empty>No sessions</Empty>}
        </div>
      </Panel>

      <Panel title={d ? `${d.session.label || d.session.id}` : "Timeline"} note={d?.session.agent_name}>
        {!d ? <Empty>Select a session</Empty> : (
          <>
            {d.agent?.status === "paused" && (
              <div className="banner crit" role="alert"><span>{d.agent.paused_reason || "This agent is paused."}</span><button className="btn s" onClick={resume}>Resume agent</button></div>
            )}
            {d.incident && (
              <div className="banner warn"><span><b>{d.incident.title}</b> · {d.incident.status.replace("_", " ")}</span>
                <span className="row">{["acknowledged", "resolved", "false_positive"].filter((s) => s !== d.incident.status).map((s) =>
                  <button key={s} className="btn s" onClick={() => setInc(s)}>{s === "false_positive" ? "False positive" : s[0].toUpperCase() + s.slice(1)}</button>)}</span></div>
            )}
            {d.events.map((e: any) => {
              const a = e.assessment;
              const bad = a && (a.verdict === "block" || a.verdict === "hold");
              return (
                <div key={e.id} className={`ev ${sel === e.id ? "sel" : ""} ${bad ? "bad" : ""}`} onClick={() => { setSel(e.id); setTab("why"); }} tabIndex={0}
                     onKeyDown={(k) => { if (k.key === "Enter") setSel(e.id); }} role="button" aria-label={`${TYPE_LABEL[e.type] || e.type}: ${e.summary}`}>
                  <span className="num muted">{fmtTime(e.ts)}</span><span className="k">{TYPE_LABEL[e.type] || e.type}</span>
                  <span className="t">{e.summary}</span>
                  <span>{a?.enforceable ? <><Scores dev={a.deviation} risk={a.risk} /> <Verdict verdict={a.verdict} band={a.band} would={a.explain?.would_verdict} /></> : a ? <Scores dev={a.deviation} risk={a.risk} /> : null}</span>
                </div>
              );
            })}
          </>
        )}
      </Panel>

      <Panel title="Why this score">
        {!ev ? <Empty>Select an event</Empty> : !ev.assessment ? <Empty>This event is not scored.</Empty> : (() => {
          const a = ev.assessment;
          return (
            <>
              <div className="row"><Scores dev={a.deviation} risk={a.risk} /><Verdict verdict={a.verdict} band={a.band} would={a.explain?.would_verdict} />
                {a.explain?.mode === "observe" && <Chip kind="n">Observation mode</Chip>}
                <span className="muted small">Judged by {a.judge || "rules"} · tier {a.tier} · {Math.round(a.latency_ms)} ms · charter v{a.charter_version}</span></div>
              <div className="tabs" role="tablist">{[["why", "Explain"], ["rules", "Rules"], ["json", "Raw"]].map(([k, l]) => <button key={k} role="tab" aria-selected={tab === k} className={tab === k ? "on" : ""} onClick={() => setTab(k)}>{l}</button>)}</div>
              {tab === "why" && (
                <div className="stack">
                  <div className="why"><b>Decision</b><span>{a.reason || "Allowed."}</span></div>
                  {!!a.explain?.company_policy?.length && <div className="why"><b>Company policy</b><span>{a.explain.company_policy.join(" ")}{a.explain.company_policy_version ? ` (company policy v${a.explain.company_policy_version})` : ""}</span></div>}
                  {a.explain?.deviation_reason && <div className="why"><b>Deviation {Math.round(a.deviation)}</b><span>{a.explain.deviation_reason}</span></div>}
                  {a.explain?.risk_reason && <div className="why"><b>Risk {Math.round(a.risk)}</b><span>{a.explain.risk_reason}</span></div>}
                  {(a.explain?.factors || []).map((f: any, i: number) => <div className="why" key={i}><b>{f.name || f.label}</b><span>{f.category ? `${String(f.category).replace("_", " ")}: ` : ""}{f.likelihood !== undefined ? `likelihood ${f.likelihood}, severity ${f.severity}/5, contributes ${Math.round(f.contribution ?? 0)}` : (f.detail || f.text || "")}</span></div>)}
                  {a.explain?.components && <div className="why"><b>Deviation components</b><span className="mono">{Object.entries(a.explain.components).map(([k, v]) => `${k} ${typeof v === "number" ? Math.round(v) : v}`).join(" · ")}</span></div>}
                </div>
              )}
              {tab === "rules" && (a.rule_hits?.length ? a.rule_hits.map((h: any, i: number) => (
                <div className="why" key={i}><b>{h.id || h.rule} · {String(h.category || "").replace("_", " ")} · floor {Math.round(h.dev_floor ?? h.floor ?? 0)}</b><span>{h.text || h.message || h.reason}</span></div>
              )) : <Empty>No rules fired.</Empty>)}
              {tab === "json" && <pre className="json">{JSON.stringify(ev.payload, null, 2)}</pre>}
              {ev.type === "tool_call" && (
                <div className="row" style={{ marginTop: 12 }}>
                  <button className="btn s" disabled={a.feedback === "false_positive"} onClick={() => fb("false_positive")}>False positive</button>
                  <button className="btn s" disabled={a.feedback === "confirmed"} onClick={() => fb("confirmed")}>Confirm</button>
                  {(a.verdict === "block" || a.verdict === "hold") && ev.approval?.status !== "approved" && <button className="btn s" onClick={() => fb("allow_once")}>Allow once</button>}
                  {ev.approval && <Chip kind="info">Approval {ev.approval.status}</Chip>}
                  {a.feedback && <span className="muted small">Marked {a.feedback.replace("_", " ")}</span>}
                </div>
              )}
            </>
          );
        })()}
      </Panel>
    </div>
  );
}
