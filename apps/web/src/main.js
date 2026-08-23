import "./style.css";

import { buildResumeArray, randomUUID } from "@ag-ui/client";

import { ApiError, createChatAgent, getMe } from "./api.js";
import { initializeAuth, login, logout } from "./auth.js";

const JIRA_CREATE_TOOL = "jira_create_customer_request";

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

let chatAgent = null;

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
  if (!message || !chatAgent || chatAgent.isRunning || chatAgent.pendingInterrupts.length) {
    return;
  }

  hideError(elements.chatError);
  appendMessage("You", message);
  elements.messageInput.value = "";
  chatAgent.addMessage({ id: randomUUID(), role: "user", content: message });
  await runChat();
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
  chatAgent = null;
  elements.loadingView.hidden = true;
  elements.signedInView.hidden = true;
  elements.signedOutView.hidden = false;
}

function showSignedIn(account) {
  chatAgent = createChatAgent(randomUUID());
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

async function runChat(parameters = {}) {
  if (!chatAgent || chatAgent.isRunning) return;
  setChatBusy(true);
  hideError(elements.chatError);

  const pendingMessage = appendMessage("Assistant", "Thinking…", true);
  let hasText = false;

  try {
    await chatAgent.runAgent(parameters, {
      onTextMessageContentEvent({ event, textMessageBuffer }) {
        hasText = true;
        replacePendingMessage(pendingMessage, `${textMessageBuffer}${event.delta}`);
      },
      onCustomEvent({ event }) {
        if (event.name === "sources" && Array.isArray(event.value)) {
          appendSources(pendingMessage, event.value);
        }
      },
      onRunErrorEvent() {
        showError(elements.chatError, "The assistant request failed. Please try again.");
      },
      onRunFinishedEvent(details) {
        if (details.outcome === "interrupt") {
          if (!hasText) pendingMessage.remove();
          renderJiraApproval(details.interrupts, details.messages);
          return;
        }
        if (!hasText) pendingMessage.remove();
      },
    });
  } catch (error) {
    pendingMessage.remove();
    showError(elements.chatError, userMessageFor(error));
  } finally {
    setChatBusy(false);
    elements.messageInput.focus();
  }
}

function renderJiraApproval(interrupts, messages) {
  if (interrupts.length !== 1) {
    showError(elements.chatError, "The assistant requested an unsupported approval operation.");
    return;
  }
  const interrupt = interrupts[0];
  const toolCall = findToolCall(messages, interrupt.toolCallId);
  if (toolCall?.name !== JIRA_CREATE_TOOL) {
    showError(elements.chatError, "The assistant requested an unsupported approval operation.");
    return;
  }

  const fields = jiraDraft(toolCall.args);
  const article = document.createElement("article");
  article.className = "message assistant approval-message";
  const heading = document.createElement("h2");
  heading.textContent = "Approval required";
  const card = document.createElement("section");
  card.className = "approval-card";
  const title = document.createElement("h3");
  title.textContent = "Create Jira service request";
  card.append(title);
  card.append(detail("Summary", fields.summary || "Not provided"));
  card.append(detail("Description", fields.description || "Not provided"));
  card.append(detail("Request type", fields.requestTypeId || "Not provided"));

  const actions = document.createElement("div");
  actions.className = "approval-actions";
  actions.append(
    approvalButton("Reject", "secondary-button", false, interrupt, article),
    approvalButton("Approve and create", "primary-button", true, interrupt, article),
  );
  card.append(actions);
  article.append(heading, card);
  elements.messages.append(article);
  article.scrollIntoView({ block: "end", behavior: "smooth" });
}

function approvalButton(label, className, approved, interrupt, article) {
  const button = document.createElement("button");
  button.type = "button";
  button.className = className;
  button.textContent = label;
  button.addEventListener("click", async () => {
    article.querySelectorAll("button").forEach((item) => { item.disabled = true; });
    article.classList.add(approved ? "approved" : "rejected");
    const response = {
      status: "resolved",
      payload: approved
        ? { approved: true }
        : { approved: false, reason: "The user declined this operation." },
    };
    const resume = buildResumeArray([interrupt], { [interrupt.id]: response });
    await runChat({ resume });
  });
  return button;
}

function jiraDraft(args = {}) {
  let fields = args.request_field_values;
  if (typeof fields === "string") {
    try {
      fields = JSON.parse(fields);
    } catch {
      fields = {};
    }
  }
  if (!fields || typeof fields !== "object" || Array.isArray(fields)) fields = {};
  return {
    summary: typeof fields.summary === "string" ? fields.summary.trim() : "",
    description: typeof fields.description === "string" ? fields.description.trim() : "",
    requestTypeId: args.request_type_id == null ? "" : String(args.request_type_id),
  };
}

function findToolCall(messages, toolCallId) {
  for (const message of messages || []) {
    for (const toolCall of message.toolCalls || []) {
      if (toolCall.id !== toolCallId) continue;
      let args = {};
      try {
        args = JSON.parse(toolCall.function.arguments);
      } catch {
        // Leave malformed arguments empty; the server will deny invalid Jira drafts.
      }
      return { name: toolCall.function.name, args };
    }
  }
  return null;
}

function detail(label, value) {
  const row = document.createElement("div");
  row.className = "approval-detail";
  const heading = document.createElement("strong");
  heading.textContent = label;
  const content = document.createElement("p");
  content.textContent = value;
  row.append(heading, content);
  return row;
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

function replacePendingMessage(article, answer) {
  article.classList.remove("pending");
  article.querySelector("p").textContent = answer;
}

function appendSources(article, sources = []) {
  article.querySelector(".sources")?.remove();
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

function safeHttpUrl(value) {
  if (typeof value !== "string") return null;
  try {
    const url = new URL(value);
    return ["http:", "https:"].includes(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

function setChatBusy(busy) {
  const blocked = busy || Boolean(chatAgent?.pendingInterrupts.length);
  elements.messageInput.disabled = blocked;
  elements.sendButton.disabled = blocked;
  elements.sendButton.textContent = busy ? "Thinking…" : "Send";
}

function setButtonBusy(button, busy, label) {
  button.disabled = busy;
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
