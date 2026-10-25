import {
  Expand,
  Hand,
  Keyboard,
  LoaderCircle,
  RotateCw,
  Undo2,
} from "lucide-react";
import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent,
} from "react";

import { Button } from "@/components/ui/button";
import { describeError, errorDetail } from "@/lib/api";

import {
  giveBack,
  liveSocketUrl,
  liveTicket,
  takeOver,
  type InputHolder,
} from "./browser-api";
import { screenFactory, type Screen } from "./screen";

const SPECIAL_KEYS: Record<string, number> = {
  Backspace: 0xff08,
  Tab: 0xff09,
  Enter: 0xff0d,
};

type Phase = "connecting" | "connected" | "closed";

const TOO_MANY = "Too many open views of your browser. Close one.";

const STATUS: Record<InputHolder, string> = {
  assistant: "You are watching your browser. Take over to use it yourself.",
  you: "You have the keyboard and the mouse. Give back when you are done.",
};
const OWN_SESSION =
  "This is your own session: you have the keyboard and the mouse.";
const WATCHING = "You are watching your browser.";

function sentence(error: unknown): string {
  return errorDetail(error) ?? describeError(error);
}

/**
 * The live view of the person's own browser (SPEC-browser.md, The live view): the screen drawn by
 * noVNC over /vnc/websockify with a one use ticket, view only until "Take over", which gives this
 * tab the keyboard and the mouse (the agent's next action waits) until "Give back". `status` is the
 * caller's sentence for the status line while the assistant has the browser ("The assistant is
 * filling the form"), or while the person has it (a sign in hand off). `person`: the person's own session (no assistant, browser B7), where this
 * view has the keyboard and the mouse from the start and there is nothing to take over or give
 * back. `watchOnly`: the person only watches, with no Take over and no keyboard (the submit lease,
 * whose read back a person's input would void). Used by Account's "Your browser" card and by the
 * apply module's job page.
 */
