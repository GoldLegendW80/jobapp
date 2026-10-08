import { delay, http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { server } from "@/test/server";

import {
  ApiError,
  DEFAULT_TIMEOUT_MS,
  describeError,
  getHealth,
  isApiError,
  request,
  validateHealth,
} from "./api";

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError);
    return error as ApiError;
  }
  throw new Error("expected the request to fail");
}

function health(resolver: Parameters<typeof http.get>[1]) {
  server.use(http.get("*/api/health", resolver));
}

describe("request: success", () => {
  it("returns the validated health body on 200", async () => {
    await expect(getHealth()).resolves.toEqual({ status: "ok" });
  });

  it("calls /api plus the path and asks for JSON", async () => {
    let seen: Request | undefined;
    health(({ request: req }) => {
      seen = req;
      return HttpResponse.json({ status: "ok" });
    });
    await getHealth();
    expect(new URL(seen!.url).pathname).toBe("/api/health");
    expect(seen!.method).toBe("GET");
    expect(seen!.headers.get("accept")).toBe("application/json");
    expect(seen!.headers.get("content-type")).toBeNull();
  });

  it("drops extra fields through the validator", async () => {
    health(() =>
      HttpResponse.json({ status: "ok", version: "1", extra: [1, 2] }),
    );
    await expect(getHealth()).resolves.toEqual({ status: "ok" });
  });

  it("returns the raw JSON when no validator is given", async () => {
    server.use(
      http.get("*/api/things", () =>
        HttpResponse.json([{ name: "café ☕ 東京" }]),
      ),
    );
    await expect(request("/things")).resolves.toEqual([
      { name: "café ☕ 東京" },
    ]);
  });

  it("returns undefined for an empty 204", async () => {
    server.use(
      http.delete(
        "*/api/things/1",
        () => new HttpResponse(null, { status: 204 }),
      ),
    );
    await expect(
      request("/things/1", { method: "DELETE" }),
    ).resolves.toBeUndefined();
  });

  it("serialises a body as JSON with a content type", async () => {
    let received: unknown;
    let contentType: string | null = null;
    server.use(
      http.post("*/api/echo", async ({ request: req }) => {
        contentType = req.headers.get("content-type");
        received = await req.json();
        return HttpResponse.json({ ok: true }, { status: 201 });
      }),
    );
    const payload = { title: "Ingénieur IA", long: "x".repeat(10_000) };
    await expect(
      request("/echo", { method: "POST", body: payload }),
    ).resolves.toEqual({
      ok: true,
    });
    expect(contentType).toBe("application/json");
    expect(received).toEqual(payload);
  });

  it("rejects a path without a leading slash before any fetch", async () => {
    const spy = vi.spyOn(globalThis, "fetch");
    await expect(request("health")).rejects.toThrow(TypeError);
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("request: http errors", () => {
  it("turns a 503 JSON answer into an http error carrying status and body", async () => {
    health(() =>
      HttpResponse.json(
        { status: "error", detail: "database unavailable" },
        { status: 503 },
      ),
    );
    const error = await failure(getHealth());
    expect(error.kind).toBe("http");
    expect(error.status).toBe(503);
    expect(error.body).toEqual({
      status: "error",
      detail: "database unavailable",
    });
    expect(describeError(error)).toBe(
      "The API answered 503: database unavailable.",
    );
  });

  it("keeps a non JSON error body as text", async () => {
    health(
      () =>
        new HttpResponse("<html><body>502 Bad Gateway</body></html>", {
          status: 502,
          headers: { "Content-Type": "text/html" },
        }),
    );
    const error = await failure(getHealth());
    expect(error.kind).toBe("http");
    expect(error.status).toBe(502);
    expect(error.body).toBe("<html><body>502 Bad Gateway</body></html>");
    expect(describeError(error)).toBe("The API answered 502.");
  });

  it("handles an error with an empty body", async () => {
    health(() => new HttpResponse(null, { status: 500 }));
    const error = await failure(getHealth());
    expect(error).toMatchObject({ kind: "http", status: 500, body: undefined });
  });

  it("ignores a detail that is not a string", async () => {
    health(() =>
      HttpResponse.json({ detail: [{ msg: "bad" }] }, { status: 422 }),
    );
    expect(describeError(await failure(getHealth()))).toBe(
      "The API answered 422.",
    );
  });
});

describe("request: unreadable success bodies", () => {
  it("rejects a 200 whose body is not JSON", async () => {
    health(() => HttpResponse.text("OK"));
    const error = await failure(getHealth());
    expect(error.kind).toBe("parse");
    expect(error.status).toBe(200);
    expect(error.body).toBe("OK");
  });

  it("rejects malformed JSON", async () => {
    health(
      () =>
        new HttpResponse('{"status": "ok"', {
          headers: { "Content-Type": "application/json" },
        }),
    );
    expect((await failure(getHealth())).kind).toBe("parse");
  });

  it("rejects a 200 with an empty body when a shape is expected", async () => {
    health(() => new HttpResponse(null, { status: 200 }));
    expect((await failure(getHealth())).kind).toBe("parse");
  });

  it.each([
    [{ status: "error" }],
    [{ status: "OK" }],
    [{}],
    [null],
    [["ok"]],
    ["ok"],
    [42],
  ])("rejects an unexpected health body %j", async (body) => {
    health(() => HttpResponse.json(body));
    const error = await failure(getHealth());
    expect(error.kind).toBe("parse");
    expect(error.body).toEqual(body);
    expect(describeError(error)).toBe(
      "The API sent a response this page cannot read.",
    );
  });
});

describe("request: no response", () => {
  it("reports a network error", async () => {
    health(() => HttpResponse.error());
    const error = await failure(getHealth());
    expect(error.kind).toBe("network");
    expect(error.status).toBeUndefined();
    expect(describeError(error)).toBe("The API could not be reached.");
  });

  it("aborts after the timeout when the server never answers", async () => {
    health(async () => {
      await delay("infinite");
      return HttpResponse.json({ status: "ok" });
    });
    const started = Date.now();
    const error = await failure(getHealth({ timeoutMs: 50 }));
    expect(error.kind).toBe("timeout");
    expect(Date.now() - started).toBeLessThan(2000);
    expect(describeError(error)).toBe("The API did not answer in time.");
  });

  it("times out a body that starts and then stalls", async () => {
    health(() => {
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          controller.enqueue(new TextEncoder().encode('{"status":'));
        },
      });
      return new HttpResponse(stream, {
        headers: { "Content-Type": "application/json" },
      });
    });
    expect((await failure(getHealth({ timeoutMs: 100 }))).kind).toBe("timeout");
  });

  it("succeeds when the answer arrives just before the timeout", async () => {
    health(async () => {
      await delay(20);
      return HttpResponse.json({ status: "ok" });
    });
    await expect(getHealth({ timeoutMs: 1000 })).resolves.toEqual({
      status: "ok",
    });
  });

  it("uses a five second default timeout", () => {
    expect(DEFAULT_TIMEOUT_MS).toBe(5000);
  });

  it("reports a cancellation by the caller as aborted, not timeout", async () => {
    health(async () => {
      await delay("infinite");
      return HttpResponse.json({ status: "ok" });
    });
    const controller = new AbortController();
    const pending = getHealth({ signal: controller.signal, timeoutMs: 10_000 });
    setTimeout(() => {
      controller.abort();
    }, 20);
    const error = await failure(pending);
    expect(error.kind).toBe("aborted");
    expect(describeError(error)).toBe("The request was cancelled.");
  });

  it("does not fetch at all when the signal is already aborted", async () => {
    const spy = vi.spyOn(globalThis, "fetch");
    const controller = new AbortController();
    controller.abort();
    expect((await failure(getHealth({ signal: controller.signal }))).kind).toBe(
      "aborted",
    );
    expect(spy).not.toHaveBeenCalled();
  });

  it("clears its timer and detaches from the caller signal once done", async () => {
    const clear = vi.spyOn(globalThis, "clearTimeout");
    const controller = new AbortController();
    const remove = vi.spyOn(controller.signal, "removeEventListener");
    await getHealth({ signal: controller.signal });
    expect(clear).toHaveBeenCalled();
    expect(remove).toHaveBeenCalledWith("abort", expect.any(Function));
    // Aborting afterwards must not throw or affect anything.
    controller.abort();
  });

  it("clears its timer after a failure too", async () => {
    const clear = vi.spyOn(globalThis, "clearTimeout");
    health(() => HttpResponse.error());
    await failure(getHealth());
    expect(clear).toHaveBeenCalled();
  });
});

describe("helpers", () => {
  it("validateHealth accepts only status ok", () => {
    expect(validateHealth({ status: "ok" })).toEqual({ status: "ok" });
    expect(() => validateHealth({ status: "error" })).toThrow(TypeError);
    expect(() => validateHealth(undefined)).toThrow(TypeError);
  });

  it("isApiError tells ApiError apart from other errors", () => {
    expect(isApiError(new ApiError("network", "x"))).toBe(true);
    expect(isApiError(new Error("x"))).toBe(false);
    expect(isApiError("x")).toBe(false);
  });

  it("describeError has a fallback for unknown errors", () => {
    expect(describeError(new Error("boom"))).toBe(
      "Something unexpected went wrong.",
    );
    expect(describeError(undefined)).toBe("Something unexpected went wrong.");
  });

  it("never uses dashes as separators in user facing messages", () => {
    const errors = [
      new ApiError("http", "", {
        status: 503,
        body: { detail: "database unavailable" },
      }),
      new ApiError("http", "", { status: 500 }),
      new ApiError("timeout", ""),
      new ApiError("network", ""),
      new ApiError("parse", ""),
      new ApiError("aborted", ""),
      new Error(""),
    ];
    for (const error of errors)
      expect(describeError(error)).not.toMatch(/[–—]| - /);
  });
});
