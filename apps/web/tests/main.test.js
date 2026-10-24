import assert from "node:assert/strict";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import test from "node:test";
import vm from "node:vm";

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
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.children = children; }
  querySelectorAll(selector) {
    return this.children.flatMap((child) => [
      ...(child.tagName === selector ? [child] : []), ...child.querySelectorAll(selector),
    ]);
  }
  focus() {}
  scrollIntoView() {}
  remove() {}
  showModal() { this.open = true; }
  async close(value) {
    this.returnValue = value;
    this.open = false;
    await this.listeners.close();
  }
}

async function page(storage = new Map(), scenarios = config) {
  const elements = new Map();
  const agents = [];
  const fetches = [];
  let reloads = 0;
  const context = {
    console, randomUUID,
    marked: { use() {} }, DOMPurify: { addHook() {} },
    document: {
      querySelector: (selector) => {
        if (!elements.has(selector)) elements.set(selector, new Element());
        return elements.get(selector);
      },
      createElement: (tagName) => Object.assign(new Element(), { tagName }),
    },
    sessionStorage: { getItem: (key) => storage.get(key), setItem: (key, value) => storage.set(key, value) },
    window: { location: { reload: () => { reloads++; } } },
    initializeAuth: async () => ({ username: "test@example.com" }),
    fetch: async (...args) => {
      fetches.push(args);
      return { ok: true, json: async () => scenarios };
    },
    createChatAgent: (threadId) => {
      const agent = {
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
  return { elements, agents, context, fetches, storage, reloads: () => reloads };
}

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
    ["gateway_guardrail_blocked", "此请求已被企业安全策略拦截。请修改请求；如有疑问，请联系 IT 支持。"],
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
