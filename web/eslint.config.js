import { readdirSync } from "node:fs";

import js from "@eslint/js";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import globals from "globals";
import tseslint from "typescript-eslint";

/** An HTML element, a role or a class that a shadcn component of src/components/ui stands for. */
const element = (names, use) => ({
  selector: `JSXOpeningElement[name.name=/^(${names})$/]`,
  message: `Use ${use} from @/components/ui instead of a hand-built one.`,
});
const SHADCN_ONLY = [
  element("button", "Button"),
  element("input", "Input, Checkbox, RadioGroup, Switch or Slider"),
  element("textarea", "Textarea"),
  element("select", "NativeSelect"),
  element("label", "Label or FieldLabel"),
  element("table|thead|tbody|tfoot|tr|th|td|caption", "Table"),
  element("details|summary", "Collapsible"),
  element("hr", "Separator"),
  element("kbd", "Kbd"),
  element("progress|meter", "Progress"),
  element("dialog", "Dialog, AlertDialog or Sheet"),
  {
    selector:
      "JSXAttribute[name.name='role'][value.value=/^(switch|checkbox|radio|radiogroup|slider|progressbar|tooltip|dialog|alertdialog|tab|tablist|tabpanel|menu|menuitem|separator)$/]",
    message:
      "This role is a shadcn component (Switch, Checkbox, RadioGroup, Slider, Progress, Tooltip, Dialog, Tabs, DropdownMenu, Separator): use it from @/components/ui.",
  },
  {
    // A title shows only to a mouse, after a wait; an iframe's title is its accessible name.
    selector:
      "JSXOpeningElement[name.name=/^(?!iframe$)[a-z]/] > JSXAttribute[name.name='title']",
    message: "Use Tooltip from @/components/ui instead of a title attribute.",
  },
  {
    selector:
      ":matches(Literal[value=/\\banimate-spin\\b/], TemplateElement[value.raw=/\\banimate-spin\\b/])",
    message: "Use Spinner from @/components/ui instead of a spinning icon.",
  },
  {
    selector:
      ":matches(Literal[value=/\\banimate-pulse\\b/], TemplateElement[value.raw=/\\banimate-pulse\\b/])",
    message: "Use Skeleton from @/components/ui for a placeholder that pulses.",
  },
];

/**
 * The layers of web/ARCHITECTURE.md, as import bans per folder. ESLint keeps one
 * no-restricted-imports per file (a later block replaces an earlier one), so each folder gets all
 * its bans in one block. Tests and stories may reach any layer to build fixtures.
 */
const FEATURES = readdirSync(new URL("./src/features", import.meta.url));
const NOT_APP = ["@/app/**", "@/design-system/**"];
const banned = (patterns) => ({
  files: patterns.files,
  ignores: ["src/**/*.test.{ts,tsx}", "src/**/*.stories.tsx"],
  rules: {
    "no-restricted-imports": [
      "error",
      {
        patterns: patterns.groups.map(([group, message]) => ({
          group,
          message,
        })),
      },
    ],
  },
});
const LAYERS = [
  banned({
    files: ["src/lib/**", "src/hooks/**"],
    groups: [
      [
        ["@/components/**", "@/features/**", ...NOT_APP],
        "lib/ and hooks/ are the bottom layer: they import no component, feature or app file.",
      ],
    ],
  }),
  banned({
    files: ["src/components/ui/**", "src/components/blocks/**"],
    groups: [
      [
        ["@/components/shared/**", "@/features/**", ...NOT_APP],
        "A generic component knows no feature: pass what it needs as props.",
      ],
    ],
  }),
  banned({
    files: [
      "src/features/*/api/**",
      "src/features/*/helpers/**",
      "src/features/*/hooks/**",
    ],
    groups: [
      [
        [
          "@/components/**",
          "@/features/*/components/**",
          "@/features/*/pages/**",
          ...NOT_APP,
        ],
        "api/, helpers/ and hooks/ hold data and logic: they render nothing, so they import no component or page.",
      ],
    ],
  }),
  banned({
    files: ["src/components/shared/**"],
    groups: [
      [
        ["@/features/*/components/**", "@/features/*/pages/**", ...NOT_APP],
        "A shared component serves several pages: it imports no page and no component of a single page.",
      ],
    ],
  }),
  ...FEATURES.flatMap((feature) => [
    banned({
      files: [`src/features/${feature}/components/**`],
      groups: [
        [
          [
            "@/features/*/components/**",
            `!@/features/${feature}/components/**`,
          ],
          "A component of one page lives in that page's feature. Used by two pages or more, it belongs in components/shared/.",
        ],
        [
          ["@/features/*/pages/**", ...NOT_APP],
          "A component imports no page and no app file.",
        ],
      ],
    }),
    banned({
      files: [`src/features/${feature}/pages/**`],
      groups: [
        [
          [
            "@/features/*/components/**",
            `!@/features/${feature}/components/**`,
          ],
          "A page composes its own feature's components and the shared ones (components/shared/).",
        ],
        [
          ["@/features/*/pages/**", ...NOT_APP],
          "A page imports no other page and no app file.",
        ],
      ],
    }),
  ]),
];

export default tseslint.config(
  {
    ignores: [
      "dist",
      "coverage",
      "playwright-report",
      "test-results",
      "storybook-static",
      ".storybook/public",
    ],
  },
  {
    files: ["**/*.{ts,tsx}"],
    extends: [js.configs.recommended, ...tseslint.configs.strictTypeChecked],
    languageOptions: {
      ecmaVersion: 2022,
      globals: globals.browser,
      parserOptions: {
        projectService: true,
        tsconfigRootDir: import.meta.dirname,
      },
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": [
        "error",
        { allowConstantExport: true },
      ],
      "@typescript-eslint/restrict-template-expressions": [
        "error",
        { allowNumber: true },
      ],
    },
  },
  {
    // shadcn components export their variants next to the component, as generated.
    files: ["src/components/ui/**/*.tsx"],
    rules: { "react-refresh/only-export-components": "off" },
  },
  {
    // Pages are compositions of shadcn components (owner, 2026-11-05): never a hand-built
    // version of one that src/components/ui has. Add the missing component there instead.
    files: ["src/**/*.tsx"],
    ignores: ["src/components/ui/**", "src/**/*.test.tsx"],
    rules: { "no-restricted-syntax": ["error", ...SHADCN_ONLY] },
  },
  {
    // Story files and their helpers export meta, story objects and helpers, not only components.
    // The route table is configuration: a change to it reloads the page.
    files: [
      "src/**/*.stories.tsx",
      "src/design-system/**",
      ".storybook/**",
      "src/app/routes.tsx",
    ],
    rules: { "react-refresh/only-export-components": "off" },
  },
  {
    files: ["src/**/*.test.{ts,tsx}", "src/test/**", "e2e/**", "*.config.ts"],
    languageOptions: { globals: { ...globals.node } },
    rules: {
      "react-refresh/only-export-components": "off",
      "@typescript-eslint/no-non-null-assertion": "off",
    },
  },
  ...LAYERS,
  {
    files: ["**/*.js"],
    extends: [js.configs.recommended],
    languageOptions: { globals: globals.node },
  },
);
