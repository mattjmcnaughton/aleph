import { describe, expect, it } from "vitest";
import { authRedirect } from "./auth";
import type { AuthSession } from "./api";
import { fillEmptyFields, isPublicPathname, parseFrom, publicHandleOf } from "./public-profiles";
import { safeSourceHref } from "../components/brief-sources";

const ID = "5b0f4c1e-1a2b-4c3d-8e9f-0a1b2c3d4e5f";
const signedOut: AuthSession = { authenticated: false, provider: "keycloak", user: null };

describe("public route shapes", () => {
  const publicShapes = [
    "/@matt",
    `/@matt/paths/${ID}`,
    `/@matt/beats/${ID}`,
    `/@matt/beats/${ID}/briefs/${ID}`,
    "/@learner-42",
  ];
  const privateShapes = [
    "/",
    "/new",
    `/paths/${ID}`,
    "/@ma",
    "/@Matt",
    "/@matt/",
    "/@matt/settings",
    `/@matt/paths/${ID}/lessons`,
    "/@matt/paths/not-a-uuid",
    `/@matt/briefs/${ID}`,
    `/@matt/beats/${ID}/briefs`,
    "/@@matt",
    `/x/@matt/paths/${ID}`,
  ];

  it.each(publicShapes)("lets %s through signed out", (pathname) => {
    expect(isPublicPathname(pathname)).toBe(true);
    expect(authRedirect(signedOut, pathname)).toBeNull();
  });

  it.each(privateShapes)("sends %s to sign in", (pathname) => {
    expect(isPublicPathname(pathname)).toBe(false);
    expect(authRedirect(signedOut, pathname)).toEqual({ to: "/login" });
  });

  it("names the handle of a public page", () => {
    expect(publicHandleOf(`/@matt/paths/${ID}`)).toBe("matt");
    expect(publicHandleOf("/new")).toBeNull();
  });
});

describe("parseFrom", () => {
  it("reads <handle>/<uuid>", () => {
    expect(parseFrom(`matt/${ID}`)).toEqual({ handle: "matt", id: ID });
  });

  it.each([undefined, 42, "", "matt", `matt/${ID}/x`, "matt/nope", `M!/${ID}`, `ab/${ID}`])(
    "rejects %s",
    (value) => {
      expect(parseFrom(value)).toBeNull();
    },
  );
});

describe("fillEmptyFields", () => {
  const source = { topic: "Rust", guidance: "I write Go.", teaching_style: "Short." };

  it("fills only fields that are still empty", () => {
    expect(
      fillEmptyFields({ topic: "My own topic", guidance: "  ", teaching_style: "" }, source),
    ).toEqual({
      guidance: "I write Go.",
      teaching_style: "Short.",
    });
  });

  it("never fills from a missing source value", () => {
    expect(fillEmptyFields({ topic: "", guidance: "" }, { topic: "Rust", guidance: null })).toEqual(
      {
        topic: "Rust",
      },
    );
  });
});

describe("safeSourceHref", () => {
  it.each([
    ["https://example.com/a?b=1", "https://example.com/a?b=1"],
    ["http://example.com", "http://example.com/"],
  ])("links %s", (url, href) => {
    expect(safeSourceHref(url)).toBe(href);
  });

  it.each([
    "javascript:alert(1)",
    "JAVASCRIPT:alert(1)",
    "data:text/html,<script>",
    "/relative/path",
    "//evil.example/x",
    "ftp://example.com/file",
    "mailto:a@example.com",
    "https://",
    "not a url",
  ])("does not link %s", (url) => {
    expect(safeSourceHref(url)).toBeNull();
  });
});
