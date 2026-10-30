import { useQuery } from "@tanstack/react-query";

import {
  getMailAccount,
  mailAccountQueryKey,
} from "@/features/mail/account-api";
import type { AlertEmails as Settings } from "@/features/profile/api";

import { ALERT_PROVIDERS } from "./alert-providers";

/*
 * The "Alert e-mails" group of Find jobs setup (SPEC-mail.md, "Job alert e-mails: a narrow
 * exception"): one box per provider, a short how-to, and whether Gmail is connected. A ticked
 * box is saved with the search; a sweep then reads that website's own alert e-mails and
 * nothing else of the mailbox.
 */

/** The connection line: what the last successful read of /api/mail/account says. */
function Connection() {
  const account = useQuery({
    queryKey: mailAccountQueryKey,
    queryFn: ({ signal }) => getMailAccount(signal),
    retry: false,
  });
  if (account.isPending)
    return <p className="text-sm text-muted-foreground">Checking Gmail…</p>;
  if (!account.data)
    return (
      <p className="text-sm text-muted-foreground">
        Whether Gmail is connected could not be checked.
      </p>
    );
  const { status, can_read: canRead } = account.data;
  if (status === "ok" && canRead)
    return (
      <p className="text-sm text-muted-foreground">
        Gmail is connected: ticked alert e-mails are read at each search.
      </p>
    );
  return (
    <p className="text-sm">
      {status === "needs_reconnect"
        ? "Gmail needs to be connected again"
        : "Gmail is not connected"}
      : connect it on the Account page (Gmail card) before alert e-mails can be
      read.
    </p>
  );
}

export function AlertEmailsGroup({
  id,
  value,
  onChange,
}: {
  id: string;
  value: Settings;
  onChange: (next: Settings) => void;
}) {
  return (
    <section className="space-y-3" aria-labelledby={`${id}-alerts`}>
      <h3 id={`${id}-alerts`} className="font-medium">
        Alert e-mails
      </h3>
      <p className="text-sm text-muted-foreground">
        The app reads the job alert e-mails these websites send to your
        connected Gmail, from their own addresses only, and adds the postings
        they list. It never opens their links: you open a posting yourself.
      </p>
      <Connection />
      <ul aria-label="Alert e-mails" className="divide-y rounded-md border">
        {ALERT_PROVIDERS.map((provider) => (
          <li key={provider.key} className="min-w-0 space-y-1 p-3 text-sm">
            <label className="flex min-w-0 items-center gap-2 font-medium">
              <input
                type="checkbox"
                className="size-4 shrink-0 accent-primary"
                aria-describedby={`${id}-${provider.key}-alert-how`}
                checked={value[provider.key]}
                onChange={(event) => {
                  onChange({ ...value, [provider.key]: event.target.checked });
                }}
              />
              {provider.label} alert e-mails
            </label>
            <p
              id={`${id}-${provider.key}-alert-how`}
              className="text-muted-foreground"
            >
              {provider.howTo}
            </p>
          </li>
        ))}
      </ul>
    </section>
  );
}
