"use strict";
const https = require("node:https"),
  http = require("node:http");
function deviceHeaders(hostname) {
  if (hostname !== "chat.harmonika.id") return {};
  let id = process.env.HARMONIKA_DEVICE_ID;
  if (!id) {
    try {
      id = require("node:fs")
        .readFileSync(
          require("node:path").join(
            require("node:os").homedir(),
            ".config/harmonika/device-id",
          ),
          "utf8",
        )
        .trim();
    } catch {}
  }
  return /^dev_[a-f0-9]{32}$/.test(id || "")
    ? { "X-Harmonika-Device-ID": id }
    : {};
}
function request(
  url,
  body,
  {
    key,
    timeout = 30000,
    idempotency,
    allowLocal = false,
    method = "POST",
    maxResponseBytes = 2 * 1024 * 1024,
  } = {},
) {
  const u = new URL(url);
  if (
    u.protocol !== "https:" &&
    !(allowLocal && ["127.0.0.1", "localhost"].includes(u.hostname))
  )
    return Promise.reject(Error("gateway_url_denied"));
  return new Promise((resolve, reject) => {
    const payload = method === "GET" || body === undefined ? null : JSON.stringify(body);
    const req = (u.protocol === "https:" ? https : http).request(
      u,
      {
        method,
        headers: {
          authorization: "Bearer " + key,
          "X-HAI-Agent": "true",
          ...deviceHeaders(u.hostname),
          ...(payload === null
            ? {}
            : {
                "content-type": "application/json",
                "content-length": Buffer.byteLength(payload),
              }),
          ...(idempotency ? { "Idempotency-Key": idempotency } : {}),
        },
      },
      (res) => {
        const chunks = [];
        let bytes = 0;
        res.on("data", (b) => {
          bytes += b.length;
          if (bytes > maxResponseBytes) {
            req.destroy(Error("gateway_output_limit"));
            return;
          }
          chunks.push(b);
        });
        res.on("aborted", () => reject(Error("gateway_response_interrupted")));
        res.on("error", () => reject(Error("gateway_transport")));
        res.on("end", () => {
          let value;
          try {
            value = JSON.parse(Buffer.concat(chunks).toString("utf8"));
          } catch {
            return reject(Error("gateway_invalid_json"));
          }
          resolve({
            status: res.statusCode,
            value,
            retryAfter: Number(res.headers["retry-after"]) || 0,
          });
        });
      },
    );
    const timer = setTimeout(
      () => req.destroy(Error("gateway_timeout")),
      timeout,
    );
    req.on("error", (e) =>
      reject(
        Object.assign(
          Error(
            ["gateway_timeout", "gateway_output_limit"].includes(e.message)
              ? e.message
              : "gateway_transport",
          ),
          { causeCode: e.code },
        ),
      ),
    );
    req.on("close", () => clearTimeout(timer));
    req.end(payload === null ? undefined : payload);
  });
}
async function model(messages, tools, options) {
  const now = options.now || (() => performance.now());
  const start = now(),
    budget = options.budgetMs || 120000;
  for (let attempt = 0; attempt < 3; attempt++) {
    const remaining = budget - (now() - start);
    if (remaining <= 0) throw Error("gateway_deadline");
    let out;
    try {
      out = await (options.request || request)(
        options.url,
        {
          model:
            options.modelName ||
            (typeof options.model === "string"
              ? options.model
              : "harmonika-ai"),
          messages,
          tools,
          stream: false,
        },
        { ...options, timeout: Math.min(60000, remaining) },
      );
    } catch (e) {
      if (
        ![
          "gateway_timeout",
          "gateway_transport",
          "gateway_response_interrupted",
        ].includes(e.message) ||
        attempt === 2
      )
        throw e;
    }
    if (out) {
      if (out.status === 200 && out.value.choices?.[0]?.message)
        return out.value.choices[0];
      if (![429, 502, 503, 504].includes(out.status))
        throw Error(
          out.status === 401 || out.status === 403
            ? "gateway_access_denied"
            : "gateway_rejected",
        );
      if (attempt === 2) throw Error("gateway_unavailable");
    }
    const delay = Math.max(
      1000 * (attempt + 1),
      Math.min(out?.retryAfter * 1000 || 0, 10000),
    );
    if (now() - start + delay >= budget) throw Error("gateway_deadline");
    await (options.wait || ((ms) => new Promise((r) => setTimeout(r, ms))))(
      delay,
    );
  }
  throw Error("gateway_unavailable");
}
class Delegation {
  constructor(options) {
    this.options = options;
    this.request = options.request || request;
  }
  async submit(task, requestId) {
    const out = await this.request(
      this.options.skillsUrl,
      {
        skill_id: "agent.delegate",
        input: {
          action: "submit",
          task,
          provider: "auto",
          timeout_seconds: 120,
          request_id: requestId,
        },
      },
      { ...this.options, idempotency: requestId, timeout: 15000 },
    );
    if (![200, 202].includes(out.status))
      throw Error(
        out.status >= 500
          ? "delegate_outcome_unknown"
          : out.status === 429
            ? "delegate_busy"
            : "delegate_rejected",
      );
    const result = out.value.result;
    if (!result || !/^dg_[a-f0-9]{32}$/.test(result.job_id || ""))
      throw Error("delegate_invalid_receipt");
    return result;
  }
  async status(jobId, requestId) {
    const out = await this.request(
      this.options.skillsUrl,
      {
        skill_id: "agent.delegate",
        input: {
          action: "status",
          ...(jobId ? { job_id: jobId } : { request_id: requestId }),
        },
      },
      { ...this.options, timeout: 15000 },
    );
    if (out.status !== 200) throw Error("delegate_status_unavailable");
    const result = out.value.result;
    if (
      !result ||
      (jobId && result.job_id !== jobId) ||
      (!jobId && result.request_id !== requestId)
    )
      throw Error("delegate_correlation_mismatch");
    return result;
  }
}
async function toolRequest(target, body, options = {}) {
  const out = await request(target, body, options);
  if ([429, 502, 503, 504].includes(out.status))
    throw Error("gateway_unavailable");
  if (out.status === 401 || out.status === 403)
    throw Error("gateway_access_denied");
  if (out.status >= 500) throw Error("gateway_outcome_unknown");
  if (out.status !== 200) throw Error("gateway_rejected");
  return out.value;
}
async function toolsGet(target, options = {}) {
  return toolRequest(target, undefined, { ...options, method: "GET" });
}
async function webRead(target, options = {}) {
  return toolsGet(target, options);
}
async function toolsPost(target, body, options = {}) {
  return toolRequest(target, body, { ...options, method: "POST" });
}
async function modelStream(messages, tools, options, onDelta) {
  const streamResult = await new Promise((resolve, reject) => {
    let sawByte = false;
    const u = new URL(options.url);
    const payload = JSON.stringify({
      model: options.model || "harmonika-ai",
      messages,
      tools,
      stream: true,
    });
    const req = https.request(
      u,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: "Bearer " + options.key,
          "X-HAI-Agent": "true",
          "content-length": Buffer.byteLength(payload),
        },
      },
      (res) => {
        if (res.statusCode !== 200) {
          res.resume();
          return reject(Error(
            res.statusCode === 401 || res.statusCode === 403
              ? "gateway_access_denied"
              : "gateway_rejected"));
        }
        let buf = "",
          content = "",
          calls = [],
          done = false;
        const finish = () => {
          if (done) return;
          done = true;
          const message = { role: "assistant", content: content || "" };
          const valid = calls.filter((c) => c && (c.id || c.function?.name));
          if (valid.length)
            message.tool_calls = valid.map((c, i) => ({
              id: c.id || "call_stream_" + i,
              type: "function",
              function: {
                name: c.function?.name || "",
                arguments: c.function?.arguments || "{}",
              },
            }));
          resolve({ message });
        };
        res.on("data", (chunk) => {
          sawByte = true;
          buf += chunk;
          let idx;
          while ((idx = buf.indexOf("\n")) >= 0) {
            const line = buf.slice(0, idx).trim();
            buf = buf.slice(idx + 1);
            if (!line.startsWith("data:")) continue;
            const body = line.slice(5).trim();
            if (body === "[DONE]") { finish(); return; }
            let j;
            try { j = JSON.parse(body); } catch { continue; }
            const d = j.choices?.[0]?.delta;
            if (!d) continue;
            if (typeof d.content === "string" && d.content) {
              content += d.content;
              try { onDelta && onDelta(d.content); } catch {}
            }
            for (const tc of d.tool_calls || []) {
              const i = tc.index ?? 0;
              calls[i] = calls[i] || { id: "", function: { name: "", arguments: "" } };
              if (tc.id) calls[i].id = tc.id;
              if (tc.function?.name) calls[i].function.name += tc.function.name;
              if (tc.function?.arguments) calls[i].function.arguments += tc.function.arguments;
            }
          }
        });
        res.on("end", finish);
        res.on("aborted", () => (sawByte ? finish() : reject(Error("gateway_response_interrupted"))));
      },
    );
    req.setTimeout(120000, () => req.destroy(Error("gateway_timeout")));
    req.on("error", (e) =>
      reject(/timeout/.test(e.message) ? e : Error("gateway_transport")));
    req.write(payload);
    req.end();
  });
  // Safety net: streaming shape invalid -> non-stream fallback.
  const hasContent = streamResult.message.content?.trim();
  const hasCalls = streamResult.message.tool_calls?.length;
  if (!hasContent && !hasCalls) return model(messages, tools, options);
  return streamResult;
}

module.exports = { request, model, modelStream, Delegation, webRead, toolsGet, toolsPost };
