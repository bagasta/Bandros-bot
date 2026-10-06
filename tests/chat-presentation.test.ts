import assert from "node:assert/strict";
import test from "node:test";
import { groupMemberSubtitle, hasVisibleBubble, presentGroupMembers, turnSignalNames, typingBubbleNames } from "../apps/web/app/chat-presentation.ts";

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

test("a blank stored member name is filled from the live roster", () => {
  const members = [
    { id: "1", name: "Bandros" },
    { id: "2", name: "Tester Tiga" },
    { id: "3", name: "Tester Dua" },
    { id: "4", name: " " },
  ];
  const directory = [{ id: "4", name: "Tester Satu" }];
  const roster = presentGroupMembers(members, directory);
  assert.equal(roster.length, members.length);
  assert.equal(groupMemberSubtitle(members, directory), "Bandros, Tester Tiga, Tester Dua, Tester Satu");
});

test("a short sidebar list picks up the member the open group already has", () => {
  const sidebar = [
    { id: "1", name: "Bandros" },
    { id: "2", name: "Tester Tiga" },
    { id: "3", name: "Tester Dua" },
  ];
  const openGroup = [...sidebar, { id: "4", name: "Tester Satu" }];
  const storedPreview = "Bandros, Tester Tiga, Tester Dua";
  const roster = presentGroupMembers(sidebar, [], openGroup);
  assert.equal(roster.length, openGroup.length);
  assert.equal(groupMemberSubtitle(sidebar, [], openGroup), "Bandros, Tester Tiga, Tester Dua, Tester Satu");
  assert.notEqual(groupMemberSubtitle(sidebar, [], openGroup), storedPreview);
});

test("queued members do not get a typing bubble", () => {
  const activity = roster.map((member) => ({ name: member.name, status: "queued" }));
  assert.deepEqual(typingBubbleNames(activity), []);
  assert.deepEqual(turnSignalNames(activity), roster.map((member) => member.name));
});

test("only a live composer gets a typing bubble", () => {
  const activity = [
    { name: "Tester Tiga", status: "running" },
    { name: "Tester Satu", status: "queued" },
    { name: "Tester Dua", status: "queued" },
    { name: "Bandros", status: "queued" },
    { name: "  ", status: "running" },
  ];
  assert.deepEqual(typingBubbleNames(activity), ["Tester Tiga"]);
  assert.equal(typingBubbleNames(activity).some((name) => name === "Bandros" || name === "Tester Satu" || name === "Tester Dua"), false);
});

test("approval wait is still typing for that bot alone", () => {
  const activity = [
    { name: "Tester Dua", status: "waiting_approval" },
    { name: "Bandros", status: "queued" },
  ];
  assert.deepEqual(typingBubbleNames(activity), ["Tester Dua"]);
});

test("blank message content is not a bubble", () => {
  assert.equal(hasVisibleBubble(""), false);
  assert.equal(hasVisibleBubble("   \n"), false);
  assert.equal(hasVisibleBubble("Siap."), true);
});
