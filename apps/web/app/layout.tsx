import type { Metadata } from "next";
import "./styles.css";
import "./grok.css";
import "./bandros.css";

export const metadata: Metadata = { title: "Agent Workspace", description: "Persistent AI teammate workspace" };

export default function Layout({ children }: Readonly<{ children: React.ReactNode }>) {
  return <html lang="id"><body>{children}</body></html>;
}
