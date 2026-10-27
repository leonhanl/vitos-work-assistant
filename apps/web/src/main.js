import "./style.css";

import { buildResumeArray, randomUUID } from "@ag-ui/client";
import DOMPurify from "dompurify";
import { marked } from "marked";

import { ApiError, createChatAgent, getCurrentUser, submitFeedback } from "./api.js";
import { initializeAuth, login, logout } from "./auth.js";

const JIRA_CREATE_TOOL = "jira_create_customer_request";
const GUARDRAIL_STORAGE_KEY = "work-assistant.guardrails-enabled";

marked.use({ gfm: true, breaks: true });

// Assistant answers are Markdown, so they are rendered as HTML. The model output is
// untrusted, so the generated HTML is sanitized before it reaches the DOM.
DOMPurify.addHook("afterSanitizeAttributes", (node) => {
  if (node.tagName === "A" && node.hasAttribute("href")) {
    node.setAttribute("target", "_blank");
    node.setAttribute("rel", "noopener noreferrer");
  }
});

const elements = {
  loadingView: document.querySelector("#loading-view"),
  signedOutView: document.querySelector("#signed-out-view"),
  signedInView: document.querySelector("#signed-in-view"),
  signInButton: document.querySelector("#sign-in-button"),
  signOutButton: document.querySelector("#sign-out-button"),
  signInError: document.querySelector("#sign-in-error"),
  accountName: document.querySelector("#account-name"),
  accountUsername: document.querySelector("#account-username"),
  messages: document.querySelector("#messages"),
  chatForm: document.querySelector("#chat-form"),
  messageInput: document.querySelector("#message-input"),
  sendButton: document.querySelector("#send-button"),
  chatError: document.querySelector("#chat-error"),
  guardrailSwitch: document.querySelector("#guardrail-switch"),
  guardrailStatus: document.querySelector("#guardrail-status"),
  guardrailDialog: document.querySelector("#guardrail-dialog"),
  scenarioList: document.querySelector("#scenario-list"),
  scenarioError: document.querySelector("#scenario-error"),
};

let chatAgent = null;
const guardrailsEnabled = sessionStorage.getItem(GUARDRAIL_STORAGE_KEY) === "true";
elements.guardrailSwitch.checked = guardrailsEnabled;
elements.guardrailStatus.textContent = guardrailsEnabled ? "已开启" : "已关闭";

elements.guardrailSwitch.addEventListener("change", () => {
  // Keep the current mode until the user confirms the reload.
  elements.guardrailSwitch.checked = guardrailsEnabled;
  elements.guardrailDialog.returnValue = "";
  elements.guardrailDialog.showModal();
});

elements.guardrailDialog.addEventListener("close", () => {
  if (elements.guardrailDialog.returnValue !== "continue") return;
  sessionStorage.setItem(GUARDRAIL_STORAGE_KEY, String(!guardrailsEnabled));
  window.location.reload();
});

elements.signInButton.addEventListener("click", async () => {
  setButtonBusy(elements.signInButton, true, "正在跳转…");
  hideError(elements.signInError);
  try {
    await login();
  } catch {
    showError(elements.signInError, "无法启动 Microsoft 登录。");
    setButtonBusy(elements.signInButton, false, "使用 Microsoft 账号登录");
  }
});

elements.signOutButton.addEventListener("click", async () => {
  elements.signOutButton.disabled = true;
  hideError(elements.chatError);
  try {
    await logout();
  } catch {
    showError(elements.chatError, "无法完成 Microsoft 退出登录。");
    elements.signOutButton.disabled = false;
  }
});

elements.chatForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const message = elements.messageInput.value.trim();
  await sendMessage(message);
});

async function sendMessage(message) {
  if (!message || !chatAgent || chatAgent.isRunning || chatAgent.pendingInterrupts.length) {
    return;
  }

  hideError(elements.chatError);
  appendMessage("You", message);
  elements.messageInput.value = "";
  chatAgent.addMessage({ id: randomUUID(), role: "user", content: message });
  await runChat();
}

start();

async function start() {
  try {
    const account = await initializeAuth();
    if (!account) {
      showSignedOut();
      return;
    }

    showSignedIn(account);
    await Promise.all([loadScenarios(), loadAccountDepartments()]);
  } catch (error) {
    showSignedOut();
    const isConfigurationError = error instanceof Error && (
      error.message.startsWith("缺少前端配置") ||
      error.message.startsWith("VITE_")
    );
    const message = isConfigurationError
      ? error.message
      : "无法初始化身份验证。请检查前端配置。";
    showError(elements.signInError, message);
  }
}

