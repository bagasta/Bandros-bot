"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { renderMarkdown } from "./markdown";

type Bot = { id: string; name: string; description: string; instructions?: string; model?: string | null; status: "active" | "archived" };
type Message = { id: string; role: string; content: string; created_at?: string };
type Run = { id: string; status: string; error: string | null };
type Group = { id: string; name: string; description: string; members: Bot[] };
type GroupMessage = { id: string; sender_type: string; sender_bot_id: string | null; content: string; created_at?: string };
type GroupActivity = { bot_id: string; name: string; status: string };
type ChatGPTStatus = {
  connected: boolean;
  available?: boolean;
  email?: string | null;
  subscription_enabled?: boolean;
  preferred_model?: string | null;
  models?: ChatGPTModel[];
  reason?: string | null;
};
type ChatGPTModel = { id: string; display_name: string };
const latestCodexModel: ChatGPTModel = { id: "gpt-6-luna", display_name: "GPT-6 Luna" };

function withLatestCodexModel(models: ChatGPTModel[]): ChatGPTModel[] {
  return [latestCodexModel, ...models.filter((model) => model.id !== latestCodexModel.id)];
}
type DeviceFlow = {
  flow_id: string;
  user_code: string;
  verification_url: string;
  interval: number;
};

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL;

const nameColors = ["var(--sender-green)", "var(--sender-amber)", "var(--sender-purple)", "var(--sender-blue)", "var(--sender-pink)", "var(--sender-teal)"];

function nameColor(name: string) {
  const index = [...name].reduce((sum, char) => sum + char.charCodeAt(0), 0) % nameColors.length;
  return nameColors[index];
}

const bandrosToppings = [
  { cake: "#f6c453", crust: "#e08a2c" },
  { cake: "#c6e38a", crust: "#6aaa4a" },
  { cake: "#f3b7c8", crust: "#e07a93" },
  { cake: "#e7c08a", crust: "#8a5a32" },
  { cake: "#f7d36a", crust: "#d4892a" },
  { cake: "#b7e0d2", crust: "#3d8f78" },
];

function BandrosAvatar({ name, working = false }: { name: string; working?: boolean }) {
  const topping = bandrosToppings[[...name].reduce((sum, char) => sum + char.charCodeAt(0), 0) % bandrosToppings.length];
  return (
    <span className={`bandros-avatar ${working ? "is-working" : ""}`} aria-hidden="true">
      <span className="bandros-cake">
      <svg viewBox="0 0 64 64">
        <ellipse cx="32" cy="58" rx="16" ry="3" fill="#000" opacity=".28" />
        <circle cx="32" cy="32" r="26" fill={topping.crust} />
        <circle cx="32" cy="30" r="22" fill={topping.cake} />
        <circle cx="22" cy="22" r="1.5" fill={topping.crust} opacity=".55" />
        <circle cx="34" cy="18" r="1.1" fill={topping.crust} opacity=".4" />
        <circle cx="42" cy="24" r="1.3" fill={topping.crust} opacity=".45" />
        <ellipse cx="18" cy="36" rx="3" ry="1.6" fill="#f3a0a0" opacity=".75" />
        <ellipse cx="46" cy="36" rx="3" ry="1.6" fill="#f3a0a0" opacity=".75" />
        <g className="bandros-eyes">
          <ellipse cx="24" cy="30" rx="3.2" ry="3.6" fill="#2b2118" />
          <ellipse cx="40" cy="30" rx="3.2" ry="3.6" fill="#2b2118" />
          <circle cx="25.2" cy="28.8" r="1.1" fill="#fff" />
          <circle cx="41.2" cy="28.8" r="1.1" fill="#fff" />
        </g>
        <path d="M26 38 Q32 44 38 38" fill="none" stroke="#2b2118" strokeWidth="1.7" strokeLinecap="round" />
      </svg>
      </span>
    </span>
  );
}

function chatTime(value?: string) {
  if (!value) return "";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleTimeString("id-ID", { hour: "2-digit", minute: "2-digit" });
}

function MemberPicker({ members, onChoose }: { members: Bot[]; onChoose: (id: string) => void }) {
  const pickerRef = useRef<HTMLDetailsElement>(null);
  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && !pickerRef.current?.contains(event.target)) pickerRef.current?.removeAttribute("open");
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, []);
  return <details className="bandros-member-picker" ref={pickerRef} onKeyDown={(event) => {
    if (event.key === "Escape") {
      event.preventDefault();
      pickerRef.current?.removeAttribute("open");
      pickerRef.current?.querySelector("summary")?.focus();
    }
  }} onBlur={(event) => {
    if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget)) event.currentTarget.removeAttribute("open");
  }}>
    <summary><span aria-hidden="true">＋</span>Tambah anggota<span className="bandros-picker-chevron" aria-hidden="true" /></summary>
    <div className="bandros-member-options" role="group" aria-label="Bot yang tersedia">
      <p>Tambahkan ke grup</p>
      {members.length ? members.map((member) => <button type="button" key={member.id} onClick={() => {
        pickerRef.current?.removeAttribute("open");
        pickerRef.current?.querySelector("summary")?.focus();
        onChoose(member.id);
      }}><BandrosAvatar name={member.name} /><span>{member.name}</span><span className="bandros-member-add-mark" aria-hidden="true">＋</span></button>) : <p className="bandros-member-empty">Semua bot sudah bergabung.</p>}
    </div>
  </details>;
}

