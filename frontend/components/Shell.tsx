"use client";

import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState, type ReactNode } from "react";

const NAV = [
  { href: "/", label: "Desk" },
  { href: "/runs", label: "Runs" },
  { href: "/books", label: "Books" },
  { href: "/tracking", label: "Tracking" },
  { href: "/cash", label: "Cash" },
  { href: "/journal", label: "Journal" },
  { href: "/settings", label: "Settings" },
];

function ThemeToggle() {
  const [theme, setTheme] = useState<string | null>(null);
  useEffect(() => {
    try { setTheme(localStorage.getItem("theme")); } catch { /* storage unavailable */ }
  }, []);
  useEffect(() => {
    if (theme) document.documentElement.dataset.theme = theme;
    else delete document.documentElement.dataset.theme;
    try { if (theme) localStorage.setItem("theme", theme); else localStorage.removeItem("theme"); } catch { /* ignore */ }
  }, [theme]);
  const next = theme === null ? "dark" : theme === "dark" ? "light" : null;
  return (
    <button onClick={() => setTheme(next)} className="w-full rounded px-2 py-1 text-left text-[12px] text-muted hover:text-ink">
      Theme: {theme ?? "system"}
    </button>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const [qc] = useState(() => new QueryClient({ defaultOptions: { queries: { staleTime: 15_000, refetchOnWindowFocus: false, retry: 1 } } }));
  const path = usePathname();
  return (
    <QueryClientProvider client={qc}>
      <div className="flex min-h-screen">
        <nav className="sticky top-0 flex h-screen w-48 shrink-0 flex-col border-r border-line bg-panel px-3 py-4">
          <div className="px-2 pb-5">
            <div className="text-[11px] uppercase tracking-widest text-muted">tvagent</div>
            <div className="text-[15px] font-semibold">Fund Desk</div>
          </div>
          <ul className="flex flex-col gap-0.5">
            {NAV.map((n) => {
              const active = n.href === "/" ? path === "/" : path.startsWith(n.href);
              return (
                <li key={n.href}>
                  <Link href={n.href} className={`block rounded-md px-2 py-1.5 text-[13.5px] ${active ? "bg-accent-soft font-semibold text-accent" : "text-ink-2 hover:bg-panel-2"}`}>
                    {n.label}
                  </Link>
                </li>
              );
            })}
          </ul>
          <div className="mt-auto border-t border-line pt-3">
            <ThemeToggle />
            <div className="px-2 pt-1 text-[11px] leading-snug text-muted">Model portfolio · manual execution · no broker link</div>
          </div>
        </nav>
        <main className="min-w-0 flex-1 px-6 py-5">{children}</main>
      </div>
    </QueryClientProvider>
  );
}
