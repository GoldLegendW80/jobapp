import { fireEvent } from "@testing-library/react";

/**
 * Move a shadcn Slider (its thumb, role="slider") to `value` with the keyboard, as a person
 * does: Home and End for the ends, Page Up and Page Down for big moves (ten steps each), the
 * arrows for the rest. Fails the test when the value cannot be reached.
 */
export function setSlider(thumb: HTMLElement, value: number) {
  const read = (name: string) => Number(thumb.getAttribute(name));
  const now = () => read("aria-valuenow");
  const close = (a: number, b: number) => Math.abs(a - b) < 1e-9;
  const press = (key: string) => {
    fireEvent.keyDown(thumb, { key });
  };
  const fail = () =>
    new Error(`The slider stopped at ${String(now())}, not ${String(value)}.`);

  if (close(now(), value)) return;
  if (close(value, read("aria-valuemin"))) press("Home");
  else if (close(value, read("aria-valuemax"))) press("End");
  else {
    // One arrow press tells the step.
    const before = now();
    press(before < value ? "ArrowRight" : "ArrowLeft");
    const step = Math.abs(now() - before);
    if (step === 0) throw fail();
    while (Math.abs(value - now()) >= 10 * step - 1e-9)
      press(now() < value ? "PageUp" : "PageDown");
    for (let at = 0; at < 10 && !close(now(), value); at += 1)
      press(now() < value ? "ArrowRight" : "ArrowLeft");
  }
  if (!close(now(), value)) throw fail();
}
