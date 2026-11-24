import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { go, useApp, useData } from "../state";
import { Chip, Empty, Panel, Scores, Verdict } from "../ui";

export default function Demo({ agentKey }: { agentKey?: string }) {
  const { data: agents, reload: reloadAgents } = useData<any[]>("/v1/demo/agents");
  const { refreshAgents, toast } = useApp();
  const key = agentKey || agents?.[0]?.key;
  const info = agents?.find((a) => a.key === key);
  const [sid, setSid] = useState<string | null>(null);
  const [msgs, setMsgs] = useState<any[]>([]);
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const [asUser, setAsUser] = useState<string>("");
  const end = useRef<HTMLDivElement>(null);
  const { data: watch, reload: reloadWatch } = useData<any>(sid ? `/v1/sessions/${sid}` : null, [sid]);

  const store = (k: string, v: string | null) => { try { v ? sessionStorage.setItem(`varuna_demo_${k}`, v) : sessionStorage.removeItem(`varuna_demo_${k}`); } catch { /* ignore */ } };
  useEffect(() => {
    setSid(null); setMsgs([]);
    if (!key) return;
    try { setAsUser(sessionStorage.getItem(`varuna_demo_as_${key}`) || ""); } catch { /* ignore */ }
    let saved: string | null = null;
    try { saved = sessionStorage.getItem(`varuna_demo_${key}`); } catch { /* ignore */ }
    if (saved) api(`/v1/demo/${key}/chat/${saved}`).then((r) => { if (r.messages.length) { setSid(saved); setMsgs(r.messages); } }).catch(() => {});
  }, [key]);
  useEffect(() => { end.current?.scrollIntoView({ block: "end" }); }, [msgs]);

  const send = async (message: string) => {
    if (!message.trim() || !key || busy) return;
    setBusy(true); setText("");
    setMsgs((m) => [...m, { role: "user", text: message }]);
    try {
      const r = await api(`/v1/demo/${key}/chat`, { body: { message, session_id: sid, as_user: info?.personas?.length ? (asUser || info.default_persona) : undefined } });
      setSid(r.session_id); store(key, r.session_id);
      setMsgs((m) => [...m, ...r.messages.slice(1)]);
      await Promise.all([reloadAgents(), refreshAgents(), reloadWatch()]);
    } catch (e: any) { toast(e.message); } finally { setBusy(false); }
  };
  const retry = async (approvalId: number) => {
    setBusy(true);
    try {
      const r = await api(`/v1/demo/${key}/retry`, { body: { session_id: sid, approval_id: approvalId } });
      setMsgs((m) => [...m, ...r.messages]); await Promise.all([refreshAgents(), reloadWatch()]);
    } catch (e: any) { toast(e.message); } finally { setBusy(false); }
  };
  const resume = async () => { await api(`/v1/agents/${info.agent_id}/resume`, { body: {} }); toast("Agent resumed"); reloadAgents(); refreshAgents(); };
  const pickUser = (h: string) => { setAsUser(h); store(`as_${key}`, h); fresh(); };
  const fresh = () => { setSid(null); setMsgs([]); if (key) store(key, null); };

  // approvals for this session, to know when a held call can be retried
  const approvals: Record<number, any> = {};
  (watch?.events || []).forEach((e: any) => { if (e.approval) approvals[e.approval.id] = e.approval; });
  const { data: apList } = useData<any[]>(sid && info ? `/v1/approvals?agent_id=${info.agent_id}&status=pending,approved,rejected,used` : null, [sid]);
  const apStatus = (id: number) => apList?.find((a) => a.id === id)?.status || approvals[id]?.status;

  if (!agents) return <Empty>Loading</Empty>;
  const events = (watch?.events || []).filter((e: any) => e.assessment?.enforceable);
  const scoring = (watch?.events || []).filter((e: any) => e.type === "tool_call" && !e.assessment).length;

  return (
    <>
      <div className="row sp">
        <div className="seg" role="tablist" aria-label="Demo agent">
          {agents.map((a) => <button key={a.key} role="tab" aria-pressed={a.key === key} onClick={() => go(`/demo/${a.key}`)}>{a.name}</button>)}
        </div>
        <button className="btn" onClick={fresh}>New conversation</button>
      </div>
      {info && (
        <p className="muted small" style={{ maxWidth: 760 }}>{info.description} {info.model
          ? <>Scenario buttons follow a fixed script so each demo is repeatable. Anything you type is handled by a language model ({info.model}) working through Meridian's stubbed MCP servers. Every tool call is sent to Varuna and judged exactly as a real agent's would be.</>
          : <>This agent is simulated and follows a script, but every tool call it makes is sent to Varuna and judged exactly as a real agent's would be.</>}</p>
      )}
      {info?.status === "paused" && (
        <div className="banner crit" role="alert"><span>{info.paused_reason || "Paused."}</span><button className="btn s" onClick={resume}>Resume agent</button></div>
      )}
      <div className="demo">
        <Panel title={info?.name || "Agent"}>
          {!!info?.personas?.length && (
            <label className="row small" style={{ marginBottom: 8, gap: 8 }}>
              <span className="muted">Chatting as</span>
              <select className="input" value={asUser || info.default_persona} disabled={busy} onChange={(e) => pickUser(e.target.value)}>
                {info.personas.map((p: any) => <option key={p.id} value={p.id}>{p.label}</option>)}
              </select>
            </label>
          )}
          <div className="pills">{(info?.scenarios || []).map((s: any) => <button key={s.id} className="btn s" disabled={busy} onClick={() => send(s.message)}>{s.label}</button>)}</div>
          <div className="chat" aria-live="polite" style={{ marginTop: 10 }}>
            {!msgs.length && <div className="msg a">{info?.greeting}</div>}
            {msgs.map((m, i) => {
              if (m.role === "user") return <div key={i} className="msg u">{m.text}</div>;
              if (m.role === "thought") return <div key={i} className="thought">{m.text}</div>;
              if (m.role === "tool") {
                const cls = m.verdict === "block" ? "bl" : m.verdict === "hold" || m.verdict === "delay" ? "hd" : "";
                const st = m.approval_id ? apStatus(m.approval_id) : null;
                return (
                  <div key={i} className={`toolc ${cls}`}>
                    <div className="row sp"><span className="mono"><b>{m.tool}</b>({Object.entries(m.args || {}).map(([k, v]) => `${k}=${JSON.stringify(v)}`).join(", ")})</span>
                      <Verdict verdict={m.verdict} band={m.band} would={m.would_verdict} /></div>
                    {m.verdict && m.verdict !== "allow" && <span>{m.reason}</span>}
                    {m.verdict === "hold" && m.approval_id && (
                      st === "approved" ? <div className="row"><Chip kind="ok">Approved</Chip><button className="btn s p" disabled={busy} onClick={() => retry(m.approval_id)}>Retry the action</button></div>
                        : st === "rejected" ? <Chip kind="crit">Rejected by reviewer</Chip>
                        : st === "used" ? <Chip kind="ok">Approved and run</Chip>
                        : <span className="muted">Waiting for a reviewer. <a href="#/approvals">Open approvals</a></span>
                    )}
                    {m.result && m.verdict === "allow" && <span className="muted">{typeof m.result === "string" ? m.result : JSON.stringify(m.result)}</span>}
                  </div>
                );
              }
              return <div key={i} className="msg a">{m.text}</div>;
            })}
            <div ref={end} />
          </div>
          <form className="row" style={{ marginTop: 10 }} onSubmit={(e) => { e.preventDefault(); send(text); }}>
            <input className="input grow" value={text} onChange={(e) => setText(e.target.value)} placeholder="Ask the agent to do something" aria-label="Message" disabled={busy} />
            <button className="btn p" disabled={busy || !text.trim()}>{busy ? "Working…" : "Send"}</button>
          </form>
        </Panel>

        <Panel title="Varuna is watching" note={sid ? (info?.mode === "observe" ? "Observation only: nothing is stopped" : "Enforcing") : "Start a conversation"}>
          {scoring > 0 && <p className="muted small">Scoring {scoring} action{scoring > 1 ? "s" : ""} in the background…</p>}
          {!events.length ? <Empty>Every action the agent takes will be scored here as it happens.</Empty> : (
            <div className="stack">
              {events.map((e: any) => (
                <div key={e.id} className={`fi ${e.assessment.verdict === "block" ? "bl" : ""}`} onClick={() => go(`/trace/${sid}?event=${e.id}`)} role="button" tabIndex={0}>
                  <div className="row sp"><span className="mono">{e.summary}</span><Scores dev={e.assessment.deviation} risk={e.assessment.risk} /></div>
                  <div className="row sp"><span className="muted small">{e.assessment.reason}</span><Verdict verdict={e.assessment.verdict} band={e.assessment.band} would={e.assessment.explain?.would_verdict} /></div>
                </div>
              ))}
              <a href={`#/trace/${sid}`} className="small">Open the full trace →</a>
            </div>
          )}
        </Panel>
      </div>
    </>
  );
}
