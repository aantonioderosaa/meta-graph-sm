"use client";

/**
 * Shared chrome: global SSE subscriber + route nav (F3.2 / F3.4).
 */

import type { ReactNode } from "react";
import Link from "next/link";
import { usePathname } from "next/navigation";

import { PipelineEventSubscriber } from "@/components/PipelineEventSubscriber";
import { cn } from "@/lib/utils";

const LINKS = [
  { href: "/", label: "Dashboard" },
  { href: "/documents", label: "Documenti" },
] as const;

export function AppShell({ children }: { children: ReactNode }) {
  const pathname = usePathname();

  return (
    <>
      <PipelineEventSubscriber />
      <nav
        aria-label="Navigazione principale"
        className="flex items-center gap-1 border-b border-border bg-background px-4 py-1.5 text-sm"
      >
        {LINKS.map(({ href, label }) => {
          const active =
            href === "/"
              ? pathname === "/"
              : pathname === href || pathname.startsWith(`${href}/`);
          return (
            <Link
              key={href}
              href={href}
              className={cn(
                "rounded px-2.5 py-1 text-muted-foreground transition-colors hover:text-foreground",
                active && "bg-muted font-medium text-foreground",
              )}
              aria-current={active ? "page" : undefined}
            >
              {label}
            </Link>
          );
        })}
      </nav>
      {children}
    </>
  );
}
