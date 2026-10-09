import { act, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { delay, http, HttpResponse } from "msw";
import { describe, expect, it, vi } from "vitest";

import { admin, fakeSession, member } from "@/test/auth";
import { renderApp } from "@/test/render";
import { server } from "@/test/server";

import { SERVER_UNREACHABLE, TOO_MANY_ATTEMPTS } from "./login-errors";

const email = () => screen.getByLabelText("Email");
const password = () => screen.getByLabelText("Password");
const submit = () => screen.getByRole("button", { name: /sign(ing)? in/i });

/** Login answers `respond`; on 200 the fake session is signed in as that user. */
function loginAnswers(
  respond: (body: {
    email: string;
    password: string;
  }) => Response | Promise<Response>,
) {
  const state = fakeSession(null);
  const seen: Array<{ body: unknown; headers: Headers }> = [];
  server.use(
    http.post("*/api/auth/login", async ({ request }) => {
      const body = (await request.json()) as {
        email: string;
        password: string;
      };
      seen.push({ body, headers: request.headers });
      const response = await respond(body);
      if (response.status === 200)
        state.user = (await response.clone().json()) as typeof admin;
      return response;
    }),
  );
  return { state, seen };
}

async function fillAndSubmit(
  mail = "Harald@Example.org",
  pass = "correct horse",
) {
  const user = userEvent.setup();
  await user.type(await screen.findByLabelText("Email"), mail);
  await user.type(password(), pass);
  await user.click(submit());
  return user;
}

describe("login page", () => {
  it("is reached from / when signed out, with next=/", async () => {
    const { location } = renderApp("/");
    expect(
      await screen.findByRole("heading", { name: "Sign in" }),
    ).toBeInTheDocument();
    expect(location()).toBe("/login?next=/");
    expect(document.title).toBe("Sign in · jobapp");
  });

  it("signs in and lands on the page in next, sending the CSRF header", async () => {
    const { seen } = loginAnswers(() => HttpResponse.json(admin));
    const { location } = renderApp("/login?next=/admin/users");
    await fillAndSubmit("  Harald@Example.org ", "correct horse");
    expect(
      await screen.findByRole("heading", { name: "Users" }),
    ).toBeInTheDocument();
    expect(location()).toBe("/admin/users");
    expect(seen).toHaveLength(1);
    // The email is trimmed but its case kept: the api compares without case.
    expect(seen[0]!.body).toEqual({
      email: "Harald@Example.org",
      password: "correct horse",
    });
    expect(seen[0]!.headers.get("x-requested-with")).toBe("jobapp");
  });

  it("lands on / without next", async () => {
    loginAnswers(() => HttpResponse.json(member));
    const { location } = renderApp("/login");
    await fillAndSubmit();
    expect(
      await screen.findByRole("heading", { level: 1, name: "Jobs" }),
    ).toBeInTheDocument();
    expect(location()).toBe("/jobs");
  });

  it.each([
    "//evil.com",
    "https://evil.com",
    "/\\evil.com",
    "javascript:alert(1)",
    "%2F%2Fevil.com",
    "%252F%252Fevil.com",
  ])("ignores an unsafe next %j and lands on /", async (next) => {
    loginAnswers(() => HttpResponse.json(admin));
    const { location } = renderApp(`/login?next=${next}`);
    await fillAndSubmit();
    expect(
      await screen.findByRole("heading", { level: 1, name: "Jobs" }),
    ).toBeInTheDocument();
    expect(location()).toBe("/jobs");
  });

  it("shows the api's generic message on 401 and stays on the page", async () => {
    loginAnswers(() =>
      HttpResponse.json(
        { detail: "Incorrect email or password." },
        { status: 401 },
      ),
    );
    const { location } = renderApp("/login?next=/admin/users");
    await fillAndSubmit();
    const alert = await screen.findByRole("alert");
    expect(alert).toHaveTextContent("Incorrect email or password.");
    expect(location()).toBe("/login?next=/admin/users");
    expect(submit()).toBeEnabled();
    expect(email()).toHaveValue("Harald@Example.org");
  });

  it("shows a 422 detail verbatim", async () => {
    loginAnswers(() =>
      HttpResponse.json(
        { detail: "email: String should have at most 512 characters" },
        { status: 422 },
      ),
    );
    renderApp("/login");
    await fillAndSubmit();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "email: String should have at most 512 characters",
    );
  });

  it("says the server cannot be reached on a network error", async () => {
    loginAnswers(() => HttpResponse.error());
    renderApp("/login");
    await fillAndSubmit();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      SERVER_UNREACHABLE,
    );
  });

  it("shows the placeholder message on 429 without a detail, the detail when given", async () => {
    let call = 0;
    loginAnswers(() => {
      call += 1;
      // No Retry-After here: nothing to count down, the button stays usable.
      return call === 1
        ? new HttpResponse(null, { status: 429 })
        : HttpResponse.json(
            { detail: "Too many attempts. Try again in 15 minutes." },
            { status: 429 },
          );
    });
    renderApp("/login");
    const user = await fillAndSubmit();
    expect(await screen.findByRole("alert")).toHaveTextContent(
      TOO_MANY_ATTEMPTS,
    );
    await user.click(submit());
    await waitFor(() => {
      expect(screen.getByRole("alert")).toHaveTextContent(
        "Too many attempts. Try again in 15 minutes.",
      );
    });
  });

  it("after a 429 disables the button with a countdown until Retry-After has passed", async () => {
    // Fake only the interval and the clock: msw and user-event keep their real timeouts.
    vi.useFakeTimers({
      shouldAdvanceTime: true,
      toFake: ["setInterval", "clearInterval", "Date"],
    });
    try {
      const detail = "Too many sign in attempts. Try again in 2 minutes.";
      let call = 0;
      const { seen } = loginAnswers(() => {
        call += 1;
        return call === 1
          ? HttpResponse.json(
              { detail },
              { status: 429, headers: { "Retry-After": "65" } },
            )
          : HttpResponse.json(admin);
      });
      const { location } = renderApp("/login");
      const user = await fillAndSubmit();
      expect(await screen.findByRole("alert")).toHaveTextContent(detail);
      const locked = screen.getByRole("button", { name: /try again in/i });
      expect(locked).toBeDisabled();
      expect(locked).toHaveTextContent("Try again in 1:05");

      await act(async () => {
        await vi.advanceTimersByTimeAsync(30_000);
      });
      expect(locked).toHaveTextContent("Try again in 0:35");
      expect(locked).toBeDisabled();
      await user.click(locked); // disabled: sends nothing
      expect(seen).toHaveLength(1);

      await act(async () => {
        await vi.advanceTimersByTimeAsync(34_000);
      });
      expect(locked).toHaveTextContent("Try again in 0:01");
      await act(async () => {
        await vi.advanceTimersByTimeAsync(1_000);
      });
      const again = screen.getByRole("button", { name: "Sign in" });
      expect(again).toBeEnabled();
      expect(screen.queryByRole("alert")).not.toBeInTheDocument();

      await user.click(again);
      await waitFor(() => {
        expect(location()).toBe("/");
      });
      expect(seen).toHaveLength(2);
    } finally {
      vi.useRealTimers();
    }
  });

  it("a later 429 restarts the countdown with its own Retry-After", async () => {
    vi.useFakeTimers({
      shouldAdvanceTime: true,
      toFake: ["setInterval", "clearInterval", "Date"],
    });
    try {
      let call = 0;
      loginAnswers(() => {
        call += 1;
        return HttpResponse.json(
          { detail: "Too many sign in attempts. Try again in 1 minute." },
          {
            status: 429,
            headers: { "Retry-After": call === 1 ? "3" : "120" },
          },
        );
      });
      renderApp("/login");
      const user = await fillAndSubmit();
      expect(
        await screen.findByRole("button", { name: /try again in 0:03/i }),
      ).toBeDisabled();
      await act(async () => {
        await vi.advanceTimersByTimeAsync(3_000);
      });
      await user.click(screen.getByRole("button", { name: "Sign in" }));
      expect(
        await screen.findByRole("button", { name: /try again in 2:00/i }),
      ).toBeDisabled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("disables the button while signing in, so a double click sends one request", async () => {
    const { seen } = loginAnswers(async () => {
      await delay(150);
      return HttpResponse.json(admin);
    });
    renderApp("/login");
    const user = await fillAndSubmit();
    expect(submit()).toBeDisabled();
    expect(submit()).toHaveTextContent("Signing in");
    await user.dblClick(submit());
    expect(
      await screen.findByRole("heading", { level: 1, name: "Jobs" }),
    ).toBeInTheDocument();
    expect(seen).toHaveLength(1);
  });

  it("checks the fields first, marks them invalid and focuses the first", async () => {
    const { seen } = loginAnswers(() => HttpResponse.json(admin));
    renderApp("/login");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("button", { name: "Sign in" }));
    expect(
      await screen.findByText("Enter your email address."),
    ).toBeInTheDocument();
    expect(screen.getByText("Enter your password.")).toBeInTheDocument();
    expect(email()).toHaveAttribute("aria-invalid", "true");
    expect(password()).toHaveAttribute("aria-invalid", "true");
    expect(email()).toHaveFocus();
    expect(email()).toHaveAccessibleDescription("Enter your email address.");

    await user.type(email(), "not-an-email");
    await user.click(submit());
    expect(
      await screen.findByText("Enter a valid email address."),
    ).toBeInTheDocument();
    expect(email()).toHaveFocus();

    await user.clear(email());
    await user.type(email(), "zoe@example.org");
    await user.click(submit());
    await waitFor(() => {
      expect(password()).toHaveFocus();
    });
    expect(email()).toHaveAttribute("aria-invalid", "false");
    expect(seen).toHaveLength(0);
  });

  it("redirects a signed in visitor to /", async () => {
    fakeSession(admin);
    const { location } = renderApp("/login");
    expect(
      await screen.findByRole("heading", { level: 1, name: "Jobs" }),
    ).toBeInTheDocument();
    expect(location()).toBe("/jobs");
  });

  it("redirects a signed in visitor to a safe next", async () => {
    fakeSession(admin);
    const { location } = renderApp("/login?next=/admin/users");
    expect(
      await screen.findByRole("heading", { name: "Users" }),
    ).toBeInTheDocument();
    expect(location()).toBe("/admin/users");
  });

  it("shows the form when the session check itself fails", async () => {
    server.use(http.get("*/api/auth/me", () => HttpResponse.error()));
    renderApp("/login");
    expect(
      await screen.findByRole("heading", { name: "Sign in" }),
    ).toBeInTheDocument();
  });

  it("has labelled fields, autocomplete hints and no dashes", async () => {
    const { container } = renderApp("/login");
    await screen.findByRole("heading", { name: "Sign in" });
    expect(email()).toHaveAttribute("autocomplete", "username");
    expect(email()).toHaveAttribute("type", "email");
    expect(password()).toHaveAttribute("autocomplete", "current-password");
    expect(screen.getByRole("form", { name: "Sign in" })).toBeInTheDocument();
    expect(container.textContent).not.toMatch(/[–—]| - /);
  });
});
