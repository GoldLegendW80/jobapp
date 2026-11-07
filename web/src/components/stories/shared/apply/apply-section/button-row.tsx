import type { Decorator } from "@storybook/react-vite";

/**
 * The row of buttons at the bottom of Your application: its card's content is the container, so
 * the buttons sit in a row once it is wide enough and stack in the side rail or on a phone, where
 * the card's content is about 320 px wide (`parameters.phone`).
 */
export const withButtonRow: Decorator = (Story, { parameters }) => (
  <div
    className={parameters.phone ? "@container max-w-80" : "@container max-w-xl"}
  >
    <div className="flex flex-col gap-2 @sm:flex-row @sm:flex-wrap">
      <Story />
    </div>
  </div>
);
