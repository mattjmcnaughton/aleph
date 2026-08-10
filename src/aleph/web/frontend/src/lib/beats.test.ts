import { describe, expect, it } from "vitest";
import {
  ANCHOR_WEEKDAYS,
  DEFAULT_ANCHOR_WEEKDAY,
  cadenceLabel,
  formatBriefDate,
  formatElapsed,
} from "./beats";

// Direct coverage for the analyst surfaces' display helpers (code-review
// FIX 5 on AL-530), modelled on `lib/onboarding.test.ts`. Before this file,
// none of `lib/beats.ts`'s 85 lines had a single direct test:
// `formatElapsed`'s minute/hour branches, `formatBriefDate`'s
// year-suppression branch, and the west-of-UTC guard `localDateFromISO`
// exists for (its own docstring's whole justification) were all unreached —
// swap `localDateFromISO`'s body for a bare `new Date(iso)` and this file is
// the one that would have caught it.

describe("ANCHOR_WEEKDAYS", () => {
  it("offers all seven days, Python's Monday==0 convention, in display order", () => {
    expect(ANCHOR_WEEKDAYS.map((d) => d.value)).toEqual([0, 1, 2, 3, 4, 5, 6]);
    expect(ANCHOR_WEEKDAYS.map((d) => d.label)).toEqual([
      "Monday",
      "Tuesday",
      "Wednesday",
      "Thursday",
      "Friday",
      "Saturday",
      "Sunday",
    ]);
  });

  it("DEFAULT_ANCHOR_WEEKDAY is Monday — the PRD's own running example", () => {
    expect(DEFAULT_ANCHOR_WEEKDAY).toBe(0);
  });
});

describe("cadenceLabel", () => {
  it("capitalises the only Cadence this slice ships", () => {
    expect(cadenceLabel("weekly")).toBe("Weekly");
  });
});

describe("formatBriefDate", () => {
  it("renders month + day, no year, for a date within the current year", () => {
    const now = new Date(2026, 7, 10); // 2026-08-10
    expect(formatBriefDate("2026-08-03", now)).toBe("Aug 3");
  });

  it("[year-suppression branch] includes the year once the date is not this year", () => {
    const now = new Date(2026, 7, 10); // 2026-08-10
    expect(formatBriefDate("2025-12-27", now)).toBe("Dec 27, 2025");
  });

  it("[the west-of-UTC pin] does not shift the calendar day for a learner west of UTC", () => {
    // `localDateFromISO` parses `YYYY-MM-DD` via the *local* Date
    // constructor overload specifically so this never happens (its own
    // docstring). A bare `new Date("2026-01-01")` parses as UTC midnight,
    // which in a zone west of UTC (e.g. Los Angeles, UTC-8) is still
    // 2025-12-31 in the evening — exactly the shift this pins against.
    const originalTz = process.env.TZ;
    process.env.TZ = "America/Los_Angeles";
    try {
      const now = new Date(2026, 0, 15); // 2026-01-15, same year — isolates the day, not the year branch
      expect(formatBriefDate("2026-01-01", now)).toBe("Jan 1");
    } finally {
      process.env.TZ = originalTz;
    }
  });

  it("[the west-of-UTC pin, year boundary] does not shift a year-end date into next year", () => {
    const originalTz = process.env.TZ;
    process.env.TZ = "Pacific/Honolulu"; // UTC-10, further west than Los Angeles
    try {
      const now = new Date(2025, 11, 31); // 2025-12-31, same year as the Brief
      expect(formatBriefDate("2025-12-31", now)).toBe("Dec 31");
    } finally {
      process.env.TZ = originalTz;
    }
  });
});

describe("formatElapsed", () => {
  const now = new Date(2026, 7, 10, 12, 0, 0);

  it("[seconds branch] reads 'Ns ago' under a minute", () => {
    expect(formatElapsed(new Date(now.getTime() - 0).toISOString(), now)).toBe("0s ago");
    expect(formatElapsed(new Date(now.getTime() - 45_000).toISOString(), now)).toBe("45s ago");
  });

  it("clamps a startedAt in the future to '0s ago' rather than going negative", () => {
    expect(formatElapsed(new Date(now.getTime() + 5_000).toISOString(), now)).toBe("0s ago");
  });

  it("[minutes branch] reads 'Nm ago' from a minute up to an hour", () => {
    expect(formatElapsed(new Date(now.getTime() - 60_000).toISOString(), now)).toBe("1m ago");
    expect(formatElapsed(new Date(now.getTime() - 90_000).toISOString(), now)).toBe("2m ago");
    expect(formatElapsed(new Date(now.getTime() - 40 * 60_000).toISOString(), now)).toBe("40m ago");
  });

  it("[hours branch] reads 'Nh ago' from an hour onward", () => {
    expect(formatElapsed(new Date(now.getTime() - 3600_000).toISOString(), now)).toBe("1h ago");
    expect(formatElapsed(new Date(now.getTime() - 7200_000).toISOString(), now)).toBe("2h ago");
    // Just past the minutes ceiling: 61 real minutes still rounds to 1h, not
    // 61m — proving the branch boundary is on `minutes < 60`, not seconds.
    expect(formatElapsed(new Date(now.getTime() - 61 * 60_000).toISOString(), now)).toBe("1h ago");
  });
});
