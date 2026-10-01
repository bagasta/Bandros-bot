"use client";

import { FormEvent, useEffect, useMemo, useRef, useState } from "react";

type Bot = { id: string; name: string; description: string; instructions?: string; model?: string | null; status: "active" | "archived" };
type Message = { id: string; role: string; content: string };
type Run = { id: string; status: string; error: string | null };
type Group = { id: string; name: string; description: string; members: Bot[] };
type GroupMessage = { id: string; sender_type: string; sender_bot_id: string | null; content: string };
type ChatGPTStatus = {
  connected: boolean;
  available?: boolean;
  email?: string | null;
  subscription_enabled?: boolean;
  preferred_model?: string | null;
  reason?: string | null;
};
type ChatGPTModel = { id: string; display_name: string };

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL;
const quickPrompts = [
  "Analisis performa semua Bot",
  "Buat Bot baru untuk riset LinkedIn",
  "Alokasikan batas token tiap Bot",
  "Tunjukkan pekerjaan yang sedang tertunda",
];

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (!apiBase) throw new Error("NEXT_PUBLIC_API_BASE_URL belum diatur.");
  const response = await fetch(`${apiBase}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...init?.headers,
    },
  });
  if (!response.ok) {
    const body = await response.json().catch(() => null);
    throw new Error(typeof body?.detail === "string" ? body.detail : `Request gagal (${response.status}).`);
  }
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
  const chatRef = useRef<HTMLDivElement>(null);

  const activeBots = useMemo(() => bots.filter((bot) => bot.status === "active"), [bots]);

  const loadBots = async () => {
    try {
      const [nextBots, nextGroups] = await Promise.all([
        request<Bot[]>("/bots"),
        request<Group[]>("/groups"),
      ]);
      setBots((current) => (nextBots.length === 0 && current.length > 0 ? current : nextBots));
      setGroups((current) => (nextGroups.length === 0 && current.length > 0 ? current : nextGroups));
      if (!selectedBot && nextBots[0]) setSelectedBot(nextBots[0]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Bot tidak dapat dimuat.");
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => { void loadBots(); }, []);
  useEffect(() => {
    void request<ChatGPTStatus>("/auth/chatgpt/status").then(async (status) => {
      setChatGPT(status);
      if (status.connected && status.subscription_enabled) {
        const result = await request<{ models: ChatGPTModel[] }>("/auth/chatgpt/models");
        setChatGPTModels(result.models);
      }
      if (new URLSearchParams(window.location.search).has("chatgpt")) {
        window.history.replaceState({}, "", window.location.pathname);
      }
    }).catch(() => undefined);
  }, []);

  useEffect(() => {
    if (!selectedBot) { setMessages([]); return; }
    let active = true;
    const botId = selectedBot.id;
    void request<Message[]>(`/bots/${botId}/messages`)
      .then((nextMessages) => { if (active) setMessages(nextMessages); })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : "Chat tidak dapat dimuat."); });
    return () => { active = false; };
  }, [selectedBot]);

  useEffect(() => {
    if (!selectedGroup) { setGroupMessages([]); return; }
    let active = true;
    void request<GroupMessage[]>(`/groups/${selectedGroup.id}/messages`)
      .then((nextMessages) => { if (active) setGroupMessages(nextMessages); })
      .catch((cause) => { if (active) setError(cause instanceof Error ? cause.message : "Grup tidak dapat dimuat."); });
    return () => { active = false; };
  }, [selectedGroup]);

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: "smooth" });
  }, [messages, groupMessages, working]);

  const sendMessage = async (event: FormEvent) => {
    event.preventDefault();
    if ((!selectedBot && !selectedGroup) || !prompt.trim() || working) return;
    const content = prompt.trim();
    setPrompt(""); setWorking(true); setError(null);
    try {
      if (selectedGroup) {
        const groupId = selectedGroup.id;
        const expectedReplies = selectedGroup.members.filter((member) => member.status === "active").length;
        const previousCount = groupMessages.length;
        await request<GroupMessage>(`/groups/${groupId}/messages`, { method: "POST", body: JSON.stringify({ content }) });
        let receivedReplies = false;
        for (let attempt = 0; attempt < 40; attempt += 1) {
          const nextMessages = await request<GroupMessage[]>(`/groups/${groupId}/messages`);
          if (selectedGroup?.id !== groupId) return;
          setGroupMessages(nextMessages);
          receivedReplies = nextMessages.length >= previousCount + 1 + expectedReplies;
          if (receivedReplies) break;
          await new Promise((resolve) => setTimeout(resolve, 500));
        }
        if (!receivedReplies) throw new Error("Sebagian respons grup belum selesai. Coba refresh grup.");
        return;
      }
      const botId = selectedBot?.id;
      if (!botId) return;
      const run = await request<Run>(`/bots/${botId}/messages`, { method: "POST", body: JSON.stringify({ content }) });
      setActiveRunId(run.id);
      if (["completed", "failed", "failed_retryable", "cancelled"].includes(run.status)) {
        if (run.error) throw new Error(run.error);
        setMessages(await request<Message[]>(`/bots/${botId}/messages`));
        return;
      }
      const response = await fetch(`${apiBase}/runs/${run.id}/events/stream`);
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
  };

  const openSettings = () => {
    if (!selectedBot) return;
    setEditName(selectedBot.name);
    setEditDescription(selectedBot.description);
    setEditInstructions(selectedBot.instructions || "");
    setEditModel(selectedBot.model || "");
    setSettingsOpen(true);
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

  const archiveSelectedBot = async () => {
    if (!selectedBot || !window.confirm(`Archive ${selectedBot.name}? Riwayat chat tetap disimpan.`)) return;
    try {
      const archived = await request<Bot>(`/bots/${selectedBot.id}/archive`, { method: "POST" });
      setBots((current) => current.map((bot) => bot.id === archived.id ? archived : bot));
      setSelectedBot(null);
      setSettingsOpen(false);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Agent tidak dapat di-archive.");
    }
  };

  const openGroup = (group: Group) => {
    setSelectedBot(null);
    setSelectedGroup(group);
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
      void loadBots();
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Bot tidak dapat dibuat."); }
  };

  const connectChatGPT = async () => {
    if (chatGPT.available === false) {
      setError(chatGPT.reason || "Sign in with ChatGPT belum tersedia untuk deployment ini.");
      return;
    }
    try {
      const result = await request<{ authorization_url: string }>("/auth/chatgpt/start");
      window.location.assign(result.authorization_url);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "ChatGPT tidak dapat dihubungkan.");
    }
  };

  const displayName = selectedGroup?.name || selectedBot?.name || "Bandros Manager";

  return (
    <main className="bandros-app">
      <aside className="bandros-sidebar">
        <div className="bandros-brand"><div className="bandros-logo" aria-hidden="true">B</div><div><strong>Bandros AI Pro</strong><span>Bot Orchestration Hub</span></div></div>
        <div className="bandros-sidebar-tools"><button className="bandros-icon-button" aria-label="Cari Bot">⌕</button><button className="bandros-icon-button" aria-label="Buat Bot" onClick={() => void createBot()}>＋</button></div>
        <div className="bandros-agents-heading"><span>Active Agents ({activeBots.length})</span><button aria-label="Filter agents">Filter</button></div>
        <div className="bandros-agent-list">
          {loading && <p className="bandros-muted">Memuat agents…</p>}
          {activeBots.map((bot) => <button className={`bandros-agent ${selectedBot?.id === bot.id ? "is-selected" : ""}`} key={bot.id} onClick={() => openBot(bot)}><span className="bandros-avatar">{bot.name.slice(0, 1).toUpperCase()}</span><span className="bandros-agent-copy"><strong>{bot.name}</strong><small>{bot.description || "Siap menerima perintah dari kamu."}</small></span><time>Now</time></button>)}
          {!loading && bots.length === 0 && <p className="bandros-muted">Belum ada Bot. Buat Bot pertama.</p>}
        </div>
        <div className="bandros-groups-heading"><span>Groups ({groups.length})</span><button aria-label="Buat grup" onClick={() => void createGroup()}>＋</button></div>
        <div className="bandros-group-list">
          {groups.map((group) => <button className={`bandros-group ${selectedGroup?.id === group.id ? "is-selected" : ""}`} key={group.id} onClick={() => openGroup(group)}><span className="bandros-group-icon">G</span><span><strong>{group.name}</strong><small>{group.members.length} agents</small></span></button>)}
          {groups.length === 0 && <button className="bandros-create-group" onClick={() => void createGroup()}>+ Create group</button>}
        </div>
        <button className="bandros-marketplace" type="button" onClick={() => void connectChatGPT()}>{chatGPT.connected ? `ChatGPT · ${chatGPT.email || "Connected"}` : "Sign in with ChatGPT"}</button>
        <div className="bandros-user"><span className="bandros-avatar">RA</span><span><strong>Rizky A.</strong><small>24.5k tokens</small></span></div>
      </aside>
      <section className="bandros-main">
        <header className="bandros-topbar"><button className="bandros-title-pill" type="button"><span className="bandros-status-dot" />{displayName}<small>Agent</small></button><div className="bandros-window-actions"><button aria-label="Open agent settings" onClick={openSettings}>⚙</button><button aria-label="Share">↗</button><button aria-label="Minimize">−</button><button aria-label="Close">×</button></div></header>
        {error && <div className="bandros-alert" role="alert">{error}<button aria-label="Tutup notifikasi" onClick={() => setError(null)}>×</button></div>}
        <div className="bandros-chat" ref={chatRef}>
          <div className="bandros-date">Today · 10:35 AM</div>
          {!selectedGroup && messages.length === 0 ? <section className="bandros-welcome"><span className="bandros-welcome-mark">B</span><h1>What can I take off your plate?</h1><p>Think of me as a teammate with my own computer. I can dig up answers, write and send things, work in your tools once you connect them, and run recurring work in the background while you&apos;re busy.</p><div className="bandros-quick-prompts">{quickPrompts.map((item, index) => <button key={item} onClick={() => setPrompt(item)}><span>{String.fromCharCode(65 + index)}</span>{item}</button>)}</div></section> : selectedGroup ? groupMessages.map((message) => <article className={`bandros-message ${message.sender_type === "user" ? "from-user" : "from-bot"}`} key={message.id}><span>{message.sender_type === "user" ? "Kamu" : selectedGroup.members.find((member) => member.id === message.sender_bot_id)?.name || "Agent"}</span><p>{message.content}</p></article>) : messages.map((message) => <article className={`bandros-message ${message.role === "user" ? "from-user" : "from-bot"}`} key={message.id}><span>{message.role === "user" ? "Kamu" : displayName}</span><p>{message.content}</p></article>)}
        </div>
        <form className="bandros-composer" onSubmit={sendMessage}><textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder={`Message ${displayName}`} rows={1} aria-label={`Message ${displayName}`} /><div className="bandros-composer-footer"><span>Enter untuk kirim · Shift + Enter untuk baris baru</span>{working ? <button type="button" className="bandros-stop" onClick={() => void stopRun()} aria-label="Stop run">■</button> : <button type="submit" disabled={!prompt.trim()} aria-label="Send message">↑</button>}</div></form>
        <footer className="bandros-footer">Bandros v2.4 · Orchestrator Mode</footer>
      </section>
      {settingsOpen && selectedBot && <aside className="bandros-settings" aria-label="Agent settings">
        <div className="bandros-settings-header"><div><span className="bandros-settings-eyebrow">Settings</span><h2>{selectedBot.name}</h2></div><button aria-label="Close settings" onClick={() => setSettingsOpen(false)}>×</button></div>
        <form onSubmit={saveSettings}>
          <label htmlFor="agent-name">Name</label>
          <input id="agent-name" value={editName} onChange={(event) => setEditName(event.target.value)} required />
          <label htmlFor="agent-model">Model</label>
          {chatGPTModels.length > 0 ? <select id="agent-model" value={editModel} onChange={(event) => setEditModel(event.target.value)}>
            <option value="">Otomatis · {chatGPT.preferred_model || "ChatGPT"}</option>
            {chatGPTModels.map((model) => <option key={model.id} value={`chatgpt/${model.id}`}>{model.display_name}</option>)}
            <option value="openrouter/free">OpenRouter free</option>
          </select> : <input id="agent-model" value={editModel} onChange={(event) => setEditModel(event.target.value)} placeholder="chatgpt/model atau openrouter/free" />}
          <label htmlFor="agent-description">Description</label>
          <textarea id="agent-description" value={editDescription} onChange={(event) => setEditDescription(event.target.value)} rows={5} />
          <label htmlFor="agent-instructions">Working instructions</label>
          <textarea id="agent-instructions" value={editInstructions} onChange={(event) => setEditInstructions(event.target.value)} placeholder="Atur cara Agent bekerja..." rows={5} />
          <button type="submit">Save changes</button>
        </form>
        <button className="bandros-danger-button" type="button" onClick={() => void archiveSelectedBot()}>Archive Agent</button>
      </aside>}
    </main>
  );
}
