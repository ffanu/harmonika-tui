#!/usr/bin/env node
"use strict";
// MCP Harmonika Tools — exposes the self-hosted AI services (web search,
// web reader, knowledge base, file parser, ASR, image/video generation,
// repo reader, safety, tokenizer, similarity, rerank) to the native
// Harmonika terminal via the chat.harmonika.id gateway.
const https = require("https");
const fs = require("fs");
const path = require("path");
const readline = require("readline");

const BASE = "https://chat.harmonika.id/api/tools";

function apiKey() {
  if (process.env.HARMONIKA_API_KEY) return process.env.HARMONIKA_API_KEY.trim();
  const f = path.join(process.env.HOME || "/Users/id", ".config/harmonika/harmonika-api-key");
  return fs.existsSync(f) ? fs.readFileSync(f, "utf8").trim() : "";
}

function request(method, p, body) {
  return new Promise((resolve, reject) => {
    const payload = body ? JSON.stringify(body) : null;
    const req = https.request(
      BASE + p,
      {
        method,
        headers: {
          authorization: "Bearer " + apiKey(),
          "X-HAI-Agent": "true",
          ...(payload
            ? {
                "content-type": "application/json",
                "content-length": Buffer.byteLength(payload),
              }
            : {}),
        },
      },
      (res) => {
        let raw = "";
        res.on("data", (b) => (raw += b));
        res.on("end", () => {
          if (res.statusCode !== 200)
            return reject(Error("gateway_" + res.statusCode + ": " + raw.slice(0, 200)));
          try {
            resolve(JSON.parse(raw));
          } catch {
            reject(Error("invalid_response"));
          }
        });
      },
    );
    req.setTimeout(300000, () => req.destroy(Error("timeout")));
    req.on("error", (e) => reject(/timeout/.test(e.message) ? e : Error("transport")));
    if (payload) req.write(payload);
    req.end();
  });
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

const TOOLS = [
  {
    name: "web_search",
    description: "Search the web (DuckDuckGo) — top results with titles, URLs, snippets",
    inputSchema: { type: "object", properties: { query: { type: "string" } }, required: ["query"] },
    run: (a) => request("GET", "/webreader/search?q=" + encodeURIComponent(a.query)),
  },
  {
    name: "web_read",
    description: "Fetch one public URL and return cleaned text (title + body)",
    inputSchema: { type: "object", properties: { url: { type: "string" } }, required: ["url"] },
    run: (a) => request("GET", "/webreader/read?url=" + encodeURIComponent(a.url)),
  },
  {
    name: "kb_store",
    description: "Store one knowledge entry for later semantic retrieval",
    inputSchema: {
      type: "object",
      properties: { text: { type: "string" }, tags: { type: "array", items: { type: "string" } } },
      required: ["text"],
    },
    run: (a) =>
      request("POST", "/knowledge/store", {
        text: a.text,
        source: "mcp-harmonika",
        kind: "note",
        tags: Array.isArray(a.tags) ? a.tags.slice(0, 8) : [],
      }),
  },
  {
    name: "kb_search",
    description: "Semantic search over the Harmonika knowledge base",
    inputSchema: {
      type: "object",
      properties: { query: { type: "string" }, rerank: { type: "boolean" } },
      required: ["query"],
    },
    run: (a) => request("POST", "/knowledge/search", { query: a.query, limit: 5, rerank: !!a.rerank }),
  },
  {
    name: "file_parse",
    description: "Parse pdf/docx/xlsx/pptx/txt/csv base64 content into text",
    inputSchema: {
      type: "object",
      properties: { filename: { type: "string" }, content_b64: { type: "string" } },
      required: ["filename", "content_b64"],
    },
    run: (a) => request("POST", "/fileparser/parse", a),
  },
  {
    name: "transcribe",
    description: "Transcribe audio/video (base64) to text via faster-whisper on RTX",
    inputSchema: {
      type: "object",
      properties: {
        filename: { type: "string" },
        content_b64: { type: "string" },
        language: { type: "string", description: "ISO code hint, e.g. id / en" },
      },
      required: ["filename", "content_b64"],
    },
    run: (a) => request("POST", "/asr/transcribe", a),
  },
  {
    name: "image_generate",
    description: "Generate a PNG image from a prompt via sd-turbo on RTX (~seconds). Returns png_b64",
    inputSchema: {
      type: "object",
      properties: { prompt: { type: "string" }, steps: { type: "number" }, size: { type: "number" } },
      required: ["prompt"],
    },
    run: async (a) => {
      const d = await request("POST", "/imagegen/generate", { prompt: a.prompt, steps: a.steps, size: a.size });
      return { ok: d.ok, took_seconds: d.took_seconds, format: d.format, png_b64_kb: Math.round((d.png_b64 || "").length / 1370), png_b64: d.png_b64 };
    },
  },
  {
    name: "video_generate",
    description: "Generate a 6s video from a prompt via CogVideoX-2b on RTX (minutes). Polls until done; returns job id + fetch path",
    inputSchema: { type: "object", properties: { prompt: { type: "string" } }, required: ["prompt"] },
    run: async (a) => {
      const sub = await request("POST", "/video/generate", { prompt: a.prompt });
      const deadline = Date.now() + 10 * 60 * 1000;
      let st = { state: "queued" };
      while (Date.now() < deadline) {
        await sleep(10000);
        st = await request("GET", "/video/status/" + sub.job);
        if (st.state === "done" || st.state === "failed") break;
      }
      return {
        job: sub.job,
        state: st.state,
        size: st.size || null,
        error: st.error || null,
        fetch: "/api/tools/video/file/" + sub.job,
      };
    },
  },
  {
    name: "repo_read",
    description: "Read one public GitHub repo: tree, languages, README, key files",
    inputSchema: { type: "object", properties: { url: { type: "string" } }, required: ["url"] },
    run: (a) => request("POST", "/zread/read", { url: a.url }),
  },
  {
    name: "safety_check",
    description: "Heuristic content safety check — returns safe/risk/flags",
    inputSchema: { type: "object", properties: { text: { type: "string" } }, required: ["text"] },
    run: (a) => request("POST", "/safety/check", a),
  },
  {
    name: "tokenize",
    description: "Count tokens for texts using the multilingual tokenizer",
    inputSchema: { type: "object", properties: { texts: { type: "array", items: { type: "string" } } }, required: ["texts"] },
    run: (a) => request("POST", "/embedding/tokenize", { texts: a.texts.slice(0, 64) }),
  },
  {
    name: "similarity",
    description: "Semantic similarity of texts against one query",
    inputSchema: {
      type: "object",
      properties: { query: { type: "string" }, texts: { type: "array", items: { type: "string" } } },
      required: ["query", "texts"],
    },
    run: (a) => request("POST", "/embedding/similarity", { query: a.query, texts: a.texts.slice(0, 64) }),
  },
  {
    name: "rerank",
    description: "Cross-encoder rerank of documents against a query",
    inputSchema: {
      type: "object",
      properties: { query: { type: "string" }, documents: { type: "array", items: { type: "string" } } },
      required: ["query", "documents"],
    },
    run: (a) => request("POST", "/rerank/rerank", { query: a.query, documents: a.documents.slice(0, 64) }),
  },
];

function respond(id, result) {
  process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, result }) + "\n");
}
function respondErr(id, message) {
  process.stdout.write(JSON.stringify({ jsonrpc: "2.0", id, error: { code: -32000, message } }) + "\n");
}

