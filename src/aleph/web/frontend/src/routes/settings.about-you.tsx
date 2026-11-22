import { createFileRoute } from "@tanstack/react-router";
import { AboutYouForm } from "../components/about-you-form";
import { Breadcrumbs } from "../components/breadcrumbs";
import { Workspace } from "../components/workspace";

export const Route = createFileRoute("/settings/about-you")({ component: AboutYouPage });

function AboutYouPage() {
  return (
    <Workspace testid="about-you-settings" width="lesson">
      <Breadcrumbs current="About you" />
      <p className="kicker">Account</p>
      <h1 className="mt-2 page-title">About you</h1>
      <p className="mb-6 mt-3 text-base leading-6 text-mist">
        Aleph reads this before it drafts a path, writes a lesson, or answers in the tutor. It
        applies to every path.
      </p>
      <AboutYouForm />
    </Workspace>
  );
}
