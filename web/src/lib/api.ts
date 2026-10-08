/**
 * Typed fetch client for the backend. Every call goes to `/api<path>`, which Vite proxies in
 * development and Caddy proxies in production, so the browser never needs the api origin.
 *
 * Failures are always an `ApiError` whose `kind` says what went wrong:
 * - `http`: the server answered with a non 2xx status (`status` and the decoded `body` are set)
 * - `network`: no response at all (server down, DNS, CORS, connection reset)
 * - `timeout`: no complete response within `timeoutMs`
 * - `aborted`: the caller's own signal was aborted (for example TanStack Query cancelling)
 * - `parse`: a 2xx response whose body is not JSON or fails the caller's validator
 *
 * Every request sends the session cookie (`credentials: "include"`, the api is same origin
 * anyway), and every non-GET request carries `X-Requested-With: jobapp`, the CSRF header the api
 * demands (SPEC-identity.md). A 401 answer is reported to the `onUnauthorized` listeners (the
 * auth provider signs the browser out locally and the protected routes send it to /login),
 * unless the caller opts out with `authRedirect: false`: sign in itself, and the "who am I" call.
 */

export const API_BASE = "/api";
export const DEFAULT_TIMEOUT_MS = 5000;
export const CSRF_HEADER = "X-Requested-With";
export const CSRF_VALUE = "jobapp";

type UnauthorizedListener = (error: ApiError) => void;
const unauthorizedListeners = new Set<UnauthorizedListener>();

/** Be told about every 401 answer (except the opted out calls). Returns the unsubscribe. */
export function onUnauthorized(listener: UnauthorizedListener): () => void {
  unauthorizedListeners.add(listener);
  return () => {
    unauthorizedListeners.delete(listener);
  };
}

export type ApiErrorKind = "http" | "network" | "timeout" | "aborted" | "parse";

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | undefined;
  readonly body: unknown;

  constructor(
    kind: ApiErrorKind,
    message: string,
    options: { status?: number; body?: unknown; cause?: unknown } = {},
  ) {
    super(message, { cause: options.cause });
    this.name = "ApiError";
    this.kind = kind;
    this.status = options.status;
    this.body = options.body;
  }
}

export function isApiError(value: unknown): value is ApiError {
  return value instanceof ApiError;
}

export type Validator<T> = (data: unknown) => T;

export interface RequestOptions<T> {
  method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  /** Serialised as JSON when set. */
  body?: unknown;
  headers?: Record<string, string>;
  /** Caller's cancellation, combined with the timeout. */
  signal?: AbortSignal;
  timeoutMs?: number;
  /** Runtime check of the decoded body; throw to reject it as a `parse` error. */
  validate?: Validator<T>;
  /** false: a 401 is this call's own answer, not a lost session (default true). */
  authRedirect?: boolean;
}

function decode(text: string): { ok: true; value: unknown } | { ok: false } {
  if (text.trim() === "") return { ok: true, value: undefined };
  try {
    return { ok: true, value: JSON.parse(text) as unknown };
  } catch {
    return { ok: false };
  }
}

/**
 * Read the body, rejecting as soon as `signal` aborts. Browsers already fail `text()` on abort,
 * but not every fetch implementation does once the headers have arrived, so this does not rely on it.
 */
function readText(response: Response, signal: AbortSignal): Promise<string> {
  return new Promise<string>((resolve, reject) => {
    const onAbort = () => {
      reject(new DOMException("body read aborted", "AbortError"));
      response.body?.cancel().catch(() => {});
    };
    if (signal.aborted) {
      onAbort();
      return;
    }
    signal.addEventListener("abort", onAbort, { once: true });
    response.text().then(
      (text) => {
        signal.removeEventListener("abort", onAbort);
        resolve(text);
      },
      (error: unknown) => {
        signal.removeEventListener("abort", onAbort);
        reject(error instanceof Error ? error : new Error(String(error)));
      },
    );
  });
}

