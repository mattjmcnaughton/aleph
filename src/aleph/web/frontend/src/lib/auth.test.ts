import { describe, expect, it } from "vitest";
import type { AuthSession } from "./api";
import { authRedirect, LOGIN_PATH } from "./auth";

const signedIn: AuthSession = {
  authenticated: true,
  provider: "keycloak",
  user: {
    id: "u-1",
    username: "dev",
    display_name: "Dev Learner",
    email: "dev@example.com",
    is_admin: false,
    model_allowlist: [],
    feature_flags: {},
  },
};

const signedOut: AuthSession = {
  authenticated: false,
  provider: "keycloak",
  user: null,
};

describe("authRedirect", () => {
  it("redirects an unauthenticated learner off a protected route to /login", () => {
    expect(authRedirect(signedOut, "/")).toEqual({ to: LOGIN_PATH });
    expect(authRedirect(undefined, "/paths")).toEqual({ to: LOGIN_PATH });
  });

  it("lets an unauthenticated learner stay on the login screen", () => {
    expect(authRedirect(signedOut, "/login")).toBeNull();
  });

  it("bounces an authenticated learner off /login back into the app", () => {
    expect(authRedirect(signedIn, "/login")).toEqual({ to: "/" });
  });

  it("lets an authenticated learner stay on a protected route", () => {
    expect(authRedirect(signedIn, "/")).toBeNull();
  });
});
