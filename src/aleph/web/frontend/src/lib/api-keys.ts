import { API_V1_BASE, apiFetch, apiV1Path } from "./api";

// API keys (docs/mcp.md): list, create and revoke the learner's keys for the
// MCP server. Browser session only; the secret appears in the create response
// and nowhere else.

export interface ApiKeyMetadata {
  id: string;
  name: string;
  created_at: string;
  revoked_at: string | null;
}

export interface CreatedApiKey {
  id: string;
  name: string;
  /** The secret, shown once. */
  key: string;
}

export const API_KEYS_QUERY_KEY = ["api-keys"] as const;
export const API_KEYS_URL = `${API_V1_BASE}/api-keys`;

export async function listApiKeys(): Promise<ApiKeyMetadata[]> {
  return (await apiFetch<{ keys: ApiKeyMetadata[] }>(apiV1Path("/api-keys"))).keys;
}

export function createApiKey(name: string): Promise<CreatedApiKey> {
  return apiFetch<CreatedApiKey>(apiV1Path("/api-keys"), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
}

export function revokeApiKey(id: string): Promise<void> {
  return apiFetch<void>(apiV1Path(`/api-keys/${id}`), { method: "DELETE" });
}

/** The config most MCP clients (Claude, Cursor, Amp) accept for a remote server. */
export function mcpConfigSnippet(origin: string, key: string): string {
  return JSON.stringify(
    {
      mcpServers: {
        aleph: {
          type: "http",
          url: `${origin}/mcp`,
          headers: { Authorization: `Bearer ${key}` },
        },
      },
    },
    null,
    2,
  );
}
