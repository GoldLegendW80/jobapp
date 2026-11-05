import {
  keepPreviousData,
  useMutation,
  useQuery,
  useQueryClient,
} from "@tanstack/react-query";
import {
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  MailPlus,
  RotateCw,
} from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { Alert, AlertDescription } from "@/components/ui/alert";
import {
  AlertDialog,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/ui/alert-dialog";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import {
  Empty,
  EmptyDescription,
  EmptyHeader,
  EmptyMedia,
  EmptyTitle,
} from "@/components/ui/empty";
import { Spinner } from "@/components/ui/spinner";
import {
  Table,
  TableBody,
  TableCaption,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { isApiError } from "@/lib/api";
import { cn } from "@/lib/utils";

import {
  INVITES_PAGE_SIZE,
  invitesKey,
  listInvites,
  revokeInvite,
  type AdminInvite,
  type InviteStatus,
} from "./api";
import { message } from "./errors";
import { InviteDialog } from "./invite-dialog";
import { exactTime, relativeTime } from "./relative-time";
import { ACTIONS, BODY, cell, HEAD, ROW } from "./table-cell";

const STATUS: Record<InviteStatus, { label: string; className: string }> = {
  active: {
    label: "Active",
    className: "border-success/30 bg-success/15 text-success",
  },
  used: { label: "Used", className: "bg-secondary text-secondary-foreground" },
  revoked: {
    label: "Revoked",
    className: "border-destructive/30 bg-destructive/10 text-destructive",
  },
  expired: {
    label: "Expired",
    className: "border-border text-muted-foreground",
  },
};

const KIND = { invite: "Invite", reset: "Password reset" } as const;
const ROLE = { member: "Member", admin: "Admin" } as const;

/** The admin's invite and reset links: who, what, status, expiry, and revoke while active. */
export function InvitesSection() {
  const [offset, setOffset] = useState(0);
  const [revoking, setRevoking] = useState<AdminInvite | null>(null);
  const invites = useQuery({
    queryKey: [...invitesKey, offset],
    queryFn: ({ signal }) => listInvites(offset, signal),
    placeholderData: keepPreviousData,
  });

  const page = invites.data;
  const now = new Date();

  return (
    <section aria-labelledby="invites-title" className="flex flex-col gap-4">
      <div className="flex flex-wrap items-end justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h2
            id="invites-title"
            className="text-lg font-semibold tracking-tight"
          >
            Invites
          </h2>
          <p className="text-sm text-muted-foreground">
            One-time links to create an account or reset a password.
          </p>
        </div>
        <InviteDialog />
      </div>

      {invites.isPending ? (
        <p
          role="status"
          aria-live="polite"
          className="rounded-lg border px-4 py-8 text-center text-sm text-muted-foreground"
        >
          <span className="delayed-appear inline-flex items-center gap-2">
            <Spinner aria-hidden="true" role={undefined} />
            Loading invites
          </span>
        </p>
      ) : invites.isError ? (
        <Alert variant="destructive">
          <CircleAlert aria-hidden="true" />
          <AlertDescription className="flex flex-col gap-3">
            <p>The invites could not be loaded. {message(invites.error)}</p>
            <Button
              variant="outline"
              size="sm"
              onClick={() => void invites.refetch()}
              disabled={invites.isFetching}
            >
              <RotateCw aria-hidden="true" />
              Retry
            </Button>
          </AlertDescription>
        </Alert>
      ) : page && page.total === 0 ? (
        <Empty className="flex-none gap-2 border px-4 py-10 md:px-4 md:py-10">
          <EmptyHeader className="max-w-none">
            <EmptyMedia className="mb-0 text-muted-foreground">
              <MailPlus aria-hidden="true" className="size-6" />
            </EmptyMedia>
            <EmptyTitle className="text-base tracking-normal">
              No invites yet
            </EmptyTitle>
            <EmptyDescription className="max-w-sm">
              Invite someone to give them access. Their link appears here so you
              can follow it and revoke it.
            </EmptyDescription>
          </EmptyHeader>
        </Empty>
      ) : page ? (
        <>
          <InvitesTable
            items={page.items}
            now={now}
            onRevoke={setRevoking}
            busy={invites.isPlaceholderData}
          />
          {page.total > INVITES_PAGE_SIZE ? (
            <nav
              aria-label="Invites pages"
              className="flex items-center justify-between gap-2 text-sm text-muted-foreground"
            >
              <span>
                {page.offset + 1} to {page.offset + page.items.length} of{" "}
                {page.total}
              </span>
              <div className="flex gap-2">
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page.offset === 0}
                  onClick={() => {
                    setOffset(Math.max(0, page.offset - INVITES_PAGE_SIZE));
                  }}
                >
                  <ChevronLeft aria-hidden="true" />
                  Previous
                </Button>
                <Button
                  variant="outline"
                  size="sm"
                  disabled={page.offset + page.items.length >= page.total}
                  onClick={() => {
                    setOffset(page.offset + INVITES_PAGE_SIZE);
                  }}
                >
                  Next
                  <ChevronRight aria-hidden="true" />
                </Button>
              </div>
            </nav>
          ) : null}
        </>
      ) : null}

      <RevokeDialog
        invite={revoking}
        onClose={() => {
          setRevoking(null);
        }}
      />
    </section>
  );
}

