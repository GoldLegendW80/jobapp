import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement, ReactNode } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";

import { createQueryClient } from "@/lib/query";
import { TooltipProvider } from "@/components/ui/tooltip";
import { routes } from "@/app/routes";

/**
 * Render with a fresh QueryClient using the app's own defaults, so no cache leaks between tests.
 * The providers are the render's `wrapper`, so `rerender(ui)` keeps them and the tree under them.
 */
export function renderWithQuery(ui: ReactElement) {
  const client = createQueryClient();
  const Providers = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={client}>
      <TooltipProvider>{children}</TooltipProvider>
    </QueryClientProvider>
  );
  return {
    client,
    ...render(ui, { wrapper: Providers }),
  };
}

/** The whole app on an in-memory history starting at `path`; `router` exposes the location. */
export function renderApp(path: string) {
  const client = createQueryClient();
  const router = createMemoryRouter(routes, { initialEntries: [path] });
  const utils = render(
    <QueryClientProvider client={client}>
      <RouterProvider router={router} />
    </QueryClientProvider>,
  );
  const location = () => {
    const { pathname, search, hash } = router.state.location;
    return `${pathname}${search}${hash}`;
  };
  return { client, router, location, ...utils };
}
