#!/usr/bin/env node
"use strict";
// MCP Harmonika Tools — exposes the self-hosted AI services (web search,
// web reader, knowledge base, file parser, ASR, image/video generation,
// repo reader, safety, tokenizer, similarity, rerank, vision) to the native
// Harmonika terminal via the chat.harmonika.id gateway. Importing this
// module has no side effects; the stdio loop only starts when executed
// directly. Network access goes exclusively through the shared helper
// ./agent-http.cjs (or an injected replacement for tests).
const fs = require("node:fs");
const path = require("node:path");
const os = require("node:os");
const crypto = require("node:crypto");
const readline = require("node:readline");
const { pathToFileURL } = require("node:url");
const sharedHttp = require("./agent-http.cjs");

const BASE = "https://chat.harmonika.id/api/tools";
const VISION_URL = "https://chat.harmonika.id/api/tools/vlm/generate";
const RESULT_LIMIT = 180000;
const IMAGE_MAX_BYTES = 24 * 1024 * 1024;
const PNG_SIG = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]);
const JOB_RE = /^[A-Za-z0-9_-]{1,128}$/;
const DEFAULT_MAX_RESPONSE_BYTES = 2 * 1024 * 1024;
const ARG_STRING_MAX = 1000000;
const ARG_B64_MAX = 64 * 1024 * 1024;
const ARG_ARRAY_MAX = 64;
const ARG_DEPTH_MAX = 8;
const SAFE_ERRORS = new Set([
  "arguments_must_be_object",
  "invalid_response",
  "invalid_image_data",
  "image_too_large",
  "output_write_failed",
  "output_limit",
  "video_timeout",
  "video_failed",
  "video_status_failed",
  "tool_failed",
  "gateway_invalid_response",
  "gateway_url_denied",
  "gateway_access_denied",
  "gateway_busy",
  "gateway_unavailable",
  "gateway_rejected",
  "gateway_transport",
  "gateway_timeout",
  "gateway_output_limit",
  "gateway_invalid_json",
  "gateway_response_interrupted",
  "gateway_deadline",
]);
const SAFE_PREFIXES = [
  "missing_argument:",
  "invalid_argument_type:",
  "unknown_argument:",
  "argument_too_long:",
  "argument_too_many_items:",
  "invalid_argument_format:",
];

function apiKey() {
  if (process.env.HARMONIKA_API_KEY) return process.env.HARMONIKA_API_KEY.trim();
  const f = path.join(
    process.env.HOME || os.homedir(),
    ".config/harmonika/harmonika-api-key",
  );
  try {
    return fs.existsSync(f) ? fs.readFileSync(f, "utf8").trim() : "";
  } catch {
    return "";
  }
}

function safeGatewayError(e) {
  const m = e instanceof Error ? e.message : "";
  return /^gateway_[a-z0-9_]+$/.test(m) ? m : "gateway_transport";
}

function safeError(e) {
  const m = e instanceof Error ? e.message : "";
  if (SAFE_ERRORS.has(m)) return m;
  for (const p of SAFE_PREFIXES)
    if (
      m.startsWith(p) &&
      m.length - p.length <= 64 &&
      /^[A-Za-z0-9_.[\]-]+$/.test(m.slice(p.length))
    )
      return m;
  return "tool_failed";
}

function isPng(buf) {
  return (
    Buffer.isBuffer(buf) &&
    buf.length >= 33 &&
    buf.subarray(0, 8).equals(PNG_SIG) &&
    buf.readUInt32BE(8) === 13 &&
    buf.toString("ascii", 12, 16) === "IHDR"
  );
}