async function loadAccountDepartments() {
  try {
    const { groups } = await getCurrentUser();
    const departments = [
      ["vitos_sales", "Sales"],
      ["vitos_it_support", "IT Support"],
    ].filter(([group]) => groups.includes(group)).map(([, label]) => label);
    if (departments.length) {
      elements.accountName.textContent += ` · ${departments.join(" / ")}`;
    }
  } catch (error) {
    console.error("Could not load account departments", error);
  }
}

async function loadScenarios() {
  try {
    const response = await fetch("/config/scenarios.json", { cache: "no-store" });
    if (!response.ok) throw new Error("Could not load scenarios.json.");
    const config = await response.json();
    if (!Array.isArray(config?.scenarios) || !config.scenarios.every((scenario) => (
      typeof scenario?.title === "string" && scenario.title.trim() &&
      Array.isArray(scenario.prompts) && scenario.prompts.every((prompt) => (
        typeof prompt?.title === "string" && prompt.title.trim() &&
        typeof prompt.message === "string" && prompt.message.trim() &&
        prompt.message.length <= 20000
      ))
    ))) {
      throw new Error("Invalid scenario configuration.");
    }

    elements.scenarioList.replaceChildren();
    for (const scenario of config.scenarios) {
      const group = document.createElement("details");
      group.className = "scenario-group";
      group.open = true;
      const heading = document.createElement("summary");
      heading.textContent = scenario.title;
      group.append(heading);
      for (const prompt of scenario.prompts) {
        const button = document.createElement("button");
        button.type = "button";
        button.className = "preset-question";
        button.textContent = prompt.title;
        button.title = prompt.message;
        button.addEventListener("click", () => sendMessage(prompt.message.trim()));
        group.append(button);
      }
      elements.scenarioList.append(group);
    }
    setChatBusy(Boolean(chatAgent?.isRunning));
  } catch {
    elements.scenarioList.replaceChildren();
    showError(elements.scenarioError, "无法加载场景。请检查 scenarios.json 并刷新页面。");
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

  elements.accountName.textContent = account.name || account.username || "已登录用户";
  elements.accountUsername.textContent = account.username || "";
  elements.messages.replaceChildren();
  appendMessage(
    "Assistant",
    `${account.name || account.username || "你好"}，有什么可以帮你的吗？`,
  );
  elements.messageInput.focus();
}

async function runChat(parameters = {}) {
  if (!chatAgent || chatAgent.isRunning) return;
  setChatBusy(true);
  hideError(elements.chatError);

  const messagesBeforeRun = structuredClone(chatAgent.messages);
  const pendingMessage = appendMessage("Assistant", "正在思考…", true);
  let hasText = false;
  let traceId = null;

  try {
    await chatAgent.runAgent({
      ...parameters,
      forwardedProps: { ...parameters.forwardedProps, guardrails_enabled: guardrailsEnabled },
    }, {
      onTextMessageContentEvent({ event, textMessageBuffer }) {
        hasText = true;
        replacePendingMessage(pendingMessage, `${textMessageBuffer}${event.delta}`);
      },
      onCustomEvent({ event }) {
        if (
          event.name === "trace" &&
          event.value &&
          typeof event.value.trace_id === "string"
        ) {
          traceId = event.value.trace_id;
        } else if (event.name === "sources" && Array.isArray(event.value)) {
          appendSources(pendingMessage, event.value);
        }
      },
      onRunErrorEvent({ event }) {
        // The server sends a safe message plus a stable code; RUN_ERROR is never
        // followed by RUN_FINISHED, so the pending bubble has to be cleared here.
        console.error("Agent run error", event.code, event.message);
        const blocked = event.code === "gateway_guardrail_blocked";
        if (blocked || !hasText) pendingMessage.remove();
        const messages = {
          gateway_rate_limited: "AI 网关请求过于频繁。请等待一分钟后重试。",
          gateway_budget_exhausted: "AI 助手的使用预算已耗尽。请联系 IT 支持，重试无法解决此问题。",
          gateway_guardrail_blocked: hasText
            ? "本次回答未通过企业内容安全检查，已撤回。如需协助，请联系 IT 支持。"
            : "本次处理因企业安全策略而中止。如需协助，请联系 IT 支持。",
          agent_execution_failed: "助手无法完成此请求。",
          jira_identity_unavailable: "无法确定当前用户的 Jira 客户身份。请联系 IT 支持。",
          invalid_jira_ticket_draft: "助手未能生成有效的 Jira 工单草稿。请重新描述需求后重试。",
        };
        showError(
          elements.chatError,
          messages[event.code] || "助手请求失败，请重试。",
        );
        // Restore the whole run so tool calls/results stay paired, and blocked
        // content is not submitted as history on the next turn.
        if (blocked) return { messages: messagesBeforeRun };
      },
      onRunFinishedEvent(details) {
        if (details.outcome === "interrupt") {
          if (!hasText) pendingMessage.remove();
          renderJiraApproval(details.interrupts, details.messages);
          return;
        }
        if (!hasText) {
          pendingMessage.remove();
          return;
        }
        if (traceId) appendFeedback(pendingMessage, traceId);
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

function appendFeedback(article, traceId) {
  const section = document.createElement("section");
  section.className = "feedback";

  const prompt = document.createElement("span");
  prompt.textContent = "为本次回答评分：";

  const rating = document.createElement("span");
  rating.className = "feedback-rating";
  rating.setAttribute("role", "group");
  rating.setAttribute("aria-label", "请为本次回答评 1 到 5 星");

  const status = document.createElement("span");
  status.className = "feedback-status";
  status.setAttribute("aria-live", "polite");

  const buttons = Array.from(
    { length: 5 },
    (_, index) => feedbackButton(index + 1),
  );

  function feedbackButton(value) {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "feedback-button";
    button.textContent = "★";
    button.setAttribute("aria-label", `${value} 星（满分 5 星）`);
    button.title = `${value} 星（满分 5 星）`;
    button.addEventListener("mouseenter", () => previewRating(value));
    button.addEventListener("focus", () => previewRating(value));
    button.addEventListener("click", async () => {
      buttons.forEach((item) => { item.disabled = true; });
      status.textContent = "正在保存…";
      try {
        await submitFeedback(traceId, value);
        buttons.forEach((item, index) => {
          item.classList.toggle("selected", index < value);
        });
        status.textContent = "感谢你的反馈。";
      } catch {
        buttons.forEach((item) => { item.disabled = false; });
        previewRating(0);
        status.textContent = "无法保存反馈，请重试。";
      }
    });
    return button;
  }

  function previewRating(value) {
    buttons.forEach((button, index) => {
      button.classList.toggle("preview", index < value);
    });
  }

  rating.addEventListener("mouseleave", () => previewRating(0));
  rating.addEventListener("focusout", (event) => {
    if (!rating.contains(event.relatedTarget)) previewRating(0);
  });
  rating.append(...buttons);
  section.append(prompt, rating, status);
  article.append(section);
}

function renderJiraApproval(interrupts, messages) {
  if (interrupts.length !== 1) {
    showError(elements.chatError, "助手请求的审批操作暂不受支持。");
    return;
  }
  const interrupt = interrupts[0];
  const toolCall = findToolCall(messages, interrupt.toolCallId);
  if (toolCall?.name !== JIRA_CREATE_TOOL) {
    showError(elements.chatError, "助手请求的审批操作暂不受支持。");
    return;
  }

  const fields = jiraDraft(toolCall.args);
  const article = document.createElement("article");
  article.className = "message assistant approval-message";
  const heading = document.createElement("h2");
  heading.textContent = "需要审批";
  const card = document.createElement("section");
  card.className = "approval-card";
  const title = document.createElement("h3");
  title.textContent = "创建 Jira 服务请求";
  card.append(title);
  card.append(detail("摘要", fields.summary || "未提供"));
  card.append(detail("描述", fields.description || "未提供"));
  card.append(detail("请求类型", fields.requestTypeId || "未提供"));

  const actions = document.createElement("div");
  actions.className = "approval-actions";
  actions.append(
    approvalButton("拒绝", "secondary-button", false, interrupt, article),
    approvalButton("批准并创建", "primary-button", true, interrupt, article),
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
  heading.textContent = label === "You" ? "你" : "助手";
  const content = document.createElement("div");
  content.className = "message-body";
  content.textContent = text;

  article.append(heading, content);
  elements.messages.append(article);
  article.scrollIntoView({ block: "end", behavior: "smooth" });
  return article;
}

function replacePendingMessage(article, answer) {
  article.classList.remove("pending");
  const content = article.querySelector(".message-body");
  content.classList.add("markdown");
  content.innerHTML = DOMPurify.sanitize(marked.parse(answer));
}

function appendSources(article, sources = []) {
  article.querySelector(".sources")?.remove();
  const section = document.createElement("section");
  section.className = "sources";
  const heading = document.createElement("h3");
  heading.textContent = "参考来源";
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
  elements.sendButton.textContent = busy ? "正在思考…" : "发送";
  elements.guardrailSwitch.disabled = blocked;
  elements.scenarioList.querySelectorAll("button").forEach((button) => {
    button.disabled = blocked;
  });
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
    not_signed_in: "你尚未登录。请使用 Microsoft 账号登录后重试。",
    interaction_required: "Microsoft 身份验证需要你完成操作。请完成登录后重试。",
    authentication_required: "需要身份验证。请退出并重新登录后重试。",
    permission_denied: "权限不足。请联系管理员授予 access_as_user 权限。",
    backend_unavailable: "Vito's Work Assistant 后端服务暂不可用。",
    agent_request_failed: "助手请求失败，请重试。",
    unknown: "无法完成请求。",
  };
  return messages[kind];
}
