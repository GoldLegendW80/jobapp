import { QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { createMemoryRouter, RouterProvider } from "react-router";

import { createQueryClient } from "@/lib/query";
import { routes } from "@/app/routes";

/**
 * The whole app on an in-memory history at `path`, as the person would see it: the real routes,
 * guards, shell and page, with the api answered by the test fakes a story registers.
 */
export function AppAt({ path }: { path: string }) {
  const [app] = useState(() => ({
    client: createQueryClient(),
    router: createMemoryRouter(routes, { initialEntries: [path] }),
  }));
  return (
    <QueryClientProvider client={app.client}>
      <RouterProvider router={app.router} />
    </QueryClientProvider>
  );
}
