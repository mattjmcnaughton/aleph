import { useQuery } from "@tanstack/react-query";
import { createFileRoute } from "@tanstack/react-router";
import { Breadcrumbs } from "../components/breadcrumbs";
import { NotificationSettings } from "../components/notification-settings";
import { Workspace } from "../components/workspace";
import { sessionQueryOptions } from "../lib/auth";

export const Route = createFileRoute("/settings/notifications")({ component: Settings });

function Settings() {
  const session = useQuery(sessionQueryOptions).data;
  const account = session?.authenticated ? session.user?.id : undefined;
  return (
    <Workspace testid="notification-settings" width="lesson">
      <Breadcrumbs current="Notifications" />
      <h1 className="mb-6 text-2xl font-semibold">Notifications</h1>
      {account ? <NotificationSettings key={account} account={account} /> : null}
    </Workspace>
  );
}
