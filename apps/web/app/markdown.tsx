import { Fragment, ReactNode } from "react";

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function renderInline(text: string, names: string[], keyBase: string): ReactNode[] {
  const mention = names.filter(Boolean).sort((left, right) => right.length - left.length);
  const mentionPattern = mention.length ? mention.map((name) => `@${escapeRegExp(name)}`).join("|") : "";
  const pattern = new RegExp(
    `(${["`[^`\\n]+`", "\\*\\*[^*\\n]+\\*\\*", "\\*[^*\\n]+\\*", "\\[[^\\]\\n]+\\]\\(https?:\\/\\/[^\\s)]+\\)", mentionPattern].filter(Boolean).join("|")})`,
    "gi",
  );
  return text.split(pattern).filter((part) => part !== "").map((part, index) => {
    const key = `${keyBase}-${index}`;
    if (part.startsWith("`") && part.endsWith("`") && part.length > 2) return <code key={key}>{part.slice(1, -1)}</code>;
    if (part.startsWith("**") && part.endsWith("**") && part.length > 4) return <strong key={key}>{part.slice(2, -2)}</strong>;
    if (part.startsWith("*") && part.endsWith("*") && part.length > 2) return <em key={key}>{part.slice(1, -1)}</em>;
    const link = /^\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)$/.exec(part);
    if (link) return <a key={key} href={link[2]} target="_blank" rel="noreferrer">{link[1]}</a>;
    if (mention.some((name) => part.toLowerCase() === `@${name.toLowerCase()}`)) return <mark className="bandros-mention" key={key}>{part}</mark>;
    return <Fragment key={key}>{part}</Fragment>;
  });
}

export function renderMarkdown(source: string, names: string[] = []): ReactNode {
  const lines = source.replace(/\r\n/g, "\n").split("\n");
  const blocks: ReactNode[] = [];
  let index = 0;
  let key = 0;
  const isList = (line: string) => /^\s*[-*]\s+/.test(line);
  const isOrdered = (line: string) => /^\s*\d+[.)]\s+/.test(line);
  const isHeading = (line: string) => /^(#{1,3})\s+\S/.test(line);
  const isFence = (line: string) => line.trim().startsWith("```");

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
      blocks.push(<pre key={key}><code>{code.join("\n")}</code></pre>);
      key += 1;
      continue;
    }
    const heading = /^(#{1,3})\s+(.+)$/.exec(line);
    if (heading) {
      const level = heading[1].length;
      const content = renderInline(heading[2], names, `h${key}`);
      blocks.push(level === 1 ? <h3 key={key}>{content}</h3> : level === 2 ? <h4 key={key}>{content}</h4> : <h5 key={key}>{content}</h5>);
      key += 1;
      index += 1;
      continue;
    }
    if (isList(line) || isOrdered(line)) {
      const ordered = isOrdered(line);
      const items: ReactNode[] = [];
      while (index < lines.length && (ordered ? isOrdered(lines[index]) : isList(lines[index]))) {
        const item = lines[index].replace(ordered ? /^\s*\d+[.)]\s+/ : /^\s*[-*]\s+/, "");
        items.push(<li key={items.length}>{renderInline(item, names, `li${key}-${items.length}`)}</li>);
        index += 1;
      }
      blocks.push(ordered ? <ol key={key}>{items}</ol> : <ul key={key}>{items}</ul>);
      key += 1;
      continue;
    }
    const paragraph: string[] = [];
    while (index < lines.length && lines[index].trim() && !isFence(lines[index]) && !isHeading(lines[index]) && !isList(lines[index]) && !isOrdered(lines[index])) {
      paragraph.push(lines[index].trim());
      index += 1;
    }
    blocks.push(<p key={key}>{renderInline(paragraph.join(" "), names, `p${key}`)}</p>);
    key += 1;
  }

  return <div className="bandros-rich">{blocks}</div>;
}
