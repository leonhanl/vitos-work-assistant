import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";
import { HttpAgent } from "@ag-ui/client";

// Exercise the browser entry point with DOM/auth doubles, without calling services.
const source = (await readFile(new URL("../src/main.js", import.meta.url), "utf8"))
  .replace(/^import .*;\n/gm, "");
const config = JSON.parse(await readFile(new URL("../public/config/scenarios.json", import.meta.url)));

class Element {
  children = [];
  listeners = {};
  classList = { add() {}, remove() {}, toggle() {} };
  disabled = false;
  hidden = true;
  value = "";
  textContent = "";
  addEventListener(name, handler) { this.listeners[name] = handler; }
  append(...children) {
    for (const child of children) child.parentElement = this;
    this.children.push(...children);
  }
  replaceChildren(...children) {
    for (const child of this.children) child.parentElement = null;
    this.children = [];
    this.append(...children);
  }
  querySelector(selector) { return this.querySelectorAll(selector)[0] || null; }
  querySelectorAll(selector) {
    return this.children.flatMap((child) => [
      ...((selector.startsWith(".")
        ? (child.className || "").split(" ").includes(selector.slice(1))
        : child.tagName === selector) ? [child] : []), ...child.querySelectorAll(selector),
    ]);
  }
  focus() {}
  scrollIntoView() {}
  remove() {
    if (this.parentElement) {
      this.parentElement.children = this.parentElement.children.filter((child) => child !== this);
      this.parentElement = null;
    }
  }
  showModal() { this.open = true; }
  async close(value) {
    this.returnValue = value;
    this.open = false;
    await this.listeners.close();
  }
}

async function page(
  storage = new Map(), scenarios = config,
  user = { groups: [] }, account = { username: "test@example.com" },
  agentFactory = null,
) {
  const elements = new Map();
  const agents = [];
  const fetches = [];
  const errors = [];
  let reloads = 0;
  const context = {
    console: { ...console, error: (...args) => errors.push(args) }, randomUUID, structuredClone,
    marked: { use() {}, parse: (text) => text }, DOMPurify: { addHook() {}, sanitize: (text) => text },
    document: {
      querySelector: (selector) => {
        if (!elements.has(selector)) elements.set(selector, new Element());
        return elements.get(selector);
      },
      createElement: (tagName) => Object.assign(new Element(), { tagName }),
    },
    sessionStorage: { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value) },
    window: { location: { reload: () => { reloads++; } } },
    initializeAuth: async () => account,
    getCurrentUser: async () => {
      if (user instanceof Error) throw user;
      return user;
    },
    fetch: async (...args) => {
      fetches.push(args);
      return { ok: true, json: async () => scenarios };
    },
    createChatAgent: (threadId) => {
      const agent = agentFactory ? agentFactory(threadId) : {
        threadId, messages: [], runs: [], pendingInterrupts: [], isRunning: false,
        addMessage(message) { this.messages.push(message); },
        async runAgent(parameters, subscriber) {
          this.runs.push(parameters);
          subscriber.onRunFinishedEvent({ outcome: "success" });
        },
      };
      agents.push(agent);
      return agent;
    },
  };
  vm.runInNewContext(source, context);
  await new Promise(setImmediate);
  return { elements, agents, context, fetches, errors, storage, reloads: () => reloads };
}

test("account header displays departments from mapped groups", async () => {
  const cases = [
    ["Alice", "alice@vitosdemo.com", ["vitos_sales"], "Alice · Sales"],
    ["Bob", "bob@vitosdemo.com", ["vitos_it_support"], "Bob · IT Support"],
    ["Alice", "alice@vitosdemo.com", ["unrelated", "vitos_it_support", "vitos_sales"], "Alice · Sales / IT Support"],
    ["Alice", "alice@vitosdemo.com", ["unrelated"], "Alice"],
    ["Alice", "alice@vitosdemo.com", [], "Alice"],
  ];
  for (const [name, username, groups, expected] of cases) {
    const p = await page(new Map(), config, { groups }, { name, username });
    assert.equal(p.elements.get("#account-name").textContent, expected);
    assert.equal(p.elements.get("#account-username").textContent, username);
    assert.equal(p.errors.length, 0);
  }
});

