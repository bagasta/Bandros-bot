"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { renderMarkdown } from "./markdown";

type Bot = { id: string; name: string; description: string; instructions?: string; model?: string | null; status: "active" | "archived" };
type Message = { id: string; role: string; content: string };
type Run = { id: string; status: string; error: string | null };
type Group = { id: string; name: string; description: string; members: Bot[] };
type GroupMessage = { id: string; sender_type: string; sender_bot_id: string | null; content: string };
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
      const result = await request<{ models: ChatGPTModel[] }>("/auth/chatgpt/models");
      rememberModels(result.models);
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
    const tick = async () => {
      try {
        const [nextMessages, activity] = await Promise.all([
          request<Message[]>(`/bots/${botId}/messages`),
          request<{ working: boolean; error?: string | null }>(`/bots/${botId}/activity`),
        ]);
        if (!active) return;
        const visible = nextMessages.filter((message) => message.role !== "group");
        setMessages((current) => {
          if (hasOlderMessages(current, visible)) return current;
          const pending = current.filter((item) => item.id.startsWith("local-") && !visible.some((message) => message.role === item.role && message.content === item.content));
          return [...visible, ...pending];
        });
        setBotWorking(activity.working);
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
        setGroupMessages((current) => (hasOlderMessages(current, nextMessages) ? current : nextMessages));
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

  const sendMessage = async (event: FormEvent) => {
    event.preventDefault();
    if ((!selectedBot && !selectedGroup) || !prompt.trim() || working || (selectedGroup && typingNames.length > 0)) return;
    const content = prompt.trim();
    stickToBottom.current = true;
    setPrompt(""); setWorking(true); setError(null);
    try {
      if (selectedGroup) {
        const groupId = selectedGroup.id;
        setGroupMessages((current) => [...current, { id: `local-${Date.now()}`, sender_type: "user", sender_bot_id: null, content }]);
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
    <main className={`bandros-app ${mobilePane === "chat" ? "is-chat" : "is-list"}`}>
      <aside className="bandros-sidebar">
        <div className="bandros-brand"><strong>Bandros</strong><button type="button" onClick={() => void createBot()} disabled={!chatGPT.connected}>New</button></div>
        <div className="bandros-agent-list">
          {loading && <p className="bandros-muted">Memuat Bot…</p>}
          {activeBots.map((bot) => <div className={`bandros-agent ${selectedBot?.id === bot.id && !selectedGroup ? "is-selected" : ""}`} key={bot.id}><button type="button" className="bandros-agent-open" onClick={() => openBot(bot)}><span className={`bandros-avatar ${(typingPeople.includes(bot.name) || (working && selectedBot?.id === bot.id && !selectedGroup)) ? "is-live" : ""}`}>{bot.name.slice(0, 1).toUpperCase()}</span><span className="bandros-agent-copy"><strong>{bot.name}</strong><small>{bot.description || "Belum ada peran"}</small></span></button><span className="bandros-agent-actions"><button type="button" aria-label={`Edit ${bot.name}`} onClick={() => openBotSettings(bot)}>Edit</button><button type="button" className="is-danger" aria-label={`Hapus ${bot.name}`} onClick={() => void deleteBot(bot)}>Hapus</button></span></div>)}
          {!loading && chatGPT.connected && bots.length === 0 && <p className="bandros-muted">Belum ada Bot. Buat Bot pertama.</p>}
          {authReady && !chatGPT.connected && <p className="bandros-muted">Masuk dengan ChatGPT untuk membuka Bot kamu.</p>}
        </div>
        <div className="bandros-groups-heading"><span>Groups</span><button aria-label="Buat grup" onClick={() => void createGroup()} disabled={!chatGPT.connected}>New</button></div>
        <div className="bandros-group-list">
          {groups.map((group) => <button className={`bandros-group ${selectedGroup?.id === group.id ? "is-selected" : ""}`} key={group.id} onClick={() => openGroup(group)}><span><strong>{group.name}</strong><small>{group.members.length} Bots</small></span></button>)}
        </div>
        <button className="bandros-account" type="button" onClick={() => chatGPT.connected ? void disconnectChatGPT() : void connectChatGPT()}>{chatGPT.connected ? chatGPT.email || "ChatGPT terhubung" : "Sign in with ChatGPT"}<small>{chatGPT.connected ? "Putuskan" : "Pakai paket ChatGPT"}</small></button>
      </aside>
      <section className="bandros-main">
        <header className="bandros-topbar">
          <button className="bandros-back" type="button" onClick={() => { setMobilePane("list"); setSettingsOpen(false); }} aria-label="Kembali ke daftar Bot">‹</button>
          {chatGPT.connected && (selectedBot || selectedGroup) && <button className="bandros-title" type="button" onClick={openSettings} disabled={!selectedBot}>
            <span className={`bandros-status-dot ${working || botWorking || typingNames.length > 0 ? "is-live" : ""}`} />
            <span><strong>{displayName}</strong><small>{selectedGroup ? selectedGroup.members.map((member) => member.name).join(", ") : selectedBot?.description || "Klik untuk mengatur peran Bot"}</small></span>
          </button>}
          {selectedBot && !selectedGroup && <span className="bandros-topbar-actions"><button type="button" onClick={openSettings}>Edit</button><button type="button" className="is-danger" onClick={() => void deleteBot(selectedBot)}>Hapus</button></span>}
          {selectedGroup && <select className="bandros-add-member" aria-label="Tambah anggota" value="" onChange={(event) => { const botId = event.target.value; if (botId) void addGroupMember(botId); }}>
            <option value="">{availableMembers.length ? "Tambah anggota" : "Semua Bot sudah masuk"}</option>
            {availableMembers.map((bot) => <option key={bot.id} value={bot.id}>{bot.name}</option>)}
          </select>}
        </header>
        {error && <div className="bandros-alert" role="alert">{error}<button aria-label="Tutup notifikasi" onClick={() => setError(null)}>×</button></div>}
        <div className="bandros-chat" ref={chatRef} onScroll={() => {
          const node = chatRef.current;
          if (!node) return;
          stickToBottom.current = node.scrollHeight - node.scrollTop - node.clientHeight < 48;
        }}>
          {!authReady ? <p className="bandros-muted">Memuat akun…</p> : !chatGPT.connected ? <section className="bandros-welcome"><h1>Masuk dengan ChatGPT</h1><p>Bot, grup, dan berkas terikat ke akun ChatGPT kamu. Akun lain tidak bisa melihatnya.</p><button className="bandros-signin" type="button" onClick={() => void connectChatGPT()}>Sign in with ChatGPT</button></section> : !selectedGroup && messages.length === 0 ? <section className="bandros-welcome"><h1>What can I take off your plate?</h1><p>{selectedBot?.name.toLowerCase() === "bandros" ? "Bandros adalah orkestrator. Minta dia membuat Bot spesialis; dia yang menulis tugas, cara kerja, output, dan batasannya." : `Kirim satu tugas yang selesai jelas. ${displayName} mengerjakannya di komputer akunmu, menyimpan berkas, dan hanya kembali saat butuh persetujuanmu.`}</p><div className="bandros-quick-prompts">{quickPrompts.map((item) => <button key={item} onClick={() => setPrompt(item)}>{item}</button>)}</div></section> : selectedGroup ? groupMessages.map((message) => <article className={`bandros-message ${message.sender_type === "user" ? "from-user" : "from-bot"}`} key={message.id}><span>{message.sender_type === "user" ? "Kamu" : selectedGroup.members.find((member) => member.id === message.sender_bot_id)?.name || "Bot"}</span>{message.sender_type === "user" ? <p className="bandros-bubble">{message.content}</p> : <div className="bandros-bubble">{renderMarkdown(message.content, selectedGroup.members.map((member) => member.name))}</div>}</article>) : messages.map((message) => <article className={`bandros-message ${message.role === "user" ? "from-user" : "from-bot"}`} key={message.id}><span>{message.role === "user" ? "Kamu" : displayName}</span>{message.role === "user" ? <p className="bandros-bubble">{message.content}</p> : <div className="bandros-bubble">{renderMarkdown(message.content)}</div>}</article>)}
          {typingPeople.map((name) => <article className="bandros-message from-bot bandros-typing" key={`typing-${name}`} aria-label={`${name} sedang mengetik`}><span>{name}</span><div className="bandros-bubble bandros-typing-bubble"><i /><i /><i /></div></article>)}
        </div>
        {mentionSuggestions.length > 0 && <div className="bandros-mentions" role="listbox" aria-label="Saran mention">{mentionSuggestions.map((member) => <button type="button" key={member.id} onMouseDown={(event) => event.preventDefault()} onClick={() => insertMention(member.name)}>@{member.name}</button>)}</div>}
        {chatGPT.connected && <form className="bandros-composer" onSubmit={sendMessage}>
          <textarea ref={composerRef} value={prompt} onChange={(event) => { setPrompt(event.target.value); syncMention(event.target.value, event.target.selectionStart); }} onClick={(event) => syncMention(event.currentTarget.value, event.currentTarget.selectionStart)} onKeyUp={(event) => syncMention(event.currentTarget.value, event.currentTarget.selectionStart)} onKeyDown={(event) => { if (event.key === "Escape") setMentionQuery(null); if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); if (mentionQuery !== null && mentionSuggestions[0]) insertMention(mentionSuggestions[0].name); else event.currentTarget.form?.requestSubmit(); } }} placeholder={selectedGroup ? `Message ${displayName}. Ketik @ untuk menyebut Bot` : `Message ${displayName}`} rows={1} aria-label={`Message ${displayName}`} />
          <div className="bandros-composer-footer">
            <label className="bandros-model-picker">Model{chatGPT.connected ? modelSelect : <button type="button" onClick={() => void connectChatGPT()}>Sign in</button>}</label>
            {working || (selectedGroup && typingNames.length > 0) ? <button type="button" className="bandros-stop" onClick={() => void stopRun()} aria-label="Stop run">Stop</button> : <button type="submit" disabled={!prompt.trim()} aria-label="Send message">Send</button>}
          </div>
        </form>}
      </section>
      <aside className="bandros-computer" aria-label="Computer">
        <div className="bandros-computer-head"><span className={`bandros-status-dot ${working ? "is-live" : ""}`} /><strong>Computer</strong></div>
        <p>{working ? `${displayName} sedang memakai layar ini.` : "Komputer akun ini sedang diam. Bot milik akun ChatGPT lain tidak memakai berkas ini."}</p>
        <p>Hasil yang perlu disimpan taruh di workspace. Menutup halaman ini tidak menghentikan pekerjaan yang sudah berjalan di server.</p>
      </aside>
      {settingsOpen && selectedBot && <aside className="bandros-settings" aria-label="Bot settings">
        <div className="bandros-settings-header"><div><span className="bandros-settings-eyebrow">Bot settings</span><h2>{selectedBot.name}</h2></div><button aria-label="Close settings" onClick={() => setSettingsOpen(false)}>×</button></div>
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
