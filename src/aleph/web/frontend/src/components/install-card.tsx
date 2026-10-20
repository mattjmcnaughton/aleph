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
      className="mt-6 rounded-lg border border-divider bg-surface p-5"
    >
      <div className="flex items-start gap-4">
        <AlephGlyph size="md" />
        <div className="min-w-0">
          <h2 className="text-lg font-semibold">Add aleph to your Home Screen</h2>
          <p className="mt-2 text-sm leading-6 text-porcelain">
            Tap <strong>Share</strong>, then <strong>Add to Home Screen</strong>.
          </p>
          <p className="mt-2 text-sm leading-6 text-mist">
            Reminders and other Notifications only work from there.
          </p>
          <button
            type="button"
            className="mt-3 min-h-11 rounded-md px-3 text-sm text-mist hover:text-porcelain focus-visible:outline focus-visible:outline-2 focus-visible:outline-teal"
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
      </div>
    </section>
  );
}
