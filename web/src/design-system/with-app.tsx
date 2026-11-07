import { QueryClientProvider } from "@tanstack/react-query";
import type { Decorator } from "@storybook/react-vite";
import { useState, type ReactNode } from "react";
import { MemoryRouter, Route, Routes } from "react-router";

import { createQueryClient } from "@/lib/query";

/**
 * What a shared component needs around it in a story: a fresh query client, whose requests the
 * MSW fakes answer (add a story's own with `server.use` in its `beforeEach`), and a router at
 * `parameters.at`, matched against `parameters.route` when the component reads route params.
 */
export const withApp: Decorator = (Story, { parameters }) => (
  <AppProviders
    at={parameters.at as string | undefined}
    route={parameters.route as string | undefined}
  >
    <Story />
  </AppProviders>
);

function AppProviders({
  at = "/",
  route = "*",
  children,
}: {
  at?: string;
  route?: string;
  children: ReactNode;
}) {
  const [client] = useState(createQueryClient);
  return (
    <QueryClientProvider client={client}>
      <MemoryRouter initialEntries={[at]}>
        <Routes>
          <Route path={route} element={children} />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
}
