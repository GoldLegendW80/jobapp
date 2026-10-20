import { useQuery } from "@tanstack/react-query";
import { ListChecks } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";
import { Link, useLocation, useNavigate } from "react-router";

import { Button } from "@/components/ui/button";
import { useMe } from "@/features/auth/auth-context";

import { getOnboarding, onboardingKey } from "./onboarding-api";

export const WELCOME = "/welcome";
const checkedKey = (userId: string) => `welcome:checked:${userId}`;

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
 * The first login redirect (opportunities O5): once per browser session, a person the api says
 * should see the wizard (`open_wizard`: a member with an empty profile who neither finished nor
 * put it off) is sent to /welcome, once. The page asked for renders meanwhile; an api that fails
 * sends nobody anywhere.
 */
export function OnboardingGate({ children }: { children: ReactNode }) {
  const me = useMe();
  const userId = me.data?.id;
  const location = useLocation();
  const navigate = useNavigate();
  const [checked] = useState(() => (userId ? wasChecked(userId) : true));
  const handled = useRef(checked);
  const state = useQuery({
    queryKey: onboardingKey,
    queryFn: ({ signal }) => getOnboarding(signal),
    enabled: !checked && userId !== undefined,
  });
  const decided = state.isSuccess || state.isError;
  const open = state.data?.open_wizard === true;
  useEffect(() => {
    // Once per mount and per session: after that the person goes wherever they like.
    if (handled.current || !userId || !decided) return;
    handled.current = true;
    markChecked(userId);
    if (open && location.pathname !== WELCOME)
      void navigate(WELCOME, { replace: true });
  }, [decided, open, userId, location.pathname, navigate]);
  return children;
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