const rl = readline.createInterface({ input: process.stdin });
rl.on("line", (line) => {
  line = line.trim();
  if (!line) return;
  let msg;
  try {
    msg = JSON.parse(line);
  } catch {
    return;
  }
  if (msg.method === "initialize") {
    respond(msg.id, {
      protocolVersion: "2024-11-05",
      capabilities: { tools: {} },
      serverInfo: { name: "harmonika-tools", version: "1.0.0" },
    });
  } else if (msg.method === "notifications/initialized") {
    // no response
  } else if (msg.method === "ping") {
    respond(msg.id, {});
  } else if (msg.method === "tools/list") {
    respond(msg.id, {
      tools: TOOLS.map((t) => ({
        name: t.name,
        description: t.description,
        inputSchema: t.inputSchema,
      })),
    });
  } else if (msg.method === "tools/call") {
    const tool = TOOLS.find((t) => t.name === msg.params.name);
    if (!tool) return respondErr(msg.id, "unknown_tool");
    let out;
    tool
      .run(msg.params.arguments || {})
      .then((d) => {
        let text;
        try {
          text = JSON.stringify(d);
        } catch {
          text = String(d);
        }
        if (text.length > 180000) text = text.slice(0, 180000) + "…";
        respond(msg.id, { content: [{ type: "text", text }] });
      })
      .catch((e) =>
        respond(msg.id, {
          content: [{ type: "text", text: "error: " + e.message }],
          isError: true,
        }),
      );
  }
});
