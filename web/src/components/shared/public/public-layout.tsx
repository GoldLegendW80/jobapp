import type { ReactNode } from "react";

import { Brand } from "@/components/blocks/brand";
import { PillNav, PillNavLink } from "@/components/blocks/pill-nav";
import { useTitle } from "@/hooks/use-title";

import { useNoindex } from "@/features/public/hooks/use-noindex";

const links = [
  { to: "/", label: "Home", end: true },
  { to: "/privacy", label: "Privacy", end: false },
  { to: "/terms", label: "Terms", end: false },
  { to: "/login", label: "Sign in", end: false },
] as const;

/**
 * Frame of the public pages (home, privacy, terms): the app name, one navigation that links all
 * of them and the sign in page, one readable column. It reads no session and calls no api, so
 * the pages answer with the api down, and it asks search engines not to index them.
 */
export function PublicLayout({
  title,
  children,
}: {
  /** The tab title; empty for the home page. */
  title: string;
  children: ReactNode;
}) {
  useTitle(title);
  useNoindex();
  return (
    <div className="mx-auto flex min-h-dvh w-full max-w-2xl flex-col px-4 py-6 sm:py-10">
      <header className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
        <Brand to="/" />
        <PillNav label="Public pages">
          {links.map((link) => (
            <PillNavLink key={link.to} to={link.to} end={link.end}>
              {link.label}
            </PillNavLink>
          ))}
        </PillNav>
      </header>
      <main className="flex flex-1 flex-col gap-6 py-8 sm:py-10">
        {children}
      </main>
    </div>
  );
}

/** One titled block of a public page. */
export function Section({
  id,
  title,
  children,
}: {
  id: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-2">
      <h2 id={id} className="text-lg font-semibold tracking-tight">
        {title}
      </h2>
      {children}
    </section>
  );
}
