import { API_V1_BASE, apiFetch, apiV1Path } from "./api";

// The consent step of Aleph's OAuth flow for MCP clients (docs/mcp.md). The
// backend's `/oauth/authorize` validates the client's request, parks it in the
// signed session and sends the browser to `/connect`; this module reads and
// answers that parked request. Browser session only.

export interface ConsentRequest {
  /** Identifies the parked request, so a stale tab can't answer a newer one. */
  id: string;
  /** The app's own name, from its client metadata document. */
  client_name: string;
  client_id: string;
  client_host: string;
  /** Where the browser returns after a decision. */
  redirect_host: string;
  /** Every redirect goes to this device: any local program could claim the name. */
  loopback_only: boolean;
}

export const CONSENT_URL = `${API_V1_BASE}/oauth/consent`;
export const CONSENT_QUERY_KEY = ["oauth-consent"] as const;

export function getConsent(): Promise<ConsentRequest> {
  return apiFetch<ConsentRequest>(apiV1Path("/oauth/consent"));
}

export function decideConsent(id: string, approve: boolean): Promise<{ redirect_to: string }> {
  return apiFetch<{ redirect_to: string }>(apiV1Path("/oauth/consent"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ id, approve }),
  });
}
