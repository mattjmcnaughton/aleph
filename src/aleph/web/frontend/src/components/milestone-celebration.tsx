import type { Milestone } from "../lib/api";
import "./milestone-celebration.css";

/** One quiet constellation for all the learning-day milestones earned together. */
export function MilestoneCelebration({ milestones }: { milestones: Milestone[] }) {
  const days = Math.max(...milestones.map((milestone) => milestone.days));
  const count = days === 100 ? 14 : days === 500 ? 26 : 40;
  let distance = 0;
  const points: { x: number; y: number; distance: number }[] = [];
  for (let i = 0; i < count; i++) {
    const x = 18 + (i * 194) / (count - 1);
    const y = 56 + Math.sin(i * 0.8) * 28;
    if (i > 0) distance += Math.hypot(x - points[i - 1].x, y - points[i - 1].y);
    points.push({ x, y, distance });
  }
  const path = points
    .map((p, i) => `${i ? "L" : "M"}${p.x.toFixed(2)} ${p.y.toFixed(2)}`)
    .join(" ");
  const last = points[points.length - 1];

  return (
    <section
      data-testid="milestone-celebration"
      aria-live="polite"
      className="milestone mb-4 overflow-hidden rounded-lg border border-teal/40 bg-teal/10 p-5 text-center shadow-sm"
    >
      <p className="kicker">Milestone reached</p>
      <svg
        viewBox="0 0 230 112"
        aria-hidden="true"
        className="mx-auto h-28 w-[230px] max-w-full text-teal"
      >
        <path d={path} fill="none" stroke="currentColor" opacity=".12" />
        <path
          className="milestone-connection"
          d={path}
          pathLength="1"
          fill="none"
          stroke="currentColor"
          opacity=".65"
        />
        {points.map((point, i) => (
          <circle
            key={point.x}
            className="milestone-star"
            style={{ animationDelay: `${(point.distance / distance) * 2.4}s` }}
            cx={point.x}
            cy={point.y}
            r={i === count - 1 ? 3.2 : 2}
            fill="currentColor"
          />
        ))}
        <circle className="milestone-traveler" r="2.5" style={{ offsetPath: `path('${path}')` }} />
        <circle
          className="milestone-ripple"
          cx={last.x}
          cy={last.y}
          r="7"
          fill="none"
          stroke="currentColor"
        />
      </svg>
      <p
        aria-hidden="true"
        className="milestone-number text-[56px] font-semibold leading-none tracking-tight tabular-nums text-porcelain"
      >
        {days.toLocaleString()}
      </p>
      <h2 className="mt-3 text-lg font-semibold text-porcelain">
        {milestones.map((milestone) => (
          <span key={milestone.metric} className="block">
            {milestone.metric === "daily_streak"
              ? `${milestone.days.toLocaleString()}-day Daily streak`
              : `${milestone.days.toLocaleString()} total Active days`}
          </span>
        ))}
      </h2>
      <p className="mt-2 text-sm leading-6 text-mist">Every day you came back added up.</p>
    </section>
  );
}
