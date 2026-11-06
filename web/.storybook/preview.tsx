import { withThemeByDataAttribute } from "@storybook/addon-themes";
import type { Preview } from "@storybook/react-vite";

import { Toaster } from "@/components/ui/sonner";
import { TooltipProvider } from "@/components/ui/tooltip";

import { server, startWorker } from "./msw";

import "../src/index.css";
import "./docs.css";

const preview: Preview = {
  parameters: {
    layout: "padded",
    controls: { expanded: true },
    backgrounds: { disable: true },
    a11y: { test: "error" },
    options: {
      storySort: {
        order: [
          "Introduction",
          "Foundations",
          [
            "Color",
            "Typography",
            "Spacing",
            "Shape and depth",
            "Motion",
            "Icons",
          ],
          "Components",
          "Patterns",
          "Pages",
        ],
      },
    },
  },
  loaders: [startWorker],
  // Every story starts from the default api of the tests; a story adds its own fakes after.
  beforeEach: () => {
    server.resetHandlers();
  },
  decorators: [
    // The providers the app mounts once in routes.tsx; a page story renders routes.tsx itself.
    (Story, { parameters }) =>
      parameters.page ? (
        <Story />
      ) : (
        <TooltipProvider>
          <Story />
          <Toaster position="bottom-center" />
        </TooltipProvider>
      ),
    withThemeByDataAttribute({
      themes: { System: "", Light: "light", Dark: "dark" },
      defaultTheme: "System",
      attributeName: "data-theme",
    }),
  ],
};

export default preview;
