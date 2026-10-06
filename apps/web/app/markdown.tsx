import { Fragment, ReactNode } from "react";
import { Block, Inline, parseMarkdown } from "./markdown-blocks";

function renderInlines(inlines: Inline[], keyBase: string): ReactNode[] {
  return inlines.map((inline, index) => {
    const key = `${keyBase}-${index}`;
    switch (inline.type) {
      case "code":
        return <code key={key}>{inline.text}</code>;
      case "strong":
        return <strong key={key}>{inline.text}</strong>;
      case "em":
        return <em key={key}>{inline.text}</em>;
      case "link":
        return <a key={key} href={inline.href} target="_blank" rel="noreferrer">{inline.text}</a>;
      case "mention":
        return <mark className="bandros-mention" key={key}>{inline.text}</mark>;
      case "text":
        return <Fragment key={key}>{inline.text}</Fragment>;
      default: {
        const exhaustive: never = inline;
        return exhaustive;
      }
    }
  });
}

function renderBlock(block: Block, key: number): ReactNode {
  switch (block.type) {
    case "code":
      return <pre key={key}><code>{block.text}</code></pre>;
    case "heading": {
      const content = renderInlines(block.inlines, `h${key}`);
      switch (block.level) {
        case 1:
          return <h3 key={key}>{content}</h3>;
        case 2:
          return <h4 key={key}>{content}</h4>;
        case 3:
          return <h5 key={key}>{content}</h5>;
        default: {
          const exhaustive: never = block.level;
          return exhaustive;
        }
      }
    }
    case "list": {
      const items = block.items.map((item, itemIndex) => <li key={itemIndex}>{renderInlines(item, `li${key}-${itemIndex}`)}</li>);
      return block.ordered ? <ol key={key}>{items}</ol> : <ul key={key}>{items}</ul>;
    }
    case "table":
      return (
        <div className="bandros-table-wrap" key={key}>
          <table>
            <thead>
              <tr>{block.headers.map((cell, cellIndex) => <th key={cellIndex}>{renderInlines(cell, `th${key}-${cellIndex}`)}</th>)}</tr>
            </thead>
            <tbody>
              {block.rows.map((row, rowIndex) => (
                <tr key={rowIndex}>{row.map((cell, cellIndex) => <td key={cellIndex}>{renderInlines(cell, `td${key}-${rowIndex}-${cellIndex}`)}</td>)}</tr>
              ))}
            </tbody>
          </table>
        </div>
      );
    case "paragraph":
      return <p key={key}>{renderInlines(block.inlines, `p${key}`)}</p>;
    default: {
      const exhaustive: never = block;
      return exhaustive;
    }
  }
}

export function renderMarkdown(source: string, names: string[] = []): ReactNode {
  const blocks = parseMarkdown(source, names);
  return <div className="bandros-rich">{blocks.map((block, index) => renderBlock(block, index))}</div>;
}
