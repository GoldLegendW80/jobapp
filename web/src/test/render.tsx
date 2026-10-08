import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";

import { createQueryClient } from "@/lib/query";
import { routes } from "@/routes";

/** Render with a fresh QueryClient using the app's own defaults, so no cache leaks between tests. */
export function renderWithQuery(ui: ReactElement) {
  const client = createQueryClient();
  return {
    client,
    ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>),
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
