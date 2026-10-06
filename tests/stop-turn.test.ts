import assert from "node:assert/strict";
import test from "node:test";
import { entityId, isTerminalRunStatus, planStop, recordsWithId, replyHasFinished } from "../apps/web/app/stop-turn.ts";

test("stop after the reply finished is a no-op even if a run id is still around", () => {
  assert.deepEqual(planStop({ replyFinished: true, groupId: null, runId: "run-1", turnBusy: true }), { kind: "noop" });
  assert.deepEqual(planStop({ replyFinished: true, groupId: "group-1", runId: "run-1", turnBusy: true }), { kind: "noop" });
});

test("stop during a live turn still cancels that run or group", () => {
  assert.deepEqual(planStop({ replyFinished: false, groupId: null, runId: "run-1", turnBusy: true }), { kind: "run", runId: "run-1" });
  assert.deepEqual(planStop({ replyFinished: false, groupId: "group-1", runId: null, turnBusy: true }), { kind: "group", groupId: "group-1" });
});

test("stop before a run id exists only aborts the local request", () => {
  assert.deepEqual(planStop({ replyFinished: false, groupId: null, runId: null, turnBusy: true }), { kind: "abort" });
  assert.deepEqual(planStop({ replyFinished: false, groupId: null, runId: null, turnBusy: false }), { kind: "noop" });
});

test("a null run does not expose an id", () => {
  assert.equal(entityId(null), null);
  assert.equal(entityId(undefined), null);
  assert.equal(entityId({ id: null }), null);
  assert.equal(entityId({ id: "run-1" }), "run-1");
  assert.deepEqual(recordsWithId([{ id: "a" }, null, { id: null }, { name: "x" }]), [{ id: "a" }]);
  assert.deepEqual(recordsWithId(null), []);
});

test("a finished assistant event marks the reply done", () => {
  assert.equal(replyHasFinished("assistant.delta", { payload: { content: "Siap.", final: true } }), true);
  assert.equal(replyHasFinished("assistant.delta", { payload: { content: "sebagian" } }), false);
  assert.equal(replyHasFinished("run.completed", { id: 4, payload: {} }), true);
  assert.equal(replyHasFinished("tool.started", { payload: { tool: "get_time" } }), false);
  assert.equal(isTerminalRunStatus("completed"), true);
  assert.equal(isTerminalRunStatus("running"), false);
});
