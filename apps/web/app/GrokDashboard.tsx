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
const quickPrompts = [
  "Analisis performa semua Bot",
  "Buat Bot baru untuk riset LinkedIn",
  "Alokasikan batas token tiap Bot",
  "Tunjukkan pekerjaan yang sedang tertunda",
];

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (!apiBase) throw new Error("NEXT_PUBLIC_API_BASE_URL belum diatur.");
  const session = typeof window !== "undefined" ? window.localStorage.getItem("bandros_chatgpt_session") : null;
  const response = await fetch(`${apiBase}${path}`, {
    ...init,
    headers: {
      "Content-Type": "application/json",
      ...(session ? { "X-Bandros-Session": session } : {}),
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
  const [modelByBot, setModelByBot] = useState<Record<string, string>>({});
  const [deviceFlow, setDeviceFlow] = useState<DeviceFlow | null>(null);
  const [deviceStatus, setDeviceStatus] = useState("");
  const [mobilePane, setMobilePane] = useState<"list" | "chat">("list");
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
    void loadChatGPT().catch((cause) => {
      setError(cause instanceof Error ? cause.message : "Model ChatGPT tidak dapat dimuat.");
    });
  }, []);

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
      const model = modelByBot[botId] ?? selectedBot?.model ?? null;
      const run = await request<Run>(`/bots/${botId}/messages`, { method: "POST", body: JSON.stringify({ content, model }) });
      setActiveRunId(run.id);
      if (["completed", "failed", "failed_retryable", "cancelled"].includes(run.status)) {
        if (run.error) throw new Error(run.error);
        setMessages(await request<Message[]>(`/bots/${botId}/messages`));
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

  const openSettings = () => {
    if (!selectedBot) return;
    setEditName(selectedBot.name);
    setEditDescription(selectedBot.description);
    setEditInstructions(selectedBot.instructions || "");
    setEditModel(modelByBot[selectedBot.id] ?? selectedBot.model ?? "");
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
      setChatGPT({ connected: false, available: true });
      setChatGPTModels([]);
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

  const displayName = selectedGroup?.name || selectedBot?.name || "New Bot";
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
        <div className="bandros-brand"><strong>Bandros</strong><button type="button" onClick={() => void createBot()}>New</button></div>
        <div className="bandros-agent-list">
          {loading && <p className="bandros-muted">Memuat Bot…</p>}
          {activeBots.map((bot) => <button className={`bandros-agent ${selectedBot?.id === bot.id && !selectedGroup ? "is-selected" : ""}`} key={bot.id} onClick={() => openBot(bot)}><span className={`bandros-avatar ${working && selectedBot?.id === bot.id ? "is-live" : ""}`}>{bot.name.slice(0, 1).toUpperCase()}</span><span className="bandros-agent-copy"><strong>{bot.name}</strong><small>{bot.description || "Belum ada peran"}</small></span></button>)}
          {!loading && bots.length === 0 && <p className="bandros-muted">Belum ada Bot. Buat Bot pertama.</p>}
        </div>
        <div className="bandros-groups-heading"><span>Groups</span><button aria-label="Buat grup" onClick={() => void createGroup()}>New</button></div>
        <div className="bandros-group-list">
          {groups.map((group) => <button className={`bandros-group ${selectedGroup?.id === group.id ? "is-selected" : ""}`} key={group.id} onClick={() => openGroup(group)}><span><strong>{group.name}</strong><small>{group.members.length} Bots</small></span></button>)}
        </div>
        <button className="bandros-account" type="button" onClick={() => chatGPT.connected ? void disconnectChatGPT() : void connectChatGPT()}>{chatGPT.connected ? chatGPT.email || "ChatGPT terhubung" : "Sign in with ChatGPT"}<small>{chatGPT.connected ? "Putuskan" : "Pakai paket ChatGPT"}</small></button>
      </aside>
      <section className="bandros-main">
        <header className="bandros-topbar">
          <button className="bandros-back" type="button" onClick={() => { setMobilePane("list"); setSettingsOpen(false); }} aria-label="Kembali ke daftar Bot">Bots</button>
          <button className="bandros-title" type="button" onClick={openSettings} disabled={!selectedBot}>
            <span className={`bandros-status-dot ${working ? "is-live" : ""}`} />
            <span><strong>{displayName}</strong><small>{selectedBot?.description || "Klik untuk mengatur peran Bot"}</small></span>
          </button>
        </header>
        {error && <div className="bandros-alert" role="alert">{error}<button aria-label="Tutup notifikasi" onClick={() => setError(null)}>×</button></div>}
        <div className="bandros-chat" ref={chatRef}>
          {!selectedGroup && messages.length === 0 ? <section className="bandros-welcome"><h1>What can I take off your plate?</h1><p>Kirim satu tugas yang selesai jelas. {displayName} mengerjakannya di komputer bersama, menyimpan berkas, dan hanya kembali saat butuh persetujuanmu.</p><div className="bandros-quick-prompts">{quickPrompts.map((item) => <button key={item} onClick={() => setPrompt(item)}>{item}</button>)}</div></section> : selectedGroup ? groupMessages.map((message) => <article className={`bandros-message ${message.sender_type === "user" ? "from-user" : "from-bot"}`} key={message.id}><span>{message.sender_type === "user" ? "Kamu" : selectedGroup.members.find((member) => member.id === message.sender_bot_id)?.name || "Bot"}</span><p>{message.content}</p></article>) : messages.map((message) => <article className={`bandros-message ${message.role === "user" ? "from-user" : "from-bot"}`} key={message.id}><span>{message.role === "user" ? "Kamu" : displayName}</span><p>{message.content}</p></article>)}
          {working && <p className="bandros-working">Sedang bekerja…</p>}
        </div>
        <form className="bandros-composer" onSubmit={sendMessage}>
          <textarea value={prompt} onChange={(event) => setPrompt(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder={`Message ${displayName}`} rows={1} aria-label={`Message ${displayName}`} />
          <div className="bandros-composer-footer">
            <label className="bandros-model-picker">Model{chatGPT.connected ? modelSelect : <button type="button" onClick={() => void connectChatGPT()}>Sign in</button>}</label>
            {working ? <button type="button" className="bandros-stop" onClick={() => void stopRun()} aria-label="Stop run">Stop</button> : <button type="submit" disabled={!prompt.trim()} aria-label="Send message">Send</button>}
          </div>
        </form>
      </section>
      <aside className="bandros-computer" aria-label="Computer">
        <div className="bandros-computer-head"><span className={`bandros-status-dot ${working ? "is-live" : ""}`} /><strong>Computer</strong></div>
        <p>{working ? `${displayName} sedang memakai layar ini.` : "Komputer bersama sedang diam. Semua Bot memakai berkas dan sesi yang sama."}</p>
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
          <button type="submit">Save</button>
        </form>
        <button className="bandros-danger-button" type="button" onClick={() => void archiveSelectedBot()}>Hide Bot</button>
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
