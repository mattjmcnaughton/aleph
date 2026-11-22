import { Link } from "@tanstack/react-router";

/** Home's door into the Learning summary, styled like `FlowDoor`. */
export function LearningDoor() {
  return (
    <Link
      to="/learning"
      data-testid="learning-door"
      className="mt-1 flex min-h-14 items-center gap-3 rounded-lg px-5 py-3 text-porcelain no-underline transition-colors hover:bg-surface lg:px-6"
    >
      <div className="min-w-0 flex-1">
        <p className="text-[15px] font-semibold">Your learning</p>
        <p className="mt-0.5 text-sm leading-5 text-mist">
          Where you've got to, and what to learn next.
        </p>
      </div>
      <svg
        viewBox="0 0 16 16"
        width="14"
        height="14"
        fill="none"
        aria-hidden="true"
        className="shrink-0 text-slate"
      >
        <path
          d="M6 3.5L10.5 8 6 12.5"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    </Link>
  );
}
