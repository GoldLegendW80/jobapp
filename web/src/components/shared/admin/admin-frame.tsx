import type { ReactNode } from "react";

import { Page, PageBody } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { PageTabs } from "@/components/blocks/page-tabs";

const VIEWS = [
  { to: "/admin/users", label: "Users" },
  { to: "/admin/agents", label: "Agents" },
] as const;

/** The frame of Admin's two views: one header, then Users and Agents as tabs, each at its own address. */
export function AdminFrame({ children }: { children: ReactNode }) {
  return (
    <Page>
      <PageHeader
        title="Admin"
        description="Who can sign in, and how the app's agents run."
      />
      <PageTabs label="Admin views" tabs={VIEWS}>
        <PageBody>{children}</PageBody>
      </PageTabs>
    </Page>
  );
}
