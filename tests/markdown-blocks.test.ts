import assert from "node:assert/strict";
import test from "node:test";
import { parseMarkdown } from "../apps/web/app/markdown-blocks.ts";

test("ordered items stay one list and number in sequence across blank lines", () => {
  const blocks = parseMarkdown("1. Pertama\n\n2. Kedua\n\n3. Ketiga\n");
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].type, "list");
  if (blocks[0].type !== "list") return;
  assert.equal(blocks[0].ordered, true);
  assert.equal(blocks[0].items.length, 3);
  assert.deepEqual(blocks[0].items.map((item) => item.map((part) => part.type === "text" ? part.text : "").join("")), ["Pertama", "Kedua", "Ketiga"]);
});

test("repeated 1. markers still form one sequential list", () => {
  const blocks = parseMarkdown("1. Satu\n1. Dua\n1. Tiga");
  assert.equal(blocks.length, 1);
  if (blocks[0].type !== "list") return;
  assert.equal(blocks[0].items.length, 3);
});

test("a markdown table is a table instead of raw pipe text", () => {
  const blocks = parseMarkdown("| Nama | Nilai |\n| --- | --- |\n| A | 1 |\n| B | 2 |\n");
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].type, "table");
  if (blocks[0].type !== "table") return;
  assert.equal(blocks[0].headers.length, 2);
  assert.equal(blocks[0].rows.length, 2);
  assert.equal(blocks[0].headers[0][0].type === "text" ? blocks[0].headers[0][0].text : "", "Nama");
  assert.equal(blocks[0].rows[1][0][0].type === "text" ? blocks[0].rows[1][0][0].text : "", "B");
});

test("a paragraph that mentions a pipe is not a table", () => {
  const blocks = parseMarkdown("Gunakan a | b bila perlu.");
  assert.equal(blocks.length, 1);
  assert.equal(blocks[0].type, "paragraph");
});
