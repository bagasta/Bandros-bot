import assert from "node:assert/strict";
import test from "node:test";
import { groupMemberSubtitle, hasVisibleBubble, mergeGroupRosters, presentGroupMembers, toolUseLabel, turnSignalNames, typingActivityAfterTurn, typingBubbleNames, typingToolLabel } from "../apps/web/app/chat-presentation.ts";

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

test("a stale group list still gains members for a closed group", () => {
  const closed = {
    id: "g1",
    members: [
      { id: "1", name: "Bandros" },
      { id: "2", name: "Tester Tiga" },
      { id: "3", name: "Tester Dua" },
    ],
  };
  const incoming = {
    id: "g1",
    members: [...closed.members, { id: "4", name: "Tester Satu" }],
  };
  const merged = mergeGroupRosters([closed], [incoming], false);
  assert.equal(merged[0].members.length, 4);
  assert.equal(groupMemberSubtitle(merged[0].members), "Bandros, Tester Tiga, Tester Dua, Tester Satu");
  assert.equal(groupMemberSubtitle(merged[0].members).split(", ").length, merged[0].members.length);
});

test("a stale shorter list does not shrink a closed group", () => {
  const full = {
    id: "g1",
    members: [
      { id: "1", name: "Bandros" },
      { id: "2", name: "Tester Satu" },
      { id: "3", name: "Tester Dua" },
      { id: "4", name: "Tester Tiga" },
    ],
  };
  const short = { id: "g1", members: full.members.slice(0, 3) };
  const merged = mergeGroupRosters([full], [short], false);
  assert.equal(merged[0].members.length, 4);
  const fresh = mergeGroupRosters([short], [full], true);
  assert.equal(fresh[0].members.length, 4);
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

test("a finished speaker does not keep the typing label on the next turn", () => {
  const previous = [
    { name: "QA-Penulis", status: "running" },
    { name: "QA-Analis", status: "queued" },
  ];
  const handed = typingActivityAfterTurn(previous, {
    speaker: "QA-Penulis",
    pending: 1,
    activity: [
      { name: "QA-Analis", status: "running" },
      { name: "QA-Reviewer", status: "queued" },
    ],
  });
  assert.deepEqual(typingBubbleNames(handed), ["QA-Analis"]);
});

test("handoff drops the finished name when the turn has no fresh activity", () => {
  const previous = [
    { name: "QA-Penulis", status: "running" },
    { name: "QA-Analis", status: "queued" },
  ];
  const handed = typingActivityAfterTurn(previous, { speaker: "QA-Penulis", pending: 1 });
  assert.deepEqual(typingBubbleNames(handed), ["QA-Analis"]);
});

test("a finished turn clears the typing label", () => {
  const previous = [{ name: "QA-Penulis", status: "running" }];
  assert.deepEqual(typingActivityAfterTurn(previous, { speaker: "QA-Penulis", pending: 0, activity: previous }), []);
});

test("approval wait is still typing for that bot alone", () => {
  const activity = [
    { name: "Tester Dua", status: "waiting_approval" },
    { name: "Bandros", status: "queued" },
  ];
  assert.deepEqual(typingBubbleNames(activity), ["Tester Dua"]);
});

test("web search shows a tool label on the composer only", () => {
  const activity = [
    { name: "Tester Tiga", status: "running", tool: "web_search" },
    { name: "Bandros", status: "queued", tool: "web_search" },
  ];
  assert.equal(toolUseLabel("web_search"), "Mencari web");
  assert.equal(toolUseLabel("fetch_url"), "Menggunakan fetch_url");
  assert.equal(toolUseLabel(""), "");
  assert.equal(typingToolLabel(activity, "Tester Tiga"), "Mencari web");
  assert.equal(typingToolLabel(activity, "Bandros"), "");
  assert.deepEqual(typingBubbleNames(activity), ["Tester Tiga"]);
});

test("blank message content is not a bubble", () => {
  assert.equal(hasVisibleBubble(""), false);
  assert.equal(hasVisibleBubble("   \n"), false);
  assert.equal(hasVisibleBubble("Siap."), true);
});
