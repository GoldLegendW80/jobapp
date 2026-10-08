import { useMe } from "@/features/auth/auth-context";
import { HealthCard } from "@/features/health/health-card";
import { useTitle } from "@/lib/use-title";

export function HomePage() {
  const { data: user } = useMe();
  useTitle("Home");
  return (
    <div className="mx-auto flex max-w-md flex-col items-center gap-8">
      <div className="flex flex-col items-center gap-2 text-center">
        <h1 className="text-3xl font-semibold tracking-tight break-words">
          {user ? `Welcome, ${user.name}` : "Welcome"}
        </h1>
        <p className="text-muted-foreground">
          Find postings, tailor documents, apply.
        </p>
      </div>
      <HealthCard />
    </div>
  );
}
