import { QueryClientProvider } from "@tanstack/react-query";
import { render } from "@testing-library/react";
import type { ReactElement } from "react";

import { createQueryClient } from "@/lib/query";

/** Render with a fresh QueryClient using the app's own defaults, so no cache leaks between tests. */
export function renderWithQuery(ui: ReactElement) {
  const client = createQueryClient();
  return {
    client,
    ...render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>),
  };
}
