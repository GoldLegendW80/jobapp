import { useMutation, useQueryClient } from "@tanstack/react-query";
import { toast } from "sonner";

import { isApiError } from "@/lib/api";

import { type AdminUser, updateUser, usersKey } from "./admin-api";
import { message } from "../helpers/errors";
import { displayName } from "../helpers/user-actions";

/** Saves an account's browsers at once as soon as the admin picks it, with a toast either way. */
export function useUserBrowsers() {
  const client = useQueryClient();
  const mutation = useMutation({
    mutationFn: ({ user, browsers }: { user: AdminUser; browsers: number }) =>
      updateUser(user.id, { browsers }),
    onSuccess: async (saved) => {
      toast.success(
        `${displayName(saved)} now has ${String(saved.browsers)} ${saved.browsers === 1 ? "browser" : "browsers"} at once.`,
      );
      await client.invalidateQueries({ queryKey: usersKey });
    },
    onError: (failure) => {
      void client.invalidateQueries({ queryKey: usersKey });
      toast.error(
        isApiError(failure) && failure.status === 404
          ? "This account no longer exists."
          : message(failure),
      );
    },
  });
  return {
    set: (user: AdminUser, browsers: number) => {
      mutation.mutate({ user, browsers });
    },
    saving: mutation.isPending ? mutation.variables.user.id : undefined,
  };
}
