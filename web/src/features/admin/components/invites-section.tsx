import { CircleAlert, MailPlus } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { EmptyState } from "@/components/blocks/empty-state";
import { LoadError } from "@/components/blocks/load-error";
import { LoadingCard } from "@/components/blocks/loading-card";
import { Section } from "@/components/blocks/section";
import { Moment } from "@/components/shared/admin/moment";
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

import {
  INVITES_PAGE_SIZE,
  type AdminInvite,
  type InviteStatus,
} from "../api/admin-api";
import { useInvites, useRevokeInvite } from "../api/use-invites";
import { message } from "../helpers/errors";
import { InviteDialog } from "./invite-dialog";
import { ListPager } from "./list-pager";

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
  const invites = useInvites(offset);
  const now = new Date();

  return (
    <Section
      title="Invites"
      description="One-time links to create an account or reset a password."
      actions={<InviteDialog />}
    >
      {invites.isPending ? (
        <LoadingCard>Loading invites</LoadingCard>
      ) : invites.isError ? (
        <LoadError query={invites}>The invites could not be loaded.</LoadError>
      ) : invites.data.total === 0 ? (
        <EmptyState
          icon={MailPlus}
          title="No invites yet"
          description="Invite someone to give them access. Their link appears here so you can follow it and revoke it."
        />
      ) : (
        <>
          <InvitesTable
            items={invites.data.items}
            now={now}
            onRevoke={setRevoking}
            busy={invites.isPlaceholderData}
          />
          {invites.data.total > INVITES_PAGE_SIZE ? (
            <ListPager
              label="Invites pages"
              pageSize={INVITES_PAGE_SIZE}
              offset={invites.data.offset}
              shown={invites.data.items.length}
              total={invites.data.total}
              onOffset={setOffset}
            />
          ) : null}
        </>
      )}

      <RevokeDialog
        invite={revoking}
        onClose={() => {
          setRevoking(null);
        }}
      />
    </Section>
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
    <Table busy={busy}>
      <TableCaption className="sr-only">Invites, newest first</TableCaption>
      <TableHeader>
        <TableRow>
          <TableHead>Email</TableHead>
          <TableHead>Kind</TableHead>
          <TableHead>Role</TableHead>
          <TableHead>Status</TableHead>
          <TableHead>Expires</TableHead>
          <TableHead className="text-right">
            <span className="sr-only">Actions</span>
          </TableHead>
        </TableRow>
      </TableHeader>
      <TableBody>
        {items.map((invite) => {
          const status = STATUS[invite.status];
          return (
            <TableRow key={invite.id}>
              <TableHead className="break-all">
                {invite.email}
                {invite.created_by_name ? (
                  <span className="block text-xs font-normal text-muted-foreground">
                    by {invite.created_by_name}
                  </span>
                ) : null}
              </TableHead>
              <TableCell label="Kind">{KIND[invite.kind]}</TableCell>
              <TableCell label="Role">
                {invite.role ? (
                  ROLE[invite.role]
                ) : (
                  <span className="text-muted-foreground">Unchanged</span>
                )}
              </TableCell>
              <TableCell label="Status">
                <Badge variant="outline" className={status.className}>
                  {status.label}
                </Badge>
              </TableCell>
              <TableCell label="Expires">
                <When invite={invite} now={now} />
              </TableCell>
              <TableCell className="text-right max-sm:pt-2 max-sm:text-left">
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
        {ended.label} <Moment at={ended.at} now={now} />
      </span>
    );
  return (
    <Moment
      at={invite.expires_at}
      now={now}
      className={invite.status === "expired" ? "text-muted-foreground" : ""}
    />
  );
}

function RevokeDialog({
  invite,
  onClose,
}: {
  invite: AdminInvite | null;
  onClose: () => void;
}) {
  const [error, setError] = useState<string>();
  const mutation = useRevokeInvite({
    onMutate: () => {
      setError(undefined);
    },
    onSuccess: () => {
      toast.success("Link revoked. It no longer works.");
      onClose();
    },
    onError: (failure) => {
      if (isApiError(failure) && failure.status === 404) {
        toast.error("This link was already used, revoked or expired.");
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
