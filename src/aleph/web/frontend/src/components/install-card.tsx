import { useEffect, useState } from "react";
import { isStandalone, shouldShowInstallCard } from "../lib/install";
import { AlephGlyph } from "./aleph-logo";

const DISMISSED_KEY = "aleph.install.dismissedAt";
// A visit is one loaded document, including route changes. Kept in memory so
// blocked storage cannot cause repeated cards, and a new visit can try again.
let shownThisVisit = false;

export function InstallCard() {
  const [visible, setVisible] = useState(false);
  useEffect(() => {
    if (shownThisVisit) return;
    let dismissedAt: number | null = null;
    try {
      const stored = localStorage.getItem(DISMISSED_KEY);
      dismissedAt = stored === null || stored.trim() === "" ? null : Number(stored);
    } catch {
      // Storage is optional; instructions still work without it.
    }
    if (
      shouldShowInstallCard({
        userAgent: navigator.userAgent,
        standalone: isStandalone(),
        dismissedAt,
        now: Date.now(),
      })
    ) {
      shownThisVisit = true;
      setVisible(true);
    }
  }, []);
  if (!visible) return null;
  return (
    <section
      aria-label="Add aleph to your Home Screen"
      className="mt-5 flex items-start gap-3.5 rounded-lg border border-divider p-4"
    >
      <AlephGlyph size="sm" />
      <div className="min-w-0">
        <h2 className="text-[15px] font-semibold leading-6">Add aleph to your Home Screen</h2>
        <p className="mt-0.5 text-sm leading-6 text-mist">
          Tap <strong className="font-semibold text-porcelain">Share</strong>, then{" "}
          <strong className="font-semibold text-porcelain">Add to Home Screen</strong>.
        </p>
        <p className="text-sm leading-6 text-mist">
          Reminders and other Notifications only work from there.
        </p>
        <button
          type="button"
          className="-ml-3 mt-1 min-h-11 rounded-md px-3 text-sm font-semibold text-porcelain hover:text-teal-bright"
          onClick={() => {
            setVisible(false);
            try {
              localStorage.setItem(DISMISSED_KEY, String(Date.now()));
            } catch {
              /* Hidden for this visit even if storage is blocked. */
            }
          }}
        >
          Not now
        </button>
      </div>
    </section>
  );
}
