import assert from "node:assert/strict";
import test from "node:test";
import { groupMemberSubtitle, hasVisibleBubble, turnSignalNames, typingBubbleNames } from "../apps/web/app/chat-presentation.ts";

const roster = [
  { name: "Bandros" },
  { name: "Tester Satu" },
  { name: "Tester Dua" },
  { name: "Tester Tiga" },
];

test("group subtitle lists every member", () => {
  assert.equal(groupMemberSubtitle(roster), "Bandros, Tester Satu, Tester Dua, Tester Tiga");
  assert.equal(groupMemberSubtitle(roster).split(", ").length, roster.length);
});

test("empty group subtitle stays explicit", () => {
  assert.equal(groupMemberSubtitle([]), "Grup kosong");
  assert.equal(groupMemberSubtitle([{ name: "  " }]), "Grup kosong");
});

test("queued members do not get a typing bubble", () => {
  const activity = roster.map((member) => ({ name: member.name, status: "queued" }));
  assert.deepEqual(typingBubbleNames(activity), []);
  assert.deepEqual(turnSignalNames(activity), roster.map((member) => member.name));
});

test("only a live composer gets a typing bubble", () => {
  const activity = [
    { name: "Bandros", status: "queued" },
    { name: "Tester Dua", status: "running" },
    { name: "  ", status: "running" },
    { name: "Tester Tiga", status: "waiting_approval" },
  ];
  assert.deepEqual(typingBubbleNames(activity), ["Tester Dua", "Tester Tiga"]);
});

test("blank message content is not a bubble", () => {
  assert.equal(hasVisibleBubble(""), false);
  assert.equal(hasVisibleBubble("   \n"), false);
  assert.equal(hasVisibleBubble("Siap."), true);
});
