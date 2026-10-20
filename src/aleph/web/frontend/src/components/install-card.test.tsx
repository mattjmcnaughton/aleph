import "@testing-library/jest-dom/vitest";
import { StrictMode } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, expect, it, vi } from "vitest";

beforeEach(() => {
  vi.resetModules();
  localStorage.clear();
  vi.spyOn(navigator, "userAgent", "get").mockReturnValue(
    "iPhone Version/26.0 Mobile Safari/604.1",
  );
});

it("dismisses for 30 days and does not reappear on route remount, even with failed storage", async () => {
  const { InstallCard } = await import("./install-card");
  vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
    throw new Error("blocked");
  });
  const view = render(
    <StrictMode>
      <InstallCard />
    </StrictMode>,
  );
  expect(screen.getByRole("region", { name: "Add aleph to your Home Screen" })).toBeVisible();
  fireEvent.click(screen.getByRole("button", { name: "Not now" }));
  expect(screen.queryByRole("region")).not.toBeInTheDocument();
  view.unmount();
  render(<InstallCard />);
  expect(screen.queryByRole("region")).not.toBeInTheDocument();
});

it("writes the dismissal timestamp", async () => {
  const { InstallCard } = await import("./install-card");
  vi.spyOn(Date, "now").mockReturnValue(123456789);
  render(<InstallCard />);
  fireEvent.click(screen.getByRole("button", { name: "Not now" }));
  expect(localStorage.getItem("aleph.install.dismissedAt")).toBe("123456789");
});

it.each(["invalid", "", null])(
  "unreadable dismissal %s leaves instructions usable",
  async (value) => {
    const { InstallCard } = await import("./install-card");
    if (value !== null) localStorage.setItem("aleph.install.dismissedAt", value);
    else
      vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
        throw new Error("blocked");
      });
    render(<InstallCard />);
    expect(
      screen.getByText("Reminders and other Notifications only work from there."),
    ).toBeVisible();
  },
);

it("does not show again on route return even without dismissal", async () => {
  const { InstallCard } = await import("./install-card");
  const view = render(<InstallCard />);
  expect(screen.getByRole("region")).toBeVisible();
  view.unmount();
  render(<InstallCard />);
  expect(screen.queryByRole("region")).not.toBeInTheDocument();
});

it("honors a persisted recent dismissal on a new visit", async () => {
  const { InstallCard } = await import("./install-card");
  localStorage.setItem("aleph.install.dismissedAt", String(Date.now()));
  render(<InstallCard />);
  expect(screen.queryByRole("region")).not.toBeInTheDocument();
});
