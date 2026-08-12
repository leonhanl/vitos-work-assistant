import {
  InteractionRequiredAuthError,
  PublicClientApplication,
} from "@azure/msal-browser";

const tenantId = import.meta.env.VITE_ENTRA_TENANT_ID?.trim();
const clientId = import.meta.env.VITE_ENTRA_CLIENT_ID?.trim();
const apiClientId = import.meta.env.VITE_WORK_ASSISTANT_API_CLIENT_ID?.trim();
const configuredScope = import.meta.env.VITE_WORK_ASSISTANT_API_SCOPE?.trim();

const redirectUri = new URL("/redirect.html", window.location.origin).href;
const apiScope = configuredScope || "";

let msalClient;

export async function initializeAuth() {
  requireConfiguration();

  msalClient = new PublicClientApplication({
    auth: {
      clientId,
      authority: `https://login.microsoftonline.com/${tenantId}`,
      redirectUri,
      postLogoutRedirectUri: redirectUri,
      navigateToLoginRequestUrl: true,
    },
    cache: {
      cacheLocation: "sessionStorage",
    },
  });

  await msalClient.initialize();
  const redirectResult = await msalClient.handleRedirectPromise();

  if (redirectResult?.account) {
    msalClient.setActiveAccount(redirectResult.account);
  } else if (!msalClient.getActiveAccount()) {
    const [cachedAccount] = msalClient.getAllAccounts();
    if (cachedAccount) {
      msalClient.setActiveAccount(cachedAccount);
    }
  }

  return getCurrentAccount();
}

export async function login() {
  requireClient();
  await msalClient.loginRedirect({ scopes: [apiScope] });
}

export async function logout() {
  requireClient();
  await msalClient.logoutRedirect({
    account: getCurrentAccount() || undefined,
  });
}

export function getCurrentAccount() {
  return msalClient?.getActiveAccount() || null;
}

export async function getAccessToken() {
  requireClient();
  const account = getCurrentAccount();
  if (!account) {
    throw new Error("NOT_SIGNED_IN");
  }

  const request = {
    account,
    scopes: [apiScope],
  };

  try {
    const response = await msalClient.acquireTokenSilent(request);
    return response.accessToken;
  } catch (error) {
    if (error instanceof InteractionRequiredAuthError) {
      await msalClient.acquireTokenRedirect(request);
      return null;
    }
    throw error;
  }
}

function requireConfiguration() {
  const missing = [];
  if (!tenantId) missing.push("VITE_ENTRA_TENANT_ID");
  if (!clientId) missing.push("VITE_ENTRA_CLIENT_ID");
  if (!apiClientId) missing.push("VITE_WORK_ASSISTANT_API_CLIENT_ID");
  if (!configuredScope) missing.push("VITE_WORK_ASSISTANT_API_SCOPE");

  if (missing.length) {
    throw new Error(`Missing frontend configuration: ${missing.join(", ")}`);
  }

  const expectedScope = `api://${apiClientId}/access_as_user`;
  if (apiScope !== expectedScope) {
    throw new Error(
      `VITE_WORK_ASSISTANT_API_SCOPE must be ${expectedScope}`,
    );
  }
}

function requireClient() {
  if (!msalClient) {
    throw new Error("Authentication has not been initialized.");
  }
}
