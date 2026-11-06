import { setupServer } from "msw/node";

import { handlers } from "./handlers";

// The default handlers live in handlers.ts, so Storybook can serve the same ones in the browser.
export * from "./handlers";
export const server = setupServer(...handlers);