function validateValue(schema, value, name, depth) {
  if (depth > ARG_DEPTH_MAX) return "invalid_argument_type:" + name;
  const type = schema && schema.type;
  if (type === "object") {
    if (typeof value !== "object" || value === null || Array.isArray(value))
      return "invalid_argument_type:" + name;
    const props = (schema.properties || {});
    const keys = Object.keys(props);
    for (const k of schema.required || [])
      if (!(k in value)) return "missing_argument:" + k;
    if (keys.length)
      for (const k of Object.keys(value)) {
        if (!Object.prototype.hasOwnProperty.call(props, k))
          return "unknown_argument:" + k;
        const err = validateValue(
          props[k],
          value[k],
          (name ? name + "." : "") + k,
          depth + 1,
        );
        if (err) return err;
      }
    return null;
  }
  if (type === "array") {
    if (!Array.isArray(value)) return "invalid_argument_type:" + name;
    if (value.length > ARG_ARRAY_MAX) return "argument_too_many_items:" + name;
    if (schema.items)
      for (let i = 0; i < value.length; i++) {
        const err = validateValue(
          schema.items,
          value[i],
          name + "[" + i + "]",
          depth + 1,
        );
        if (err) return err;
      }
    return null;
  }
  if (type === "string") {
    if (typeof value !== "string") return "invalid_argument_type:" + name;
    const leaf = name.split(".").pop() || name;
    const cap = /_b64$/.test(leaf) ? ARG_B64_MAX : ARG_STRING_MAX;
    if (value.length > cap) return "argument_too_long:" + name;
    return null;
  }
  if (type === "number") {
    if (
      typeof value !== "number" ||
      !Number.isFinite(value) ||
      value < 0 ||
      value > 1e9
    )
      return "invalid_argument_type:" + name;
    return null;
  }
  if (type === "boolean") {
    if (typeof value !== "boolean") return "invalid_argument_type:" + name;
    return null;
  }
  return null;
}

function validateArgs(schema, args) {
  const a = args === undefined || args === null ? {} : args;
  if (typeof a !== "object" || Array.isArray(a)) return "arguments_must_be_object";
  return validateValue(schema, a, "", 0);
}

async function defaultOutput(buffer, dir) {
  try {
    await fs.promises.mkdir(dir, { recursive: true, mode: 0o700 });
    for (let i = 0; i < 5; i++) {
      const name =
        "harmonika-" +
        Date.now().toString(36) +
        "-" +
        crypto.randomBytes(8).toString("hex") +
        ".png";
      const file = path.join(dir, name);
      let fh;
      try {
        fh = await fs.promises.open(file, "wx", 0o600);
      } catch (e) {
        if (e && e.code === "EEXIST") continue;
        throw e;
      }
      try {
        await fh.writeFile(buffer);
      } catch (e) {
        await fh.close().catch(() => {});
        await fs.promises.unlink(file).catch(() => {});
        throw e;
      }
      await fh.close();
      return { path: file, uri: pathToFileURL(file).href };
    }
    throw Error("output_write_failed");
  } catch {
    throw Error("output_write_failed");
  }
}

function errResult(code, extra) {
  return {
    content: [
      { type: "text", text: JSON.stringify({ ok: false, error: code, ...(extra || {}) }) },
    ],
    isError: true,
  };
}

function errResp(id, code, message) {
  return {
    jsonrpc: "2.0",
    id: id === undefined ? null : id,
    error: { code, message },
  };
}


const TRANSLATION_FIELDS = {
  text: {type:"string",minLength:1,maxLength:40000},
  source: {type:"string",maxLength:64,description:"auto, language code, or language name"},
  target: {type:"string",minLength:1,maxLength:64,description:"Language code/name, e.g. en, id, jv, su, ace, min"},
  tone: {type:"string",enum:["neutral","formal","informal"]},
  register: {type:"string",enum:["neutral","ngoko","krama","krama_inggil","loma","lemes","andap","alus"]},
  glossary: {type:"object",description:"Exact source terms mapped to required translations (max 64 terms)"}
};
function translationInput(a, detect=false) {
  const allowed=detect?["text"]:Object.keys(TRANSLATION_FIELDS);
  for(const k of Object.keys(a))if(!allowed.includes(k))throw Error("unknown_argument:"+k);
  if(typeof a.text!=="string"||!a.text.trim())throw Error("missing_argument:text");
  if(a.text.length>40000||Buffer.byteLength(a.text,"utf8")>160000)throw Error("argument_too_long:text");
  if(!detect){
    if(typeof a.target!=="string"||!a.target.trim())throw Error("missing_argument:target");
    for(const name of ["source","target"]){
      if(a[name]!==undefined&&(typeof a[name]!=="string"||a[name].length>64||!a[name].trim()))throw Error("invalid_argument_format:"+name);
    }
    if(a.tone!==undefined&&!TRANSLATION_FIELDS.tone.enum.includes(a.tone))throw Error("invalid_argument_format:tone");
    if(a.register!==undefined&&!TRANSLATION_FIELDS.register.enum.includes(a.register))throw Error("invalid_argument_format:register");
    if(a.glossary!==undefined){
      if(!a.glossary||typeof a.glossary!=="object"||Array.isArray(a.glossary))throw Error("invalid_argument_type:glossary");
      const entries=Object.entries(a.glossary);
      if(entries.length>64)throw Error("argument_too_many_items:glossary");
      for(const [k,v] of entries)if(!k||k.length>160||typeof v!=="string"||!v||v.length>160||["__proto__","constructor","prototype"].includes(k))throw Error("invalid_argument_format:glossary");
    }
  }
  return a;
}

