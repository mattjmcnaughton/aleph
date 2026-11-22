import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ABOUT_YOU_QUERY_KEY, getAboutYou } from "../lib/api";

/**
 * On New path: says that the learner's About you (docs/CONTEXT.md) applies to
 * this path too, so they stop retyping their background into Guidance.
 * Renders nothing until the read settles; a failed read hides it.
 */
export function AboutYouApplies() {
  const query = useQuery({ queryKey: ABOUT_YOU_QUERY_KEY, queryFn: getAboutYou });
  if (!query.data) return null;
  const text = query.data.about_you;
  if (!text) {
    return (
      <p data-testid="about-you-applies" className="mt-4 text-sm leading-6 text-mist">
        Tell Aleph about yourself once, in{" "}
        <Link to="/settings/about-you" className="text-teal-bright hover:underline">
          About you
        </Link>
        , instead of on every path.
      </p>
    );
  }
  return (
    <div data-testid="about-you-applies" className="mt-6 border-y border-divider py-3">
      <div className="flex items-baseline justify-between gap-3">
        <span className="text-sm font-medium text-porcelain">About you applies</span>
        <Link to="/settings/about-you" className="text-sm text-teal-bright hover:underline">
          Edit
        </Link>
      </div>
      <p className="mt-1 line-clamp-2 text-sm leading-6 text-mist">{text}</p>
    </div>
  );
}