function CreateMenu({ disabled, onCreateBot, onCreateGroup }: { disabled: boolean; onCreateBot: () => void; onCreateGroup: () => void }) {
  const menuRef = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);
  const [open, setOpen] = useState(false);
  useEffect(() => {
    const dismiss = (event: PointerEvent) => {
      if (event.target instanceof Node && !menuRef.current?.contains(event.target)) setOpen(false);
    };
    document.addEventListener("pointerdown", dismiss);
    return () => document.removeEventListener("pointerdown", dismiss);
  }, []);
  useEffect(() => { if (disabled) setOpen(false); }, [disabled]);
  const choose = (action: () => void) => {
    setOpen(false);
    triggerRef.current?.focus();
    action();
  };
  return <div className="bandros-create" ref={menuRef} onBlur={(event) => {
    if (event.relatedTarget instanceof Node && !event.currentTarget.contains(event.relatedTarget)) setOpen(false);
  }} onKeyDown={(event) => {
    if (event.key === "Escape") { setOpen(false); triggerRef.current?.focus(); }
  }}>
    <button className="bandros-toolbar-button" ref={triggerRef} type="button" aria-label="Buat bot atau grup" title="Buat bot atau grup" aria-expanded={open} aria-controls="bandros-create-options" disabled={disabled} onClick={() => setOpen((value) => !value)}><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 5v14M5 12h14" /></svg></button>
    {open && <div className="bandros-create-options" id="bandros-create-options" role="group" aria-label="Buat baru">
      <button type="button" onClick={() => choose(onCreateBot)}>Buat bot</button>
      <button type="button" onClick={() => choose(onCreateGroup)}>Buat grup</button>
    </div>}
  </div>;
}

function hasOlderMessages<T extends { id: string }>(current: T[], next: T[]): boolean {
  const nextIds = new Set(next.map((item) => item.id));
  const kept = current.filter((item) => !item.id.startsWith("local-"));
  return kept.some((item) => !nextIds.has(item.id)) && next.length < kept.length;
}

const quickPrompts = [
  "Analisis performa semua Bot",
  "Buat Bot baru untuk riset LinkedIn",
  "Alokasikan batas token tiap Bot",
  "Tunjukkan pekerjaan yang sedang tertunda",
];

function workspaceKey(session: string | null): string {
  return session ? `bandros_workspace:${session.slice(0, 80)}` : "";
}

function revisionKey(session: string | null): string {
  return session ? `bandros_workspace_rev:${session.slice(0, 80)}` : "";
}

function rememberSnapshot(session: string | null, response: Response) {
  const key = workspaceKey(session);
  const revKey = revisionKey(session);
  const snapshot = response.headers.get("X-Bandros-Snapshot");
  if (!key || !snapshot) return;
  const rev = Number(response.headers.get("X-Bandros-Snapshot-Rev") || "0");
  const prev = Number(window.localStorage.getItem(revKey) || "0");
  if (rev < prev) return;
  window.localStorage.setItem(key, snapshot);
  window.localStorage.setItem(revKey, String(rev));
}

