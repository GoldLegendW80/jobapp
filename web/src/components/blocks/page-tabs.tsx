import type { ReactNode } from "react";
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
 * Applications; Admin: Users and Agents). shadcn Tabs, so arrows move between them and Enter
 * opens one; the page's content is the open tab's panel.
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
      <TabsList aria-label={label}>
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
