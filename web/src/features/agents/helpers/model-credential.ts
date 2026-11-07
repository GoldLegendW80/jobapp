/** The kinds of model credential a person can save, and what the Model access card says. */
import type { CredentialKind } from "../api/agents-api";

export const KIND_LABEL: Record<CredentialKind, string> = {
  claude_subscription: "Claude subscription token",
  anthropic_api_key: "Anthropic API key",
};

export const isKind = (value: string): value is CredentialKind =>
  value in KIND_LABEL;

export const SAVED = "Credential saved. Verify it to check it works.";
export const REMOVED = "Credential removed.";
export const EMPTY_SECRET = "Paste the token or key.";