test("a streamed guardrail error retracts the whole answer and restores real AG-UI history", async () => {
  const requests = [];
  const p = await page(new Map(), config, { groups: [] }, { username: "test@example.com" },
    (threadId) => new HttpAgent({
      threadId, url: "https://agent.invalid/chat",
      fetch: async (url, options) => {
        const input = JSON.parse(options.body);
        requests.push(input);
        const events = [
          { type: "RUN_STARTED", threadId, runId: input.runId },
          { type: "TEXT_MESSAGE_START", messageId: "answer-1", role: "assistant" },
          { type: "TEXT_MESSAGE_CONTENT", messageId: "answer-1", delta: "First partial answer" },
          { type: "TEXT_MESSAGE_END", messageId: "answer-1" },
        ];
        if (requests.length === 1) events.push(
          { type: "TOOL_CALL_START", toolCallId: "call-1", toolCallName: "search", parentMessageId: "answer-1" },
          { type: "TOOL_CALL_ARGS", toolCallId: "call-1", delta: "{}" },
          { type: "TOOL_CALL_END", toolCallId: "call-1" },
          { type: "TOOL_CALL_RESULT", messageId: "tool-1", toolCallId: "call-1", content: "tool result" },
          { type: "TEXT_MESSAGE_START", messageId: "answer-2", role: "assistant" },
          { type: "TEXT_MESSAGE_CONTENT", messageId: "answer-2", delta: "Blocked answer" },
          { type: "TEXT_MESSAGE_END", messageId: "answer-2" },
          { type: "CUSTOM", name: "sources", value: [{ name: "Test source" }] },
          { type: "RUN_ERROR", code: "gateway_guardrail_blocked", message: "Blocked by policy" },
        );
        else events.push({ type: "RUN_FINISHED", threadId, runId: input.runId });
        return new Response(events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join(""), {
          headers: { "Content-Type": "text/event-stream" },
        });
      },
    }));
  const previous = [
    { id: "old-user", role: "user", content: "previous question" },
    { id: "old-answer", role: "assistant", content: "previous answer" },
  ];
  p.agents[0].setMessages(previous);
  const previousBubble = p.context.appendMessage("Assistant", "previous answer");
  await p.context.sendMessage("blocked question");

  const container = p.elements.get("#messages");
  assert.ok(container.children.includes(previousBubble));
  assert.equal(container.children.length, 3); // Welcome, prior answer, current user.
  assert.equal(container.querySelectorAll(".sources").length, 0);
  assert.equal(p.elements.get("#chat-error").textContent, "本次回答未通过企业内容安全检查，已撤回。如需协助，请联系 IT 支持。");
  assert.deepEqual(p.agents[0].messages.map((message) => message.content), [
    "previous question", "previous answer", "blocked question",
  ]);
  assert.ok(p.agents[0].messages.every((message) => !message.toolCalls && message.role !== "tool"));

  await p.context.sendMessage("next question");
  assert.deepEqual(requests[1].messages.map((message) => message.content), [
    "previous question", "previous answer", "blocked question", "next question",
  ]);
  assert.equal(p.elements.get("#chat-error").hidden, true);
  assert.equal(container.children.at(-1).querySelector(".message-body").innerHTML, "First partial answer");
});

test("department lookup failure keeps the account and chat usable and logs the error", async () => {
  const error = new Error("backend_unavailable");
  const p = await page(new Map(), config, error);
  assert.equal(p.elements.get("#account-name").textContent, "test@example.com");
  assert.equal(p.elements.get("#signed-in-view").hidden, false);
  assert.equal(p.elements.get("#scenario-list").children.length, config.scenarios.length);
  assert.deepEqual(p.errors, [["Could not load account departments", error]]);
  await p.context.sendMessage("hello");
  assert.equal(p.agents[0].runs.length, 1);
});

test("preset questions across groups and typed input share one conversation, default OFF", async () => {
  const p = await page();
  const groups = p.elements.get("#scenario-list").children;
  assert.equal(groups.length, config.scenarios.length);
  assert.equal(p.fetches[0][0], "/config/scenarios.json");
  assert.equal(p.fetches[0][1].cache, "no-store");
  await groups[0].children[1].listeners.click();
  await groups[1].children[1].listeners.click();
  p.elements.get("#message-input").value = "Follow up";
  await p.elements.get("#chat-form").listeners.submit({ preventDefault() {} });
  assert.equal(p.agents.length, 1);
  assert.deepEqual(p.agents[0].messages.map((message) => message.content), [
    config.scenarios[0].prompts[0].message, config.scenarios[1].prompts[0].message, "Follow up",
  ]);
  assert.ok(p.agents[0].runs.every((run) => run.forwardedProps.guardrails_enabled === false));
  assert.equal(p.reloads(), 0);
});

