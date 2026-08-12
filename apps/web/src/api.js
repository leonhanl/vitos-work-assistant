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

export async function sendChat(message, threadId = null) {
  const body = { message };
  if (threadId) {
    body.thread_id = threadId;
  }

  return apiRequest("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
}

async function apiRequest(path, options = {}) {
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
