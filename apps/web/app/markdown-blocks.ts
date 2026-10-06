export type Inline =
  | { type: "text"; text: string }
  | { type: "code"; text: string }
  | { type: "strong"; text: string }
  | { type: "em"; text: string }
  | { type: "link"; text: string; href: string }
  | { type: "mention"; text: string };

export type Block =
  | { type: "code"; text: string }
  | { type: "heading"; level: 1 | 2 | 3; inlines: Inline[] }
  | { type: "list"; ordered: boolean; items: Inline[][] }
  | { type: "table"; headers: Inline[][]; rows: Inline[][][] }
  | { type: "paragraph"; inlines: Inline[] };

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

export function parseInline(text: string, names: readonly string[] = []): Inline[] {
  const mention = names.filter(Boolean).slice().sort((left, right) => right.length - left.length);
  const mentionPattern = mention.length ? mention.map((name) => `@${escapeRegExp(name)}`).join("|") : "";
  const pattern = new RegExp(
    `(${["`[^`\\n]+`", "\\*\\*[^*\\n]+\\*\\*", "\\*[^*\\n]+\\*", "\\[[^\\]\\n]+\\]\\(https?:\\/\\/[^\\s)]+\\)", mentionPattern].filter(Boolean).join("|")})`,
    "gi",
  );
  return text.split(pattern).filter((part) => part !== "").map((part): Inline => {
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) return { type: "code", text: part.slice(1, -1) };
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) return { type: "strong", text: part.slice(2, -2) };
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) return { type: "em", text: part.slice(1, -1) };
    const link = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(part);
    if (link) return { type: "link", text: link[1], href: link[2] };
    if (mention.some((name) => part.toLowerCase() === `@${name.toLowerCase()}`)) return { type: "mention", text: part };
    return { type: "text", text: part };
  });
}

const orderedPattern = /^\s*\d+[.)]\s+(.*)$/;
const unorderedPattern = /^\s*[-*]\s+(.*)$/;
const headingPattern = /^(#{1,3})\s+(\S.*)$/;

function isFence(line: string) {
  return line.trim().startsWith("```");
}

function isHeading(line: string) {
  return headingPattern.test(line);
}

function isOrdered(line: string) {
  return orderedPattern.test(line);
}

function isUnordered(line: string) {
  return unorderedPattern.test(line);
}

function splitCells(line: string): string[] {
  const trimmed = line.trim().replace(/^\|/, "").replace(/\|$/, "");
  return trimmed.split("|").map((cell) => cell.trim());
}

function isSeparator(line: string) {
  if (!line.includes("-")) return false;
  const cells = splitCells(line);
  return cells.length > 0 && cells.every((cell) => /^:?-+:?$/.test(cell));
}

function isTableStart(lines: readonly string[], index: number) {
  const header = lines[index] ?? "";
  const divider = lines[index + 1] ?? "";
  return header.includes("|") && isSeparator(divider) && splitCells(header).length >= 1;
}

function nextContent(lines: readonly string[], index: number) {
  let cursor = index;
  while (cursor < lines.length && !lines[cursor].trim()) cursor += 1;
  return cursor;
}

function collectList(lines: readonly string[], start: number, ordered: boolean, names: readonly string[]) {
  const items: Inline[][] = [];
  let index = start;
  const pattern = ordered ? orderedPattern : unorderedPattern;
  const sameKind = ordered ? isOrdered : isUnordered;
  while (index < lines.length) {
    if (!lines[index].trim()) {
      const ahead = nextContent(lines, index);
      if (ahead < lines.length && sameKind(lines[ahead]) && !isTableStart(lines, ahead)) {
        index = ahead;
        continue;
      }
      break;
    }
    const match = pattern.exec(lines[index]);
    if (!match) break;
    items.push(parseInline(match[1], names));
    index += 1;
  }
  return { items, index };
}

function collectTable(lines: readonly string[], start: number, names: readonly string[]) {
  const headers = splitCells(lines[start]).map((cell) => parseInline(cell, names));
  const rows: Inline[][][] = [];
  let index = start + 2;
  while (index < lines.length && lines[index].trim() && lines[index].includes("|") && !isSeparator(lines[index]) && !isFence(lines[index])) {
    rows.push(splitCells(lines[index]).map((cell) => parseInline(cell, names)));
    index += 1;
  }
  return { headers, rows, index };
}

export function parseMarkdown(source: string, names: readonly string[] = []): Block[] {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    if (isFence(line)) {
      const code: string[] = [];
      index += 1;
      while (index < lines.length && !isFence(lines[index])) {
        code.push(lines[index]);
        index += 1;
      }
      if (index < lines.length) index += 1;
      blocks.push({ type: "code", text: code.join("\n") });
      continue;
    }
    const heading = headingPattern.exec(line);
    if (heading) {
      const level = heading[1].length;
      blocks.push({
        type: "heading",
        level: level === 1 ? 1 : level === 2 ? 2 : 3,
        inlines: parseInline(heading[2], names),
      });
      index += 1;
      continue;
    }
    if (isTableStart(lines, index)) {
      const table = collectTable(lines, index, names);
      blocks.push({ type: "table", headers: table.headers, rows: table.rows });
      index = table.index;
      continue;
    }
    if (isUnordered(line) || isOrdered(line)) {
      const ordered = isOrdered(line);
      const list = collectList(lines, index, ordered, names);
      blocks.push({ type: "list", ordered, items: list.items });
      index = list.index;
      continue;
    }
    const paragraph: string[] = [];
    while (
      index < lines.length
      && lines[index].trim()
      && !isFence(lines[index])
      && !isHeading(lines[index])
      && !isUnordered(lines[index])
      && !isOrdered(lines[index])
      && !isTableStart(lines, index)
    ) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push({ type: "paragraph", inlines: parseInline(paragraph.join(" "), names) });
  }
  return blocks;
}
