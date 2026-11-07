import type { ReactNode } from "react";

import { Page, PageBody } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { PageTabs } from "@/components/blocks/page-tabs";
import { FinishSetupCard } from "@/components/shared/onboarding/onboarding-gate";

/**
 * The Jobs area around both of its views: one header (the h1 "Jobs", the view's count, the
 * area's actions), the List and Map tabs, each carrying the current filter query so switching
 * keeps them, then the open view under the setup reminder.
 */
export function JobsFrame({
  query,
  description,
  actions,
  children,
}: {
  /** The canonical filter query of the view, without `?`. */
  query: string;
  description: ReactNode;
  actions: ReactNode;
  children: ReactNode;
}) {
  const suffix = query ? `?${query}` : "";
  return (
    <Page>
      <PageHeader title="Jobs" description={description} actions={actions} />
      <PageTabs
        label="Jobs views"
        tabs={[
          { to: `/jobs${suffix}`, label: "List" },
          { to: `/jobs/map${suffix}`, label: "Map" },
        ]}
      >
        <PageBody className="gap-4">
          <FinishSetupCard />
          {children}
        </PageBody>
      </PageTabs>
    </Page>
  );
}
