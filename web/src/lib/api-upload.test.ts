import { http, HttpResponse } from "msw";
import { afterEach, describe, expect, it, vi } from "vitest";

import { server } from "@/test/server";

import { ApiError, onUnauthorized, upload } from "./api";

async function failure(promise: Promise<unknown>): Promise<ApiError> {
  try {
    await promise;
  } catch (error) {
    expect(error).toBeInstanceOf(ApiError);
    return error as ApiError;
  }
  throw new Error("expected the upload to fail");
}

const file = (text = "some bytes", type = "") =>
  new File([text], "a.txt", { type });

function files(resolver: Parameters<typeof http.post>[1]) {
  server.use(http.post("*/api/files", resolver));
}

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("upload: success", () => {
  it("posts the file as the raw body with the session, the CSRF header and JSON asked for", async () => {
    let seen: { headers: Headers; body: string; method: string } | undefined;
    files(async ({ request }) => {
      seen = {
        headers: request.headers,
        body: await request.text(),
        method: request.method,
      };
      return HttpResponse.json({ id: 7 }, { status: 201 });
    });
    await expect(upload("/files", file("the bytes"))).resolves.toEqual({
      id: 7,
    });
    expect(seen?.method).toBe("POST");
    expect(seen?.body).toBe("the bytes");
    expect(seen?.headers.get("x-requested-with")).toBe("jobapp");
    expect(seen?.headers.get("accept")).toBe("application/json");
    expect(seen?.headers.get("content-type")).toBe("application/octet-stream");
  });

  it("sends the file's own type when it has one, and the caller's headers over it", async () => {
    const types: Array<string | null> = [];
    let name: string | null = null;
    files(({ request }) => {
      types.push(request.headers.get("content-type"));
      name = request.headers.get("x-filename");
      return HttpResponse.json({});
    });
    await upload("/files", file("x", "text/markdown"));
    await upload("/files", file("x", "text/markdown"), {
      headers: {
        "Content-Type": "application/octet-stream",
        "X-Filename": "n",
      },
    });
    expect(types).toEqual(["text/markdown", "application/octet-stream"]);
    expect(name).toBe("n");
  });

  it("runs the validator on the answer", async () => {
    files(() => HttpResponse.json({ id: 7, extra: true }));
    await expect(
      upload("/files", file(), {
        validate: (body) => ({ id: (body as { id: number }).id }),
      }),
    ).resolves.toEqual({ id: 7 });
  });

  it("returns undefined for an empty 204", async () => {
    files(() => new HttpResponse(null, { status: 204 }));
    await expect(upload("/files", file())).resolves.toBeUndefined();
  });

  it("rejects a path without a leading slash before anything is sent", () => {
    expect(() => upload("files", file())).toThrow(TypeError);
  });
});

describe("upload: failures", () => {
  it("turns a refusal into an http error with the status, the body and Retry-After", async () => {
    files(() =>
      HttpResponse.json(
        { detail: "Other uploads are in progress." },
        { status: 503, headers: { "Retry-After": "5" } },
      ),
    );
    const error = await failure(upload("/files", file()));
    expect(error.kind).toBe("http");
    expect(error.status).toBe(503);
    expect(error.body).toEqual({ detail: "Other uploads are in progress." });
    expect(error.retryAfter).toBe(5);
  });

  it("keeps a plain text error body as text", async () => {
    files(
      () =>
        new HttpResponse("413 Request Entity Too Large", {
          status: 413,
          headers: { "Content-Type": "text/plain" },
        }),
    );
    const error = await failure(upload("/files", file()));
    expect(error.status).toBe(413);
    expect(error.body).toBe("413 Request Entity Too Large");
  });

  it("tells the listeners about a 401, as request does", async () => {
    files(() =>
      HttpResponse.json({ detail: "Sign in to continue." }, { status: 401 }),
    );
    const listener = vi.fn();
    const unsubscribe = onUnauthorized(listener);
    const error = await failure(upload("/files", file()));
    unsubscribe();
    expect(error.status).toBe(401);
    expect(listener).toHaveBeenCalledExactlyOnceWith(error);
  });

  it("rejects a success whose body is not JSON", async () => {
    files(() => new HttpResponse("<html>", { status: 200 }));
    const error = await failure(upload("/files", file()));
    expect(error.kind).toBe("parse");
    expect(error.body).toBe("<html>");
  });

  it("rejects a success the validator refuses", async () => {
    files(() => HttpResponse.json({ id: "seven" }));
    const error = await failure(
      upload("/files", file(), {
        validate: () => {
          throw new TypeError("expected a number");
        },
      }),
    );
    expect(error.kind).toBe("parse");
    expect(error.body).toEqual({ id: "seven" });
  });

  it("reports a network error", async () => {
    files(() => HttpResponse.error());
    const error = await failure(upload("/files", file()));
    expect(error.kind).toBe("network");
  });

  it("does not send at all when the signal is already aborted", async () => {
    const handler = vi.fn(() => HttpResponse.json({}));
    files(handler);
    const controller = new AbortController();
    controller.abort();
    const error = await failure(
      upload("/files", file(), { signal: controller.signal }),
    );
    expect(error.kind).toBe("aborted");
    expect(handler).not.toHaveBeenCalled();
  });
});

