import { HttpAgent } from "@ag-ui/client";

import { getAccessToken } from "./auth.js";

export class ApiError extends Error {
  constructor(kind) {
    super(kind);
    this.name = "ApiError";
    this.kind = kind;
  }
}

export async function getMe() {
  return apiRequest("/api/me");
}

export async function submitFeedback(traceId, value) {
  return apiRequest("/api/feedback", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ trace_id: traceId, value }),
  });
}

export function createChatAgent(conversationId) {
  return new HttpAgent({
    url: "/api/chat",
    agentId: "work-assistant",
    threadId: conversationId,
    fetch: authenticatedFetch,
  });
}

async function authenticatedFetch(url, options = {}) {
  const accessToken = await acquireAccessToken();
  const headers = new Headers(options.headers);
  headers.set("Authorization", `Bearer ${accessToken}`);

  let response;
  try {
    response = await fetch(url, { ...options, headers });
  } catch {
    throw new ApiError("backend_unavailable");
  }

  if (response.status === 401) throw new ApiError("authentication_required");
  if (response.status === 403) throw new ApiError("permission_denied");
  if (!response.ok) throw new ApiError("agent_request_failed");
  return response;
}

async function apiRequest(path, options = {}) {
  const accessToken = await acquireAccessToken();

  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: {
        ...options.headers,
        Authorization: `Bearer ${accessToken}`,
      },
    });
  } catch {
    throw new ApiError("backend_unavailable");
  }

  if (response.status === 401) throw new ApiError("authentication_required");
  if (response.status === 403) throw new ApiError("permission_denied");
  if (!response.ok) throw new ApiError("agent_request_failed");

  try {
    return await response.json();
  } catch {
    throw new ApiError("agent_request_failed");
  }
}

async function acquireAccessToken() {
  let accessToken;
  try {
    accessToken = await getAccessToken();
  } catch (error) {
    if (error instanceof Error && error.message === "NOT_SIGNED_IN") {
      throw new ApiError("not_signed_in");
    }
    throw error;
  }

  if (!accessToken) {
    throw new ApiError("interaction_required");
  }
  return accessToken;
}
