export type StopPlan =
  | { kind: "noop" }
  | { kind: "abort" }
  | { kind: "group"; groupId: string }
  | { kind: "run"; runId: string };

/** A missing run or message must not be read as `.id`. */
export function entityId(value: unknown): string | null {
  if (value == null || typeof value !== "object") return null;
  const id = (value as { id?: unknown }).id;
  return typeof id === "string" && id.length > 0 ? id : null;
}

export function recordsWithId<T extends { id?: string | null }>(value: unknown): T[] {
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is T => entityId(item) != null);
}

export function isAbortError(cause: unknown): boolean {
  if (cause == null || typeof cause !== "object") return false;
  return (cause as { name?: unknown }).name === "AbortError";
}

/**
 * Stop after the assistant reply is already done does nothing.
 * A busy turn that has no id yet only aborts the local request.
 */
export function planStop(input: {
  replyFinished: boolean;
  groupId: string | null;
  runId: string | null;
  turnBusy: boolean;
}): StopPlan {
  if (input.replyFinished) return { kind: "noop" };
  if (input.groupId) return { kind: "group", groupId: input.groupId };
  if (input.runId) return { kind: "run", runId: input.runId };
  if (input.turnBusy) return { kind: "abort" };
  return { kind: "noop" };
}

export function isTerminalRunStatus(status: string | null | undefined): boolean {
  return status === "completed" || status === "failed" || status === "failed_retryable" || status === "cancelled";
}

/** Final assistant text or a terminal run event means the reply is already done. */
export function replyHasFinished(eventName: string | undefined, data: unknown): boolean {
  if (eventName === "run.completed" || eventName === "run.failed" || eventName === "run.cancelled") return true;
  if (data == null || typeof data !== "object") return false;
  const payload = (data as { payload?: unknown }).payload;
  if (payload != null && typeof payload === "object" && (payload as { final?: unknown }).final === true) {
    const content = (payload as { content?: unknown }).content;
    if (typeof content === "string") return true;
  }
  const status = (data as { status?: unknown }).status;
  return typeof status === "string" && isTerminalRunStatus(status);
}
