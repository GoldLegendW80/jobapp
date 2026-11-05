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

export default tseslint.config(
  { ignores: ["dist", "coverage", "playwright-report", "test-results"] },
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
    files: ["src/**/*.test.{ts,tsx}", "src/test/**", "e2e/**", "*.config.ts"],
    languageOptions: { globals: { ...globals.node } },
    rules: {
      "react-refresh/only-export-components": "off",
      "@typescript-eslint/no-non-null-assertion": "off",
    },
  },
  {
    files: ["**/*.js"],
    extends: [js.configs.recommended],
    languageOptions: { globals: globals.node },
  },
);
