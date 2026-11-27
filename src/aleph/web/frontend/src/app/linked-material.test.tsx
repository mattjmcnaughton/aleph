import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE, type AuthSession } from "../lib/api";
import { learnerUser } from "../mocks/handlers";
import { createPathBodies } from "../mocks/paths";
import { server } from "../mocks/server";
import { App } from "./app";

// Linked Material on /new (docs/tdds/linked-material.md), through the real
// router, TanStack Query and MSW. The server reads the Material at create; the
// client's job is to send it with its mode and to show a failed read on the
// item that failed.

function useMaterialFlag(enabled: boolean): void {
  const session: AuthSession = {
    authenticated: true,
    provider: "keycloak",
    user: { ...learnerUser, feature_flags: { material: enabled } },
  };
  server.use(http.get(`${API_V1_BASE}/auth/session`, () => HttpResponse.json(session)));
}

async function gotoForm() {
  window.history.pushState({}, "", "/new");
  render(<App />);
  fireEvent.click(await screen.findByRole("button", { name: "I'll fill in the form myself" }));
  return (await screen.findByRole("textbox", { name: /topic/i })) as HTMLInputElement;
}

function addLink(url: string) {
  fireEvent.change(screen.getByRole("textbox", { name: /link to material/i }), {
    target: { value: url },
  });
  fireEvent.click(screen.getByRole("button", { name: "Add" }));
}

describe("Linked Material — /new", () => {
  it("is hidden while the material flag is off", async () => {
    useMaterialFlag(false);
    await gotoForm();
    expect(screen.queryByTestId("material-field")).toBeNull();
  });

  it("sends links and a pasted summary with the chosen mode", async () => {
    useMaterialFlag(true);
    const topic = await gotoForm();
    fireEvent.change(topic, { target: { value: "Warp factories" } });

    addLink("https://docs.warp.dev/factories");
    fireEvent.click(screen.getByRole("button", { name: "Paste an AI summary" }));
    fireEvent.change(screen.getByRole("textbox", { name: /^summary/i }), {
      target: { value: "word ".repeat(60) },
    });
    fireEvent.click(screen.getByRole("button", { name: "Add summary" }));
    fireEvent.click(screen.getByRole("radio", { name: /start from this/i }));
    fireEvent.click(screen.getByRole("button", { name: /build my path/i }));

    await waitFor(() => expect(createPathBodies()).toHaveLength(1));
    expect(createPathBodies()[0]).toMatchObject({
      topic: "Warp factories",
      material_mode: "start",
      material: [
        // A docs host guesses "read the pages under this link".
        { kind: "docs", url: "https://docs.warp.dev/factories" },
        { kind: "ai_summary", text: "word ".repeat(60).trim() },
      ],
    });
  });

  it("sends a bare arXiv id and an arXiv PDF link as papers", async () => {
    useMaterialFlag(true);
    const topic = await gotoForm();
    fireEvent.change(topic, { target: { value: "Transformers" } });

    addLink("1706.03762");
    addLink("https://arxiv.org/pdf/2106.09685v2");
    expect(screen.getAllByTestId("material-item")[0].textContent).toContain("Paper");
    fireEvent.click(screen.getByRole("button", { name: /build my path/i }));

    await waitFor(() => expect(createPathBodies()).toHaveLength(1));
    expect(createPathBodies()[0].material).toEqual([
      { kind: "arxiv", url: "https://arxiv.org/abs/1706.03762" },
      { kind: "arxiv", url: "https://arxiv.org/pdf/2106.09685v2" },
    ]);
  });

  it("opens a transcript box for a video link instead of adding it", async () => {
    useMaterialFlag(true);
    await gotoForm();
    addLink("https://www.youtube.com/watch?v=abc");
    const paste = await screen.findByTestId("material-paste");
    expect(paste.textContent).toMatch(/Paste the video's transcript/);
    expect(screen.queryByTestId("material-item")).toBeNull();
  });

  it("shows a failed read on the item that failed, and keeps the others", async () => {
    useMaterialFlag(true);
    server.use(
      http.post(`${API_V1_BASE}/paths`, () =>
        HttpResponse.json(
          {
            error: {
              code: "validation_error",
              message: "Some Material could not be read.",
              details: {
                reason: "material_unreadable",
                items: [{ index: 1, reason: "empty" }],
              },
            },
          },
          { status: 422 },
        ),
      ),
    );
    const topic = await gotoForm();
    fireEvent.change(topic, { target: { value: "Delivery" } });
    addLink("https://ok.example.com/post");
    addLink("https://paywalled.example.com/story");
    fireEvent.click(screen.getByRole("button", { name: /build my path/i }));

    const items = await waitFor(() => {
      const found = screen.getAllByTestId("material-item");
      expect(found[1].getAttribute("aria-invalid")).toBe("true");
      return found;
    });
    expect(items[0].getAttribute("aria-invalid")).toBeNull();
    expect(within(items[1]).getByRole("alert").textContent).toMatch(/paywall/);
    // Not the generic "something went wrong" surface.
    expect(screen.queryByTestId("onboarding-error")).toBeNull();
  });
});
