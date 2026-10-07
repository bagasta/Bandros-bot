export type TypingActivity = { name: string; status: string; tool?: string | null };

export type RosterMember = { id?: string; name?: string | null };

export type RosterGroup<T extends RosterMember = RosterMember> = { id: string; members: readonly T[] };

export type SelectedBotLike = { id: string; name?: string | null; description?: string | null; model?: string | null; status?: string | null };

/**
 * Keep the open 1:1 bot across poll/refresh.
 * An empty or partial /bots payload (replica lag) must not clear selection to null.
 */
export function resolveSelectedBot<T extends SelectedBotLike>(
  current: T | null,
  incoming: readonly T[],
  options: {
    incomingFresh: boolean;
    quiet: boolean;
    groupSelected: boolean;
    fallback: T | null;
  },
): T | null {
  if (current) {
    const match = incoming.find((bot) => bot.id === current.id) ?? null;
    if (!match) return current;
    if (
      match.name === current.name
      && match.description === current.description
      && match.model === current.model
      && match.status === current.status
    ) {
      return current;
    }
    return match;
  }
  if (options.quiet || options.groupSelected) return null;
  if (!options.incomingFresh && incoming.length === 0) return null;
  return options.fallback;
}

/**
 * Apply a bot-list response to the sidebar.
 * A stale or empty payload must not wipe bots the UI already has.
 */
export function mergeBotDirectory<T extends { id: string }>(
  current: readonly T[],
  incoming: readonly T[],
  incomingFresh: boolean,
): T[] {
  if (!incomingFresh) return current.length > 0 ? [...current] : [...incoming];
  if (incoming.length === 0 && current.length > 0) return [...current];
  return [...incoming];
}

const liveTypingStatuses = new Set(["running", "waiting_approval"]);

function uniqueNames(names: string[]): string[] {
  const seen = new Set<string>();
  const unique: string[] = [];
  for (const name of names) {
    if (seen.has(name)) continue;
    seen.add(name);
    unique.push(name);
  }
  return unique;
}

function cleaned(value: string | null | undefined): string {
  return (value ?? "").trim();
}

/**
 * Member rows for the badge and the subtitle.
 * Names are taken from the live bot directory when a stored row is blank,
 * then from the open group when the sidebar list is missing someone.
 * A short cached label is not an input: the roster is the only source.
 */
export function presentGroupMembers(
  members: readonly RosterMember[],
  directory: readonly RosterMember[] = [],
  extras: readonly RosterMember[] = [],
): { id: string; name: string }[] {
  const known = new Map<string, string>();
  for (const bot of directory) {
    const id = cleaned(bot.id);
    const name = cleaned(bot.name);
    if (id && name) known.set(id, name);
  }
  const seen = new Set<string>();
  const roster: { id: string; name: string }[] = [];
  const add = (member: RosterMember) => {
    const id = cleaned(member.id);
    const name = cleaned(member.name) || (id ? known.get(id) ?? "" : "");
    const key = id || `name:${name}`;
    if (!name || seen.has(key)) return;
    seen.add(key);
    roster.push({ id, name });
  };
  for (const member of members) add(member);
  for (const member of extras) add(member);
  return roster;
}

/**
 * Apply a group-list response to the sidebar.
 * A fresh response replaces the list. A stale one cannot shrink a roster,
 * but it can add a group or members the sidebar has not shown yet.
 */
export function mergeGroupRosters<T extends RosterGroup>(
  current: readonly T[],
  incoming: readonly T[],
  incomingFresh: boolean,
): T[] {
  if (incomingFresh) return [...incoming];
  const incomingById = new Map(incoming.map((group) => [group.id, group]));
  const seen = new Set<string>();
  const merged: T[] = [];
  for (const group of current) {
    seen.add(group.id);
    const next = incomingById.get(group.id);
    if (next && next.members.length > group.members.length) merged.push(next);
    else merged.push(group);
  }
  for (const group of incoming) {
    if (!seen.has(group.id)) merged.push(group);
  }
  return merged;
}

/** Every member name, in roster order, so the subtitle matches the count badge. */
export function groupMemberSubtitle(
  members: readonly RosterMember[],
  directory: readonly RosterMember[] = [],
  extras: readonly RosterMember[] = [],
): string {
  const names = presentGroupMembers(members, directory, extras).map((member) => member.name);
  return names.length > 0 ? names.join(", ") : "Grup kosong";
}

/** Anyone still in the turn, including people waiting to speak. Drives Stop, not the bubble. */
export function turnSignalNames(activity: readonly TypingActivity[]): string[] {
  return uniqueNames(activity.map((item) => item.name.trim()).filter((name) => name.length > 0));
}

/** Typing bubbles only for the bot who is composing. A queued name has nothing to show. */
export function typingBubbleNames(activity: readonly TypingActivity[]): string[] {
  return uniqueNames(
    activity
      .filter((item) => liveTypingStatuses.has(item.status))
      .map((item) => item.name.trim())
      .filter((name) => name.length > 0),
  );
}

export type TurnActivityUpdate<T extends TypingActivity = TypingActivity> = {
  speaker?: string | null;
  pending: number;
  activity?: readonly T[];
};

/**
 * The turn response is the moment the composer changes. Keep that payload,
 * and otherwise hand the bubble to the next queued bot. The previous
 * speaker's name must not stay on screen until the next activity poll.
 */
export function typingActivityAfterTurn<T extends TypingActivity>(
  previous: readonly T[],
  turn: TurnActivityUpdate<T>,
): T[] {
  if (turn.pending <= 0) return [];
  const incoming = (turn.activity ?? [])
    .map((item) => ({ ...item, name: item.name.trim() }))
    .filter((item) => item.name.length > 0);
  if (incoming.length > 0) return incoming;
  return releaseFinishedTyper(previous, turn.speaker ?? null);
}

function releaseFinishedTyper<T extends TypingActivity>(activity: readonly T[], finishedName: string | null): T[] {
  const finished = (finishedName ?? "").trim();
  const kept = activity
    .map((item) => ({ ...item, name: item.name.trim() }))
    .filter((item) => item.name.length > 0 && item.name !== finished);
  if (kept.some((item) => liveTypingStatuses.has(item.status))) return kept;
  const nextIndex = kept.findIndex((item) => item.name.length > 0);
  if (nextIndex < 0) return [];
  return kept.map((item, index) => (index === nextIndex ? { ...item, status: "running" } : item));
}

export function hasVisibleBubble(content: string): boolean {
  return content.trim().length > 0;
}

/** Short label for the tool the composing bot is running. */
export function toolUseLabel(tool: string | null | undefined): string {
  const name = (tool ?? "").trim();
  if (!name) return "";
  if (name === "web_search") return "Mencari web";
  return `Menggunakan ${name}`;
}

/** Tool label for the bot who owns the typing bubble. */
export function typingToolLabel(activity: readonly TypingActivity[], name: string): string {
  const match = activity.find(
    (item) => item.name.trim() === name.trim() && liveTypingStatuses.has(item.status) && Boolean(item.tool?.trim()),
  );
  return toolUseLabel(match?.tool);
}
