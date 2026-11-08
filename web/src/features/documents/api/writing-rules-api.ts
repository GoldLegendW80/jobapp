/**
 * Keep a correction of the document's chat as a writing rule (SPEC-documents.md, "The chat, and
 * keeping a rule"): the person's click on Keep is the acceptance, and the rule joins the block
 * "Writing rules" of their profile's facts. `kept` is false when the block held it already.
 */
import { z } from "zod";

import { DOCUMENTS_ROOT, pathId, sendJson } from "./documents-request";

export const keptRuleSchema = z.object({ rule: z.string(), kept: z.boolean() });
export type KeptRule = z.infer<typeof keptRuleSchema>;

/** The api's writing_rules.RULE_MAX. */
export const RULE_MAX = 300;

export const keepRule = (
  documentId: string,
  messageId: number,
  text: string,
): Promise<KeptRule> =>
  sendJson(
    `${DOCUMENTS_ROOT}/${pathId(documentId)}/messages/${pathId(messageId)}/rule`,
    "POST",
    keptRuleSchema,
    { text },
  );
