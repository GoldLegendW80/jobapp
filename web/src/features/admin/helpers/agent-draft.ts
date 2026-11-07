import type { Editable } from "../api/agents-api";

/** The draft's tools with one switched on or off, in the code's order. */
export function withTool(
  codeTools: string[],
  current: Editable,
  tool: string,
  on: boolean,
): Pick<Editable, "tools"> {
  return {
    tools: codeTools.filter((name) =>
      name === tool ? on : current.tools.includes(name),
    ),
  };
}

/** The draft's model for one credential kind; "" drops it, so that kind uses the default model. */
export function withKindModel(
  current: Editable,
  kind: string,
  model: string,
): Pick<Editable, "model_by_credential"> {
  const next = Object.fromEntries(
    Object.entries(current.model_by_credential).filter(([key]) => key !== kind),
  );
  if (model) next[kind] = model;
  return { model_by_credential: next };
}

/** A number field's text as a number; an empty field reads as NaN, which the checks reject. */
export const numberOrNaN = (text: string) =>
  text === "" ? Number.NaN : Number(text);
