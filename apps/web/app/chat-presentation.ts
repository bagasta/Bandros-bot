export type TypingActivity = { name: string; status: string };

export type RosterMember = { id?: string; name?: string | null };

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

export function hasVisibleBubble(content: string): boolean {
  return content.trim().length > 0;
}
