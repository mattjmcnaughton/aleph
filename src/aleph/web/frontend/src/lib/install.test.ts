import { afterEach, describe, expect, it, vi } from "vitest";
import { installState, isStandalone, shouldShowInstallCard } from "./install";

const safari =
  "Mozilla/5.0 (iPhone; CPU iPhone OS 26_0 like Mac OS X) AppleWebKit/605.1.15 Version/26.0 Mobile/15E148 Safari/604.1";
const now = 2_000_000_000_000;
const month = 30 * 24 * 60 * 60 * 1000;
const context = { userAgent: safari, standalone: false, dismissedAt: null, now };

describe("standalone detection", () => {
  afterEach(() => vi.unstubAllGlobals());

  it.each([
    [false, false, false],
    [true, false, true],
    [false, true, true],
  ])("media query %s, Safari standalone %s", (matches, standalone, expected) => {
    vi.spyOn(window, "matchMedia").mockReturnValue({ matches } as MediaQueryList);
    vi.stubGlobal("navigator", { standalone });
    expect(isStandalone()).toBe(expected);
  });
});

describe("install card", () => {
  it.each([
    [safari, true],
    [safari.replace("iPhone", "iPad"), false],
    ["Mozilla/5.0 Chrome/130 Safari/537.36", false],
    ...["CriOS", "FxiOS", "EdgiOS", "OPiOS"].map((browser) => [`${safari} ${browser}/123`, false]),
    ["iPhone embedded app", false],
  ])("browser eligibility %s", (userAgent, expected) => {
    expect(shouldShowInstallCard({ ...context, userAgent: String(userAgent) })).toBe(expected);
  });
  it.each([
    [null, true],
    [now - month - 1, true],
    [now - month, true],
    [now - month + 1, false],
    [now, false],
    [now + 1000, false],
    [Number.NaN, true],
  ])("dismissal boundary %s", (dismissedAt, expected) => {
    expect(shouldShowInstallCard({ ...context, dismissedAt })).toBe(expected);
  });
  it("never shows in standalone", () => {
    expect(shouldShowInstallCard({ ...context, standalone: true })).toBe(false);
  });
  it("distinguishes Notification installation states", () => {
    expect(installState({ ...context, standalone: true, pushSupported: true })).toBe("home_screen");
    expect(installState({ ...context, pushSupported: false })).toBe("needs_home_screen");
    expect(installState({ ...context, userAgent: "Chrome", pushSupported: true })).toBe(
      "browser_ok",
    );
    expect(installState({ ...context, standalone: true, pushSupported: false })).toBe(
      "unsupported",
    );
  });
});