function InvitesTable({
  items,
  now,
  onRevoke,
  busy,
}: {
  items: AdminInvite[];
  now: Date;
  onRevoke: (invite: AdminInvite) => void;
  busy: boolean;
}) {
  return (
    <div
      className={cn(
        "sm:overflow-x-auto sm:rounded-lg sm:border",
        busy && "opacity-60",
      )}
      aria-busy={busy}
    >
      <Table className="max-sm:block">
        <TableCaption className="sr-only">Invites, newest first</TableCaption>
        <TableHeader className="bg-muted/50 text-xs max-sm:sr-only">
          <TableRow>
            <TableHead scope="col" className={HEAD}>
              Email
            </TableHead>
            <TableHead scope="col" className={HEAD}>
              Kind
            </TableHead>
            <TableHead scope="col" className={HEAD}>
              Role
            </TableHead>
            <TableHead scope="col" className={HEAD}>
              Status
            </TableHead>
            <TableHead scope="col" className={HEAD}>
              Expires
            </TableHead>
            <TableHead scope="col" className={cn(HEAD, "text-right")}>
              <span className="sr-only">Actions</span>
            </TableHead>
          </TableRow>
        </TableHeader>
        <TableBody className={BODY}>
          {items.map((invite) => {
            const status = STATUS[invite.status];
            return (
              <TableRow key={invite.id} className={ROW}>
                <TableHead
                  scope="row"
                  className="h-auto px-3 py-2 break-all whitespace-normal max-sm:px-0 max-sm:pb-2 max-sm:text-base"
                >
                  {invite.email}
                  {invite.created_by_name ? (
                    <span className="block text-xs font-normal text-muted-foreground">
                      by {invite.created_by_name}
                    </span>
                  ) : null}
                </TableHead>
                <TableCell {...cell("Kind")}>{KIND[invite.kind]}</TableCell>
                <TableCell {...cell("Role")}>
                  {invite.role ? (
                    ROLE[invite.role]
                  ) : (
                    <span className="text-muted-foreground">Unchanged</span>
                  )}
                </TableCell>
                <TableCell {...cell("Status")}>
                  <Badge variant="outline" className={status.className}>
                    {status.label}
                  </Badge>
                </TableCell>
                <TableCell {...cell("Expires")}>
                  <When invite={invite} now={now} />
                </TableCell>
                <TableCell className={ACTIONS}>
                  {invite.status === "active" ? (
                    <Button
                      variant="outline"
                      size="sm"
                      className="max-sm:w-full"
                      onClick={() => {
                        onRevoke(invite);
                      }}
                      aria-label={`Revoke the link for ${invite.email}`}
                    >
                      Revoke
                    </Button>
                  ) : null}
                </TableCell>
              </TableRow>
            );
          })}
        </TableBody>
      </Table>
    </div>
  );
}

/** When it expires while active; once used or revoked, when that happened instead. */
function When({ invite, now }: { invite: AdminInvite; now: Date }) {
  const ended =
    invite.status === "used"
      ? { label: "Used", at: invite.used_at }
      : invite.status === "revoked"
        ? { label: "Revoked", at: invite.revoked_at }
        : null;
  if (ended?.at)
    return (
      <span className="text-muted-foreground">
        {ended.label}{" "}
        <time dateTime={ended.at} title={exactTime(ended.at)}>
          {relativeTime(ended.at, now)}
        </time>
      </span>
    );
  return (
    <time
      dateTime={invite.expires_at}
      title={exactTime(invite.expires_at)}
      className={invite.status === "expired" ? "text-muted-foreground" : ""}
    >
      {relativeTime(invite.expires_at, now)}
    </time>
  );
}

function RevokeDialog({
  invite,
  onClose,
}: {
  invite: AdminInvite | null;
  onClose: () => void;
}) {
  const client = useQueryClient();
  const [error, setError] = useState<string>();
  const mutation = useMutation({
    mutationFn: (id: string) => revokeInvite(id),
    onMutate: () => {
      setError(undefined);
    },
    onSuccess: () => {
      toast.success("Link revoked. It no longer works.");
      void client.invalidateQueries({ queryKey: invitesKey });
      onClose();
    },
    onError: (failure) => {
      if (isApiError(failure) && failure.status === 404) {
        toast.error("This link was already used, revoked or expired.");
        void client.invalidateQueries({ queryKey: invitesKey });
        onClose();
        return;
      }
      setError(message(failure));
    },
  });

  return (
    <AlertDialog
      open={invite !== null}
      onOpenChange={(open) => {
        if (!open && !mutation.isPending) {
          setError(undefined);
          onClose();
        }
      }}
    >
      <AlertDialogContent>
        <AlertDialogHeader>
          <AlertDialogTitle>Revoke this link?</AlertDialogTitle>
          <AlertDialogDescription>
            The {invite?.kind === "reset" ? "reset" : "invite"} link for{" "}
            <span className="font-medium break-all text-foreground">
              {invite?.email}
            </span>{" "}
            stops working at once. You can create a new one later.
          </AlertDialogDescription>
        </AlertDialogHeader>
        {error ? (
          <Alert variant="destructive">
            <CircleAlert aria-hidden="true" />
            <AlertDescription>{error}</AlertDescription>
          </Alert>
        ) : null}
        <AlertDialogFooter>
          <AlertDialogCancel disabled={mutation.isPending}>
            Cancel
          </AlertDialogCancel>
          <Button
            variant="destructive"
            disabled={mutation.isPending}
            aria-busy={mutation.isPending}
            onClick={() => {
              if (invite) mutation.mutate(invite.id);
            }}
          >
            {mutation.isPending ? (
              <>
                <Spinner aria-hidden="true" role={undefined} />
                Revoking
              </>
            ) : (
              "Revoke link"
            )}
          </Button>
        </AlertDialogFooter>
      </AlertDialogContent>
    </AlertDialog>
  );
}
