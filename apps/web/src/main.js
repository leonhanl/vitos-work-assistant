import "./style.css";

import { ApiError, getMe, sendChat } from "./api.js";
import {
  initializeAuth,
  login,
  logout,
} from "./auth.js";

const elements = {
  loadingView: document.querySelector("#loading-view"),
  signedOutView: document.querySelector("#signed-out-view"),
  signedInView: document.querySelector("#signed-in-view"),
  signInButton: document.querySelector("#sign-in-button"),
  signOutButton: document.querySelector("#sign-out-button"),
  signInError: document.querySelector("#sign-in-error"),
  accountName: document.querySelector("#account-name"),
  accountUsername: document.querySelector("#account-username"),
  apiIdentity: document.querySelector("#api-identity"),
  messages: document.querySelector("#messages"),
  chatForm: document.querySelector("#chat-form"),
  messageInput: document.querySelector("#message-input"),
  sendButton: document.querySelector("#send-button"),
  chatError: document.querySelector("#chat-error"),
};

let threadId = null;

elements.signInButton.addEventListener("click", async () => {
  setButtonBusy(elements.signInButton, true, "Redirecting…");
  hideError(elements.signInError);
  try {
    await login();
  } catch {
    showError(elements.signInError, "Microsoft sign-in could not be started.");
    setButtonBusy(elements.signInButton, false, "Sign in with Microsoft");
  }
});

elements.signOutButton.addEventListener("click", async () => {
  elements.signOutButton.disabled = true;
  hideError(elements.chatError);
  try {
    await logout();
  } catch {
    showError(elements.chatError, "Microsoft sign-out could not be completed.");
    elements.signOutButton.disabled = false;
  }
});

elements.chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = elements.messageInput.value.trim();
  if (!message) return;

  hideError(elements.chatError);
  appendMessage("You", message);
  elements.messageInput.value = "";
  setChatBusy(true);
  const pendingMessage = appendMessage("Assistant", "Thinking…", true);

  try {
    const response = await sendChat(message, threadId);
    threadId = response.thread_id;
    replacePendingMessage(pendingMessage, response.answer, response.sources);
  } catch (error) {
    pendingMessage.remove();
    showError(elements.chatError, userMessageFor(error));
  } finally {
    setChatBusy(false);
    elements.messageInput.focus();
  }
});

start();

async function start() {
  try {
    const account = await initializeAuth();
    if (!account) {
      showSignedOut();
      return;
    }

    showSignedIn(account);
    await confirmApiIdentity();
  } catch (error) {
    showSignedOut();
    const isConfigurationError = error instanceof Error && (
      error.message.startsWith("Missing frontend configuration") ||
      error.message.startsWith("VITE_")
    );
    const message = isConfigurationError
      ? error.message
      : "Authentication could not be initialized. Check the frontend configuration.";
    showError(elements.signInError, message);
  }
}

function showSignedOut() {
  elements.loadingView.hidden = true;
  elements.signedInView.hidden = true;
  elements.signedOutView.hidden = false;
}

function showSignedIn(account) {
  elements.loadingView.hidden = true;
  elements.signedOutView.hidden = true;
  elements.signedInView.hidden = false;

  elements.accountName.textContent = account.name || account.username || "Signed-in user";
  elements.accountUsername.textContent = account.username || "";
  elements.messages.replaceChildren();
  appendMessage(
    "Assistant",
    `Hi ${account.name || account.username || "there"}, how can I help?`,
  );
  elements.messageInput.focus();
}

async function confirmApiIdentity() {
  elements.apiIdentity.textContent = "Confirming API identity…";
  try {
    const user = await getMe();
    elements.apiIdentity.textContent = user.username
      ? `API authenticated as ${user.username}`
      : "API authentication confirmed";
  } catch (error) {
    elements.apiIdentity.textContent = "";
    showError(elements.chatError, userMessageFor(error));
  }
}

function appendMessage(label, text, pending = false) {
  const article = document.createElement("article");
  article.className = `message ${label.toLowerCase()}`;
  if (pending) article.classList.add("pending");

  const heading = document.createElement("h2");
  heading.textContent = label;
  const content = document.createElement("p");
  content.textContent = text;

  article.append(heading, content);
  elements.messages.append(article);
  article.scrollIntoView({ block: "end", behavior: "smooth" });
  return article;
}

function replacePendingMessage(article, answer, sources = []) {
  article.classList.remove("pending");
  article.querySelector("p").textContent = answer;

  if (Array.isArray(sources) && sources.length) {
    const section = document.createElement("section");
    section.className = "sources";
    const heading = document.createElement("h3");
    heading.textContent = "Sources";
    const list = document.createElement("ul");

    for (const source of sources) {
      if (!source || typeof source.name !== "string") continue;
      const item = document.createElement("li");
      const url = safeHttpUrl(source.url);
      if (url) {
        const link = document.createElement("a");
        link.href = url;
        link.target = "_blank";
        link.rel = "noopener noreferrer";
        link.textContent = source.name;
        item.append(link);
      } else {
        item.textContent = source.name;
      }
      list.append(item);
    }

    if (list.children.length) {
      section.append(heading, list);
      article.append(section);
    }
  }
}

function safeHttpUrl(value) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function setChatBusy(isBusy) {
  elements.messageInput.disabled = isBusy;
  elements.sendButton.disabled = isBusy;
  elements.sendButton.textContent = isBusy ? "Thinking…" : "Send";
}

function setButtonBusy(button, isBusy, label) {
  button.disabled = isBusy;
  button.textContent = label;
}

function showError(element, message) {
  element.textContent = message;
  element.hidden = false;
}

function hideError(element) {
  element.hidden = true;
  element.textContent = "";
}

function userMessageFor(error) {
  const kind = error instanceof ApiError ? error.kind : "unknown";
  const messages = {
    not_signed_in: "You are not signed in. Sign in with Microsoft and try again.",
    interaction_required: "Microsoft authentication requires interaction. Complete sign-in and try again.",
    authentication_required: "Authentication is required. Sign out, sign in again, and retry.",
    permission_denied: "Permission denied. Ask an administrator to grant access_as_user.",
    backend_unavailable: "The Work Assistant backend is unavailable.",
    agent_request_failed: "The assistant request failed. Please try again.",
    unknown: "The request could not be completed.",
  };
  return messages[kind];
}
