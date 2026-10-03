/** Idle composer rules, such as an empty draft, must not block a stop message mid-turn. */
export function sendControlDisabled(turnActive: boolean, draft: string): boolean {
  if (turnActive) return false;
  return draft.trim().length === 0;
}

export function messageDraft(draft: string): string | null {
  const text = draft.trim();
  return text.length > 0 ? text : null;
}
