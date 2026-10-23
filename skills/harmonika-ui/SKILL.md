---
name: harmonika-ui
description: Build or refine responsive, accessible HTML/CSS/JavaScript and React interfaces in an existing project.
---

Use for a UI implementation or visual refinement request. Read README, project instructions, source and available mockups before editing; preserve the project's stack. React is optional, and no new framework is required.

Identify the primary user flow and acceptance states: loading, empty, error, success, keyboard focus and narrow screens. For substantial new UI, save a realistic reference in the project's design folder before implementing. Match approved references and keep controls operable by keyboard, labels associated, focus visible and contrast readable. Prefer semantic HTML, responsive grids and reduced-motion support.

Use native file tools, bash and available read-only LSP for implementation and diagnostics. MCP `web_search` can find references; `analyze_image` can inspect a supplied screenshot; `generate_image` is optional only for a requested useful asset. Discover actual schemas before calling tools; never invent parameters. MCP home-scoped output must use absolute paths under Downloads or Projects, with parent folders created through `create_directory` first.

Run the project's existing build/check command. Use harmonika-browser-qa for real desktop/mobile browser evidence when its dependency exists. Report changed files, verified viewport/state coverage and anything untested. A generated mockup or successful compile does not prove browser behavior.
