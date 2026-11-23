import { useState } from "react";
import { api, ago } from "../api";
import { go, useApp, useData } from "../state";
import { Chip, Empty, Panel } from "../ui";

export default function Approvals() {
  const { agentId, toast, refreshAgents } = useApp();
  const { data: pending, reload: r1 } = useData<any[]>(`/v1/approvals?status=pending&agent_id=${agentId}`);
  const { data: history, reload: r2 } = useData<any[]>(`/v1/approvals?status=approved,rejected,used&agent_id=${agentId}`);
  const [notes, setNotes] = useState<Record<number, string>>({});

  const decide = async (id: number, decision: "approve" | "reject") => {
    try {
      await api(`/v1/approvals/${id}/decide`, { body: { decision, note: notes[id] || "" } });
      toast(decision === "approve" ? "Approved. The agent can retry the same action once." : "Rejected");
      await Promise.all([r1(), r2(), refreshAgents()]);
    } catch (e: any) { toast(e.message); }
  };

  return (
    <>
      <p className="muted" style={{ maxWidth: 680 }}>Actions held because their score reached the slow band. An approval lets the agent run that exact action once.</p>
      {!pending ? <Empty>Loading</Empty> : pending.length === 0 ? <Empty>No actions are waiting for a decision.</Empty> : (
        <div className="grid g11">
          {pending.map((a) => (
            <Panel key={a.id} title={<span>{a.agent_name} <Chip kind="warn">Score {Math.round(a.decision_score)}</Chip></span>} note={ago(a.requested_at)}>
              <div className="mono" style={{ overflowWrap: "anywhere" }}>{a.summary}</div>
              <pre className="json">{JSON.stringify(a.payload.args, null, 2)}</pre>
              <label className="f">Note (optional)<input className="input" value={notes[a.id] || ""} onChange={(e) => setNotes({ ...notes, [a.id]: e.target.value })} /></label>
              <div className="row">
                <button className="btn p" onClick={() => decide(a.id, "approve")}>Approve once</button>
                <button className="btn d" onClick={() => decide(a.id, "reject")}>Reject</button>
                <button className="btn" onClick={() => go(`/trace/${a.session_id}?event=${a.event_id}`)}>See why</button>
              </div>
            </Panel>
          ))}
        </div>
      )}
      <Panel title="Recent decisions">
        {history && history.length ? (
          <div className="tbl"><table>
            <thead><tr><th>Decided</th><th>Agent</th><th>Action</th><th>Result</th><th>By</th><th>Note</th></tr></thead>
            <tbody>{history.map((a) => (
              <tr key={a.id}><td className="muted small">{ago(a.decided_at)}</td><td>{a.agent_name}</td><td className="mono" style={{ overflowWrap: "anywhere" }}>{a.summary}</td>
                <td><Chip kind={a.status === "rejected" ? "crit" : "ok"}>{a.status === "used" ? "Approved, used" : a.status === "approved" ? "Approved" : "Rejected"}</Chip></td>
                <td>{a.decided_by}{a.source === "seed" ? " (sample)" : ""}</td><td className="muted small">{a.note}</td></tr>
            ))}</tbody>
          </table></div>
        ) : <Empty>No decisions yet</Empty>}
      </Panel>
    </>
  );
}
