import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { HttpResponse, http } from "msw";
import { describe, expect, it } from "vitest";
import { API_V1_BASE } from "../lib/api";
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
  it.each(["empty", "needs_context", "unavailable", "failed"] as const)(
    "distinguishes %s",
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
      await screen.findByTestId(`learning-${kind}`);
      expect(fake.requests).toHaveLength(0);
    },
  );
  it("keeps prior cards on failed refresh and persists dismissal/Undo across navigation", async () => {
    enabled();
    const fake = learningFake();
    fake.state.recommendations = [learningCard()];
    fake.state.latest_refresh = learningReceipt(refreshRequest, "failed");
    server.use(...fake.handlers);
    render(<App />);
    await screen.findByText(/Refresh failed. Your previous recommendations/);
    fireEvent.click(screen.getByRole("button", { name: "Not interested" }));
    await screen.findByTestId("learning-dismissed");
    expect(screen.getByRole("button", { name: "Undo" })).toHaveFocus();
    fireEvent.click(screen.getByRole("link", { name: "Tutor" }));
    await screen.findByTestId("learning-dismissed");
    fireEvent.click(screen.getByText("Dismissed recommendations", { selector: "summary" }));
    fireEvent.click(screen.getByRole("button", { name: /Restore Electricity/ }));
    await screen.findByTestId("learning-recommendation");
    expect(fake.state.recommendations).toHaveLength(1);
  });
  it("renders unsafe Markdown inertly and hides Beat actions with analyst off", async () => {
    enabled(false);
    const fake = learningFake();
    const turn = learningReceipt({ ...refreshRequest, kind: "turn", content: "Interest" });
    if (turn.output) {
      turn.output.reply = "<script>alert(1)</script> [bad](javascript:alert(1))";
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
