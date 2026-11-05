import { useQuery } from "@tanstack/react-query";
import { ListChecks } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, matchRoutes, useLocation, useNavigate } from "react-router";

import { Button } from "@/components/ui/button";
import { useMe } from "@/features/auth/auth-context";
import { cn } from "@/lib/utils";

import { getOnboarding, onboardingKey } from "./onboarding-api";

export const WELCOME = "/welcome";
/** The home page: no need to come back to it after the wizard, it is where the wizard ends. */
const HOME = "/jobs";
const checkedKey = (userId: string) => `welcome:checked:${userId}`;

function isWelcome(pathname: string): boolean {
  return matchRoutes([{ path: WELCOME }], { pathname }) !== null;
}

function wasChecked(userId: string): boolean {
  try {
    return window.sessionStorage.getItem(checkedKey(userId)) === "1";
  } catch {
    return false;
  }
}

function markChecked(userId: string): void {
  try {
    window.sessionStorage.setItem(checkedKey(userId), "1");
  } catch {
    // Storage blocked: the api answer still decides, and stops asking once the wizard ends.
  }
}

/**
 * What the gate decided, on the root element (`data-onboarding`): the journeys that create an
 * account read it to know, without waiting on a clock, whether the wizard opened.
 */
function marker(decision: "welcome" | "stay"): void {
  document.documentElement.dataset.onboarding = decision;
}

/**
 * The first login redirect (opportunities O5): once per browser session, a person the api says
 * should see the wizard (`open_wizard`: a member with an empty profile who neither finished nor
 * put it off) is sent to /welcome, once. The page asked for waits for the answer (no flash of it,
 * none of its requests), and a deep link is kept in `?next=` for the wizard to open at its end.
 * An api that fails sends nobody anywhere.
 */
export function OnboardingGate({ children }: { children: ReactNode }) {
  const me = useMe();
  const userId = me.data?.id;
  const location = useLocation();
  const atWelcome = isWelcome(location.pathname);
  const navigate = useNavigate();
  const [checked] = useState(() => (userId ? wasChecked(userId) : true));
  const handled = useRef(checked);
  // True once the gate is done in this mount: before, the page asked for may not show yet.
  const [released, setReleased] = useState(checked);
  // True once the gate sent the person to /welcome: it waits until the wizard is on screen.
  const [sent, setSent] = useState(false);
  const state = useQuery({
    queryKey: onboardingKey,
    queryFn: ({ signal }) => getOnboarding(signal),
    enabled: !checked && userId !== undefined,
  });
  const decided = state.isSuccess || state.isError;
  useEffect(() => {
    if (checked) marker("stay"); // decided earlier in this session
    return () => {
      delete document.documentElement.dataset.onboarding;
    };
  }, [checked]);
  const open = state.data?.open_wizard === true;
  useEffect(() => {
    // Once per mount and per session: after that the person goes wherever they like.
    if (handled.current || !userId || !decided) return;
    handled.current = true;
    markChecked(userId);
    const send = open && !atWelcome;
    marker(send ? "welcome" : "stay");
    const asked = `${location.pathname}${location.search}${location.hash}`;
    const keep = location.pathname !== HOME && location.pathname !== "/";
    const moving = send
      ? navigate(
          keep
            ? `${WELCOME}?${new URLSearchParams({ next: asked }).toString()}`
            : WELCOME,
          { replace: true },
        )
      : undefined;
    // Staying: every page shows again once the decision is carried out. Sent to the wizard: the
    // router applies the move in a transition, so its promise can settle while the old page is
    // still the one rendered; the gate lets go once the wizard is the page (below).
    void Promise.resolve(moving).then(() => {
      if (send) setSent(true);
      else setReleased(true);
    });
  }, [decided, open, userId, location, navigate, atWelcome]);
  if (sent && atWelcome && !released) setReleased(true);
  // Until the first answer of this session, and while the move to /welcome is on its way, the
  // page asked for is not shown.
  const waiting =
    !released &&
    userId !== undefined &&
    (!decided || (open && !atWelcome) || (sent && !atWelcome));
  if (waiting) return <PageSkeleton />;
  return children;
}

const BAR =
  "rounded-md bg-foreground/10 animate-pulse motion-reduce:animate-none";

/**
 * The page's place while the gate decides: grey bars where a heading, a line and a few rows
 * will be, inside the shell's header and footer. It fades in only after a short delay
 * (`delayed-appear`), so a quick answer shows no flash of it; screen readers hear "Loading".
 */
function PageSkeleton() {
  return (
    <div
      role="status"
      aria-live="polite"
      data-testid="page-skeleton"
      className="delayed-appear mx-auto flex w-full max-w-6xl flex-col gap-6"
    >
      <span className="sr-only">Loading</span>
      <div aria-hidden="true" className="flex flex-col gap-3">
        <div className={cn(BAR, "h-8 w-48")} />
        <div className={cn(BAR, "h-4 w-full max-w-md")} />
      </div>
      <div aria-hidden="true" className="flex flex-col gap-3">
        {[0, 1, 2, 3].map((row) => (
          <div key={row} className={cn(BAR, "h-16 w-full")} />
        ))}
      </div>
    </div>
  );
}

/** The home page's "Finish setting up" while the wizard is not finished and steps are left. */
export function FinishSetupCard() {
  const state = useQuery({
    queryKey: onboardingKey,
    queryFn: ({ signal }) => getOnboarding(signal),
  });
  if (!state.data?.show_card) return null;
  return (
    <section
      aria-labelledby="finish-setup"
      className="flex flex-wrap items-center justify-between gap-3 rounded-lg border bg-card px-4 py-3"
    >
      <div className="flex min-w-0 items-start gap-3">
        <ListChecks
          aria-hidden="true"
          className="mt-0.5 size-5 shrink-0 text-primary"
        />
        <div className="flex flex-col">
          <h2 id="finish-setup" className="text-sm font-semibold">
            Finish setting up
          </h2>
          <p className="text-sm text-muted-foreground">
            A few answers about what you look for, and the app finds postings
            that fit.
          </p>
        </div>
      </div>
      <Button asChild size="sm">
        <Link to={WELCOME}>Continue setup</Link>
      </Button>
    </section>
  );
}
