import { useLocation, useNavigate } from "react-router";

import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";

/** The parts of Settings, each at its own address (owner, 2026-11-05: "search setting should be
 * in the settings of the user"). */
const SETTINGS_PARTS = [
  { path: "/settings/search", label: "Search" },
  { path: "/settings/applications", label: "Applications" },
] as const;

/** Search and Applications, joined as tabs that change the address. */
export function SettingsTabs() {
  const { pathname } = useLocation();
  const navigate = useNavigate();
  return (
    <Tabs
      value={pathname}
      onValueChange={(next) => {
        if (next !== pathname) void navigate(next);
      }}
    >
      <TabsList aria-label="Settings">
        {SETTINGS_PARTS.map((part) => (
          <TabsTrigger key={part.path} value={part.path}>
            {part.label}
          </TabsTrigger>
        ))}
      </TabsList>
    </Tabs>
  );
}
