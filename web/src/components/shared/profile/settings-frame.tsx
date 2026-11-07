import type { ReactNode } from "react";

import { Page } from "@/components/blocks/page";
import { PageHeader } from "@/components/blocks/page-header";
import { PageTabs } from "@/components/blocks/page-tabs";
import { Section } from "@/components/blocks/section";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/** The views of Settings, each at its own address (owner, 2026-11-05: "search setting should be
 * in the settings of the user"). */
const SETTINGS_TABS = [
  { to: "/settings/search", label: "Search" },
  { to: "/settings/applications", label: "Applications" },
] as const;

/**
 * Settings, Search and Applications: one header, the two views as tabs that change the address,
 * then the open view's body. `actions` are the open view's own (History on Search).
 */
export function SettingsFrame({
  actions,
  children,
}: {
  actions?: ReactNode;
  children: ReactNode;
}) {
  return (
    <Page>
      <PageHeader
        title="Settings"
        description="What the search keeps and how the app applies for you."
        actions={actions}
      />
      <PageTabs label="Settings" tabs={SETTINGS_TABS}>
        {children}
      </PageTabs>
    </Page>
  );
}

/**
 * One group of settings: its name and purpose on the left, its controls on one card on the right
 * (stacked on a phone), at most 48rem wide so fields and lines stay readable. Used by Settings
 * and by the Account page.
 */
export function SettingsSection({
  title,
  description,
  actions,
  className,
  children,
}: {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  /** The card content's own classes, after `flex max-w-3xl flex-col gap-4`. */
  className?: string;
  children: ReactNode;
}) {
  return (
    <Section
      layout="side"
      title={title}
      description={description}
      actions={actions}
    >
      <Card>
        <CardContent className={cn("flex max-w-3xl flex-col gap-4", className)}>
          {children}
        </CardContent>
      </Card>
    </Section>
  );
}
