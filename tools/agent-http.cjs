"use strict";
const https = require("node:https"),
  http = require("node:http");
function request(
  url,
  body,
  { key, timeout = 30000, idempotency, allowLocal = false } = {},
) {
  const u = new URL(url);
  if (
    u.protocol !== "https:" &&
    !(allowLocal && ["127.0.0.1", "localhost"].includes(u.hostname))
  )
    return Promise.reject(Error("gateway_url_denied"));
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const req = (u.protocol === "https:" ? https : http).request(
      u,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: "Bearer " + key,
          "X-HAI-Agent": "true",
          "content-length": Buffer.byteLength(payload),
          ...(idempotency ? { "Idempotency-Key": idempotency } : {}),
        },
      },
      (res) => {
        let raw = "",
          bytes = 0;
        res.on("data", (b) => {
          bytes += b.length;
          if (bytes > 2 * 1024 * 1024) {
            req.destroy(Error("gateway_output_limit"));
            return;
          }
          raw += b;
        });
        res.on("aborted", () => reject(Error("gateway_response_interrupted")));
        res.on("error", () => reject(Error("gateway_transport")));
        res.on("end", () => {
          let value;
          try {
            value = JSON.parse(raw);
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
    req.end(payload);
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
        { model: options.model || "harmonika-ai", messages, tools, stream: false },
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
async function webRead(target, { key, timeout = 30000 } = {}) {
  const u = new URL(target);
  if (u.protocol !== "https:")
    return Promise.reject(Error("gateway_url_denied"));
  return new Promise((resolve, reject) => {
    const req = https.request(
      u,
      {
        method: "GET",
        headers: {
          authorization: "Bearer " + key,
          "X-HAI-Agent": "true",
        },
      },
      (res) => {
        let raw = "",
          bytes = 0;
        res.on("data", (b) => {
          bytes += b.length;
          if (bytes > 2 * 1024 * 1024) {
            req.destroy(Error("gateway_output_limit"));
            return;
          }
          raw += b;
        });
        res.on("aborted", () => reject(Error("gateway_response_interrupted")));
        res.on("end", () => {
          if ([429, 502, 503, 504].includes(res.statusCode))
            return reject(Error("gateway_unavailable"));
          if (res.statusCode === 401 || res.statusCode === 403)
            return reject(Error("gateway_access_denied"));
          if (res.statusCode !== 200)
            return reject(Error("gateway_rejected"));
          try {
            resolve(JSON.parse(raw));
          } catch {
            reject(Error("gateway_invalid_response"));
          }
        });
      },
    );
    req.setTimeout(timeout, () => req.destroy(Error("gateway_timeout")));
    req.on("error", (e) => reject(/timeout/.test(e.message) ? e : Error("gateway_transport")));
    req.end();
  });
}

async function toolsGet(path, { key, timeout = 30000 } = {}) {
  const u = new URL(path);
  if (u.protocol !== "https:")
    return Promise.reject(Error("gateway_url_denied"));
  return new Promise((resolve, reject) => {
    const req = https.request(
      u,
      {
        method: "GET",
        headers: {
          authorization: "Bearer " + key,
          "X-HAI-Agent": "true",
        },
      },
      (res) => {
        let raw = "",
          bytes = 0;
        res.on("data", (b) => {
          bytes += b.length;
          if (bytes > 2 * 1024 * 1024) {
            req.destroy(Error("gateway_output_limit"));
            return;
          }
          raw += b;
        });
        res.on("aborted", () => reject(Error("gateway_response_interrupted")));
        res.on("end", () => {
          if ([429, 502, 503, 504].includes(res.statusCode))
            return reject(Error("gateway_unavailable"));
          if (res.statusCode === 401 || res.statusCode === 403)
            return reject(Error("gateway_access_denied"));
          if (res.statusCode !== 200)
            return reject(Error("gateway_rejected"));
          try {
            resolve(JSON.parse(raw));
          } catch {
            reject(Error("gateway_invalid_response"));
          }
        });
      },
    );
    req.setTimeout(timeout, () => req.destroy(Error("gateway_timeout")));
    req.on("error", (e) =>
      reject(/timeout/.test(e.message) ? e : Error("gateway_transport")),
    );
    req.end();
  });
}

async function toolsPost(path, body, { key, timeout = 30000 } = {}) {
  const u = new URL(path);
  const isLocal = u.protocol === "http:" && u.hostname === "127.0.0.1";
  if (u.protocol !== "https:" && !isLocal)
    return Promise.reject(Error("gateway_url_denied"));
  const lib = isLocal ? http : https;
  return new Promise((resolve, reject) => {
    const payload = JSON.stringify(body);
    const req = lib.request(
      u,
      {
        method: "POST",
        headers: {
          "content-type": "application/json",
          authorization: "Bearer " + key,
          "X-HAI-Agent": "true",
          "content-length": Buffer.byteLength(payload),
        },
      },
      (res) => {
        let raw = "",
          bytes = 0;
        res.on("data", (b) => {
          bytes += b.length;
          if (bytes > 2 * 1024 * 1024) {
            req.destroy(Error("gateway_output_limit"));
            return;
          }
          raw += b;
        });
        res.on("aborted", () => reject(Error("gateway_response_interrupted")));
        res.on("end", () => {
          if ([429, 502, 503, 504].includes(res.statusCode))
            return reject(Error("gateway_unavailable"));
          if (res.statusCode === 401 || res.statusCode === 403)
            return reject(Error("gateway_access_denied"));
          if (res.statusCode !== 200)
            return reject(Error("gateway_rejected"));
          try {
            resolve(JSON.parse(raw));
          } catch {
            reject(Error("gateway_invalid_response"));
          }
        });
      },
    );
    req.setTimeout(timeout, () => req.destroy(Error("gateway_timeout")));
    req.on("error", (e) =>
      reject(/timeout/.test(e.message) ? e : Error("gateway_transport")),
    );
    req.write(payload);
    req.end();
  });
}

module.exports = { request, model, Delegation, webRead, toolsGet, toolsPost };
