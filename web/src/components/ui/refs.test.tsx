import { render } from "@testing-library/react";
import { createRef } from "react";
import { describe, expect, it } from "vitest";

import { Button } from "./button";
import { Input } from "./input";

// On React 18 a function component drops `ref` unless it uses forwardRef. Radix triggers need the
// button's node to place their menu (without it the menu renders off screen), and
// react-hook-form needs the input's node to focus the first invalid field.
describe("ui components forward their ref to the DOM node", () => {
  it("Button", () => {
    const ref = createRef<HTMLButtonElement>();
    render(<Button ref={ref}>Go</Button>);
    expect(ref.current).toBeInstanceOf(HTMLButtonElement);
  });

  it("Button asChild", () => {
    const ref = createRef<HTMLButtonElement>();
    render(
      <Button asChild ref={ref}>
        <a href="/x">Go</a>
      </Button>,
    );
    expect(ref.current).toBeInstanceOf(HTMLAnchorElement);
  });

  it("Input", () => {
    const ref = createRef<HTMLInputElement>();
    render(<Input ref={ref} />);
    expect(ref.current).toBeInstanceOf(HTMLInputElement);
  });
});
