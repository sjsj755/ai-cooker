import { spawn } from "node:child_process";
import readline from "node:readline";

const EXE =
  "C:\\Users\\陈乔源\\Documents\\Codex\\zh-cn-patched\\app\\resources\\cua_node\\bin\\node_repl.exe";

const env = {
  ...process.env,
  NODE_REPL_NATIVE_PIPE_CONNECT_TIMEOUT_MS: "1000",
  NODE_REPL_NODE_MODULE_DIRS:
    "C:\\Users\\陈乔源\\Documents\\Codex\\zh-cn-patched\\app\\resources\\cua_node\\bin\\node_modules",
  NODE_REPL_NODE_PATH:
    "C:\\Users\\陈乔源\\Documents\\Codex\\zh-cn-patched\\app\\resources\\cua_node\\bin\\node.exe",
  NODE_REPL_TRUSTED_CODE_PATHS:
    "C:\\Users\\陈乔源\\.codex;C:\\Users\\陈乔源\\Documents\\Codex\\zh-cn-patched\\app\\resources\\cua_node\\bin\\node_modules",
  CODEX_HOME: "C:\\Users\\陈乔源\\.codex",
  NODE_REPL_TRUSTED_BROWSER_CLIENT_SHA256S:
    "aa6d8ffe273c2a7b1b20105a35ae49f69bdb4f025640807a31bcb47f8b67b92b",
  BROWSER_USE_AVAILABLE_BACKENDS: "chrome,iab",
  NODE_REPL_INSTRUCTIONS_USE_CASE_BROWSER:
    "Control the in-app browser in conjunction with the Browser Plugin.",
  NODE_REPL_INSTRUCTIONS_USE_CASE_CHROME:
    "Control the Chrome browser in conjunction with the Chrome Plugin.",
  BROWSER_USE_CODEX_APP_BUILD_FLAVOR: "prod",
  BROWSER_USE_CODEX_APP_VERSION: "26.730.61639",
  SKY_CUA_NATIVE_PIPE: "1",
  SKY_CUA_NATIVE_PIPE_DIRECTORY:
    "\\\\.\\pipe\\codex-computer-use-a5fece62-ddab-46e1-9fc4-22dd4e9eb6de",
};

const child = spawn(EXE, [], { env, stdio: ["pipe", "pipe", "pipe"] });
const pending = new Map();
let nextId = 1;

function send(method, params, id) {
  const msgId = id ?? nextId++;
  child.stdin.write(JSON.stringify({ jsonrpc: "2.0", id: msgId, method, params }) + "\n");
  return msgId;
}

function notify(method, params) {
  child.stdin.write(JSON.stringify({ jsonrpc: "2.0", method, params }) + "\n");
}

function callJs(code, timeoutMs = 60000, includeMeta = true) {
  return new Promise((resolve, reject) => {
    const id = send("tools/call", {
      name: "js",
      arguments: { code, timeout_ms: timeoutMs },
      ...(includeMeta
        ? {
            _meta: {
              "x-codex-turn-metadata": {
                session_id: "local:01a048f0-fcd8-7de1-955e-2badce2c0d2e",
                turn_id: "probe-turn-001",
              },
            },
          }
        : {}),
    });
    pending.set(id, { resolve, reject });
  });
}

let stdoutBuf = "";
child.stdout.on("data", (d) => {
  stdoutBuf += d.toString("utf8");
  let idx;
  while ((idx = stdoutBuf.indexOf("\n")) >= 0) {
    const line = stdoutBuf.slice(0, idx).trim();
    stdoutBuf = stdoutBuf.slice(idx + 1);
    if (!line) continue;
    let msg;
    try {
      msg = JSON.parse(line);
    } catch {
      console.log("RAW:", line.slice(0, 500));
      continue;
    }
    if (msg.id != null && pending.has(msg.id)) {
      const { resolve, reject } = pending.get(msg.id);
      pending.delete(msg.id);
      if (msg.error) reject(new Error(JSON.stringify(msg.error)));
      else resolve(msg.result);
    }
  }
});

child.stderr.on("data", (d) => {
  const s = d.toString("utf8").trim();
  if (s) console.error("SRVERR:", s.slice(0, 2000));
});

child.on("error", (e) => {
  console.error("SPAWN ERR:", e.message);
  process.exit(1);
});

child.on("exit", (code) => {
  console.error("EXIT", code);
  process.exit(code ?? 0);
});

const rl = readline.createInterface({ input: process.stdin });

// handshake
const initId = send("initialize", {
  protocolVersion: "2024-11-05",
  capabilities: {},
  clientInfo: { name: "codex-bridge", version: "1.0" },
});
pending.set(initId, {
  resolve: async () => {
    const tools = await new Promise((resolve, reject) => {
      const id = send("tools/list", {});
      pending.set(id, { resolve, reject });
    });
    console.error("TOOLS:", tools.tools.map((t) => t.name).join(","));
    console.error("BRIDGE_READY");
  },
  reject: (e) => console.error("INIT FAIL:", e.message),
});

rl.on("line", async (line) => {
  const input = line.trim();
  if (!input) return;
  let req;
  try {
    req = JSON.parse(input);
  } catch {
    console.error("BAD REQ:", input.slice(0, 200));
    return;
  }
  try {
    const result =
      req.tool && req.tool !== "js"
        ? await new Promise((resolve, reject) => {
            const id = send("tools/call", {
              name: req.tool,
              arguments: req.args ?? {},
            });
            pending.set(id, { resolve, reject });
          })
        : await callJs(
            req.code,
            req.timeout_ms ?? 60000,
            req.meta !== false
          );
    console.log(JSON.stringify({ ok: true, result }));
  } catch (e) {
    console.log(JSON.stringify({ ok: false, error: e.message }));
  }
});
