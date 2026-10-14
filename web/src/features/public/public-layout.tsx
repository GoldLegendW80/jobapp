import type { ReactNode } from "react";
import { Link, NavLink } from "react-router";

import { APP_NAME, useTitle } from "@/lib/use-title";

import { useNoindex } from "./use-noindex";

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
      <header className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3 border-b pb-4">
        <Link to="/" className="text-lg font-semibold tracking-tight">
          {APP_NAME}
        </Link>
        <nav aria-label="Public pages">
          <ul className="flex flex-wrap gap-x-4 gap-y-1 text-sm">
            {links.map((link) => (
              <li key={link.to}>
                <NavLink
                  to={link.to}
                  end={link.end}
                  className={({ isActive }) =>
                    isActive
                      ? "font-medium underline underline-offset-4"
                      : "text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
                  }
                >
                  {link.label}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
      </header>
      <main className="flex flex-1 flex-col gap-8 py-8">{children}</main>
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