test("Cancel preserves conversation; Continue persists mode and reload starts a fresh conversation", async () => {
  const storage = new Map([["msal-test-cache", "keep"]]);
  const p = await page(storage);
  const toggle = p.elements.get("#guardrail-switch");
  const dialog = p.elements.get("#guardrail-dialog");
  await p.context.sendMessage("hello");
  toggle.checked = true;
  toggle.listeners.change();
  assert.equal(toggle.checked, false);
  assert.equal(dialog.open, true);
  await dialog.close("cancel");
  assert.equal(p.reloads(), 0);
  assert.equal(p.agents[0].messages.length, 1);
  assert.equal(storage.has("work-assistant.guardrails-enabled"), false);
  toggle.listeners.change();
  await dialog.close("continue");
  assert.equal(p.reloads(), 1);
  assert.equal(storage.get("work-assistant.guardrails-enabled"), "true");
  assert.equal(storage.get("msal-test-cache"), "keep");
  const refreshed = await page(storage);
  assert.equal(refreshed.elements.get("#guardrail-switch").checked, true);
  assert.equal(refreshed.agents[0].messages.length, 0);
  assert.notEqual(refreshed.agents[0].threadId, p.agents[0].threadId);
  await refreshed.context.sendMessage("new conversation");
  assert.equal(refreshed.agents[0].runs[0].forwardedProps.guardrails_enabled, true);
  refreshed.elements.get("#guardrail-switch").listeners.change();
  await refreshed.elements.get("#guardrail-dialog").close("continue");
  assert.equal(storage.get("work-assistant.guardrails-enabled"), "false");
});

test("approval resume carries the selected mode; pending approval blocks new questions and the switch", async () => {
  const p = await page(new Map([["work-assistant.guardrails-enabled", "true"]]));
  p.agents[0].pendingInterrupts = [{ id: "approval-1" }];
  p.context.setChatBusy(false);
  assert.equal(p.elements.get("#guardrail-switch").disabled, true);
  assert.ok(p.elements.get("#scenario-list").querySelectorAll("button").every((button) => button.disabled));
  await p.context.sendMessage("should not be sent");
  assert.equal(p.agents[0].messages.length, 0);
  const resume = [{ id: "approval-1", payload: { approved: true } }];
  await p.context.runChat({ resume });
  assert.equal(p.agents[0].runs[0].resume, resume);
  assert.equal(p.agents[0].runs[0].forwardedProps.guardrails_enabled, true);
});

test("invalid scenario configuration shows an error and leaves free chat usable", async () => {
  const p = await page(new Map(), { scenarios: [{ title: "Broken", prompts: [{}] }] });
  assert.equal(p.elements.get("#scenario-error").hidden, false);
  assert.equal(p.elements.get("#scenario-list").children.length, 0);
  await p.context.sendMessage("hello");
  assert.equal(p.agents[0].runs.length, 1);
});

test("run errors show Chinese messages and keep the original server message in the console", async () => {
  const p = await page();
  const logs = [];
  p.context.console = { error: (...args) => logs.push(args) };
  const cases = [
    ["gateway_rate_limited", "AI 网关请求过于频繁。请等待一分钟后重试。"],
    ["gateway_budget_exhausted", "AI 助手的使用预算已耗尽。请联系 IT 支持，重试无法解决此问题。"],
    ["gateway_guardrail_blocked", "本次处理因企业安全策略而中止。如需协助，请联系 IT 支持。"],
    ["agent_execution_failed", "助手无法完成此请求。"],
    ["jira_identity_unavailable", "无法确定当前用户的 Jira 客户身份。请联系 IT 支持。"],
    ["invalid_jira_ticket_draft", "助手未能生成有效的 Jira 工单草稿。请重新描述需求后重试。"],
    ["future_error_code", "助手请求失败，请重试。"],
    [undefined, "助手请求失败，请重试。"],
  ];
  for (const [code, expected] of cases) {
    const message = "Original English server error";
    p.agents[0].runAgent = async (parameters, subscriber) => {
      subscriber.onRunErrorEvent({ event: { code, message } });
    };
    await p.context.runChat();
    assert.equal(p.elements.get("#chat-error").hidden, false);
    assert.equal(p.elements.get("#chat-error").textContent, expected);
    assert.deepEqual(logs.at(-1), ["Agent run error", code, message]);
  }
});
