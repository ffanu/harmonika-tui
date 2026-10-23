---
name: harmonika-browser-qa
description: Run real isolated Playwright desktop and mobile browser smoke checks with screenshots when Playwright is installed.
---

Use to verify a web UI in an actual browser. `scripts/smoke.cjs` launches its own Chromium, captures desktop and mobile screenshots and records console/page errors and HTTP failures. This is a basic smoke check, not exhaustive accessibility or interaction coverage.

Run: `harmonika skill browser --url http://localhost:3000 --out /absolute/project/path/qa-run`

Only test a URL the user authorized. Localhost is allowed for the user's development server; remote private IPs, private hostnames, non-HTTP URLs, credentials and redirects/subresources into private networks are blocked. Public URLs may contact third parties, so use an appropriate test environment. The CLI selects the owner-installed Playwright runtime. For developer tests only, the helper supports `HARMONIKA_PLAYWRIGHT_MODULE` as an absolute trusted module path and otherwise detects an existing installation. Use the public CLI in normal sessions; private runtime paths may be blocked by the shell guard. Never install dependencies or browser binaries implicitly. A missing package/browser is an explicit unsupported result.

Create a new, empty output folder under the project. For MCP home-scoped folders, call `create_directory` with an absolute Downloads/Projects path first; the script uses native filesystem access. It refuses overwrite and symlink traversal. Inspect the screenshots through available image tools and test keyboard/navigation/form behavior separately when relevant. Report tested URL, actual viewport sizes, artifacts and errors. Close only the browser the script owns; never kill shared browsers, terminals, Harmonika or other agent processes. Use no fabricated browser evidence.
