/**
 * The call of GET /api/ingestion/prompt (api/app/ingestion/prompt_router.py): the laptop-analysis
 * prompt filled for the signed in user, once for Claude Code and once for Codex. The shape
 * mirrors the router's response model.
 */
import { z } from "zod";

import { request } from "@/lib/api";

export const promptSchema = z.object({
  /** PROMPT_VERSION of the template the two texts were filled from. */
  version: z.number(),
  sha256: z.string(),
  /** The folder the prompt has the dossier written in. */
  output_folder: z.string().min(1),
  claude_code: z.string().min(1),
  codex: z.string().min(1),
});
export type Prompt = z.infer<typeof promptSchema>;

export const promptKey = ["ingestion", "prompt"] as const;

export function getPrompt(signal?: AbortSignal): Promise<Prompt> {
  return request("/api/ingestion/prompt", {
    signal,
    timeoutMs: 15_000,
    validate: (body) => promptSchema.parse(body),
  });
}
