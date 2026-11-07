import { ESLint, Linter } from "eslint";
import tseslint from "typescript-eslint";
import { beforeAll, describe, expect, it } from "vitest";

// The guard rails of eslint.config.js (SHADCN_ONLY), as the configuration gives them to a page
// and to a shared component: each hand-built version of an installed component is refused, and
// what stays hand built for a reason passes.
const FILES = [
  "src/features/jobs/pages/probe.tsx",
  "src/components/shared/jobs/probe.tsx",
];
let rails: Linter.RuleEntry = "off";

beforeAll(async () => {
  const eslint = new ESLint();
  const [page, shared] = (await Promise.all(
    FILES.map((file) => eslint.calculateConfigForFile(file)),
  )) as Linter.Config[];
  const ofPage = page?.rules?.["no-restricted-syntax"];
  const ofShared = shared?.rules?.["no-restricted-syntax"];
  expect(ofShared).toEqual(ofPage);
  rails = ofPage ?? "off";
}, 30_000);

const linter = new Linter({ configType: "flat" });
const problems = (code: string) =>
  linter
    .verify(
      code,
      [
        {
          files: ["**/*.tsx"],
          languageOptions: {
            parser: tseslint.parser,
            parserOptions: { ecmaFeatures: { jsx: true } },
          },
          rules: { "no-restricted-syntax": rails },
        },
      ],
      "probe.tsx",
    )
    .map((problem) => problem.message);

describe("SHADCN_ONLY refuses a hand-built version of an installed component", () => {
  it.each([
    ["a date input", `<Input type="date" />`, /Calendar in a Popover/],
    ["a week input", `<Input type="week" />`, /Calendar in a Popover/],
    [
      "a Button pressed by hand, in a loop",
      `days.map((day) => (
        <Button key={day} aria-pressed={on(day)} variant={on(day) ? "default" : "outline"}>{day}</Button>
      ))`,
      /ToggleGroup/,
    ],
    [
      "a Button pressed by hand, alone",
      `<Button aria-pressed={shown} variant={shown ? "secondary" : "outline"}>Shown</Button>`,
      /Toggle/,
    ],
    [
      "a list kept at its bottom by hand",
      `function follow(node) { node.scrollTop = node.scrollHeight; }`,
      /MessageScroller/,
    ],
    [
      "the stick to bottom hook",
      `import { useStickToBottom } from "@/features/agents/hooks/use-stick-to-bottom";`,
      /MessageScroller/,
    ],
    ["a hand-made log", `<div role="log">{lines}</div>`, /MessageScroller/],
    [
      "a bubble with a tail",
      `<div className="rounded-2xl rounded-br-sm bg-primary px-3 py-2">{text}</div>`,
      /Bubble/,
    ],
    [
      "a bottom sheet",
      `<SheetContent side="bottom">{children}</SheetContent>`,
      /Drawer/,
    ],
    [
      "a button drawn over an input",
      `<div className="relative"><Input className="pr-11" /><Button className="absolute top-0 right-0" /></div>`,
      /InputGroup/,
    ],
    [
      "an icon drawn over a field",
      `<Input className="pl-9" placeholder="Search" />`,
      /InputGroup/,
    ],
    [
      "a bar measured in percent",
      `<span style={{ width: \`\${share * 100}%\` }} />`,
      /Progress/,
    ],
    [
      "a column measured in percent",
      `<span style={{ height: \`\${height}%\` }} />`,
      /Progress/,
    ],
  ])("%s", (_name, code, message) => {
    const found = problems(code);
    expect(found).toHaveLength(1);
    expect(found[0]).toMatch(message);
  });
});

describe("SHADCN_ONLY lets through what stays hand built for a reason", () => {
  it.each([
    ["a time input", `<Input type="time" />`],
    [
      "a pressed Button whose look stays (a pin on the map)",
      `pins.map((pin) => <Button key={pin} variant="ghost" aria-pressed={pin === chosen} />)`,
    ],
    ["a Toggle", `<Toggle pressed={pinned} onPressedChange={pin} />`],
    ["a list scrolled back to its top", `node.scrollTop = 0;`],
    ["a side sheet", `<SheetContent side="right">{children}</SheetContent>`],
    ["an input with its own padding", `<Input className="px-3 font-mono" />`],
    [
      "a position in percent",
      `<Button style={{ left: \`\${x * 100}%\`, width: \`\${size}px\` }} />`,
    ],
    ["rounded corners", `<div className="rounded-b-lg rounded-tl-sm" />`],
  ])("%s", (_name, code) => {
    expect(problems(code)).toEqual([]);
  });
});