function createServer(options = {}) {
  const doRequest = options.request || sharedHttp.request;
  const wait =
    options.wait || ((ms) => new Promise((r) => setTimeout(r, ms)));
  const pollMs =
    Number.isFinite(options.pollMs) && options.pollMs >= 0
      ? options.pollMs
      : 10000;
  const videoTimeoutMs =
    Number.isFinite(options.videoTimeoutMs) && options.videoTimeoutMs > 0
      ? options.videoTimeoutMs
      : 10 * 60 * 1000;
  const outputDir = () => {
    if (typeof options.outputDir === "string" && options.outputDir)
      return options.outputDir;
    if (
      typeof process.env.HARMONIKA_OUTPUT_DIR === "string" &&
      process.env.HARMONIKA_OUTPUT_DIR
    )
      return process.env.HARMONIKA_OUTPUT_DIR;
    return path.join(os.homedir(), "Downloads", "Harmonika");
  };
  const writeOutput =
    options.output || ((buf) => defaultOutput(buf, outputDir()));

  async function gw(method, p, body, opts = {}) {
    const timeout =
      Number.isFinite(opts.timeout) && opts.timeout > 0
        ? opts.timeout
        : 300000;
    const maxResponseBytes =
      Number.isFinite(opts.maxResponseBytes) && opts.maxResponseBytes > 0
        ? opts.maxResponseBytes
        : DEFAULT_MAX_RESPONSE_BYTES;
    let out;
    try {
      out = await doRequest(BASE + p, body === undefined ? null : body, {
        key: apiKey(),
        method,
        timeout,
        maxResponseBytes,
      });
    } catch (e) {
      throw Error(safeGatewayError(e));
    }
    if (!out || typeof out !== "object" || typeof out.status !== "number")
      throw Error("gateway_invalid_response");
    if (out.status !== 200)
      throw Error(
        out.status === 401 || out.status === 403
          ? "gateway_access_denied"
          : out.status === 429
            ? "gateway_busy"
            : out.status >= 500
              ? "gateway_unavailable"
              : "gateway_rejected",
      );
    if (out.value === null || typeof out.value !== "object")
      throw Error("invalid_response");
    return out.value;
  }

  const TOOLS = [
    {
      name:"translate_text",
      description:"Translate text faithfully using the backend translation engine. Supports registered world languages and Indonesian regional languages (Jawa/ngoko/krama, Sunda/lemes, Bali, Aceh, Minang, Betawi, Madura, Batak, Banjar, Bugis, Makassar, Gorontalo). Preserve names, numbers and placeholders. Regional results require review. Source auto-detection is available.",
      inputSchema:{type:"object",additionalProperties:false,properties:TRANSLATION_FIELDS,required:["text","target"]},
      run:a=>gw("POST","/translation/translate",translationInput(a),{timeout:135000})
    },
    {
      name:"detect_language",description:"Detect a text's language with model-estimated uncertainty; do not treat uncertain regional language identification as verified.",
      inputSchema:{type:"object",additionalProperties:false,properties:{text:TRANSLATION_FIELDS.text},required:["text"]},
      run:a=>gw("POST","/translation/detect",translationInput(a,true),{timeout:135000})
    },
    {
      name:"translation_languages",description:"List the backend language-selection catalog, Indonesian aliases and regional review requirements. Catalog registration is not proof of native-speaker quality.",
      inputSchema:{type:"object",additionalProperties:false,properties:{regional_only:{type:"boolean"}}},
      run:async a=>{
        if(Object.keys(a).some(k=>k!=="regional_only"))throw Error("unknown_argument:translation_languages");
        const out=await gw("GET","/translation/languages",null);
        return a.regional_only&&Array.isArray(out.languages)?{...out,languages:out.languages.filter(x=>x.regional===true)}:out;
      }
    },
    {
      name:"translate_batch",description:"Translate up to 16 text items with independent language targets and optional glossaries; total text is bounded to 40,000 characters.",
      inputSchema:{type:"object",additionalProperties:false,properties:{items:{type:"array",minItems:1,maxItems:16,items:{type:"object",additionalProperties:false,properties:TRANSLATION_FIELDS,required:["text","target"]}}},required:["items"]},
      run:a=>{
        if(Object.keys(a).some(k=>k!=="items"))throw Error("unknown_argument:translate_batch");
        if(!Array.isArray(a.items)||a.items.length<1||a.items.length>16)throw Error("invalid_argument_format:items");
        let chars=0;for(const item of a.items){translationInput(item);chars+=item.text.length;}
        if(chars>40000||Buffer.byteLength(JSON.stringify(a),"utf8")>262144)throw Error("argument_too_long:items");
        return gw("POST","/translation/batch",a,{timeout:135000});
      }
    },

    {
      name: "web_search",
      description:
        "Search the web (DuckDuckGo) — top results with titles, URLs, snippets",
      inputSchema: {
        type: "object",
        properties: { query: { type: "string" } },
        required: ["query"],
      },
      run: (a) =>
        gw(
          "GET",
          "/webreader/search?q=" + encodeURIComponent(a.query),
          null,
        ),
    },
    {
      name: "web_read",
      description: "Fetch one public URL and return cleaned text (title + body)",
      inputSchema: {
        type: "object",
        properties: { url: { type: "string" } },
        required: ["url"],
      },
      run: (a) => {
        if (!/^https?:\/\/[^\s]+$/i.test(a.url))
          throw Error("invalid_argument_format:url");
        return gw("GET", "/webreader/read?url=" + encodeURIComponent(a.url), null);
      },
    },
    {
      name: "kb_store",
      description: "Store one knowledge entry for later semantic retrieval",
      inputSchema: {
        type: "object",
        properties: {
          text: { type: "string" },
          tags: { type: "array", items: { type: "string" } },
        },
        required: ["text"],
      },
      run: (a) =>
        gw("POST", "/knowledge/store", {
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
      run: (a) =>
        gw("POST", "/knowledge/search", {
          query: a.query,
          limit: 5,
          rerank: !!a.rerank,
        }),
    },
    {
      name: "file_parse",
      description: "Parse pdf/docx/xlsx/pptx/txt/csv base64 content into text",
      inputSchema: {
        type: "object",
        properties: {
          filename: { type: "string" },
          content_b64: { type: "string" },
        },
        required: ["filename", "content_b64"],
      },
      run: (a) =>
        gw(
          "POST",
          "/fileparser/parse",
          { filename: a.filename, content_b64: a.content_b64 },
          { maxResponseBytes: 8 * 1024 * 1024 },
        ),
    },
    {
      name: "transcribe",
      description:
        "Transcribe audio/video (base64) to text via faster-whisper on RTX",
      inputSchema: {
        type: "object",
        properties: {
          filename: { type: "string" },
          content_b64: { type: "string" },
          language: { type: "string", description: "ISO code hint, e.g. id / en" },
        },
        required: ["filename", "content_b64"],
      },
      run: (a) =>
        gw(
          "POST",
          "/asr/transcribe",
          {
            filename: a.filename,
            content_b64: a.content_b64,
            ...(a.language !== undefined ? { language: a.language } : {}),
          },
          { maxResponseBytes: 4 * 1024 * 1024 },
        ),
    },
    {
      name: "image_generate",
      description:
        "Generate a PNG from a prompt via sd-turbo on RTX (~seconds). The PNG is verified (header/IHDR, <=24MiB) and saved as an exclusive private file under HARMONIKA_OUTPUT_DIR or ~/Downloads/Harmonika; returns path/uri/bytes — never base64",
      inputSchema: {
        type: "object",
        properties: {
          prompt: { type: "string" },
          steps: { type: "number" },
          size: { type: "number" },
        },
        required: ["prompt"],
      },
      run: async (a) => {
        const d = await gw(
          "POST",
          "/imagegen/generate",
          { prompt: a.prompt, steps: a.steps, size: a.size },
          { timeout: 300000, maxResponseBytes: 48 * 1024 * 1024 },
        );
        const b64 =
          d && typeof d.png_b64 === "string"
            ? d.png_b64
            : d && typeof d.image_b64 === "string"
              ? d.image_b64
              : "";
        if (!b64 || !/^[A-Za-z0-9+/]+={0,2}$/.test(b64)) throw Error("invalid_image_data");
        const buf = Buffer.from(b64, "base64");
        if (!isPng(buf)) throw Error("invalid_image_data");
        if (buf.length > IMAGE_MAX_BYTES) throw Error("image_too_large");
        const saved = await writeOutput(buf);
        return {
          ok: true,
          format: "png",
          bytes: buf.length,
          path: saved.path,
          uri: saved.uri,
          ...(typeof d.took_seconds === "number" &&
          Number.isFinite(d.took_seconds)
            ? { took_seconds: d.took_seconds }
            : {}),
        };
      },
    },
    {
      name: "video_generate",
      description:
        "Generate a 6s video from a prompt via CogVideoX-2b on RTX (minutes). Polls read-only status until done; on timeout/failure returns a structured error preserving the original job id and never re-submits",
      inputSchema: {
        type: "object",
        properties: { prompt: { type: "string" } },
        required: ["prompt"],
      },
      run: async (a) => {
        const sub = await gw(
          "POST",
          "/video/generate",
          { prompt: a.prompt },
          { timeout: 60000 },
        );
        const job = sub && sub.job;
        if (typeof job !== "string" || !JOB_RE.test(job))
          throw Error("invalid_response");
        const fetchPath = "/api/tools/video/file/" + job;
        const deadline = Date.now() + videoTimeoutMs;
        let st = { state: "queued" };
        let misses = 0;
        while (Date.now() < deadline) {
          await wait(pollMs);
          try {
            st = await gw(
              "GET",
              "/video/status/" + encodeURIComponent(job),
              null,
              { timeout: 30000 },
            );
            misses = 0;
          } catch {
            if (++misses >= 3)
              return {
                ok: false,
                error: "video_status_failed",
                job,
                state: typeof st.state === "string" ? st.state : "unknown",
                fetch: fetchPath,
              };
            continue;
          }
          if (st.state === "done" || st.state === "failed") break;
        }
        if (st.state === "done")
          return {
            ok: true,
            job,
            state: "done",
            size:
              typeof st.size === "number" && Number.isFinite(st.size) && st.size >= 0
                ? st.size
                : null,
            fetch: fetchPath,
          };
        if (st.state === "failed")
          return {
            ok: false,
            error: "video_failed",
            job,
            state: "failed",
            fetch: fetchPath,
          };
        return {
          ok: false,
          error: "video_timeout",
          job,
          state: typeof st.state === "string" ? st.state : "unknown",
          fetch: fetchPath,
        };
      },
    },
    {
      name: "video_status",
      description:
        "Read-only status of one existing video generation job by id; never submits or re-submits work",
      inputSchema: {
        type: "object",
        properties: { job: { type: "string" } },
        required: ["job"],
      },
      annotations: { readOnlyHint: true },
      run: async (a) => {
        if (typeof a.job !== "string" || !JOB_RE.test(a.job))
          throw Error("invalid_argument_format:job");
        const st = await gw(
          "GET",
          "/video/status/" + encodeURIComponent(a.job),
          null,
          { timeout: 30000 },
        );
        return {
          ok: true,
          job: a.job,
          state: typeof st.state === "string" ? st.state : "unknown",
          size:
            typeof st.size === "number" && Number.isFinite(st.size) && st.size >= 0
              ? st.size
              : null,
          fetch: "/api/tools/video/file/" + a.job,
        };
      },
    },
    {
      name: "vision",
      description:
        "Analyze an image (base64) with the vision model via the authenticated chat.harmonika.id gateway. Ask a question about the image; returns the readable answer",
      inputSchema: {
        type: "object",
        properties: {
          image_b64: { type: "string" },
          question: { type: "string" },
        },
        required: ["image_b64"],
      },
      run: async (a) => {
        let out;
        try {
          out = await doRequest(
            VISION_URL,
            {
              image_b64: a.image_b64,
              question:
                a.question ||
                "Describe this image and transcribe any visible text.",
            },
            {
              key: apiKey(),
              method: "POST",
              timeout: 300000,
              maxResponseBytes: DEFAULT_MAX_RESPONSE_BYTES,
            },
          );
        } catch (e) {
          throw Error(safeGatewayError(e));
        }
        if (!out || typeof out !== "object" || typeof out.status !== "number")
          throw Error("gateway_invalid_response");
        if (out.status !== 200)
          throw Error(
            out.status === 401 || out.status === 403
              ? "gateway_access_denied"
              : out.status === 429
                ? "gateway_busy"
                : out.status >= 500
                  ? "gateway_unavailable"
                  : "gateway_rejected",
          );
        const v = out.value;
        if (v === null || typeof v !== "object") throw Error("invalid_response");
        const answer = [
          v.answer,
          v.result && typeof v.result.answer === "string"
            ? v.result.answer
            : undefined,
          v.text,
          v.output_text,
          v.description,
          v.choices &&
          Array.isArray(v.choices) &&
          v.choices[0] &&
          v.choices[0].message &&
          typeof v.choices[0].message.content === "string"
            ? v.choices[0].message.content
            : undefined,
        ].find((x) => typeof x === "string" && x.trim().length > 0);
        if (!answer) throw Error("invalid_response");
        return {
          ok: true,
          answer,
          ...(typeof v.model === "string" ? { model: v.model } : {}),
        };
      },
    },
    {
      name: "repo_read",
      description: "Read one public GitHub repo: tree, languages, README, key files",
      inputSchema: {
        type: "object",
        properties: { url: { type: "string" } },
        required: ["url"],
      },
      run: (a) => {
        if (!/^https?:\/\/[^\s]+$/i.test(a.url))
          throw Error("invalid_argument_format:url");
        return gw("POST", "/zread/read", { url: a.url });
      },
    },
    {
      name: "safety_check",
      description: "Heuristic content safety check — returns safe/risk/flags",
      inputSchema: {
        type: "object",
        properties: { text: { type: "string" } },
        required: ["text"],
      },
      run: (a) => gw("POST", "/safety/check", { text: a.text }),
    },
    {
      name: "tokenize",
      description: "Count tokens for texts using the multilingual tokenizer",
      inputSchema: {
        type: "object",
        properties: { texts: { type: "array", items: { type: "string" } } },
        required: ["texts"],
      },
      run: (a) =>
        gw("POST", "/embedding/tokenize", { texts: a.texts.slice(0, 64) }),
    },
    {
      name: "similarity",
      description: "Semantic similarity of texts against one query",
      inputSchema: {
        type: "object",
        properties: {
          query: { type: "string" },
          texts: { type: "array", items: { type: "string" } },
        },
        required: ["query", "texts"],
      },
      run: (a) =>
        gw("POST", "/embedding/similarity", {
          query: a.query,
          texts: a.texts.slice(0, 64),
        }),
    },
    {
      name: "rerank",
      description: "Cross-encoder rerank of documents against a query",
      inputSchema: {
        type: "object",
        properties: {
          query: { type: "string" },
          documents: { type: "array", items: { type: "string" } },
        },
        required: ["query", "documents"],
      },
      run: (a) =>
        gw("POST", "/rerank/rerank", {
          query: a.query,
          documents: a.documents.slice(0, 64),
        }),
    },
  ];

  function listTools() {
    return TOOLS.map((t) => ({
      name: t.name,
      description: t.description,
      inputSchema: t.inputSchema,
      ...(t.annotations ? { annotations: t.annotations } : {}),
    }));
  }

  async function callTool(name, args) {
    const tool = TOOLS.find((t) => t.name === name);
    if (!tool)
      return errResult("unknown_tool:" + String(name).replace(/[^A-Za-z0-9_-]/g, "").slice(0, 64));
    const verr = validateArgs(tool.inputSchema, args);
    if (verr) return errResult(verr);
    try {
      const a = args === undefined || args === null ? {} : args;
      const d = await tool.run(a);
      let text;
      try {
        text = JSON.stringify(d === undefined ? null : d);
      } catch {
        return errResult("tool_failed");
      }
      if (typeof text !== "string") return errResult("tool_failed");
      if (Buffer.byteLength(text) > RESULT_LIMIT)
        return errResult("output_limit", { limit: RESULT_LIMIT, bytes: Buffer.byteLength(text) });
      const isError = !!(
        d &&
        typeof d === "object" &&
        !Array.isArray(d) &&
        (d.ok === false || typeof d.error === "string")
      );
      if (isError) {
        const job = typeof d.job === "string" && JOB_RE.test(d.job) ? d.job : null;
        return errResult(safeError(Error(typeof d.error === "string" ? d.error : "tool_failed")), {
          ...(job ? {job, fetch:"/api/tools/video/file/"+job} : {}),
          ...(["queued","running","done","failed"].includes(d.state) ? {state:d.state} : {}),
        });
      }
      return { content: [{ type: "text", text }] };
    } catch (e) {
      return errResult(safeError(e));
    }
  }

  async function handleMessage(msg) {
    let id;
    let idKnown = false;
    try {
      if (msg === null || typeof msg !== "object" || Array.isArray(msg))
        return errResp(null, -32600, "invalid_request");
      const hasId = Object.prototype.hasOwnProperty.call(msg, "id");
      if (!hasId) return null;
      id = msg.id;
      const idOk =
        id === null || typeof id === "string" || typeof id === "number";
      if (!idOk || Number.isNaN(id)) return errResp(null, -32600, "invalid_request");
      idKnown = true;
      const method = msg.method;
      if (
        typeof method !== "string" ||
        method.length === 0 ||
        method.length > 128
      )
        return errResp(id, -32600, "invalid_request");
      if (msg.jsonrpc !== undefined && msg.jsonrpc !== "2.0")
        return errResp(id, -32600, "invalid_request");
      const params = msg.params;
      const paramsOk =
        params === undefined ||
        (typeof params === "object" && params !== null && !Array.isArray(params));
      if (!paramsOk) return errResp(id, -32602, "invalid_params");
      if (method === "initialize")
        return {
          jsonrpc: "2.0",
          id,
          result: {
            protocolVersion: "2024-11-05",
            capabilities: { tools: {} },
            serverInfo: { name: "harmonika-tools", version: "1.0.0" },
          },
        };
      if (method === "ping") return { jsonrpc: "2.0", id, result: {} };
      if (method === "tools/list")
        return { jsonrpc: "2.0", id, result: { tools: listTools() } };
      if (method === "tools/call") {
        if (params === undefined) return errResp(id, -32602, "invalid_params");
        const name = params.name;
        if (typeof name !== "string" || name.length === 0)
          return errResp(id, -32602, "invalid_params");
        const rawArgs = params.arguments === undefined ? {} : params.arguments;
        if (typeof rawArgs !== "object" || rawArgs === null || Array.isArray(rawArgs))
          return errResp(id, -32602, "invalid_params");
        const result = await callTool(name, rawArgs);
        return { jsonrpc: "2.0", id, result };
      }
      return errResp(id, -32601, "method_not_found");
    } catch {
      return errResp(idKnown ? id : null, -32603, "internal_error");
    }
  }

  async function handleLine(line) {
    const s = typeof line === "string" ? line.trim() : "";
    if (!s) return null;
    let msg;
    try {
      msg = JSON.parse(s);
    } catch {
      return errResp(null, -32700, "parse_error");
    }
    return handleMessage(msg);
  }

  return { handleMessage, handleLine, callTool, listTools };
}

function main() {
  const server = createServer();
  const rl = readline.createInterface({ input: process.stdin, terminal: false });
  rl.on("line", (line) => {
    Promise.resolve()
      .then(() => server.handleLine(line))
      .then((res) => {
        if (res) process.stdout.write(JSON.stringify(res) + "\n");
      })
      .catch(() => {});
  });
}

if (require.main === module) main();

module.exports = { createServer, main };
