"use client";

import { useEffect } from "react";

export default function NotFound() {
  useEffect(() => {
    const path = window.location.pathname.replace(/^\/Bandros-bot/, "") || "/";
    const base = window.location.pathname.includes("/Bandros-bot") ? "/Bandros-bot/" : "/";
    window.location.replace(`${base}#${path.replace(/^\//, "")}`);
  }, []);
  return <p>Membuka percakapan…</p>;
}