/** An XMLHttpRequest the test drives by hand: what jsdom does not simulate (progress, timeout). */
class FakeXhr {
  static last: FakeXhr | undefined;
  status = 0;
  responseText = "";
  timeout = 0;
  withCredentials = false;
  sent: unknown;
  headers: Record<string, string> = {};
  aborted = false;
  readonly upload = new EventTarget();
  readonly #events = new EventTarget();

  constructor() {
    FakeXhr.last = this;
  }
  open() {}
  setRequestHeader(name: string, value: string) {
    this.headers[name] = value;
  }
  getResponseHeader() {
    return null;
  }
  addEventListener(type: string, listener: EventListener) {
    this.#events.addEventListener(type, listener);
  }
  send(body: unknown) {
    this.sent = body;
  }
  abort() {
    this.aborted = true;
    this.fire("abort");
  }
  fire(type: string) {
    this.#events.dispatchEvent(new Event(type));
  }
  progress(loaded: number, total: number, lengthComputable = true) {
    const event = new Event("progress");
    Object.assign(event, { loaded, total, lengthComputable });
    this.upload.dispatchEvent(event);
  }
}

function withFakeXhr(): () => FakeXhr {
  FakeXhr.last = undefined;
  vi.stubGlobal("XMLHttpRequest", FakeXhr);
  return () => {
    if (!FakeXhr.last) throw new Error("no request was made");
    return FakeXhr.last;
  };
}

describe("upload: progress and time", () => {
  it("reports how much of the body has left, from 0 to 1", async () => {
    const xhr = withFakeXhr();
    const seen: number[] = [];
    const sent = file("0123456789");
    const pending = upload("/files", sent, {
      onProgress: (fraction) => seen.push(fraction),
    });
    expect(xhr().sent).toBe(sent);
    expect(xhr().withCredentials).toBe(true);
    xhr().progress(0, 10);
    xhr().progress(5, 10);
    xhr().progress(4, 0); // no total: nothing to say
    xhr().progress(4, 10, false); // length unknown: nothing to say
    xhr().progress(12, 10); // never past the end
    xhr().status = 201;
    xhr().responseText = '{"ok":true}';
    xhr().fire("load");
    await expect(pending).resolves.toEqual({ ok: true });
    expect(seen).toEqual([0, 0.5, 1]);
  });

  it("has no time limit unless asked, and reports a timeout as one", async () => {
    const xhr = withFakeXhr();
    const unlimited = upload("/files", file());
    expect(xhr().timeout).toBe(0);
    xhr().status = 200;
    xhr().fire("load");
    await unlimited;

    const limited = failure(upload("/files", file(), { timeoutMs: 1500 }));
    expect(xhr().timeout).toBe(1500);
    xhr().fire("timeout");
    const error = await limited;
    expect(error.kind).toBe("timeout");
    expect(error.message).toContain("1500 ms");
  });

  it("stops an upload the caller cancels", async () => {
    const xhr = withFakeXhr();
    const controller = new AbortController();
    const pending = failure(
      upload("/files", file(), { signal: controller.signal }),
    );
    expect(xhr().aborted).toBe(false);
    controller.abort(new Error("the user changed their mind"));
    expect(xhr().aborted).toBe(true);
    const error = await pending;
    expect(error.kind).toBe("aborted");
    expect((error.cause as Error).message).toBe("the user changed their mind");
  });

  it("reports a connection that drops as a network error", async () => {
    const xhr = withFakeXhr();
    const pending = failure(upload("/files", file()));
    xhr().fire("error");
    expect((await pending).kind).toBe("network");
  });

  it("stops listening to the caller's signal once the upload is over", async () => {
    const xhr = withFakeXhr();
    const controller = new AbortController();
    const pending = upload("/files", file(), { signal: controller.signal });
    xhr().status = 200;
    xhr().responseText = "{}";
    xhr().fire("load");
    await pending;
    controller.abort();
    expect(xhr().aborted).toBe(false);
  });
});
