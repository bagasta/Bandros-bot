"use client";

import { FormEvent, useEffect, useState } from "react";
import GrokDashboard from "./GrokDashboard";

type Bot = { id: string; name: string; description: string; status: "active" | "archived" };
type Skill = { id: string; name: string; description: string };
type Job = { id: string; title: string; status: string; priority: string; assignee_bot_id: string | null };
type Message = { id: string; role: string; content: string; model?: string | null; attachments?: { name?: string; type?: string }[] };
type Run = { id: string; status: string; error: string | null };
type Group = { id: string; name: string; description: string; members: Bot[] };
type GroupMessage = { id: string; sender_type: string; sender_bot_id: string | null; content: string };

const apiBase = process.env.NEXT_PUBLIC_API_BASE_URL;
const delay = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  if (!apiBase) throw new Error("NEXT_PUBLIC_API_BASE_URL belum diatur.");
  const response = await fetch(`${apiBase}${path}`, { ...init, headers: { "Content-Type": "application/json", ...init?.headers } });
  if (response.status === 204) return undefined as T;
  if (!response.ok) { const body = await response.json().catch(() => null); throw new Error(typeof body?.detail === "string" ? body.detail : `Permintaan gagal (${response.status}).`); }
  return response.json() as Promise<T>;
}

function LegacyWorkspace() {
  const [bots, setBots] = useState<Bot[]>([]), [skills, setSkills] = useState<Skill[]>([]), [jobs, setJobs] = useState<Job[]>([]), [groups, setGroups] = useState<Group[]>([]);
  const [botSkills, setBotSkills] = useState<Record<string, Skill[]>>({}), [selectedBot, setSelectedBot] = useState<Bot | null>(null), [selectedGroup, setSelectedGroup] = useState<Group | null>(null);
  const [messages, setMessages] = useState<Message[]>([]), [groupMessages, setGroupMessages] = useState<GroupMessage[]>([]);
  const [name, setName] = useState(""), [description, setDescription] = useState(""), [skillIds, setSkillIds] = useState<string[]>([]), [prompt, setPrompt] = useState(""), [jobTitle, setJobTitle] = useState(""), [groupName, setGroupName] = useState(""), [groupMemberIds, setGroupMemberIds] = useState<string[]>([]), [groupPrompt, setGroupPrompt] = useState("");
  const [loading, setLoading] = useState(true), [working, setWorking] = useState(false), [error, setError] = useState<string | null>(null), [streamingAnswer, setStreamingAnswer] = useState(""), [activeRunId, setActiveRunId] = useState<string | null>(null), [model, setModel] = useState("");

  const loadWorkspace = async () => {
    setLoading(true);
    try {
      const [nextBots, nextSkills, nextJobs, nextGroups] = await Promise.all([request<Bot[]>("/bots"), request<Skill[]>("/skills"), request<Job[]>("/jobs"), request<Group[]>("/groups")]);
      const pairs = await Promise.all(nextBots.map(async (bot) => [bot.id, await request<Skill[]>(`/bots/${bot.id}/skills`)] as const));
      setBots(nextBots); setSkills(nextSkills); setJobs(nextJobs); setGroups(nextGroups); setBotSkills(Object.fromEntries(pairs)); setError(null);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Workspace tidak dapat dimuat."); } finally { setLoading(false); }
  };
  useEffect(() => { void loadWorkspace(); }, []);

  const openBot = async (bot: Bot) => { setSelectedGroup(null); setSelectedBot(bot); try { setMessages(await request<Message[]>(`/bots/${bot.id}/messages`)); } catch (cause) { setError(cause instanceof Error ? cause.message : "Percakapan tidak dapat dimuat."); } };
  const openGroup = async (group: Group) => { setSelectedBot(null); setSelectedGroup(group); try { setGroupMessages(await request<GroupMessage[]>(`/groups/${group.id}/messages`)); } catch (cause) { setError(cause instanceof Error ? cause.message : "Percakapan grup tidak dapat dimuat."); } };
  const toggle = (values: string[], value: string, set: (items: string[]) => void) => set(values.includes(value) ? values.filter((item) => item !== value) : [...values, value]);

  const createBot = async (event: FormEvent) => { event.preventDefault(); if (!name.trim()) return; setWorking(true); try { const bot = await request<Bot>("/bots", { method: "POST", body: JSON.stringify({ name: name.trim(), description: description.trim(), instructions: "Kerjakan tanggung jawabmu dengan jelas. Gunakan hanya skill yang diberikan. Laporkan hasil yang bisa ditinjau." }) }); await Promise.all(skillIds.map((skill_id) => request<void>(`/bots/${bot.id}/skills`, { method: "POST", body: JSON.stringify({ skill_id }) }))); setName(""); setDescription(""); setSkillIds([]); await loadWorkspace(); await openBot(bot); } catch (cause) { setError(cause instanceof Error ? cause.message : "Bot tidak dapat dibuat."); } finally { setWorking(false); } };
  const sendBotMessage = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedBot || !prompt.trim()) return;
    const content = prompt.trim();
    setPrompt(""); setWorking(true); setStreamingAnswer(""); setError(null);
    try {
      const run = await request<Run>(`/bots/${selectedBot.id}/messages`, { method: "POST", body: JSON.stringify({ content, model: model.trim() || null }) });
      setActiveRunId(run.id);
      await new Promise<void>((resolve, reject) => {
        void (async () => {
          try {
            const response = await fetch(`${apiBase}/runs/${run.id}/events/stream`, {
              headers: {},
            });
            if (!response.ok || !response.body) throw new Error("Streaming terputus.");
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
                  const content = parsed.payload?.content || "";
                  setStreamingAnswer(content);
                  setMessages((current) => [...current.filter((message) => message.id !== `streaming-${run.id}`), { id: `streaming-${run.id}`, role: "assistant", content }]);
                } else if (eventName === "run.completed") completed = parsed;
              }
            }
            setActiveRunId(null);
            if (!completed) throw new Error("Streaming berakhir sebelum Run selesai.");
            if (completed.error) throw new Error(completed.error);
            setMessages(await request<Message[]>(`/bots/${selectedBot.id}/messages`));
            setStreamingAnswer(""); await loadWorkspace(); resolve();
          } catch (cause) { reject(cause); }
        })();
      });
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Tugas tidak dapat dikirim."); }
    finally { setWorking(false); }
  };
  const stopGeneration = async () => { if (!activeRunId) return; try { await request<Run>(`/runs/${activeRunId}/cancel`, { method: "POST" }); } catch (cause) { setError(cause instanceof Error ? cause.message : "Run tidak dapat dihentikan."); } };
  const regenerate = () => {
    const lastUser = [...messages].reverse().find((message) => message.role === "user");
    if (!lastUser) return;
    setPrompt(lastUser.content);
    setTimeout(() => document.getElementById("task-prompt")?.focus(), 0);
  };
  const addSkill = async (skillId: string) => { if (!selectedBot || !skillId) return; try { await request<void>(`/bots/${selectedBot.id}/skills`, { method: "POST", body: JSON.stringify({ skill_id: skillId }) }); await loadWorkspace(); } catch (cause) { setError(cause instanceof Error ? cause.message : "Skill tidak dapat ditambahkan."); } };
  const createJob = async (event: FormEvent) => { event.preventDefault(); if (!selectedBot || !jobTitle.trim()) return; try { await request<Job>("/jobs", { method: "POST", body: JSON.stringify({ title: jobTitle.trim(), assignee_bot_id: selectedBot.id }) }); setJobTitle(""); await loadWorkspace(); } catch (cause) { setError(cause instanceof Error ? cause.message : "Job tidak dapat dibuat."); } };
  const createGroup = async (event: FormEvent) => { event.preventDefault(); if (!groupName.trim() || groupMemberIds.length === 0) return; try { const group = await request<Group>("/groups", { method: "POST", body: JSON.stringify({ name: groupName.trim(), member_bot_ids: groupMemberIds }) }); setGroupName(""); setGroupMemberIds([]); await loadWorkspace(); await openGroup(group); } catch (cause) { setError(cause instanceof Error ? cause.message : "Grup tidak dapat dibuat."); } };
  const sendGroupMessage = async (event: FormEvent) => {
    event.preventDefault();
    if (!selectedGroup || !groupPrompt.trim()) return;
    setWorking(true);
    try {
      const before = groupMessages.length;
      await request<GroupMessage>(`/groups/${selectedGroup.id}/messages`, { method: "POST", body: JSON.stringify({ content: groupPrompt.trim() }) });
      setGroupPrompt("");
      for (let attempt = 0; attempt < 40; attempt += 1) {
        const next = await request<GroupMessage[]>(`/groups/${selectedGroup.id}/messages`);
        setGroupMessages(next);
        if (next.length > before + 1) break;
        await delay(500);
      }
    } catch (cause) { setError(cause instanceof Error ? cause.message : "Pesan grup tidak dapat dikirim."); }
    finally { setWorking(false); }
  };

  const currentSkills = selectedBot ? botSkills[selectedBot.id] || [] : [];
  const currentJobs = selectedBot ? jobs.filter((job) => job.assignee_bot_id === selectedBot.id) : [];
  return <main className="shell"><aside><p className="brand">Agent Workspace</p><p className="context">Ruang kerja untuk tim AI yang punya peran dan koordinasi.</p><nav><a href="#team">Tim</a><a href="#groups">Grup kerja</a><a href="#new-bot">Tambah Bot</a></nav></aside><section className="content"><header><div><p className="kicker">Organisasi</p><h1>Kerjakan bersama, dengan peran yang jelas.</h1><p className="lede">Berikan setiap Bot skill dan job. Gunakan chat pribadi untuk tugas khusus, atau grup untuk arahan tim.</p></div><button onClick={() => document.getElementById("bot-name")?.focus()}>Tambah Bot</button></header>{error && <div role="alert" className="notice error">{error} <button onClick={() => void loadWorkspace()}>Coba lagi</button></div>}
    <section className="board" id="team"><div className="board-heading"><h2>Tim Bot</h2><span>{loading ? "Memuat" : `${bots.length} anggota`}</span></div>{loading ? <p className="notice">Memuat workspace…</p> : <ul>{bots.map((bot) => <li key={bot.id}><div><h3>{bot.name}</h3><p>{bot.description || "Belum ada peran."}</p><p className="skills-line">{(botSkills[bot.id] || []).map((skill) => skill.name).join(" · ") || "Belum ada skill"}</p></div><div className="bot-actions"><span className="status">{bot.status === "active" ? "Aktif" : "Diarsipkan"}</span><button className="secondary" onClick={() => void openBot(bot)}>Chat pribadi</button></div></li>)}</ul>}</section>
    {selectedBot && <section className="chat"><div className="chat-heading"><div><p className="kicker">Chat pribadi</p><h2>{selectedBot.name}</h2></div><button className="secondary" onClick={() => setSelectedBot(null)}>Tutup</button></div><div className="skill-manager"><p><strong>Skill aktif:</strong> {currentSkills.map((skill) => skill.name).join(", ") || "belum ada"}</p><label htmlFor="skill-select">Tambah skill</label><select id="skill-select" defaultValue="" onChange={(event) => { void addSkill(event.target.value); event.currentTarget.value = ""; }}><option value="">Pilih kemampuan</option>{skills.filter((skill) => !currentSkills.some((current) => current.id === skill.id)).map((skill) => <option key={skill.id} value={skill.id}>{skill.name} — {skill.description}</option>)}</select></div><div className="jobs"><h3>Job {selectedBot.name}</h3>{currentJobs.length ? <ul>{currentJobs.map((job) => <li key={job.id}><div><strong>{job.title}</strong><p>{job.status} · {job.priority}</p></div></li>)}</ul> : <p className="empty-chat">Belum ada job.</p>}<form className="inline-form" onSubmit={createJob}><label htmlFor="job-title">Job baru</label><input id="job-title" value={jobTitle} onChange={(event) => setJobTitle(event.target.value)} placeholder="Contoh: Ringkas kebutuhan desain" required /><button type="submit">Tambah job</button></form></div><div className="messages">{messages.length === 0 ? <p className="empty-chat">Tulis tugas atau pertanyaan untuk mulai bekerja.</p> : messages.map((message) => <article className={`message ${message.role === "user" ? "user" : "assistant"}`} key={message.id}><span>{message.role === "user" ? "Kamu" : message.role.startsWith("bot") ? "Rekan Bot" : selectedBot.name}</span><p>{message.content}</p></article>)}</div><form className="chat-form" onSubmit={sendBotMessage}><label htmlFor="task-prompt">Tugas atau pertanyaan</label><textarea id="task-prompt" value={prompt} onChange={(event) => setPrompt(event.target.value)} placeholder="Contoh: Buatkan anggota tim untuk riset dan delegasikan tugasnya." rows={3} required /><button type="submit" disabled={working}>{working ? "Sedang bekerja…" : "Kirim tugas"}</button></form></section>}
    <section className="groups" id="groups"><div className="board-heading"><h2>Grup kerja</h2><span>{groups.length} grup</span></div><div className="group-grid">{groups.map((group) => <button className="group-card" key={group.id} onClick={() => void openGroup(group)}><strong>{group.name}</strong><span>{group.members.map((member) => member.name).join(", ")}</span></button>)}</div><form className="group-form" onSubmit={createGroup}><label htmlFor="group-name">Grup baru</label><input id="group-name" value={groupName} onChange={(event) => setGroupName(event.target.value)} placeholder="Contoh: Tim peluncuran" required /><div className="checks">{bots.filter((bot) => bot.status === "active").map((bot) => <label key={bot.id}><input type="checkbox" checked={groupMemberIds.includes(bot.id)} onChange={() => toggle(groupMemberIds, bot.id, setGroupMemberIds)} /> {bot.name}</label>)}</div><button type="submit">Buat grup</button></form></section>
    {selectedGroup && <section className="chat"><div className="chat-heading"><div><p className="kicker">Grup kerja</p><h2>{selectedGroup.name}</h2></div><button className="secondary" onClick={() => setSelectedGroup(null)}>Tutup</button></div><div className="messages">{groupMessages.map((message) => <article className={`message ${message.sender_type === "user" ? "user" : "assistant"}`} key={message.id}><span>{message.sender_type === "user" ? "Kamu" : selectedGroup.members.find((member) => member.id === message.sender_bot_id)?.name || "Bot"}</span><p>{message.content}</p></article>)}</div><form className="chat-form" onSubmit={sendGroupMessage}><label htmlFor="group-prompt">Arahan untuk grup</label><textarea id="group-prompt" value={groupPrompt} onChange={(event) => setGroupPrompt(event.target.value)} placeholder="Contoh: Masing-masing kirim update pekerjaan dan hambatannya." rows={3} required /><button type="submit" disabled={working}>{working ? "Mengirim…" : "Kirim ke grup"}</button></form></section>}
    <section className="composer" id="new-bot"><div><p className="kicker">Anggota baru</p><h2>Tambahkan Bot dengan kemampuan yang tepat.</h2></div><form onSubmit={createBot}><label htmlFor="bot-name">Nama Bot</label><input id="bot-name" value={name} onChange={(event) => setName(event.target.value)} placeholder="Contoh: Lead riset" required /><label htmlFor="bot-description">Peran utama</label><textarea id="bot-description" value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Contoh: Meneliti pasar dan menyusun ringkasan keputusan." rows={3} /><fieldset><legend>Skill yang diizinkan</legend>{skills.map((skill) => <label key={skill.id}><input type="checkbox" checked={skillIds.includes(skill.id)} onChange={() => toggle(skillIds, skill.id, setSkillIds)} /> {skill.name}</label>)}</fieldset><button type="submit" disabled={working}>{working ? "Menyimpan…" : "Simpan Bot"}</button></form></section></section></main>;
}

export default function Workspace() {
  return <GrokDashboard />;
}
