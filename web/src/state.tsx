import { createContext, useCallback, useContext, useEffect, useRef, useState, ReactNode } from "react";
import { api, getToken } from "./api";

export interface Agent {
  id: string; slug: string; name: string; description: string; domain: string; status: string; is_demo: boolean;
  paused_reason: string | null; last_seen: string | null; charter_version: number | null; tool_count: number;
  mode?: string; mode_override?: string | null;
  actions_24h?: number; deviation_rate?: number; blocked_24h?: number; held_24h?: number; pending_approvals?: number;
}

interface Ctx {
  agents: Agent[]; agentId: string; setAgentId: (id: string) => void; refreshAgents: () => Promise<void>;
  meta: any; tick: number; connected: boolean; pending: number; toast: (m: string) => void;
}
const C = createContext<Ctx>(null as any);
export const useApp = () => useContext(C);

/** Re-renders consumers whenever the server pushes an event, at most every 300 ms. */
export function AppProvider({ children }: { children: ReactNode }) {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [agentId, setAgentIdState] = useState<string>(() => { try { return localStorage.getItem("varuna_agent") || "all"; } catch { return "all"; } });
  const [meta, setMeta] = useState<any>(null);
  const [tick, setTick] = useState(0);
  const [connected, setConnected] = useState(false);
  const [pending, setPending] = useState(0);
  const [msg, setMsg] = useState("");
  const timer = useRef<number | undefined>(undefined);

  const setAgentId = (id: string) => { setAgentIdState(id); try { localStorage.setItem("varuna_agent", id); } catch { /* ignore */ } };
  const toast = useCallback((m: string) => { setMsg(m); window.setTimeout(() => setMsg(""), 3500); }, []);
  const refreshAgents = useCallback(async () => {
    const a = await api<Agent[]>("/v1/agents");
    setAgents(a);
    setPending(a.reduce((n, x) => n + (x.pending_approvals || 0), 0));
  }, []);

  useEffect(() => { api("/v1/meta").then(setMeta).catch(() => {}); refreshAgents().catch(() => {}); }, [refreshAgents]);
  useEffect(() => { refreshAgents().catch(() => {}); }, [tick, refreshAgents]);

  useEffect(() => {
    const t = getToken();
    if (!t) return;
    const es = new EventSource(`/v1/stream?token=${encodeURIComponent(t)}`);
    es.onopen = () => setConnected(true);
    es.onerror = () => setConnected(false);
    es.onmessage = () => {
      window.clearTimeout(timer.current);
      timer.current = window.setTimeout(() => setTick((n) => n + 1), 300);
    };
    return () => es.close();
  }, []);

  return (
    <C.Provider value={{ agents, agentId, setAgentId, refreshAgents, meta, tick, connected, pending, toast }}>
      {children}
      {msg && <div className="toast" role="status">{msg}</div>}
    </C.Provider>
  );
}

export function useRoute() {
  const parse = () => {
    const raw = location.hash.replace(/^#\/?/, "");
    const [path, q = ""] = raw.split("?");
    return { parts: path.split("/").filter(Boolean), query: new URLSearchParams(q) };
  };
  const [r, setR] = useState(parse);
  useEffect(() => { const f = () => setR(parse()); window.addEventListener("hashchange", f); return () => window.removeEventListener("hashchange", f); }, []);
  return r;
}
export const go = (path: string) => { location.hash = path; };

/** Fetch with refetch on live events. */
export function useData<T>(path: string | null, deps: unknown[] = []) {
  const { tick } = useApp();
  const [data, setData] = useState<T | null>(null);
  const [err, setErr] = useState<string>("");
  const [loading, setLoading] = useState(true);
  const reload = useCallback(() => {
    if (!path) return Promise.resolve();
    return api<T>(path).then((d) => { setData(d); setErr(""); }).catch((e) => setErr(e.message)).finally(() => setLoading(false));
  }, [path]);
  // eslint-disable-next-line react-hooks/exhaustive-deps
  useEffect(() => { reload(); }, [reload, tick, ...deps]);
  return { data, err, loading, reload, setData };
}