export function LiveBrowser({
  status,
  person = false,
  watchOnly = false,
}: {
  status?: string;
  person?: boolean;
  watchOnly?: boolean;
}) {
  const [viewer] = useState(() => crypto.randomUUID());
  const target = useRef<HTMLDivElement>(null);
  const frame = useRef<HTMLElement>(null);
  const keyboard = useRef<HTMLTextAreaElement>(null);
  const screen = useRef<Screen | null>(null);
  const [phase, setPhase] = useState<Phase>("connecting");
  const [input, setInput] = useState<InputHolder>(person ? "you" : "assistant");
  const inputRef = useRef<InputHolder>(person ? "you" : "assistant");
  const [problem, setProblem] = useState<string>();
  const [busy, setBusy] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    // `closed`: the socket is already gone (noVNC logs an error when a closed view is closed again).
    const run = { gone: false, closed: false, opened: null as Screen | null };
    // A function, not the field: TypeScript would keep its narrowing across the awaits.
    const gone = () => run.gone;
    const connect = async () => {
      try {
        const ticket = await liveTicket(viewer);
        if (gone() || !target.current) return;
        const opened = await screenFactory.open(
          target.current,
          liveSocketUrl(ticket, window.location),
          {
            connect: () => {
              if (!gone()) setPhase("connected");
            },
            refused: () => {
              if (!gone()) setProblem(TOO_MANY);
            },
            disconnect: () => {
              run.closed = true;
              if (gone()) return;
              screen.current = null;
              setPhase("closed");
            },
          },
        );
        run.opened = opened;
        if (gone()) {
          if (!run.closed) opened.disconnect();
          return;
        }
        opened.viewOnly = inputRef.current !== "you";
        screen.current = opened;
      } catch (error) {
        if (gone()) return;
        setProblem(sentence(error));
        setPhase("closed");
      }
    };
    void connect();
    return () => {
      run.gone = true;
      screen.current = null;
      if (!run.closed) run.opened?.disconnect();
    };
  }, [viewer, attempt]);

  // Leaving the page while holding the input gives it back, so the agent does not wait for it.
  useEffect(
    () => () => {
      if (inputRef.current === "you" && !person)
        void giveBack(viewer).catch(() => undefined);
    },
    [viewer, person],
  );

  const holder = (next: InputHolder) => {
    inputRef.current = next;
    setInput(next);
    if (screen.current) {
      screen.current.viewOnly = next !== "you";
      if (next === "you") screen.current.focus();
    }
  };

  const switchInput = async (to: InputHolder) => {
    setBusy(true);
    setProblem(undefined);
    try {
      holder(await (to === "you" ? takeOver(viewer) : giveBack(viewer)));
    } catch (error) {
      setProblem(sentence(error));
    } finally {
      setBusy(false);
    }
  };

  const reconnect = () => {
    setPhase("connecting");
    setProblem(undefined);
    setAttempt((n) => n + 1);
  };

  const typed = (event: FormEvent<HTMLTextAreaElement>) => {
    const field = event.currentTarget;
    const current = screen.current;
    if (current && inputRef.current === "you") {
      for (const character of field.value)
        current.typeKey(current.keysymOf(character));
    }
    field.value = "";
  };

  const special = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    const keysym = SPECIAL_KEYS[event.key];
    if (keysym === undefined) return;
    event.preventDefault();
    if (screen.current && inputRef.current === "you")
      screen.current.typeKey(keysym);
  };

  const line =
    phase === "connecting"
      ? "Connecting to your browser."
      : phase === "closed"
        ? "The live view is closed."
        : person
          ? (status ?? OWN_SESSION)
          : input === "you"
            ? STATUS.you
            : (status ?? (watchOnly ? WATCHING : STATUS.assistant));

  return (
    <section
      ref={frame}
      aria-label="Live view of your browser"
      className="flex flex-col gap-3 bg-background"
    >
      <div
        ref={target}
        data-testid="live-screen"
        className="aspect-[4/3] w-full overflow-hidden rounded-md border bg-muted"
      />
      <p
        role="status"
        className="flex items-center gap-2 text-sm text-muted-foreground"
      >
        {phase === "connecting" && (
          <LoaderCircle aria-hidden className="size-4 animate-spin" />
        )}
        {line}
      </p>
      {problem && (
        <p role="alert" className="text-sm text-destructive">
          {problem}
        </p>
      )}
      <div className="flex flex-wrap gap-2">
        {person || watchOnly ? null : input === "you" ? (
          <Button
            type="button"
            disabled={busy}
            onClick={() => void switchInput("assistant")}
          >
            <Undo2 aria-hidden />
            Give back
          </Button>
        ) : (
          <Button
            type="button"
            disabled={busy || phase !== "connected"}
            onClick={() => void switchInput("you")}
          >
            <Hand aria-hidden />
            Take over
          </Button>
        )}
        {input === "you" && (
          <Button
            type="button"
            variant="outline"
            onClick={() => {
              keyboard.current?.focus();
            }}
          >
            <Keyboard aria-hidden />
            Keyboard
          </Button>
        )}
        <Button
          type="button"
          variant="outline"
          disabled={phase !== "connected"}
          onClick={() => {
            void frame.current?.requestFullscreen().catch(() => undefined);
          }}
        >
          <Expand aria-hidden />
          Full screen
        </Button>
        {phase === "closed" && (
          <Button type="button" variant="outline" onClick={reconnect}>
            <RotateCw aria-hidden />
            Reconnect
          </Button>
        )}
      </div>
      {/* The on screen keyboard of a phone: what is typed here goes to the browser, key by key. */}
      {watchOnly ? null : (
        <textarea
          ref={keyboard}
          aria-label="Type into your browser"
          autoCapitalize="off"
          autoComplete="off"
          autoCorrect="off"
          spellCheck={false}
          onInput={typed}
          onKeyDown={special}
          className="sr-only"
        />
      )}
    </section>
  );
}
