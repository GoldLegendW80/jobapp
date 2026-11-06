/**
 * The request helpers every file of the documents api shares: a typed read, a typed write and a
 * delete, each with the module's timeout, and the encoding of an id into a path.
 */
import type { z } from "zod";

import { request } from "@/lib/api";

export const DOCUMENTS_ROOT = "/documents";
export const DOCUMENTS_TIMEOUT_MS = 30_000;

/** The api path of a document or a posting: an id is encoded, never trusted into a path. */
export const pathId = (value: string | number) =>
  encodeURIComponent(String(value));

export function getJson<T>(
  path: string,
  schema: z.ZodType<T>,
  signal?: AbortSignal,
): Promise<T> {
  return request(path, {
    signal,
    timeoutMs: DOCUMENTS_TIMEOUT_MS,
    validate: (body) => schema.parse(body),
  });
}

export function sendJson<T>(
  path: string,
  method: "POST" | "PUT" | "PATCH",
  schema: z.ZodType<T>,
  body?: unknown,
  headers?: Record<string, string>,
): Promise<T> {
  return request(path, {
    method,
    body,
    headers,
    timeoutMs: DOCUMENTS_TIMEOUT_MS,
    validate: (answer) => schema.parse(answer),
  });
}

export function removeAt(path: string): Promise<void> {
  return request(path, { method: "DELETE", timeoutMs: DOCUMENTS_TIMEOUT_MS });
}
