import { useEffect, useId, type ReactNode } from "react";
import { useLocation, useNavigate } from "react-router";

import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";

export interface PageTab {
  /** The address the tab opens, query included (List and Map keep the filters). */
  to: string;
  label: ReactNode;
  /** The path the tab is current on; `to` without its query when left out. */
  path?: string;
}

const pathOf = (tab: PageTab): string =>
  tab.path ?? tab.to.replace(/\?.*$/, "");

/**
 * The views of one area, each at its own address (Jobs: List and Map; Settings: Search and
 * Applications; Admin: Users and Agents; Profile: its five parts). shadcn Tabs, so arrows move
 * between them and Enter opens one; the page's content is the open tab's panel.
 */
export function PageTabs({
  label,
  tabs,
  children,
}: {
  /** The tab list's accessible name, "Jobs views". */
  label: string;
  tabs: readonly PageTab[];
  children: ReactNode;
}) {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  const paths = tabs.map(pathOf);
  const current = paths.includes(pathname) ? pathname : (paths[0] ?? "");
  const listId = useId();
  // On a phone the tabs scroll sideways: keep the open one in view, without moving the page.
  useEffect(() => {
    const list = document.getElementById(listId);
    const open = list?.querySelector('[data-state="active"]');
    if (!list || !open) return;
    const outer = list.getBoundingClientRect();
    const inner = open.getBoundingClientRect();
    list.scrollLeft +=
      inner.left - outer.left - (outer.width - inner.width) / 2;
  }, [listId, current]);
  return (
    <Tabs
      value={current}
      activationMode="manual"
      onValueChange={(path) => {
        const next = tabs.find((tab) => pathOf(tab) === path);
        if (next && path !== pathname) void navigate(next.to);
      }}
      className="gap-6"
    >
      <TabsList id={listId} aria-label={label}>
        {tabs.map((tab) => (
          <TabsTrigger key={pathOf(tab)} value={pathOf(tab)}>
            {tab.label}
          </TabsTrigger>
        ))}
      </TabsList>
      <TabsContent value={current} className="min-w-0">
        {children}
      </TabsContent>
    </Tabs>
  );
}
