export type TypingActivity = { name: string; status: string };

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

/** Every member name, in roster order, so the subtitle matches the count badge. */
export function groupMemberSubtitle(members: readonly { name: string }[]): string {
  const names = members.map((member) => member.name.trim()).filter((name) => name.length > 0);
  return names.length > 0 ? names.join(", ") : "Grup kosong";
}

/** Anyone still in the turn, including people waiting to speak. Drives Stop, not the bubble. */
export function turnSignalNames(activity: readonly TypingActivity[]): string[] {
  return uniqueNames(activity.map((item) => item.name.trim()).filter((name) => name.length > 0));
}

/** Typing bubbles only for members who are composing. A queued name has nothing to show. */
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