function desktopUrl(ticket: string): string | null {
  if (!apiBase) return null;
  const prefix = new URL(apiBase).pathname.replace(/^\/|\/$/g, "");
  const socketPath = `${prefix}/computer/view/${ticket}/websockify`;
  const params = new URLSearchParams({
    autoconnect: "1",
    scale: "true",
    reconnect: "1",
    shared: "1",
    path: socketPath,
  });
  return `${apiBase}/computer/view/${encodeURIComponent(ticket)}/vnc_lite.html?${params}`;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (!apiBase) throw new Error("NEXT_PUBLIC_API_BASE_URL belum diatur.");
  const session = typeof window !== "undefined" ? window.localStorage.getItem("bandros_chatgpt_session") : null;
  const snapshot = session ? window.localStorage.getItem(workspaceKey(session)) : null;
  let response: Response;
  try {
    response = await fetch(`${apiBase}${path}`, {
      ...init,
      headers: {
        "Content-Type": "application/json",
        ...(session ? { "X-Bandros-Session": session } : {}),
        ...(snapshot ? { "X-Bandros-Snapshot": snapshot } : {}),
        ...init?.headers,
      },
    });
  } catch (cause) {
    if (cause instanceof TypeError) throw new Error("Koneksi ke server terputus. Kirim ulang sebentar lagi.");
    throw cause;
  }
  rememberSnapshot(session, response);
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `Request gagal (${response.status}).`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export default function GrokDashboard() {
  const [bots, setBots] = useState<Bot[]>([]);
  const [groups, setGroups] = useState<Group[]>([]);
  const [selectedBot, setSelectedBot] = useState<Bot | null>(null);
  const [selectedGroup, setSelectedGroup] = useState<Group | null>(null);
  const [messages, setMessages] = useState<Message[]>([]);
  const [groupMessages, setGroupMessages] = useState<GroupMessage[]>([]);
  const [prompt, setPrompt] = useState("");
  const [loading, setLoading] = useState(true);
  const [working, setWorking] = useState(false);
  const [activeRunId, setActiveRunId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [editInstructions, setEditInstructions] = useState("");
  const [editModel, setEditModel] = useState("");
  const [chatGPT, setChatGPT] = useState<ChatGPTStatus>({ connected: false });
  const [chatGPTModels, setChatGPTModels] = useState<ChatGPTModel[]>([]);
  const [modelByBot, setModelByBot] = useState<Record<string, string>>({});
  const [deviceFlow, setDeviceFlow] = useState<DeviceFlow | null>(null);
  const [deviceStatus, setDeviceStatus] = useState("");
  const [mobilePane, setMobilePane] = useState<"list" | "chat">("list");
  const [authReady, setAuthReady] = useState(false);
  const [typingNames, setTypingNames] = useState<string[]>([]);
  const [botWorking, setBotWorking] = useState(false);
  const [theme, setTheme] = useState<"dark" | "light">("dark");
  const [membersOpen, setMembersOpen] = useState(true);
  const [motionEnabled, setMotionEnabled] = useState(true);
  const [computerState, setComputerState] = useState<"off" | "on" | "unavailable">("off");
  const [screenUrl, setScreenUrl] = useState<string | null>(null);
  const [screenOpen, setScreenOpen] = useState(false);
  const [screenTicket, setScreenTicket] = useState<string | null>(null);
  const [computerBusy, setComputerBusy] = useState(false);
  const [approvals, setApprovals] = useState<Array<{ id: string; tool_name: string; reason: string }>>([]);
  useEffect(() => {
    setMotionEnabled(window.localStorage.getItem("bandros_motion") !== "off");
  }, []);
  useEffect(() => {
    if (!chatGPT.connected || computerState !== "on") {
      setScreenTicket(null);
      return;
    }
    let active = true;
    request<{ ticket: string }>("/computer/ticket")
      .then((next) => { if (active) setScreenTicket(next.ticket); })
      .catch(() => undefined);
    return () => { active = false; };
  }, [chatGPT.connected, computerState]);
  useEffect(() => {
    if (!chatGPT.connected) return;
    let active = true;
    request<{ state: "off" | "on" | "unavailable"; screen_url: string | null }>("/computer")
      .then((next) => {
        if (!active) return;
        setComputerState(next.state);
        setScreenUrl(next.screen_url);
        if (next.state !== "on") setScreenOpen(false);
      })
      .catch(() => undefined);
    return () => { active = false; };
  }, [chatGPT.connected]);
  const [mentionQuery, setMentionQuery] = useState<string | null>(null);
  const composerRef = useRef<HTMLTextAreaElement>(null);
  const chatRef = useRef<HTMLDivElement>(null);
  const stickToBottom = useRef(true);
  const workingRef = useRef(false);
  const shownRunError = useRef<string | null>(null);
  const selectedGroupId = useRef<string | null>(null);
  workingRef.current = working;
  selectedGroupId.current = selectedGroup?.id ?? null;

  const activeBots = useMemo(() => bots.filter((bot) => bot.status === "active"), [bots]);

  const loadBots = async (quiet = false) => {
    try {
      const [nextBots, nextGroups] = await Promise.all([
        request<Bot[]>("/bots"),
        request<Group[]>("/groups"),
      ]);
      setBots(nextBots);
      setGroups(nextGroups);
      const orchestrator = nextBots.find((bot) => bot.status === "active" && bot.name.toLowerCase() === "bandros");
      setSelectedBot((current) => {
        if (current) return nextBots.find((bot) => bot.id === current.id) ?? null;
        if (quiet || selectedGroupId.current) return null;
        return orchestrator ?? nextBots.find((bot) => bot.status === "active") ?? nextBots[0] ?? null;
      });
      setSelectedGroup((current) => {
        if (!current) return null;
        const next = nextGroups.find((group) => group.id === current.id);
        if (!next) return null;
        const sameMembers = next.members.length === current.members.length && next.members.every((member, index) => member.id === current.members[index]?.id && member.name === current.members[index]?.name);
        return next.name === current.name && sameMembers ? current : next;
      });
    } catch (cause) {
      if (!quiet) setError(cause instanceof Error ? cause.message : "Bot tidak dapat dimuat.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    try {
      const savedTheme = window.localStorage.getItem("bandros_theme");
      if (savedTheme === "light" || savedTheme === "dark") setTheme(savedTheme);
      const savedModels = JSON.parse(window.localStorage.getItem("bandros_bot_models") || "{}") as Record<string, string>;
      if (savedModels && typeof savedModels === "object") setModelByBot(savedModels);
      const cached = JSON.parse(window.localStorage.getItem("bandros_codex_models") || "[]") as ChatGPTModel[];
      if (Array.isArray(cached) && cached.length > 0) setChatGPTModels(withLatestCodexModel(cached));
    } catch {
      /* Cache is only a fallback when the catalog request is unavailable. */
    }
  }, []);
  const rememberModels = (models: ChatGPTModel[]) => {
    const listed = withLatestCodexModel(models);
    setChatGPTModels(listed);
    window.localStorage.setItem("bandros_codex_models", JSON.stringify(listed));
  };
  const loadChatGPT = async () => {
    const status = await request<ChatGPTStatus>("/auth/chatgpt/status");
    setChatGPT(status);
    if (status.models?.length) rememberModels(status.models);
    if (status.connected && status.subscription_enabled) {
      try {
        const result = await request<{ models: ChatGPTModel[] }>("/auth/chatgpt/models");
        rememberModels(result.models);
      } catch {
        if (!status.models?.length) rememberModels([latestCodexModel]);
      }
    }
  };

  useEffect(() => {
    const params = new URLSearchParams(window.location.search);
    const session = params.get("session");
    if (session) {
      window.localStorage.setItem("bandros_chatgpt_session", session);
      params.delete("session");
      params.delete("chatgpt");
      const query = params.toString();
      window.history.replaceState({}, "", query ? `?${query}` : window.location.pathname);
    }
    void loadChatGPT()
      .catch((cause) => {
        setError(cause instanceof Error ? cause.message : "Model ChatGPT tidak dapat dimuat.");
      })
      .finally(() => setAuthReady(true));
  }, []);
  useEffect(() => {
    if (!authReady) return;
    if (!chatGPT.connected) {
      setBots([]);
      setGroups([]);
      setSelectedBot(null);
      setSelectedGroup(null);
      setMessages([]);
      setGroupMessages([]);
      setLoading(false);
      return;
    }
    void loadBots();
    const timer = window.setInterval(() => void loadBots(true), 2000);
    return () => window.clearInterval(timer);
  }, [authReady, chatGPT.connected]);

  useEffect(() => {
    if (!deviceFlow) return;
    let active = true;
    const poll = async () => {
      try {
        const result = await request<{ status: string; session_token?: string; slow_down?: boolean }>(
          "/auth/chatgpt/device/poll",
          { method: "POST", body: JSON.stringify({ flow_id: deviceFlow.flow_id }) },
        );
        if (!active) return;
        if (result.status === "connected" && result.session_token) {
          window.localStorage.setItem("bandros_chatgpt_session", result.session_token);
          setDeviceStatus("ChatGPT terhubung.");
          setDeviceFlow(null);
          await loadChatGPT();
          await loadBots();
          return;
        }
        setDeviceStatus("Menunggu persetujuan di ChatGPT…");
        window.setTimeout(poll, (deviceFlow.interval + (result.slow_down ? 5 : 0)) * 1000);
      } catch (cause) {
        if (!active) return;
        setDeviceStatus(cause instanceof Error ? cause.message : "Device login gagal.");
        window.setTimeout(poll, deviceFlow.interval * 1000);
      }
    };
    const timer = window.setTimeout(poll, deviceFlow.interval * 1000);
    return () => { active = false; window.clearTimeout(timer); };
  }, [deviceFlow]);

  useEffect(() => {
    if (!selectedBot || !chatGPT.connected) {
      if (!selectedBot) setMessages([]);
      setBotWorking(false);
      return;
    }
    let active = true;
    const botId = selectedBot.id;
    setApprovals([]);
    const tick = async () => {
      try {
        const nextMessages = await request<Message[]>(`/bots/${botId}/messages`);
        if (!active) return;
        const visible = nextMessages.filter((message) => message.role !== "group");
        setMessages((current) => {
          const kept = current.filter((item) => !item.id.startsWith("local-"));
          const nextIds = new Set(visible.map((message) => message.id));
          const sameRoom = kept.length === 0 || kept.some((item) => nextIds.has(item.id));
          if (!sameRoom) return visible;
          if (hasOlderMessages(current, visible)) return current;
          const pending = current.filter((item) => item.id.startsWith("local-") && !visible.some((message) => message.role === item.role && message.content === item.content));
          return [...visible, ...pending];
        });
        const activity = await request<{ working: boolean; error?: string | null; approvals?: Array<{ id: string; tool_name: string; reason: string }> }>(`/bots/${botId}/activity`);
        if (!active) return;
        setBotWorking(activity.working);
        setApprovals(activity.approvals ?? []);
        if (activity.error && !workingRef.current && shownRunError.current !== activity.error) {
          shownRunError.current = activity.error;
          setError(activity.error);
        }
      } catch {
        /* Poll lagi pada interval berikutnya. */
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), 1200);
    return () => { active = false; window.clearInterval(timer); };
  }, [selectedBot, chatGPT.connected]);

  useEffect(() => {
    if (!selectedGroup || !chatGPT.connected) {
      if (!selectedGroup) setGroupMessages([]);
      setTypingNames([]);
      return;
    }
    let active = true;
    const groupId = selectedGroup.id;
    const tick = async () => {
      try {
        const [nextMessages, activity] = await Promise.all([
          request<GroupMessage[]>(`/groups/${groupId}/messages`),
          request<GroupActivity[]>(`/groups/${groupId}/activity`),
        ]);
        if (!active) return;
        setGroupMessages((current) => {
          const nextIds = new Set(nextMessages.map((message) => message.id));
          const sameRoom = current.length === 0 || current.some((item) => nextIds.has(item.id));
          if (!sameRoom) return nextMessages;
          if (hasOlderMessages(current, nextMessages)) return current;
          const pending = current.filter((item) => item.id.startsWith("local-") && !nextMessages.some((message) => message.sender_type === item.sender_type && message.content === item.content));
          return [...nextMessages, ...pending];
        });
        setTypingNames(activity.map((item) => item.name));
      } catch {
        /* Poll lagi pada interval berikutnya. */
      }
    };
    void tick();
    const timer = window.setInterval(() => void tick(), 1200);
    return () => { active = false; window.clearInterval(timer); };
  }, [selectedGroup, chatGPT.connected]);

  useEffect(() => {
    stickToBottom.current = true;
  }, [selectedBot?.id, selectedGroup?.id]);
  useEffect(() => {
    const node = chatRef.current;
    if (!node || !stickToBottom.current) return;
    node.scrollTop = node.scrollHeight;
  }, [messages, groupMessages, typingNames, working, botWorking]);

  const toggleComputer = async () => {
    setComputerBusy(true);
    setError(null);
    try {
      const next = await request<{ state: "off" | "on" | "unavailable"; screen_url: string | null }>(
        computerState === "on" ? "/computer/stop" : "/computer/start",
        { method: "POST" },
      );
      setComputerState(next.state);
      setScreenUrl(next.screen_url);
      if (next.state !== "on") setScreenOpen(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Komputer gagal dinyalakan.");
    } finally {
      setComputerBusy(false);
    }
  };

  const sendMessage = async (event: FormEvent) => {
    event.preventDefault();
    if ((!selectedBot && !selectedGroup) || !prompt.trim() || working) return;
    const content = prompt.trim();
    stickToBottom.current = true;
    setPrompt(""); setWorking(true); setError(null);
    try {
      if (selectedGroup) {
        const groupId = selectedGroup.id;
        setGroupMessages((current) => [...current, { id: `local-${Date.now()}`, sender_type: "user", sender_bot_id: null, content, created_at: new Date().toISOString() }]);
        await request<GroupMessage>(`/groups/${groupId}/messages`, { method: "POST", body: JSON.stringify({ content }) });
        const [nextMessages, nextGroups] = await Promise.all([
          request<GroupMessage[]>(`/groups/${groupId}/messages`),
          request<Group[]>("/groups"),
        ]);
        if (selectedGroup?.id !== groupId) return;
        setGroupMessages(nextMessages);
        setGroups(nextGroups);
        const refreshed = nextGroups.find((group) => group.id === groupId);
        if (refreshed) setSelectedGroup(refreshed);
        return;
      }
      const botId = selectedBot?.id;
      if (!botId) return;
      const model = modelByBot[botId] ?? selectedBot?.model ?? null;
      setMessages((current) => [...current, { id: `local-${Date.now()}`, role: "user", content }]);
      const run = await request<Run>(`/bots/${botId}/messages`, { method: "POST", body: JSON.stringify({ content, model }) });
      setActiveRunId(run.id);
      if (["completed", "failed", "failed_retryable", "cancelled"].includes(run.status)) {
        if (selectedBot?.id !== botId) return;
        const nextMessages = await request<Message[]>(`/bots/${botId}/messages`);
        setMessages(nextMessages.filter((message) => message.role !== "group"));
        if (run.error) setError(run.error);
        void loadBots();
        return;
      }
      const session = window.localStorage.getItem("bandros_chatgpt_session");
      const response = await fetch(`${apiBase}/runs/${run.id}/events/stream`, {
        headers: session ? { "X-Bandros-Session": session } : {},
      });
      if (!response.ok || !response.body) throw new Error("Streaming Run tidak tersedia.");
      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      let completed: Run | null = null;
      while (!completed) {
        const chunk = await reader.read();
        if (chunk.done) break;
        buffer += decoder.decode(chunk.value, { stream: true });
        const blocks = buffer.split("\n\n");
        buffer = blocks.pop() || "";
        for (const block of blocks) {
          const eventName = block.match(/^event: (.+)$/m)?.[1];
          const data = block.match(/^data: (.+)$/m)?.[1];
          if (!data) continue;
          const parsed = JSON.parse(data) as { payload?: { content?: string } } & Run;
          if (eventName === "assistant.delta") {
            const streamedContent = parsed.payload?.content || "";
            setMessages((current) => [
              ...current.filter((message) => message.id !== `streaming-${run.id}`),
              { id: `streaming-${run.id}`, role: "assistant", content: streamedContent },
            ]);
          } else if (eventName === "run.completed") {
            completed = parsed;
          }
        }
      }
      if (!completed) throw new Error("Streaming berakhir sebelum Run selesai.");
      if (completed.error) throw new Error(completed.error);
      if (selectedBot?.id !== botId) return;
      setMessages(await request<Message[]>(`/bots/${botId}/messages`));
      void loadBots();
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Pesan tidak dapat dikirim.");
    } finally { setWorking(false); setActiveRunId(null); }
  };

  const stopRun = async () => {
    try {
      if (selectedGroup) {
        await request<Run[]>(`/groups/${selectedGroup.id}/cancel`, { method: "POST" });
      } else if (activeRunId) {
        await request<Run>(`/runs/${activeRunId}/cancel`, { method: "POST" });
      } else return;
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Run tidak dapat dihentikan.");
    }
  };

  const openBot = (bot: Bot) => {
    setSelectedGroup(null);
    setSelectedBot(bot);
    setMobilePane("chat");
  };

  const openBotSettings = (bot: Bot) => {
    openBot(bot);
    setEditName(bot.name);
    setEditDescription(bot.description);
    setEditInstructions(bot.instructions || "");
    setEditModel(modelByBot[bot.id] ?? bot.model ?? "");
    setSettingsOpen(true);
  };

  const openSettings = () => {
    if (!selectedBot) return;
    openBotSettings(selectedBot);
  };

  const saveSettings = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedBot || !editName.trim()) return;
    try {
      const updated = await request<Bot>(`/bots/${selectedBot.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          name: editName.trim(),
          description: editDescription.trim(),
          instructions: editInstructions.trim(),
          model: editModel.trim() || null,
        }),
      });
      setBots((current) => current.map((bot) => bot.id === updated.id ? updated : bot));
      setSelectedBot(updated);
      setSettingsOpen(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Settings Agent tidak dapat disimpan.");
    }
  };

  const deleteBot = async (bot: Bot) => {
    if (!window.confirm(`Hapus ${bot.name}? Percakapan bot ini ikut terhapus.`)) return;
    try {
      await request<void>(`/bots/${bot.id}`, { method: "DELETE" });
      const remaining = bots.filter((item) => item.id !== bot.id);
      setBots(remaining);
      if (selectedBot?.id === bot.id) {
        const next = remaining.find((item) => item.status === "active") ?? null;
        setSelectedBot(next);
        setMessages([]);
        if (!next) setMobilePane("list");
      }
      setSettingsOpen(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Bot tidak dapat dihapus.");
    }
  };

  const openGroup = (group: Group) => {
    setSelectedBot(null);
    setSelectedGroup(group);
    setMobilePane("chat");
  };

  const createGroup = async () => {
    const name = window.prompt("Nama grup baru");
    if (!name?.trim() || activeBots.length === 0) return;
    try {
      const group = await request<Group>("/groups", {
        method: "POST",
        body: JSON.stringify({ name: name.trim(), description: "Grup koordinasi Bandros.", member_bot_ids: activeBots.map((bot) => bot.id) }),
      });
      setGroups((current) => [group, ...current]);
      openGroup(group);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Grup tidak dapat dibuat.");
    }
  };

  const createBot = async () => {
    const name = window.prompt("Nama Bot baru");
    if (!name?.trim()) return;
    try {
      const bot = await request<Bot>("/bots", { method: "POST", body: JSON.stringify({
        name: name.trim(),
        description: "Bot baru dalam ekosistem Bandros.",
        instructions: "Bantu pengguna dengan jawaban yang jelas dan dapat ditinjau.",
      }) });
      setBots((current) => [bot, ...current.filter((item) => item.id !== bot.id)]);
      setSelectedBot(bot);
      setMobilePane("chat");
      void loadBots();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Bot tidak dapat dibuat."); }
  };

  const connectChatGPT = async () => {
    try {
      setDeviceStatus("Meminta kode dari ChatGPT…");
      const result = await request<DeviceFlow>("/auth/chatgpt/device/start", { method: "POST" });
      setDeviceFlow(result);
      setDeviceStatus("Buka ChatGPT dan masukkan kode berikut.");
      window.open(result.verification_url, "_blank", "noopener,noreferrer");
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "ChatGPT tidak dapat dihubungkan.");
    }
  };

  const disconnectChatGPT = async () => {
    try {
      await request<{ disconnected: boolean }>("/auth/chatgpt/disconnect", { method: "POST" });
      window.localStorage.removeItem("bandros_chatgpt_session");
      for (const key of Object.keys(window.localStorage)) {
        if (key.startsWith("bandros_workspace:")) window.localStorage.removeItem(key);
      }
      setChatGPT({ connected: false, available: true });
      setChatGPTModels([]);
      setBots([]);
      setGroups([]);
      setSelectedBot(null);
      setSelectedGroup(null);
      setMessages([]);
      setGroupMessages([]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "ChatGPT tidak dapat diputus.");
    }
  };

  const chooseModel = async (model: string) => {
    if (!selectedBot) return;
    const next = { ...modelByBot, [selectedBot.id]: model };
    setModelByBot(next);
    window.localStorage.setItem("bandros_bot_models", JSON.stringify(next));
    setEditModel(model);
    try {
      const updated = await request<Bot>(`/bots/${selectedBot.id}`, {
        method: "PATCH",
        body: JSON.stringify({ model: model || null }),
      });
      setBots((current) => current.map((bot) => bot.id === updated.id ? updated : bot));
      setSelectedBot({ ...updated, model: model || null });
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Model tidak dapat disimpan di server. Pilihan tetap dipakai untuk pesan ini.");
    }
  };

  const syncMention = (value: string, cursor: number) => {
    if (!selectedGroup) { setMentionQuery(null); return; }
    const match = value.slice(0, cursor).match(/(?:^|\s)@([^\n@]*)$/);
    setMentionQuery(match ? match[1] : null);
  };

  const mentionSuggestions = selectedGroup && mentionQuery !== null
    ? [
        ...("everyone".includes(mentionQuery.toLowerCase()) ? [{ id: "everyone", name: "everyone" }] : []),
        ...selectedGroup.members.filter((member) => member.name.toLowerCase().includes(mentionQuery.toLowerCase())),
      ]
    : [];

  const insertMention = (name: string) => {
    const cursor = composerRef.current?.selectionStart ?? prompt.length;
    const before = prompt.slice(0, cursor).replace(/(?:^|\s)@([^\n@]*)$/, (full) => `${full.startsWith("@") ? "" : full[0]}@${name} `);
    const next = before + prompt.slice(cursor);
    setPrompt(next);
    setMentionQuery(null);
    window.requestAnimationFrame(() => {
      composerRef.current?.focus();
      composerRef.current?.setSelectionRange(before.length, before.length);
    });
  };

  const addGroupMember = async (botId: string) => {
    if (!selectedGroup || !botId) return;
    try {
      const updated = await request<Group>(`/groups/${selectedGroup.id}/members`, { method: "POST", body: JSON.stringify({ bot_id: botId }) });
      setGroups((current) => current.map((group) => group.id === updated.id ? updated : group));
      setSelectedGroup(updated);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Anggota tidak dapat ditambahkan.");
    }
  };

  const displayName = selectedGroup?.name || selectedBot?.name || "New Bot";
  const availableMembers = selectedGroup ? activeBots.filter((bot) => !selectedGroup.members.some((member) => member.id === bot.id)) : [];
  const typingPeople = selectedGroup
    ? typingNames
    : (working || botWorking) && selectedBot ? [selectedBot.name] : [];
  const currentModel = selectedBot ? (modelByBot[selectedBot.id] ?? selectedBot.model ?? "") : "";
  const selectableModels = withLatestCodexModel(chatGPTModels);
  const automaticModel = selectableModels.find((model) => model.id === chatGPT.preferred_model)?.display_name || chatGPT.preferred_model || latestCodexModel.display_name;
  const modelSelect = (
    <select aria-label="Model Codex" value={currentModel} onChange={(event) => void chooseModel(event.target.value)} disabled={!selectedBot}>
      <option value="">{`Otomatis · ${automaticModel}`}</option>
      {selectableModels.map((model) => <option key={model.id} value={`chatgpt/${model.id}`}>{model.display_name}</option>)}
    </select>
  );

  return (
    <main className={`bandros-app ${mobilePane === "chat" ? "is-chat" : "is-list"} ${selectedGroup && !membersOpen ? "is-members-closed" : ""}`} data-theme={theme} data-motion={motionEnabled ? "on" : "off"}>
      <aside className="bandros-sidebar">
        <div className="bandros-brand">
          <button className="bandros-toolbar-button" type="button" aria-label="Cari" title="Cari" onClick={() => composerRef.current?.focus()}><svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="10.5" cy="10.5" r="6.5" /><path d="m16 16 4 4" /></svg></button>
          <div className="bandros-toolbar-actions">
            <button className="bandros-toolbar-button" type="button" aria-label={theme === "dark" ? "Mode terang" : "Mode gelap"} title={theme === "dark" ? "Mode terang" : "Mode gelap"} onClick={() => setTheme((current) => { const next = current === "dark" ? "light" : "dark"; window.localStorage.setItem("bandros_theme", next); return next; })}><svg viewBox="0 0 24 24" aria-hidden="true">{theme === "dark" ? <><circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" /></> : <path d="M20 14a8 8 0 0 1-10-10 8 8 0 1 0 10 10Z" />}</svg></button>
            <CreateMenu disabled={!chatGPT.connected} onCreateBot={() => void createBot()} onCreateGroup={() => void createGroup()} />
          </div>
        </div>
        <div className="bandros-agent-list">
          {loading && <p className="bandros-muted">Memuat Bot…</p>}
          {activeBots.map((bot) => <div className={`bandros-agent ${selectedBot?.id === bot.id && !selectedGroup ? "is-selected" : ""}`} key={bot.id}><button type="button" className="bandros-agent-open" onClick={() => openBot(bot)}><BandrosAvatar name={bot.name} working={typingPeople.includes(bot.name) || (working && selectedBot?.id === bot.id && !selectedGroup)} /><span className="bandros-agent-copy"><strong>{bot.name}</strong><small>{bot.description || "Belum ada peran"}</small></span></button><button className="bandros-agent-more" type="button" aria-label={`Pengaturan ${bot.name}`} title={`Pengaturan ${bot.name}`} onClick={() => openBotSettings(bot)}>⋯</button></div>)}
          {!loading && chatGPT.connected && bots.length === 0 && <p className="bandros-muted">Belum ada Bot. Buat Bot pertama.</p>}
          {authReady && !chatGPT.connected && <p className="bandros-muted">Masuk dengan ChatGPT untuk membuka Bot kamu.</p>}
        </div>
        <div className="bandros-group-list">
          <div className="bandros-groups-heading"><span>Groups</span></div>
          {groups.map((group) => <button className={`bandros-group ${selectedGroup?.id === group.id ? "is-selected" : ""}`} key={group.id} onClick={() => openGroup(group)}><span className="bandros-group-mark"><BandrosAvatar name={group.name} working={selectedGroup?.id === group.id && typingPeople.length > 0} /><em>{group.members.length}</em></span><span className="bandros-agent-copy"><strong>{group.name}</strong><small>{group.members.slice(0, 3).map((member) => member.name).join(", ") || "Grup kosong"}</small></span></button>)}
        </div>
        <button className="bandros-account" type="button" onClick={() => chatGPT.connected ? void disconnectChatGPT() : void connectChatGPT()}><BandrosAvatar name="Marketplace" /><span><strong>Marketplace</strong><small>{chatGPT.connected ? chatGPT.email || "ChatGPT terhubung" : "Sign in with ChatGPT"}</small></span></button>
      </aside>
      <section className="bandros-main">
        <header className="bandros-topbar">
          <button className="bandros-back" type="button" onClick={() => { setMobilePane("list"); setSettingsOpen(false); }} aria-label="Kembali ke daftar Bot">‹</button>
          {chatGPT.connected && (selectedBot || selectedGroup) && <button className="bandros-title" type="button" onClick={openSettings} disabled={!selectedBot}>
            <span className={`bandros-status-dot ${working || botWorking || typingNames.length > 0 ? "is-live" : ""}`} />
            <span><strong>{displayName}</strong><small>{typingPeople.length > 0 ? `${typingPeople.join(", ")} mengetik…` : selectedGroup ? selectedGroup.members.map((member) => member.name).join(", ") : selectedBot?.description || "Klik untuk mengatur peran Bot"}</small></span>
          </button>}
          {selectedBot && !selectedGroup && <button type="button" className="bandros-panel-toggle" aria-label="Pengaturan bot" title="Pengaturan bot" onClick={openSettings}>⋯</button>}
          {selectedGroup && <button type="button" className="bandros-panel-toggle" aria-label={membersOpen ? "Tutup anggota" : "Buka anggota"} title={membersOpen ? "Tutup anggota" : "Buka anggota"} aria-expanded={membersOpen} onClick={() => setMembersOpen((open) => !open)}>{membersOpen ? "»" : "«"}</button>}
          {selectedGroup && membersOpen && <MemberPicker members={availableMembers} onChoose={(botId) => void addGroupMember(botId)} />}
        </header>
        {error && <div className="bandros-alert" role="alert">{error}<button aria-label="Tutup notifikasi" onClick={() => setError(null)}>×</button></div>}
        <div className="bandros-chat" ref={chatRef} onScroll={() => {
          const node = chatRef.current;
          if (!node) return;
          stickToBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 48;
        }}>
          {!authReady ? <p className="bandros-muted">Memuat akun…</p> : !chatGPT.connected ? <section className="bandros-welcome"><h1>Masuk dengan ChatGPT</h1><p>Bot, grup, dan berkas terikat ke akun ChatGPT kamu. Akun lain tidak bisa melihatnya.</p><button className="bandros-signin" type="button" onClick={() => void connectChatGPT()}>Sign in with ChatGPT</button></section> : !selectedGroup && messages.length === 0 ? <section className="bandros-welcome"><h1>What can I take off your plate?</h1><p>{selectedBot?.name.toLowerCase() === "bandros" ? "Bandros adalah orkestrator. Minta dia membuat Bot spesialis; dia yang menulis tugas, cara kerja, output, dan batasannya." : `Kirim satu tugas yang selesai jelas. ${displayName} mengerjakannya di komputer akunmu, menyimpan berkas, dan hanya kembali saat butuh persetujuanmu.`}</p><div className="bandros-quick-prompts">{quickPrompts.map((item) => <button key={item} onClick={() => setPrompt(item)}>{item}</button>)}</div></section> : selectedGroup ? groupMessages.map((message) => <article className={`bandros-message ${message.sender_type === "user" ? "from-user" : "from-bot"}`} key={message.id}>{(() => { const sender = message.sender_type === "user" ? "Kamu" : selectedGroup.members.find((member) => member.id === message.sender_bot_id)?.name || "Bot"; const mine = message.sender_type === "user"; return <>{!mine && <BandrosAvatar name={sender} working={typingPeople.includes(sender)} />}<div className="bandros-message-body"><button type="button" style={{ color: mine ? undefined : nameColor(sender) }} onClick={() => { if (!mine) insertMention(sender); }}>{sender}</button>{mine ? <p className="bandros-bubble">{message.content}<time>{chatTime(message.created_at)}</time></p> : <div className="bandros-bubble">{renderMarkdown(message.content, selectedGroup.members.map((member) => member.name))}<time>{chatTime(message.created_at)}</time></div>}</div></>; })()}</article>) : messages.map((message) => <article className={`bandros-message ${message.role === "user" ? "from-user" : "from-bot"}`} key={message.id}>{message.role !== "user" && <BandrosAvatar name={displayName} working={typingPeople.includes(displayName)} />}<div className="bandros-message-body"><span>{message.role === "user" ? "Kamu" : displayName}</span>{message.role === "user" ? <p className="bandros-bubble">{message.content}<time>{chatTime(message.created_at)}</time></p> : <div className="bandros-bubble">{renderMarkdown(message.content)}<time>{chatTime(message.created_at)}</time></div>}</div></article>)}
          {typingPeople.map((name) => <article className="bandros-message from-bot bandros-typing" key={`typing-${name}`} aria-label={`${name} sedang mengetik`}><BandrosAvatar name={name} working /><div className="bandros-message-body"><button type="button" style={{ color: nameColor(name) }} onClick={() => insertMention(name)}>{name}</button><div className="bandros-bubble bandros-typing-bubble"><span className="bandros-dot" /><span className="bandros-dot" /><span className="bandros-dot" /></div></div></article>)}
        </div>
        <span className="bandros-sr-only" role="status" aria-live="polite" aria-atomic="true">{typingPeople.length > 0 ? `${typingPeople.join(", ")} sedang mengetik` : ""}</span>
        {mentionSuggestions.length > 0 && <div className="bandros-mentions" role="listbox" aria-label="Saran mention">{mentionSuggestions.map((member) => <button type="button" key={member.id} onMouseDown={(event) => event.preventDefault()} onClick={() => insertMention(member.name)}>@{member.name}</button>)}</div>}
        {approvals.length > 0 && !selectedGroup && <div className="bandros-approval">{approvals.map((approval) => <div key={approval.id}><p>Perlu persetujuan: {approval.reason}</p><button type="button" onClick={() => void request(`/approvals/${approval.id}/approve`, { method: "POST" }).then(() => setApprovals((current) => current.filter((item) => item.id !== approval.id)))}>Setujui</button><button type="button" onClick={() => void request(`/approvals/${approval.id}/reject`, { method: "POST" }).then(() => setApprovals((current) => current.filter((item) => item.id !== approval.id)))}>Tolak</button></div>)}</div>}
        {chatGPT.connected && <form className="bandros-composer" onSubmit={sendMessage}>
          <textarea ref={composerRef} value={prompt} onChange={(event) => { setPrompt(event.target.value); syncMention(event.target.value, event.target.selectionStart); }} onClick={(event) => syncMention(event.currentTarget.value, event.currentTarget.selectionStart)} onKeyUp={(event) => syncMention(event.currentTarget.value, event.currentTarget.selectionStart)} onKeyDown={(event) => { if (event.key === "Escape") setMentionQuery(null); if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); if (mentionQuery !== null && mentionSuggestions[0]) insertMention(mentionSuggestions[0].name); else event.currentTarget.form?.requestSubmit(); } }} placeholder={selectedGroup ? `Message ${displayName}` : `Message ${displayName}`} rows={1} aria-label={`Message ${displayName}`} />
          <div className="bandros-composer-actions">
            {(working || botWorking || typingNames.length > 0) && <button type="button" className="bandros-stop" onClick={() => void stopRun()} aria-label="Stop run">Stop</button>}
            <button type="submit" disabled={!prompt.trim() || working} aria-label="Send message">↑</button>
          </div>
        </form>}
        {chatGPT.connected && selectedBot && !selectedGroup && <div className="bandros-composer-footer"><span>Model</span>{modelSelect}</div>}
      </section>
      <aside className="bandros-computer" aria-label={selectedGroup ? "Members" : "Computer"}>
        {selectedGroup ? <>
          <div className="bandros-computer-head"><strong>Members</strong><button type="button" aria-label="Tutup panel anggota" onClick={() => setMembersOpen(false)}>×</button></div>
          {selectedGroup.members.map((member) => <button className="bandros-member" type="button" key={member.id} onClick={() => insertMention(member.name)}><BandrosAvatar name={member.name} working={typingPeople.includes(member.name)} />{member.name}</button>)}
          <p>Routines are recurring tasks this Bot runs on a schedule. Ask it in chat to set one up.</p>
        </> : <>
          <div className={`bandros-screen${screenTicket ? " has-view" : ""}`}>{screenTicket && desktopUrl(screenTicket) ? <iframe title="Layar komputer kecil" src={desktopUrl(screenTicket) ?? undefined} /> : computerState === "on" ? "Menyambungkan…" : "Idle"}</div>
          <p>{displayName}&apos;s screen</p>
          {computerState === "on" && <button className="bandros-computer-toggle" type="button" onClick={() => setScreenOpen(true)}>Buka layar</button>}
          {computerState === "on" && <p>Semua Bot memakai desktop yang sama. Matikan setelah selesai.</p>}
          <button className="bandros-computer-toggle" type="button" disabled={computerBusy || computerState === "unavailable"} onClick={() => void toggleComputer()}>{computerBusy ? "Memulai…" : computerState === "on" ? "Matikan komputer" : "Nyalakan komputer"}</button>
          <div className="bandros-computer-head"><strong>Routines</strong></div>
          <p>Routines are recurring tasks this Bot runs on a schedule. Ask it in chat to set one up.</p>
        </>}
      </aside>
      {settingsOpen && selectedBot && <aside className="bandros-settings" aria-label="Bot settings">
        <div className="bandros-settings-header"><div><span className="bandros-settings-eyebrow">Bot settings</span><h2>{selectedBot.name}</h2></div><button aria-label="Close settings" onClick={() => setSettingsOpen(false)}>×</button></div>
        <label className="bandros-motion-setting"><input type="checkbox" checked={motionEnabled} onChange={(event) => { setMotionEnabled(event.target.checked); window.localStorage.setItem("bandros_motion", event.target.checked ? "on" : "off"); }} />Animasi aktivitas</label>
        <form onSubmit={saveSettings}>
          <label htmlFor="agent-name">Name</label>
          <input id="agent-name" value={editName} onChange={(event) => setEditName(event.target.value)} required />
          <label htmlFor="agent-description">Job</label>
          <textarea id="agent-description" value={editDescription} onChange={(event) => setEditDescription(event.target.value)} rows={3} />
          <label htmlFor="agent-instructions">How this Bot should work</label>
          <textarea id="agent-instructions" value={editInstructions} onChange={(event) => setEditInstructions(event.target.value)} placeholder="Batas persetujuan, gaya kerja, dan hal yang tidak boleh dilakukan." rows={5} />
          <label htmlFor="agent-model">Model</label>
          {chatGPT.connected ? <select id="agent-model" value={editModel} onChange={(event) => { setEditModel(event.target.value); void chooseModel(event.target.value); }}>
            <option value="">{`Otomatis · ${automaticModel}`}</option>
            {selectableModels.map((model) => <option key={model.id} value={`chatgpt/${model.id}`}>{model.display_name}</option>)}
          </select> : <button type="button" onClick={() => void connectChatGPT()}>Sign in with ChatGPT</button>}
          {chatGPT.connected && chatGPTModels.length === 0 && <button type="button" onClick={() => void loadChatGPT().catch((cause) => setError(cause instanceof Error ? cause.message : "Model tidak dapat dimuat."))}>Muat ulang model</button>}
          <button type="submit">Simpan</button>
        </form>
        <button className="bandros-danger-button" type="button" onClick={() => void deleteBot(selectedBot)}>Hapus bot</button>
      </aside>}
      {screenOpen && screenTicket && desktopUrl(screenTicket) && <div className="bandros-screen-backdrop" onClick={() => setScreenOpen(false)}>
        <div className="bandros-screen-float" role="dialog" aria-label="Layar komputer" onClick={(event) => event.stopPropagation()}>
          <div className="bandros-screen-float-bar">
            <strong>Bandros&apos;s screen</strong>
            <button type="button" aria-label="Tutup layar" onClick={() => setScreenOpen(false)}>×</button>
          </div>
          <iframe title="Layar komputer" src={desktopUrl(screenTicket) ?? undefined} />
        </div>
      </div>}
      {deviceFlow && <div className="bandros-device-backdrop" role="dialog" aria-modal="true" aria-label="Sign in with ChatGPT">
        <section className="bandros-device-card">
          <button className="bandros-device-close" aria-label="Tutup device login" onClick={() => setDeviceFlow(null)}>×</button>
          <span className="bandros-settings-eyebrow">ChatGPT</span>
          <h2>Masukkan kode ini</h2>
          <p>Buka halaman device ChatGPT, lalu kembali ke sini. Bandros akan terhubung sendiri.</p>
          <code>{deviceFlow.user_code}</code>
          <a href={deviceFlow.verification_url} target="_blank" rel="noreferrer">Buka ChatGPT</a>
          <p className="bandros-muted">{deviceStatus}</p>
        </section>
      </div>}
    </main>
  );
}
