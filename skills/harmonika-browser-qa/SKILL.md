---
name: harmonika-browser-qa
description: Run real isolated Playwright desktop and mobile browser smoke checks with screenshots when Playwright is installed.
---

Use to verify a web UI in an actual browser. `scripts/smoke.cjs` launches its own Chromium, captures desktop and mobile screenshots and records console/page errors and HTTP failures. This is a basic smoke check, not exhaustive accessibility or interaction coverage.

Run: `harmonika skill browser --url http://localhost:3000 --out /absolute/project/path/qa-run`

Only test a URL the user authorized. Localhost is allowed for the user's development server; remote private IPs, private hostnames, non-HTTP URLs, credentials and redirects/subresources into private networks are blocked. Public URLs may contact third parties, so use an appropriate test environment. The CLI selects the owner-installed Playwright runtime. For developer tests only, the helper supports `HARMONIKA_PLAYWRIGHT_MODULE` as an absolute trusted module path and otherwise detects an existing installation. Use the public CLI in normal sessions; private runtime paths may be blocked by the shell guard. Never install dependencies or browser binaries implicitly. A missing package/browser is an explicit unsupported result.

Create a new, empty output folder under the project. For MCP home-scoped folders, call `create_directory` with an absolute Downloads/Projects path first; the script uses native filesystem access. It refuses overwrite and symlink traversal. Inspect the screenshots through available image tools and test keyboard/navigation/form behavior separately when relevant. Report tested URL, actual viewport sizes, artifacts and errors. Close only the browser the script owns; never kill shared browsers, terminals, Harmonika or other agent processes. Use no fabricated browser evidence.

Wait for the actual asynchronous content, not only its parent card. For example,
after reloading a ticket list, wait for its attachment download link before taking
the screenshot and measuring controls. An empty selector match is missing evidence,
not a passing tap-target check. Include links, buttons, input, textarea and select;
apply the user's requested minimum size (the generic matrix's 24px check does not
prove a project requirement of 36px or 44px). Verify both the changed local UI and
the actual deployed revision, preserving the original failed result.

## Responsive and accessibility matrix

`scripts/qa_matrix.cjs` audits your own (or explicitly authorized) page across six emulated
profiles: desktop 1440x900 light, desktop 1280x800 dark with `id-ID`, tablet 834x1112 touch,
tablet 768x1024 touch, mobile 390x844 touch and small mobile 360x640 dark. For each profile it records status,
console/page errors, horizontal overflow, elements wider than the viewport, tap targets under
24px, images without `alt`, broken images, inputs without a label, duplicate ids, low-contrast
text samples, text under 12px and the presence of viewport/lang/title, then writes `report.json`
plus a full-page screenshot per profile.

Run through MCP tool `qa_matrix` with `url` (and optional absolute `out_dir`), or directly:

`node ~/.config/harmonika-terminal/qa_matrix.cjs --url http://localhost:3000 --out /abs/new/empty_dir`

The same SSRF guard as the smoke check applies: localhost needs an authorized development
server, private addresses and private hostnames are blocked, embedded credentials are refused
and the output directory must be new/empty without dot-directories or symlinks. Emulation is
not a real-device test: state the profiles used, report `report.json` findings honestly and do
not claim WCAG conformance from automated checks alone. No credentials are submitted; pages
behind a login need a session you already own.

## Fixed mockup frames

Use the existing `qa_matrix` MCP tool with optional `frames`, an array of at most eight
objects: `name`, `width`, `height`, `fit` and `requiredSelectors`. For example,
`[{"name":"desktop","width":1440,"height":1080,"fit":true,"requiredSelectors":[".kpis strong",".chart","tbody tr",".activity","footer"]}]`.
The CLI accepts the same JSON with `--frames`. Start only an authorized project preview
on a free loopback port, then use its HTTP URL. The existing URL/output firewall still applies.

These screenshots use scale 1 and the exact requested frame, with mobile auto-shrink disabled.
Checks distinguish file dimensions from content fitting, missing/offscreen regions and ancestor
clipping. Set fit=true when the whole mockup must fit; ordinary pages are allowed to scroll.
Required selectors must cover the actual important content, including every table row and KPI
value, not only a large container. Run the normal six-profile matrix too; a fixed mobile PNG
does not establish tablet readiness. Inspect the actual screenshots and domain data separately.
`ok=true` means execution succeeded; `verdict=needs-work` must never be called a design pass.
Console/runtime errors and failed requests now contribute to the verdict.

The report also flags single-word control/chart labels broken across lines. Prefer
localized short month names on compact charts and preserve the full names accessibly.
This heuristic and the contrast sampling are supporting evidence, not a global design
judgment. Linux reuses the installed browser with CPU rendering; its operator config
may select an existing Chromium, or PLAYWRIGHT_CHROMIUM_EXECUTABLE_PATH may select an
explicit installed executable. Missing browser/runtime remains an unsupported result;
the tool never downloads browser binaries implicitly.

The Linux operator connector may explicitly select `captureMethod=chromium-screen-pdf-raster`
for static CPU mockup export when compositor screenshots are unavailable. It uses screen
media and exact colors, rejects multiple PDF pages and reports its method. Treat this as a
browser raster export, not a compositor screenshot or proof of dynamic video/WebGL output.
Inspect the returned PNG. Mac defaults continue to use regular Chromium screenshots.