export async function request<T>(
  path: string,
  options: RequestOptions<T> = {},
): Promise<T> {
  if (!path.startsWith("/")) {
    throw new TypeError(`api path must start with "/": ${path}`);
  }
  const {
    method = "GET",
    body,
    headers = {},
    signal,
    validate,
    authRedirect = true,
  } = options;
  const timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;

  if (signal?.aborted) {
    throw new ApiError("aborted", "request cancelled", {
      cause: signal.reason,
    });
  }

  const controller = new AbortController();
  const timeoutReason = new DOMException("request timed out", "TimeoutError");
  const timer = setTimeout(() => {
    controller.abort(timeoutReason);
  }, timeoutMs);
  const onCallerAbort = () => {
    controller.abort(signal?.reason);
  };
  signal?.addEventListener("abort", onCallerAbort, { once: true });

  const url = `${API_BASE}${path}`;
  try {
    let response: Response;
    let text: string;
    try {
      response = await fetch(url, {
        method,
        headers: {
          Accept: "application/json",
          ...(body === undefined ? {} : { "Content-Type": "application/json" }),
          ...(method === "GET" ? {} : { [CSRF_HEADER]: CSRF_VALUE }),
          ...headers,
        },
        body: body === undefined ? undefined : JSON.stringify(body),
        credentials: "include",
        signal: controller.signal,
      });
      // The timeout also covers a body that starts and then stalls.
      text = await readText(response, controller.signal);
    } catch (error) {
      if (controller.signal.reason === timeoutReason) {
        throw new ApiError(
          "timeout",
          `no answer from ${url} within ${timeoutMs} ms`,
          {
            cause: error,
          },
        );
      }
      if (signal?.aborted) {
        throw new ApiError("aborted", "request cancelled", { cause: error });
      }
      throw new ApiError("network", `could not reach ${url}`, { cause: error });
    }

    const decoded = decode(text);
    if (!response.ok) {
      const error = new ApiError(
        "http",
        `${method} ${url} answered ${response.status}`,
        {
          status: response.status,
          body: decoded.ok ? decoded.value : text,
        },
      );
      if (response.status === 401 && authRedirect) {
        for (const listener of [...unauthorizedListeners]) listener(error);
      }
      throw error;
    }
    if (!decoded.ok) {
      throw new ApiError(
        "parse",
        `${method} ${url} returned a body that is not JSON`,
        {
          status: response.status,
          body: text,
        },
      );
    }
    if (!validate) return decoded.value as T;
    try {
      return validate(decoded.value);
    } catch (error) {
      throw new ApiError(
        "parse",
        `${method} ${url} returned an unexpected body`,
        {
          status: response.status,
          body: decoded.value,
          cause: error,
        },
      );
    }
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onCallerAbort);
  }
}

export interface Health {
  status: "ok";
}

export function validateHealth(data: unknown): Health {
  if (
    typeof data === "object" &&
    data !== null &&
    (data as { status?: unknown }).status === "ok"
  ) {
    return { status: "ok" };
  }
  throw new TypeError('expected {"status": "ok"}');
}

export function getHealth(
  options: Pick<RequestOptions<Health>, "signal" | "timeoutMs"> = {},
): Promise<Health> {
  return request("/health", { ...options, validate: validateHealth });
}

/** The api's `{"detail": "..."}` message of an http error, when it is a non empty string. */
export function errorDetail(error: unknown): string | undefined {
  if (!isApiError(error) || error.kind !== "http") return undefined;
  const body = error.body;
  if (typeof body !== "object" || body === null) return undefined;
  const detail = (body as { detail?: unknown }).detail;
  return typeof detail === "string" && detail.trim() !== ""
    ? detail
    : undefined;
}

/** A short sentence for the user explaining an api failure, without dashes. */
export function describeError(error: unknown): string {
  if (!isApiError(error)) return "Something unexpected went wrong.";
  switch (error.kind) {
    case "http": {
      const detail = errorDetail(error);
      return detail
        ? `The API answered ${error.status}: ${detail}.`
        : `The API answered ${error.status}.`;
    }
    case "timeout":
      return "The API did not answer in time.";
    case "network":
      return "The API could not be reached.";
    case "parse":
      return "The API sent a response this page cannot read.";
    case "aborted":
      return "The request was cancelled.";
  }
}
