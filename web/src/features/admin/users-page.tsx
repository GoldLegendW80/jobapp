import { useTitle } from "@/lib/use-title";

/** Placeholder until the admin users task (I5): the route and its guard are real already. */
export function AdminUsersPage() {
  useTitle("Users");
  return (
    <section
      aria-labelledby="users-title"
      className="mx-auto flex max-w-2xl flex-col gap-2"
    >
      <h1 id="users-title" className="text-2xl font-semibold tracking-tight">
        Users
      </h1>
      <p className="text-muted-foreground">
        The list of users and invites comes here next.
      </p>
    </section>
  );
}
