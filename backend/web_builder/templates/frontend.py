"""Frontend generators (skills 57-71): framework-aware UI artifacts.

Supported target frameworks per skill:
- react   : Next.js App Router conventions (app/<route>/page.tsx, components/)
- vue     : Nuxt 3 conventions (pages/<route>.vue, components/)
- svelte  : SvelteKit conventions (src/routes/<route>/+page.svelte, src/lib/components)
- html    : standalone static pages in site/ (no build step required)

Every user-supplied label, href, or option is escaped or validated before
interpolation; no raw string reaches generated markup or script contexts.
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Tuple

from . import safe
from .safe import SkillInputError, rel_path, render

Schema = Dict[str, Any]
Pair = Tuple[str, str]

FE_FRAMEWORKS = ["react", "vue", "svelte", "html"]

_SLUG_S = {"type": "string", "pattern": safe.SLUG_RE.pattern, "minLength": 2, "maxLength": 50}
_FRAMEWORK_S = {"type": "string", "enum": FE_FRAMEWORKS, "default": "react"}

LIMIT_NO_BUILD = (
    "Artifacts are static source files; no build, dev server, or browser check "
    "has been executed. Wire the paths in data.paths into your project."
)
LIMIT_CHART_DEP = (
    "Chart libraries are declared in data.dependencies but NOT installed; "
    "add them with your package manager before the component compiles. "
    "The html variant loads Chart.js from a CDN and needs network access at runtime."
)


def _obj(props: Dict[str, Any], required: List[str]) -> Schema:
    return {
        "type": "object",
        "additionalProperties": False,
        "required": required,
        "properties": props,
    }


def _bounded(value: Any, label: str, max_len: int = 200, min_len: int = 1) -> str:
    return safe.bounded(value, label, max_len, min_len)


def _href(value: Any, label: str = "href") -> str:
    """Root-relative internal path or https URL."""
    v = _bounded(value, label, 300)
    if re.match(r"^https://[A-Za-z0-9._~:/?#\[\]@!$&'()*+,;=%-]+$", v):
        return v
    if not re.match(r"^/[A-Za-z0-9._~\-]*$", v) or "//" in v:
        raise SkillInputError("%s: must be a root-relative path like /about or an https URL" % label)
    return v


def _endpoint(value: Any, label: str = "endpoint") -> str:
    v = _bounded(value, label, 200)
    if not re.match(r"^/[A-Za-z0-9._~\-/]*$", v) or "//" in v:
        raise SkillInputError("%s: must be a root-relative API path like /api/items" % label)
    return v


def _route(value: Any, label: str = "route") -> str:
    return safe.route_segment(value, label)


_ALLOWED_FIELD_TYPES = ("text", "email", "password", "number", "tel", "textarea", "select", "checkbox")


def _field_spec(value: Any) -> List[Dict[str, Any]]:
    """Validate the shared form-field spec used by form/auth generators."""
    if not isinstance(value, list) or not 1 <= len(value) <= 24:
        raise SkillInputError("fields: expected a list of 1..24 field objects")
    out: List[Dict[str, Any]] = []
    seen = set()
    for item in value:
        if not isinstance(item, dict):
            raise SkillInputError("fields: each field must be an object")
        name = safe.snake(item.get("name"), "field name")
        if name in seen:
            raise SkillInputError("fields: duplicate field name %r" % name)
        seen.add(name)
        ftype = item.get("type", "text")
        if ftype not in _ALLOWED_FIELD_TYPES:
            raise SkillInputError(
                "fields: unsupported type %r (allowed: %s)" % (ftype, ", ".join(_ALLOWED_FIELD_TYPES))
            )
        field: Dict[str, Any] = {
            "name": name,
            "label": _bounded(item.get("label", name.replace("_", " ").title()), "field label", 100),
            "type": ftype,
            "required": bool(item.get("required", False)),
            "placeholder": _bounded(item.get("placeholder", ""), "placeholder", 120, 0),
        }
        if ftype == "select":
            options = item.get("options")
            if not isinstance(options, list) or not 1 <= len(options) <= 20 or not all(isinstance(o, str) and len(o) <= 100 for o in options):
                raise SkillInputError("fields: select options must be 1..20 strings of <=100 chars")
            field["options"] = options
        out.append(field)
    return out


def _column_spec(value: Any) -> List[Dict[str, str]]:
    if not isinstance(value, list) or not 1 <= len(value) <= 16:
        raise SkillInputError("columns: expected a list of 1..16 column objects")
    out: List[Dict[str, str]] = []
    for item in value:
        if not isinstance(item, dict):
            raise SkillInputError("columns: each column must be an object")
        out.append({
            "key": safe.snake(item.get("key"), "column key"),
            "label": _bounded(item.get("label", ""), "column label", 60),
        })
    keys = [c["key"] for c in out]
    if len(set(keys)) != len(keys):
        raise SkillInputError("columns: duplicate column keys")
    return out


def _features_spec(value: Any, label: str = "features", max_items: int = 8) -> List[Dict[str, str]]:
    if not isinstance(value, list) or not 1 <= len(value) <= max_items:
        raise SkillInputError("%s: expected a list of 1..%d items" % (label, max_items))
    out = []
    for item in value:
        if not isinstance(item, dict):
            raise SkillInputError("%s: each item must be an object" % label)
        out.append({
            "title": _bounded(item.get("title"), "%s title" % label, 100),
            "body": _bounded(item.get("body"), "%s body" % label, 400),
        })
    return out


def _html_shell(title: str, body: str, script: str = "", extra_css: str = "") -> str:
    css = BASE_HTML_CSS + extra_css
    return render('''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>@@TITLE@@</title>
  <style>
@@CSS@@
  </style>
</head>
<body>
@@BODY@@
@@SCRIPT@@</body>
</html>
''', {
        "TITLE": safe.html_text(title),
        "CSS": css,
        "BODY": body,
        "SCRIPT": ("<script>\n%s\n</script>\n" % script) if script else "",
    })


BASE_HTML_CSS = '''    :root { font-family: system-ui, -apple-system, sans-serif; line-height: 1.55; color: #111827; }
    body { margin: 0; }
    .wrap { max-width: 44rem; margin: 0 auto; padding: 1.5rem; }
    a { color: #2563eb; }
    .error { color: #b91c1c; }
    .ok { color: #047857; }
    button { cursor: pointer; padding: .5rem 1rem; border-radius: .375rem;
             border: 1px solid #d1d5db; background: #fff; font: inherit; }
    button.primary { background: #2563eb; border-color: #2563eb; color: #fff; }
    button:disabled { opacity: .6; cursor: progress; }
    input, select, textarea { padding: .5rem; border: 1px solid #d1d5db;
                              border-radius: .375rem; font: inherit; width: 100%;
                              box-sizing: border-box; }
    label { display: block; margin: .75rem 0 .25rem; font-weight: 600; }
    table { border-collapse: collapse; width: 100%; }
    th, td { text-align: left; padding: .5rem; border-bottom: 1px solid #e5e7eb; }
    dialog { border: none; border-radius: .5rem; padding: 1.25rem; max-width: 28rem; width: calc(100vw - 2rem); }
    dialog::backdrop { background: rgb(0 0 0 / .4); }
    .cards { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr)); }
    .card { border: 1px solid #e5e7eb; border-radius: .5rem; padding: 1rem; }
    .badge { display: inline-block; border-radius: 9999px; padding: .125rem .625rem;
             font-size: .8125rem; background: #eff6ff; color: #1d4ed8; }
    .field-error { color: #b91c1c; font-size: .875rem; margin: .25rem 0 0; }'''


def _register(skill_id: str, number: int, summary: str, schema: Schema, handler: Any) -> None:
    SKILLS[skill_id] = {"number": number, "summary": summary, "schema": schema, "handler": handler}


SKILLS: Dict[str, Dict[str, Any]] = {}


# ---------------------------------------------------------------------------
# 57 fe.gen.layout
# ---------------------------------------------------------------------------

def _nav_ts(links: List[Tuple[str, str]]) -> str:
    return "\n".join('  { href: %s, label: %s },' % (safe.js_str(href), safe.js_str(label)) for label, href in links)


def _nav_html(links: List[Tuple[str, str]]) -> str:
    return "\n".join('      <a href="%s">%s</a>' % (safe.html_text(href), safe.html_text(label)) for label, href in links)


def _layout(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    framework = args["framework"]
    site = _bounded(args["site_name"], "site_name", 80)
    tagline = _bounded(args["tagline"], "tagline", 200, 0)
    links = [(safe.html_text(l["label"]), _href(l["href"])) for l in args["nav_links"]]
    has_sidebar = bool(args["sidebar"])
    paths: List[Pair] = []
    usage: str

    if framework == "react":
        paths.append((rel_path(project, "components", "SiteHeader.tsx"), render('''"use client";

import Link from "next/link";

type NavLink = { href: string; label: string };

const NAV_LINKS: NavLink[] = [
@@NAV_TS@@
];

export function SiteHeader() {
  return (
    <header>
      <div className="header-inner">
        <Link href="/" className="brand">
          @@SITE_JS@@
        </Link>
        <nav aria-label="Main navigation">
          {NAV_LINKS.map((link) => (
            <Link key={link.href} href={link.href}>
              {link.label}
            </Link>
          ))}
        </nav>
      </div>
    </header>
  );
}
''', {"NAV_TS": _nav_ts(links), "SITE_JS": safe.js_str(site)})))
        paths.append((rel_path(project, "components", "SiteFooter.tsx"), render('''import Link from "next/link";

type NavLink = { href: string; label: string };

const NAV_LINKS: NavLink[] = [
@@NAV_TS@@
];

export function SiteFooter() {
  return (
    <footer>
      <p>
        © {new Date().getFullYear()} @@SITE_JS@@. @@TAGLINE_JS@@
      </p>
      <nav aria-label="Footer navigation">
        {NAV_LINKS.map((link) => (
          <Link key={link.href} href={link.href}>
            {link.label}
          </Link>
        ))}
      </nav>
    </footer>
  );
}
''', {"NAV_TS": _nav_ts(links), "SITE_JS": safe.js_str(site), "TAGLINE_JS": safe.js_str(tagline)})))
        if has_sidebar:
            paths.append((rel_path(project, "components", "Sidebar.tsx"), '''"use client";

import Link from "next/link";

type SidebarSection = { title: string; links: { href: string; label: string }[] };

const DEFAULT_SECTIONS: SidebarSection[] = [
  { title: "Main", links: [{ href: "/", label: "Home" }] },
];

export function Sidebar({ sections = DEFAULT_SECTIONS }: { sections?: SidebarSection[] }) {
  return (
    <aside aria-label="Sidebar">
      {sections.map((section) => (
        <section key={section.title}>
          <h2>{section.title}</h2>
          <ul>
            {section.links.map((link) => (
              <li key={link.href}>
                <Link href={link.href}>{link.label}</Link>
              </li>
            ))}
          </ul>
        </section>
      ))}
    </aside>
  );
}
'''))
        paths.append((rel_path(project, "styles", "layout.css"), LAYOUT_CSS))
        usage = ('import { SiteHeader } from "@/components/SiteHeader"; and '
                 'import { SiteFooter } from "@/components/SiteFooter"; into app/layout.tsx '
                 'and render them around {children}'
                 + (', then import { Sidebar } from "@/components/Sidebar"; on pages that need it.' if has_sidebar else "."))
        files = [w[0] for w in paths]

    elif framework == "vue":
        paths.append((rel_path(project, "components", "SiteHeader.vue"), render('''<script setup lang="ts">
type NavLink = { to: string; label: string };

const links: NavLink[] = [
@@NAV_TS@@
];
</script>

<template>
  <header>
    <div class="header-inner">
      <NuxtLink to="/" class="brand">@@SITE_HTML@@</NuxtLink>
      <nav aria-label="Main navigation">
        <NuxtLink v-for="link in links" :key="link.to" :to="link.to">{{ link.label }}</NuxtLink>
      </nav>
    </div>
  </header>
</template>

<style scoped>
.header-inner { display: flex; align-items: center; justify-content: space-between;
  gap: 1rem; padding: .75rem 1.25rem; border-bottom: 1px solid #e5e7eb; }
.brand { font-weight: 700; text-decoration: none; }
nav { display: flex; gap: 1rem; flex-wrap: wrap; }
@media (max-width: 640px) { .header-inner { flex-direction: column; align-items: flex-start; } }
</style>
''', {"NAV_TS": _nav_ts(links), "SITE_HTML": safe.html_text(site)})))
        paths.append((rel_path(project, "components", "SiteFooter.vue"), render('''<script setup lang="ts">
const year = new Date().getFullYear();
</script>

<template>
  <footer>
    <p>© {{ year }} @@SITE_HTML@@. @@TAGLINE_HTML@@</p>
  </footer>
</template>

<style scoped>
footer { margin-top: 3rem; padding: 1.25rem; border-top: 1px solid #e5e7eb;
  display: flex; justify-content: space-between; flex-wrap: wrap; gap: .75rem; }
</style>
''', {"SITE_HTML": safe.html_text(site), "TAGLINE_HTML": safe.html_text(tagline)})))
        if has_sidebar:
            paths.append((rel_path(project, "components", "AppSidebar.vue"), VUE_SIDEBAR))
        usage = ("Add <SiteHeader /> and <SiteFooter /> to app.vue around <NuxtPage />"
                 + (", then use <AppSidebar /> in pages that need it." if has_sidebar else "."))
        files = [w[0] for w in paths]

    elif framework == "svelte":
        paths.append((rel_path(project, "src", "lib", "components", "SiteHeader.svelte"), render('''<script lang="ts">
  type NavLink = { href: string; label: string };
  const links: NavLink[] = [
@@NAV_TS@@
  ];
</script>

<header>
  <div class="header-inner">
    <a href="/">@@SITE_HTML@@</a>
    <nav aria-label="Main navigation">
      {#each links as link (link.href)}
        <a href={link.href}>{link.label}</a>
      {/each}
    </nav>
  </div>
</header>

<style>
  .header-inner { display: flex; align-items: center; justify-content: space-between;
    gap: 1rem; padding: .75rem 1.25rem; border-bottom: 1px solid #e5e7eb; }
  nav { display: flex; gap: 1rem; flex-wrap: wrap; }
  @media (max-width: 640px) { .header-inner { flex-direction: column; align-items: flex-start; } }
</style>
''', {"NAV_TS": _nav_ts(links), "SITE_HTML": safe.html_text(site)})))
        paths.append((rel_path(project, "src", "lib", "components", "SiteFooter.svelte"), render('''<footer>
  <p>© {new Date().getFullYear()} @@SITE_HTML@@. @@TAGLINE_HTML@@</p>
</footer>

<style>
  footer { margin-top: 3rem; padding: 1.25rem; border-top: 1px solid #e5e7eb; }
</style>
''', {"SITE_HTML": safe.html_text(site), "TAGLINE_HTML": safe.html_text(tagline)})))
        if has_sidebar:
            paths.append((rel_path(project, "src", "lib", "components", "Sidebar.svelte"), SVELTE_SIDEBAR))
        usage = ("Import both components in src/routes/+layout.svelte and render them around "
                 "{@render children()}"
                 + ("; import Sidebar on pages that need it." if has_sidebar else "."))
        files = [w[0] for w in paths]

    else:
        header_html = render('''<header>
    <div class="header-inner">
      <a href="/" class="brand">@@SITE_HTML@@</a>
      <nav aria-label="Main navigation">
@@NAV_HTML@@
      </nav>
    </div>
  </header>
''', {"SITE_HTML": safe.html_text(site), "NAV_HTML": _nav_html(links)})
        footer_html = render('''  <footer>
    <p>© <span class="js-year"></span> @@SITE_HTML@@. @@TAGLINE_HTML@@</p>
  </footer>
''', {"SITE_HTML": safe.html_text(site), "TAGLINE_HTML": safe.html_text(tagline)})
        sidebar_html = render('''  <aside class="layout-sidebar" aria-label="Sidebar">
    <h2>Menu</h2>
    <ul>
@@NAV_HTML@@
    </ul>
  </aside>
''', {"NAV_HTML": _nav_html(links)}) if has_sidebar else ""
        body = render('''<div class="wrap">
@@HEADER@@@@SIDEBAR_OPEN@@@@MAIN@@
  </main>
@@SIDEBAR_CLOSE@@@@FOOTER@@
</div>
<script>
  document.querySelectorAll(".js-year").forEach((el) => {
    el.textContent = String(new Date().getFullYear());
  });
</script>
''', {
            "HEADER": header_html,
            "SIDEBAR_OPEN": '  <div class="layout-columns">\n' + sidebar_html if has_sidebar else "",
            "MAIN": '''  <main>
    <h1>@@SITE@@</h1>
    <p>@@TAGLINE@@</p>
    <p>Replace this placeholder content with your page body.</p>'''.replace("@@SITE@@", safe.html_text(site)).replace("@@TAGLINE@@", safe.html_text(tagline) or "Add a tagline."),
            "SIDEBAR_CLOSE": "  </div>\n" if has_sidebar else "",
            "FOOTER": footer_html,
        })
        css = BASE_HTML_CSS + render('''
    .header-inner { display: flex; align-items: center; justify-content: space-between;
      gap: 1rem; padding: .75rem 1.25rem; border-bottom: 1px solid #e5e7eb; flex-wrap: wrap; }
    .brand { font-weight: 700; text-decoration: none; color: inherit; }
    header nav { display: flex; gap: 1rem; flex-wrap: wrap; }
    footer { margin-top: 3rem; padding-top: 1.25rem; border-top: 1px solid #e5e7eb; }
@@SIDEBAR_CSS@@''', {
            "SIDEBAR_CSS": '''
    .layout-columns { display: flex; gap: 1.5rem; }
    .layout-sidebar { min-width: 10rem; border-right: 1px solid #e5e7eb; padding-right: 1rem; }
    @media (max-width: 640px) { .layout-columns { flex-direction: column; } }''' if has_sidebar else "",
        })
        paths.append((rel_path(project, "site", "layout.html"), _html_shell(site, body, extra_css=css)))
        usage = "site/layout.html is a complete reference page; copy its header/footer into other site pages."
        files = [w[0] for w in paths]

    data = {
        "framework": framework,
        "site_name": site,
        "paths": files,
        "wiring": usage,
    }
    return {"data": data, "writes": paths, "limitations": [LIMIT_NO_BUILD]}


LAYOUT_CSS = '''/* Layout primitives generated by fe.gen.layout (compose with your design system). */
header > .header-inner {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  padding: 0.75rem 1.25rem;
  border-bottom: 1px solid #e5e7eb;
}

header .brand {
  font-weight: 700;
  text-decoration: none;
  color: inherit;
}

header nav {
  display: flex;
  gap: 1rem;
  flex-wrap: wrap;
}

footer {
  margin-top: 3rem;
  padding: 1.25rem;
  border-top: 1px solid #e5e7eb;
}

aside {
  padding: 1rem;
  border-right: 1px solid #e5e7eb;
  min-width: 12rem;
}

@media (max-width: 640px) {
  header > .header-inner {
    flex-direction: column;
    align-items: flex-start;
  }
}
'''

VUE_SIDEBAR = '''<script setup lang="ts">
type SidebarSection = { title: string; links: { to: string; label: string }[] };

withDefaults(
  defineProps<{ sections?: SidebarSection[] }>(),
  {
    sections: () => [{ title: "Main", links: [{ to: "/", label: "Home" }] }],
  },
);
</script>

<template>
  <aside aria-label="Sidebar">
    <section v-for="section in sections" :key="section.title">
      <h2>{{ section.title }}</h2>
      <ul>
        <li v-for="link in section.links" :key="link.to">
          <NuxtLink :to="link.to">{{ link.label }}</NuxtLink>
        </li>
      </ul>
    </section>
  </aside>
</template>

<style scoped>
aside { padding: 1rem; border-right: 1px solid #e5e7eb; min-width: 12rem; }
</style>
'''

SVELTE_SIDEBAR = '''<script lang="ts">
  type SidebarSection = { title: string; links: { href: string; label: string }[] };
  let {
    sections = [{ title: "Main", links: [{ href: "/", label: "Home" }] }],
  }: { sections?: SidebarSection[] } = $props();
</script>

<aside aria-label="Sidebar">
  {#each sections as section (section.title)}
    <section>
      <h2>{section.title}</h2>
      <ul>
        {#each section.links as link (link.href)}
          <li><a href={link.href}>{link.label}</a></li>
        {/each}
      </ul>
    </section>
  {/each}
</aside>

<style>
  aside { padding: 1rem; border-right: 1px solid #e5e7eb; min-width: 12rem; }
</style>
'''


def _layout_schema() -> Schema:
    return _obj({
        "project": _SLUG_S,
        "framework": _FRAMEWORK_S,
        "site_name": {"type": "string", "minLength": 1, "maxLength": 80},
        "tagline": {"type": "string", "minLength": 0, "maxLength": 200, "default": ""},
        "nav_links": {
            "type": "array",
            "minItems": 1,
            "maxItems": 12,
            "items": _obj({
                "label": {"type": "string", "minLength": 1, "maxLength": 60},
                "href": {"type": "string", "minLength": 1, "maxLength": 300},
            }, ["label", "href"]),
            "default": [{"label": "Home", "href": "/"}],
        },
        "sidebar": {"type": "boolean", "default": False},
    }, ["project", "site_name"])


_register("fe.gen.layout", 57, "Generate header/footer (+optional sidebar) layout for react/vue/svelte/html", _layout_schema(), _layout)


# ---------------------------------------------------------------------------
# 58 fe.gen.page
# ---------------------------------------------------------------------------

def _page(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    framework = args["framework"]
    route = _route(args["route"])
    title = _bounded(args["title"], "title", 120)
    sections = _features_spec(args["sections"], "sections", 12)

    if framework == "react":
        jsx = "\n\n".join(
            render('''      <section>
        <h2>@@H@@</h2>
        <p>@@B@@</p>
      </section>''', {"H": safe.js_str(s["title"]), "B": safe.js_str(s["body"])})
            for s in sections
        )
        content = render('''import type { Metadata } from "next";

export const metadata: Metadata = {
  title: @@T@@,
};

export default function @@COMP@@Page() {
  return (
    <main>
      <h1>@@T@@</h1>
@@SECTIONS@@
    </main>
  );
}
''', {"T": safe.js_str(title), "COMP": safe.pascal(route, "route"), "SECTIONS": jsx})
        path = rel_path(project, "app", route, "page.tsx")
    elif framework == "vue":
        jsx = "\n\n".join(
            render('''    <section>
      <h2>@@H_HTML@@</h2>
      <p>@@B_HTML@@</p>
    </section>''', {"H_HTML": safe.html_text(s["title"]), "B_HTML": safe.html_text(s["body"])})
            for s in sections
        )
        content = render('''<template>
  <main>
    <h1>@@T_HTML@@</h1>
@@SECTIONS@@
  </main>
</template>

<style scoped>
section { margin: 1.5rem 0; }
</style>
''', {"T_HTML": safe.html_text(title), "SECTIONS": jsx})
        path = rel_path(project, "pages", "%s.vue" % route)
    elif framework == "svelte":
        jsx = "\n\n".join(
            render('''  <section>
    <h2>@@H_HTML@@</h2>
    <p>@@B_HTML@@</p>
  </section>''', {"H_HTML": safe.html_text(s["title"]), "B_HTML": safe.html_text(s["body"])})
            for s in sections
        )
        content = render('''<main>
  <h1>@@T_HTML@@</h1>
@@SECTIONS@@
</main>

<style>
  section { margin: 1.5rem 0; }
</style>
''', {"T_HTML": safe.html_text(title), "SECTIONS": jsx})
        path = rel_path(project, "src", "routes", route, "+page.svelte")
    else:
        jsx = "\n\n".join(
            render('''  <section>
    <h2>@@H_HTML@@</h2>
    <p>@@B_HTML@@</p>
  </section>''', {"H_HTML": safe.html_text(s["title"]), "B_HTML": safe.html_text(s["body"])})
            for s in sections
        )
        body = render('''<div class="wrap">
  <main>
    <h1>@@T_HTML@@</h1>
@@SECTIONS@@
  </main>
</div>
''', {"T_HTML": safe.html_text(title), "SECTIONS": jsx})
        content = _html_shell(title, body)
        path = rel_path(project, "site", "%s.html" % route)

    data = {
        "framework": framework,
        "route": "/%s" % route if framework != "html" else "/site/%s.html" % route,
        "path": path,
        "wiring": "Route file is ready; routing is automatic in the target framework.",
    }
    return {"data": data, "writes": [(path, content)], "limitations": [LIMIT_NO_BUILD]}


_register("fe.gen.page", 58, "Generate a content page (sections + headings) for the target framework", _obj({
    "project": _SLUG_S,
    "framework": _FRAMEWORK_S,
    "route": {"type": "string", "pattern": safe.ROUTE_RE.pattern, "minLength": 1, "maxLength": 64},
    "title": {"type": "string", "minLength": 1, "maxLength": 120},
    "sections": {
        "type": "array",
        "minItems": 1,
        "maxItems": 12,
        "items": _obj({
            "title": {"type": "string", "minLength": 1, "maxLength": 100},
            "body": {"type": "string", "minLength": 1, "maxLength": 400},
        }, ["title", "body"]),
    },
}, ["project", "route", "title", "sections"]), _page)


# ---------------------------------------------------------------------------
# 59 fe.gen.component
# ---------------------------------------------------------------------------

def _component(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    framework = args["framework"]
    name = safe.pascal(args["name"], "name")
    kind = args["kind"]
    title = _bounded(args["title"], "title", 100)
    body = _bounded(args["body"], "body", 400)

    if framework == "react":
        if kind == "card":
            content = render('''type @@NAME@@Props = {
  title: string;
  body: string;
  href?: string;
};

export function @@NAME@@({ title, body, href }: @@NAME@@Props) {
  return (
    <article className="@@KEBAB@@">
      <h3>{title}</h3>
      <p>{body}</p>
      {href ? (
        <a href={href} className="card-link">
          Learn more<span aria-hidden="true"> →</span>
          <span className="visually-hidden"> about {title}</span>
        </a>
      ) : null}
    </article>
  );
}
''', {"NAME": name, "KEBAB": safe.snake(args["name"], "name").replace("_", "-")})
        elif kind == "badge":
            content = render('''type @@NAME@@Tone = "info" | "success" | "warning";

const TONE_CLASS: Record<@@NAME@@Tone, string> = {
  info: "badge badge-info",
  success: "badge badge-success",
  warning: "badge badge-warning",
};

export function @@NAME@@({ tone = "info", children }: { tone?: @@NAME@@Tone; children: string }) {
  return <span className={TONE_CLASS[tone]}>{children}</span>;
}
''', {"NAME": name})
        else:
            content = render('''type @@NAME@@Props = {
  label: string;
  value: string | number;
  hint?: string;
};

export function @@NAME@@({ label, value, hint }: @@NAME@@Props) {
  return (
    <div className="stat" role="group" aria-label={label}>
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
      {hint ? <span className="stat-hint">{hint}</span> : null}
    </div>
  );
}
''', {"NAME": name})
        path = rel_path(project, "components", "%s.tsx" % name)
        usage = render('import { @@NAME@@ } from "@/components/@@NAME@@"; then render '
                       '<@@NAME@@ title=@@TITLE@@ body=@@BODY@@ />.', {
                           "NAME": name, "TITLE": safe.js_str(title), "BODY": safe.js_str(body)})

    elif framework == "vue":
        if kind == "card":
            content = render('''<script setup lang="ts">
defineProps<{ title: string; body: string; href?: string }>();
</script>

<template>
  <article class="card">
    <h3>{{ title }}</h3>
    <p>{{ body }}</p>
    <a v-if="href" :href="href">Learn more</a>
  </article>
</template>

<style scoped>
.card { border: 1px solid #e5e7eb; border-radius: .5rem; padding: 1rem; }
</style>
''', {})
        elif kind == "badge":
            content = '''<script setup lang="ts">
withDefaults(defineProps<{ tone?: "info" | "success" | "warning" }>(), { tone: "info" });

const tones = {
  info: "badge badge-info",
  success: "badge badge-success",
  warning: "badge badge-warning",
} as const;
</script>

<template>
  <span :class="tones[tone]"><slot /></span>
</template>

<style scoped>
.badge { display: inline-block; border-radius: 9999px; padding: .125rem .625rem; font-size: .8125rem; }
.badge-info { background: #eff6ff; color: #1d4ed8; }
.badge-success { background: #ecfdf5; color: #047857; }
.badge-warning { background: #fffbeb; color: #b45309; }
</style>
'''
        else:
            content = '''<script setup lang="ts">
defineProps<{ label: string; value: string | number; hint?: string }>();
</script>

<template>
  <div class="stat" role="group" :aria-label="label">
    <span class="stat-label">{{ label }}</span>
    <span class="stat-value">{{ value }}</span>
    <span v-if="hint" class="stat-hint">{{ hint }}</span>
  </div>
</template>

<style scoped>
.stat { display: flex; flex-direction: column; gap: .25rem; }
.stat-value { font-size: 1.75rem; font-weight: 700; }
.stat-hint { color: #6b7280; font-size: .875rem; }
</style>
'''
        path = rel_path(project, "components", "%s.vue" % name)
        usage = "Auto-imported by Nuxt; render <%s> in any template." % name

    elif framework == "svelte":
        if kind == "card":
            content = '''<script lang="ts">
  let { title, body, href }: { title: string; body: string; href?: string } = $props();
</script>

<article class="card">
  <h3>{title}</h3>
  <p>{body}</p>
  {#if href}<a href={href}>Learn more</a>{/if}
</article>

<style>
  .card { border: 1px solid #e5e7eb; border-radius: .5rem; padding: 1rem; }
</style>
'''
        elif kind == "badge":
            content = '''<script lang="ts">
  let { tone = "info", children }: { tone?: "info" | "success" | "warning"; children: string } = $props();
  const tones = {
    info: "badge badge-info",
    success: "badge badge-success",
    warning: "badge badge-warning",
  };
</script>

<span class={tones[tone]}>{children}</span>

<style>
  .badge { display: inline-block; border-radius: 9999px; padding: .125rem .625rem; font-size: .8125rem; }
  .badge-info { background: #eff6ff; color: #1d4ed8; }
  .badge-success { background: #ecfdf5; color: #047857; }
  .badge-warning { background: #fffbeb; color: #b45309; }
</style>
'''
        else:
            content = '''<script lang="ts">
  let { label, value, hint }: { label: string; value: string | number; hint?: string } = $props();
</script>

<div class="stat" role="group" aria-label={label}>
  <span class="stat-label">{label}</span>
  <span class="stat-value">{value}</span>
  {#if hint}<span class="stat-hint">{hint}</span>{/if}
</div>

<style>
  .stat { display: flex; flex-direction: column; gap: .25rem; }
  .stat-value { font-size: 1.75rem; font-weight: 700; }
  .stat-hint { color: #6b7280; font-size: .875rem; }
</style>
'''
        path = rel_path(project, "src", "lib", "components", "%s.svelte" % name)
        usage = 'import %s from "$lib/components/%s.svelte";' % (name, name)

    else:
        if kind == "card":
            body_html = render('''<article class="card">
    <h3>@@T_HTML@@</h3>
    <p>@@B_HTML@@</p>
  </article>''', {"T_HTML": safe.html_text(title), "B_HTML": safe.html_text(body)})
        elif kind == "badge":
            body_html = render('''<span class="badge badge-info">@@T_HTML@@</span>''', {"T_HTML": safe.html_text(title)})
        else:
            body_html = render('''<div class="stat">
    <span class="stat-label">@@T_HTML@@</span>
    <span class="stat-value">@@B_HTML@@</span>
  </div>''', {"T_HTML": safe.html_text(title), "B_HTML": safe.html_text(body)})
        content = _html_shell(
            "%s component" % name,
            '<div class="wrap">\n%s\n</div>' % body_html,
            extra_css="\n    .card { border: 1px solid #e5e7eb; border-radius: .5rem; padding: 1rem; }\n"
                      "    .badge { display: inline-block; border-radius: 9999px; padding: .125rem .625rem; font-size: .8125rem; background: #eff6ff; color: #1d4ed8; }\n"
                      "    .stat { display: flex; flex-direction: column; gap: .25rem; }\n"
                      "    .stat-value { font-size: 1.75rem; font-weight: 700; }",
        )
        path = rel_path(project, "site", "components", "%s.html" % safe.snake(args["name"], "name").replace("_", "-"))
        usage = "site/components/%s.html is a live demo of the markup/styles; copy into pages." % path.rsplit("/", 1)[-1]

    data = {"framework": framework, "component": name, "kind": kind, "path": path, "wiring": usage}
    return {"data": data, "writes": [(path, content)], "limitations": [LIMIT_NO_BUILD]}


_register("fe.gen.component", 59, "Generate a reusable component (card, badge, or stat)", _obj({
    "project": _SLUG_S,
    "framework": _FRAMEWORK_S,
    "name": {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_-]*$", "minLength": 2, "maxLength": 48},
    "kind": {"type": "string", "enum": ["card", "badge", "stat"], "default": "card"},
    "title": {"type": "string", "minLength": 1, "maxLength": 100, "default": "Example"},
    "body": {"type": "string", "minLength": 1, "maxLength": 400, "default": "Component body text."},
}, ["project", "name"]), _component)


# ---------------------------------------------------------------------------
# 60 fe.gen.form
# ---------------------------------------------------------------------------

def _form_js_validate(fields: List[Dict[str, Any]]) -> str:
    """Vanilla-JS validation block shared by the html variant."""
    checks: List[str] = []
    for f in fields:
        name = f["name"]
        if f["required"] and f["type"] != "checkbox":
            checks.append('  if (!values["%s"] || !values["%s"].trim()) errors["%s"] = "This field is required";' % (name, name, name))
        if f["type"] == "email":
            checks.append('  if (values["%s"] && !/^[^@\\s]+@[^@\\s]+\\.[^@\\s]{2,}$/.test(values["%s"])) errors["%s"] = "Enter a valid email address";' % (name, name, name))
        if f["type"] == "number":
            checks.append('  if (values["%s"] && Number.isNaN(Number(values["%s"]))) errors["%s"] = "Enter a valid number";' % (name, name, name))
        if f["type"] == "password":
            checks.append('  if (values["%s"] && values["%s"].length < 8) errors["%s"] = "Use at least 8 characters";' % (name, name, name))
    return "\n".join(checks) if checks else "  // no additional constraints"


def _form(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    framework = args["framework"]
    name = safe.pascal(args["name"], "name")
    endpoint = _endpoint(args["endpoint"])
    fields = _field_spec(args["fields"])
    submit_label = _bounded(args["submit_label"], "submit_label", 40)
    success_message = _bounded(args["success_message"], "success_message", 200)

    field_specs_json = json.dumps([
        {k: f[k] for k in ("name", "label", "type", "required", "placeholder") if k in f}
        | ({"options": f["options"]} if "options" in f else {})
        for f in fields
    ], ensure_ascii=False)

    if framework == "react":
        state_inits = "\n".join(
            '  const [%s, set%s] = useState%s;' % (
                f["name"], f["name"].replace("_", " ").title().replace(" ", ""),
                '("%s")' % ("false" if f["type"] == "checkbox" else "") if f["type"] == "checkbox"
                else '("")' if f["type"] != "number" else '("")',
            )
            for f in fields
        ) if False else "\n".join(
            '  const [%s, set%s] = useState%s;' % (
                f["name"],
                "".join(part.title() for part in f["name"].split("_")),
                '("")',
            )
            for f in fields if f["type"] != "checkbox"
        ) + ("\n" + "\n".join(
            '  const [%s, set%s] = useState(false);' % (f["name"], "".join(part.title() for part in f["name"].split("_")))
            for f in fields if f["type"] == "checkbox"
        ) if any(f["type"] == "checkbox" for f in fields) else "")
        inputs: List[str] = []
        for f in fields:
            comp = "".join(part.title() for part in f["name"].split("_"))
            ph = (' placeholder="%s"' % safe.html_text(f["placeholder"])) if f["placeholder"] else ""
            required = " required" if f["required"] else ""
            label = safe.js_str(f["label"])
            if f["type"] == "textarea":
                inputs.append('''      <label htmlFor="%s">%s</label>
      <textarea id="%s" value={%s} onChange={(e) => set%s(e.target.value)}%s%s rows={4} />'''
                              % (f["name"], label, f["name"], f["name"], comp, ph, required))
            elif f["type"] == "select":
                options = "\n".join('          <option value="%s">%s</option>' % (safe.html_text(o), safe.html_text(o)) for o in f["options"])
                inputs.append('''      <label htmlFor="%s">%s</label>
      <select id="%s" value={%s} onChange={(e) => set%s(e.target.value)}%s%s>
        <option value="">Choose…</option>
%s
        </select>'''
                              % (f["name"], label, f["name"], f["name"], comp, required, ph and " " + ph.strip(), options))
            elif f["type"] == "checkbox":
                inputs.append('''      <label htmlFor="%s" className="checkbox-label">
        <input id="%s" type="checkbox" checked={%s} onChange={(e) => set%s(e.target.checked)} />
        {" "}%s
      </label>''' % (f["name"], f["name"], f["name"], comp, label))
            else:
                inputs.append('''      <label htmlFor="%s">%s</label>
      <input id="%s" type="%s" value={%s} onChange={(e) => set%s(e.target.value)}%s%s />'''
                              % (f["name"], label, f["name"], f["type"], f["name"], comp, ph, required))
        content = render('''"use client";

import { FormEvent, useState } from "react";

@@DOC@@
export function @@NAME@@Form() {
@@STATE@@
  const [status, setStatus] = useState<"idle" | "submitting" | "success" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setStatus("submitting");
    setErrorMessage(null);

    const payload: Record<string, string | boolean> = {};
@@COLLECT@@
    try {
      const res = await fetch(@@ENDPOINT@@, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!res.ok) {
        const data = (await res.json().catch(() => null)) as { error?: string } | null;
        setStatus("error");
        setErrorMessage(data?.error ?? "Request failed with status " + res.status);
        return;
      }
      setStatus("success");
    } catch {
      setStatus("error");
      setErrorMessage("Network error; please try again.");
    }
  }

  return (
    <form onSubmit={handleSubmit} noValidate>
@@INPUTS@@
      <button type="submit" disabled={status === "submitting"}>
        {status === "submitting" ? "Sending…" : @@SUBMIT@@}
      </button>
      {status === "success" ? <p className="ok" role="status">@@SUCCESS_HTML@@</p> : null}
      {errorMessage ? <p className="error" role="alert">{errorMessage}</p> : null}
    </form>
  );
}
''', {
            "NAME": name,
            "DOC": "// Client-side validation relies on native input constraints plus server-side 4xx handling.",
            "STATE": state_inits,
            "COLLECT": "\n".join(
                '    payload["%s"] = %s;' % (f["name"], f["name"]) for f in fields
            ),
            "ENDPOINT": safe.js_str(endpoint),
            "SUBMIT": safe.js_str(submit_label),
            "SUCCESS_HTML": safe.html_text(success_message),
            "INPUTS": "\n".join(inputs),
        })
        path = rel_path(project, "components", "%sForm.tsx" % name)
        wiring = 'import { %sForm } from "@/components/%sForm";' % (name, name)
        deps: Dict[str, str] = {}

    elif framework == "vue":
        content = render('''<script setup lang="ts">
import { reactive, ref } from "vue";

type Field = { name: string; label: string; type: string; required: boolean };

const fields: Field[] = JSON.parse(@@FIELDS_JSON@@) as Field[];

const values = reactive<Record<string, string | boolean>>({});
for (const field of fields) {
  values[field.name] = field.type === "checkbox" ? false : "";
}

const status = ref<"idle" | "submitting" | "success" | "error">("idle");
const errorMessage = ref<string | null>(null);

async function submit() {
  status.value = "submitting";
  errorMessage.value = null;
  try {
    const res = await $fetch(@@ENDPOINT@@, { method: "POST", body: values });
    status.value = "success";
  } catch (err: any) {
    status.value = "error";
    errorMessage.value = err?.data?.message ?? "Request failed; please try again.";
  }
}
</script>

<template>
  <form @submit.prevent="submit">
    <template v-for="field in fields" :key="field.name">
      <label :for="field.name">{{ field.label }}</label>
      <textarea v-if="field.type === 'textarea'" :id="field.name" v-model="values[field.name]" rows="4" />
      <select v-else-if="field.type === 'select'" :id="field.name" v-model="values[field.name]" :required="field.required">
        <option value="">Choose…</option>
        <option v-for="option in field.options" :key="option" :value="option">{{ option }}</option>
      </select>
      <input v-else-if="field.type === 'checkbox'" :id="field.name" v-model="values[field.name]" type="checkbox" />
      <input v-else :id="field.name" v-model="values[field.name]" :type="field.type" :required="field.required" />
    </template>
    <button type="submit" :disabled="status === 'submitting'">
      {{ status === 'submitting' ? 'Sending…' : @@SUBMIT@@ }}
    </button>
    <p v-if="status === 'success'" class="ok" role="status">@@SUCCESS_HTML@@</p>
    <p v-if="errorMessage" class="error" role="alert">{{ errorMessage }}</p>
  </form>
</template>

<style scoped>
form { max-width: 26rem; }
button { margin-top: 1rem; }
</style>
''', {
            "FIELDS_JSON": safe.js_str(field_specs_json),
            "ENDPOINT": safe.js_str(endpoint),
            "SUBMIT": safe.js_str(submit_label),
            "SUCCESS_HTML": safe.html_text(success_message),
        })
        path = rel_path(project, "components", "%sForm.vue" % name)
        wiring = "Auto-imported by Nuxt; render <%sForm /> in any template." % name
        deps = {}

    elif framework == "svelte":
        content = render('''<script lang="ts">
  type Field = { name: string; label: string; type: string; required: boolean };
  const fields: Field[] = JSON.parse(@@FIELDS_JSON@@) as Field[];
  let values: Record<string, string | boolean> = $state(
    Object.fromEntries(fields.map((f) => [f.name, f.type === "checkbox" ? false : ""])),
  );
  let status: "idle" | "submitting" | "success" | "error" = $state("idle");
  let errorMessage: string | null = $state(null);

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    status = "submitting";
    errorMessage = null;
    try {
      const res = await fetch(@@ENDPOINT@@, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify(values),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        status = "error";
        errorMessage = data?.error ?? "Request failed with status " + res.status;
        return;
      }
      status = "success";
    } catch {
      status = "error";
      errorMessage = "Network error; please try again.";
    }
  }
</script>

<form onsubmit={submit}>
  {#each fields as field (field.name)}
    {#if field.type === "checkbox"}
      <label for={field.name} class="checkbox-label">
        <input id={field.name} type="checkbox" bind:checked={values[field.name] as boolean} />
        {field.label}
      </label>
    {:else}
      <label for={field.name}>{field.label}</label>
      {#if field.type === "textarea"}
        <textarea id={field.name} bind:value={values[field.name]} rows="4" required={field.required}></textarea>
      {:else if field.type === "select"}
        <select id={field.name} bind:value={values[field.name]} required={field.required}>
          <option value="">Choose…</option>
          {#each field.options as option (option)}
            <option value={option}>{option}</option>
          {/each}
        </select>
      {:else}
        <input id={field.name} type={field.type} bind:value={values[field.name]} required={field.required} />
      {/if}
    {/if}
  {/each}
  <button type="submit" disabled={status === "submitting"}>
    {status === "submitting" ? "Sending…" : @@SUBMIT@@}
  </button>
  {#if status === "success"}<p class="ok" role="status">@@SUCCESS_HTML@@</p>{/if}
  {#if errorMessage}<p class="error" role="alert">{errorMessage}</p>{/if}
</form>

<style>
  form { max-width: 26rem; }
  button { margin-top: 1rem; }
</style>
''', {
            "FIELDS_JSON": safe.js_str(field_specs_json),
            "ENDPOINT": safe.js_str(endpoint),
            "SUBMIT": safe.js_str(submit_label),
            "SUCCESS_HTML": safe.html_text(success_message),
        })
        path = rel_path(project, "src", "lib", "components", "%sForm.svelte" % name)
        wiring = 'import %sForm from "$lib/components/%sForm.svelte";' % (name, name)
        deps = {}

    else:
        input_html: List[str] = []
        for f in fields:
            ph = (' placeholder="%s"' % safe.html_text(f["placeholder"])) if f["placeholder"] else ""
            required = " required" if f["required"] else ""
            input_html.append('  <label for="f-%s">%s</label>' % (f["name"], safe.html_text(f["label"])))
            if f["type"] == "textarea":
                input_html.append('  <textarea id="f-%s" name="%s" rows="4"%s%s></textarea>' % (f["name"], f["name"], ph, required))
            elif f["type"] == "select":
                opts = "\n".join('    <option value="%s">%s</option>' % (safe.html_text(o), safe.html_text(o)) for o in f["options"])
                input_html.append('  <select id="f-%s" name="%s"%s%s>\n    <option value="">Choose…</option>\n%s\n  </select>' % (f["name"], f["name"], required, ph and "", opts))
            elif f["type"] == "checkbox":
                input_html.append('  <label class="checkbox-label"><input type="checkbox" id="f-%s" name="%s" /> %s</label>' % (f["name"], f["name"], safe.html_text(f["label"])))
            else:
                input_html.append('  <input id="f-%s" name="%s" type="%s"%s%s />' % (f["name"], f["name"], f["type"], ph, required))
        script = render('''const FIELDS = JSON.parse(@@FIELDS_JSON@@);
const form = document.getElementById("form");
const errorBox = document.getElementById("form-error");
const okBox = document.getElementById("form-success");

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.hidden = true;
  okBox.hidden = true;

  const values = {};
  for (const field of FIELDS) {
    const el = document.getElementById("f-" + field.name);
    if (field.type === "checkbox") {
      values[field.name] = el.checked;
    } else {
      values[field.name] = el.value;
    }
  }

  // Client-side validation (server must re-validate).
  const errors = {};
@@CHECKS@@
  if (Object.keys(errors).length > 0) {
    errorBox.textContent = Object.values(errors).join(" ");
    errorBox.hidden = false;
    return;
  }

  const button = form.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const res = await fetch(@@ENDPOINT@@, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(values),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new Error(data && data.error ? data.error : "Request failed with status " + res.status);
    }
    okBox.hidden = false;
    form.reset();
  } catch (err) {
    errorBox.textContent = err.message;
    errorBox.hidden = false;
  } finally {
    button.disabled = false;
  }
});
''', {
            "FIELDS_JSON": safe.js_str(field_specs_json),
            "ENDPOINT": safe.js_str(endpoint),
            "CHECKS": _form_js_validate(fields),
        })
        body = render('''<div class="wrap">
  <form id="form" novalidate>
@@INPUTS@@
    <button type="submit" class="primary">@@SUBMIT_HTML@@</button>
    <p id="form-error" class="error" role="alert" hidden></p>
    <p id="form-success" class="ok" role="status" hidden>@@SUCCESS_HTML@@</p>
  </form>
</div>
''', {
            "INPUTS": "\n".join(input_html),
            "SUBMIT_HTML": safe.html_text(submit_label),
            "SUCCESS_HTML": safe.html_text(success_message),
        })
        content = _html_shell("%s form" % name, body, script=script)
        path = rel_path(project, "site", "%s-form.html" % safe.snake(args["name"], "name").replace("_", "-"))
        wiring = "Static page: open site/%s-form.html; it posts JSON to %s." % (path.rsplit("/", 1)[-1], endpoint)
        deps = {}

    data = {
        "framework": framework,
        "component": name,
        "path": path,
        "endpoint": endpoint,
        "fields": [f["name"] for f in fields],
        "dependencies": deps,
        "wiring": wiring,
        "note": "Validation is client-side convenience only; the server endpoint MUST re-validate every field.",
    }
    return {"data": data, "writes": [(path, content)], "limitations": [LIMIT_NO_BUILD]}


_register("fe.gen.form", 60, "Generate a validated form posting JSON to an endpoint", _obj({
    "project": _SLUG_S,
    "framework": _FRAMEWORK_S,
    "name": {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_-]*$", "minLength": 2, "maxLength": 48, "default": "contact"},
    "endpoint": {"type": "string", "pattern": r"^/", "minLength": 1, "maxLength": 200, "default": "/api/submit"},
    "fields": {
        "type": "array",
        "minItems": 1,
        "maxItems": 24,
        "items": _obj({
            "name": {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_]*$", "minLength": 1, "maxLength": 48},
            "label": {"type": "string", "minLength": 1, "maxLength": 100},
            "type": {"type": "string", "enum": list(_ALLOWED_FIELD_TYPES), "default": "text"},
            "required": {"type": "boolean", "default": False},
            "placeholder": {"type": "string", "maxLength": 120, "default": ""},
            "options": {"type": "array", "minItems": 1, "maxItems": 20,
                        "items": {"type": "string", "minLength": 1, "maxLength": 100}},
        }, ["name"]),
    },
    "submit_label": {"type": "string", "minLength": 1, "maxLength": 40, "default": "Submit"},
    "success_message": {"type": "string", "minLength": 1, "maxLength": 200, "default": "Thank you! Your submission has been received."},
}, ["project", "fields"]), _form)


# ---------------------------------------------------------------------------
# 61 fe.gen.table
# ---------------------------------------------------------------------------

def _table(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    framework = args["framework"]
    name = safe.pascal(args["name"], "name")
    endpoint = _endpoint(args["endpoint"])
    columns = _column_spec(args["columns"])
    page_size = args["page_size"]
    empty_text = _bounded(args["empty_text"], "empty_text", 100)

    cols_json = json.dumps(columns, ensure_ascii=False)

    if framework == "react":
        ths = "\n".join('          <th scope="col">%s</th>' % safe.html_text(c["label"]) for c in columns)
        tds = "\n".join('                <td>{row[%s] ?? "—"}</td>' % safe.js_str(c["key"]) for c in columns)
        content = render('''"use client";

import { useCallback, useEffect, useMemo, useState } from "react";

type Row = Record<string, unknown>;

const COLUMNS: { key: string; label: string }[] = JSON.parse(@@COLS_JSON@@);
const PAGE_SIZE = @@PAGE_SIZE@@;

export function @@NAME@@Table() {
  const [rows, setRows] = useState<Row[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [page, setPage] = useState(0);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch(@@ENDPOINT@@);
      if (!res.ok) throw new Error("status " + res.status);
      const data = (await res.json()) as { items?: Row[] } | Row[];
      setRows(Array.isArray(data) ? data : (data.items ?? []));
    } catch {
      setError("Failed to load data");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const pageCount = useMemo(
    () => (rows ? Math.max(1, Math.ceil(rows.length / PAGE_SIZE)) : 1),
    [rows],
  );
  const visible = useMemo(
    () => (rows ? rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE) : []),
    [rows, page],
  );

  return (
    <section>
      {error ? <p className="error" role="alert">{error}</p> : null}
      {rows === null && !error ? <p role="status">Loading…</p> : null}
      {rows !== null && rows.length === 0 ? <p>@@EMPTY_HTML@@</p> : null}
      {rows !== null && rows.length > 0 ? (
        <>
          <table>
            <thead>
              <tr>
@@THS@@
              </tr>
            </thead>
            <tbody>
              {visible.map((row, index) => (
                <tr key={String(row.id ?? index)}>
@@TDS@@
                </tr>
              ))}
            </tbody>
          </table>
          <nav aria-label="Pagination" className="table-pagination">
            <button type="button" onClick={() => setPage((p) => Math.max(0, p - 1))} disabled={page === 0}>
              Previous
            </button>
            <span>
              Page {page + 1} of {pageCount}
            </span>
            <button type="button" onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))} disabled={page >= pageCount - 1}>
              Next
            </button>
          </nav>
        </>
      ) : null}
    </section>
  );
}
''', {
            "NAME": name,
            "COLS_JSON": safe.js_str(cols_json),
            "PAGE_SIZE": str(page_size),
            "ENDPOINT": safe.js_str(endpoint),
            "EMPTY_HTML": safe.html_text(empty_text),
            "THS": ths,
            "TDS": tds,
        })
        path = rel_path(project, "components", "%sTable.tsx" % name)
        wiring = 'import { %sTable } from "@/components/%sTable";' % (name, name)

    elif framework == "vue":
        content = render('''<script setup lang="ts">
import { computed, onMounted, ref } from "vue";

type Row = Record<string, unknown>;

const columns: { key: string; label: string }[] = JSON.parse(@@COLS_JSON@@);
const PAGE_SIZE = @@PAGE_SIZE@@;

const rows = ref<Row[] | null>(null);
const error = ref<string | null>(null);
const page = ref(0);

const pageCount = computed(() => rows.value ? Math.max(1, Math.ceil(rows.value.length / PAGE_SIZE)) : 1);
const visible = computed(() => rows.value ? rows.value.slice(page.value * PAGE_SIZE, (page.value + 1) * PAGE_SIZE) : []);

onMounted(async () => {
  try {
    const data = await $fetch<Row[] | { items?: Row[] }>(@@ENDPOINT@@);
    rows.value = Array.isArray(data) ? data : (data.items ?? []);
  } catch {
    error.value = "Failed to load data";
  }
});
</script>

<template>
  <section>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-else-if="rows === null" role="status">Loading…</p>
    <p v-else-if="rows.length === 0">@@EMPTY_HTML@@</p>
    <template v-else>
      <table>
        <thead>
          <tr>
            <th v-for="col in columns" :key="col.key" scope="col">{{ col.label }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="(row, index) in visible" :key="String(row.id ?? index)">
            <td v-for="col in columns" :key="col.key">{{ row[col.key] ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
      <nav aria-label="Pagination" class="table-pagination">
        <button type="button" :disabled="page === 0" @click="page--">Previous</button>
        <span>Page {{ page + 1 }} of {{ pageCount }}</span>
        <button type="button" :disabled="page >= pageCount - 1" @click="page++">Next</button>
      </nav>
    </template>
  </section>
</template>

<style scoped>
.table-pagination { display: flex; gap: 1rem; align-items: center; margin-top: .75rem; }
</style>
''', {
            "COLS_JSON": safe.js_str(cols_json),
            "PAGE_SIZE": str(page_size),
            "ENDPOINT": safe.js_str(endpoint),
            "EMPTY_HTML": safe.html_text(empty_text),
        })
        path = rel_path(project, "components", "%sTable.vue" % name)
        wiring = "Auto-imported by Nuxt; render <%sTable />." % name

    elif framework == "svelte":
        content = render('''<script lang="ts">
  type Row = Record<string, unknown>;
  const columns: { key: string; label: string }[] = JSON.parse(@@COLS_JSON@@);
  const PAGE_SIZE = @@PAGE_SIZE@@;

  let rows: Row[] | null = $state(null);
  let error: string | null = $state(null);
  let page = $state(0);

  const pageCount = $derived(rows ? Math.max(1, Math.ceil(rows.length / PAGE_SIZE)) : 1);
  const visible = $derived(rows ? rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE) : []);

  $effect(() => {
    void (async () => {
      try {
        const res = await fetch(@@ENDPOINT@@);
        if (!res.ok) throw new Error("status " + res.status);
        const data = await res.json();
        rows = Array.isArray(data) ? data : (data.items ?? []);
      } catch {
        error = "Failed to load data";
      }
    })();
  });
</script>

<section>
  {#if error}<p class="error" role="alert">{error}</p>{:else if rows === null}<p role="status">Loading…</p>{:else if rows.length === 0}<p>@@EMPTY_HTML@@</p>{:else}
    <table>
      <thead>
        <tr>
          {#each columns as col (col.key)}<th scope="col">{col.label}</th>{/each}
        </tr>
      </thead>
      <tbody>
        {#each visible as row, index (String(row.id ?? index))}
          <tr>
            {#each columns as col (col.key)}<td>{row[col.key] ?? "—"}</td>{/each}
          </tr>
        {/each}
      </tbody>
    </table>
    <nav aria-label="Pagination" class="table-pagination">
      <button type="button" disabled={page === 0} onclick={() => (page = Math.max(0, page - 1))}>Previous</button>
      <span>Page {page + 1} of {pageCount}</span>
      <button type="button" disabled={page >= pageCount - 1} onclick={() => (page = Math.min(pageCount - 1, page + 1))}>Next</button>
    </nav>
  {/if}
</section>

<style>
  .table-pagination { display: flex; gap: 1rem; align-items: center; margin-top: .75rem; }
</style>
''', {
            "COLS_JSON": safe.js_str(cols_json),
            "PAGE_SIZE": str(page_size),
            "ENDPOINT": safe.js_str(endpoint),
            "EMPTY_HTML": safe.html_text(empty_text),
        })
        path = rel_path(project, "src", "lib", "components", "%sTable.svelte" % name)
        wiring = 'import %sTable from "$lib/components/%sTable.svelte";' % (name, name)

    else:
        ths = "".join('<th scope="col">%s</th>' % safe.html_text(c["label"]) for c in columns)
        tds = "".join('<td data-key="%s"></td>' % safe.html_text(c["key"]) for c in columns)
        script = render('''const COLUMNS = JSON.parse(@@COLS_JSON@@);
const PAGE_SIZE = @@PAGE_SIZE@@;
let rows = [];
let page = 0;

function renderTable() {
  const tbody = document.getElementById("tbody");
  const empty = document.getElementById("empty");
  const status = document.getElementById("status");
  const pager = document.getElementById("pager");
  tbody.innerHTML = "";
  if (rows.length === 0) {
    empty.hidden = false;
    pager.hidden = true;
    return;
  }
  empty.hidden = true;
  const pageCount = Math.max(1, Math.ceil(rows.length / PAGE_SIZE));
  page = Math.min(page, pageCount - 1);
  for (const row of rows.slice(page * PAGE_SIZE, (page + 1) * PAGE_SIZE)) {
    const tr = document.createElement("tr");
    for (const col of COLUMNS) {
      const td = document.createElement("td");
      td.textContent = row[col.key] == null ? "—" : String(row[col.key]);
      tr.append(td);
    }
    tbody.append(tr);
  }
  document.getElementById("page-label").textContent = "Page " + (page + 1) + " of " + pageCount;
  document.getElementById("prev").disabled = page === 0;
  document.getElementById("next").disabled = page >= pageCount - 1;
  pager.hidden = false;
}

async function load() {
  try {
    const res = await fetch(@@ENDPOINT@@);
    if (!res.ok) throw new Error("status " + res.status);
    const data = await res.json();
    rows = Array.isArray(data) ? data : data.items || [];
    document.getElementById("error").hidden = true;
  } catch {
    document.getElementById("error").hidden = false;
  } finally {
    status.hidden = true;
    renderTable();
  }
}

document.getElementById("prev").addEventListener("click", () => { page--; renderTable(); });
document.getElementById("next").addEventListener("click", () => { page++; renderTable(); });
load();
''', {
            "COLS_JSON": safe.js_str(cols_json),
            "PAGE_SIZE": str(page_size),
            "ENDPOINT": safe.js_str(endpoint),
        })
        body = render('''<div class="wrap">
  <p id="status" role="status">Loading…</p>
  <p id="error" class="error" role="alert" hidden>Failed to load data</p>
  <p id="empty" hidden>@@EMPTY_HTML@@</p>
  <table id="table">
    <thead><tr>@@THS@@</tr></thead>
    <tbody id="tbody"></tbody>
  </table>
  <nav id="pager" aria-label="Pagination" hidden>
    <button type="button" id="prev">Previous</button>
    <span id="page-label"></span>
    <button type="button" id="next">Next</button>
  </nav>
</div>
''', {"EMPTY_HTML": safe.html_text(empty_text), "THS": ths})
        content = _html_shell("%s table" % name, body, script=script)
        path = rel_path(project, "site", "%s-table.html" % safe.snake(args["name"], "name").replace("_", "-"))
        wiring = "Static page: open %s; it fetches %s." % (path, endpoint)

    data = {
        "framework": framework,
        "component": name,
        "path": path,
        "endpoint": endpoint,
        "columns": [c["key"] for c in columns],
        "page_size": page_size,
        "wiring": wiring,
    }
    return {"data": data, "writes": [(path, content)], "limitations": [LIMIT_NO_BUILD]}


_register("fe.gen.table", 61, "Generate a paginated data table fetching JSON rows", _obj({
    "project": _SLUG_S,
    "framework": _FRAMEWORK_S,
    "name": {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_-]*$", "minLength": 2, "maxLength": 48, "default": "items"},
    "endpoint": {"type": "string", "pattern": r"^/", "minLength": 1, "maxLength": 200, "default": "/api/items"},
    "columns": {
        "type": "array",
        "minItems": 1,
        "maxItems": 16,
        "items": _obj({
            "key": {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_]*$", "minLength": 1, "maxLength": 48},
            "label": {"type": "string", "minLength": 1, "maxLength": 60},
        }, ["key", "label"]),
    },
    "page_size": {"type": "integer", "minimum": 5, "maximum": 100, "default": 10},
    "empty_text": {"type": "string", "minLength": 1, "maxLength": 100, "default": "No items yet."},
}, ["project", "columns"]), _table)


# ---------------------------------------------------------------------------
# 62 fe.gen.chart
# ---------------------------------------------------------------------------

def _chart(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    framework = args["framework"]
    name = safe.pascal(args["name"], "name")
    chart_type = args["chart_type"]
    endpoint = _endpoint(args["endpoint"])
    title = _bounded(args["title"], "title", 100)

    if framework == "react":
        content = render('''"use client";

import { useEffect, useState } from "react";
import {
  Bar, BarChart, CartesianGrid, Cell, Legend, Line, LineChart,
  Pie, PieChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";

const COLORS = ["#2563eb", "#059669", "#d97706", "#dc2626", "#7c3aed", "#0891b2"];

export type DataPoint = { label: string; value: number };

export function @@NAME@@Chart() {
  const [data, setData] = useState<DataPoint[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const res = await fetch(@@ENDPOINT@@);
        if (!res.ok) throw new Error("status " + res.status);
        const payload = await res.json();
        const items = (Array.isArray(payload) ? payload : payload.items) as DataPoint[];
        if (!Array.isArray(items) || items.some((d) => typeof d.label !== "string" || typeof d.value !== "number")) {
          throw new Error("invalid data shape");
        }
        setData(items);
      } catch {
        setError("Failed to load chart data");
      }
    })();
  }, []);

  if (error) return <p className="error" role="alert">{error}</p>;
  if (!data) return <p role="status">Loading chart…</p>;

  return (
    <figure style={{ width: "100%", height: 320 }} aria-label={@@TITLE@@}>
      <ResponsiveContainer>
@@CHART@@
      </ResponsiveContainer>
      <figcaption>@@TITLE_HTML@@</figcaption>
    </figure>
  );
}
''', {
            "NAME": name,
            "ENDPOINT": safe.js_str(endpoint),
            "TITLE": safe.js_str(title),
            "TITLE_HTML": safe.html_text(title),
            "CHART": {
                "bar": '''        <BarChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="label" />
          <YAxis allowDecimals={false} />
          <Tooltip />
          <Bar dataKey="value" fill="#2563eb" />
        </BarChart>''',
                "line": '''        <LineChart data={data}>
          <CartesianGrid strokeDasharray="3 3" />
          <XAxis dataKey="label" />
          <YAxis allowDecimals={false} />
          <Tooltip />
          <Line type="monotone" dataKey="value" stroke="#2563eb" strokeWidth={2} />
        </LineChart>''',
                "pie": '''        <PieChart>
          <Tooltip />
          <Legend />
          <Pie data={data} dataKey="value" nameKey="label" outerRadius={100} label>
            {data.map((entry, index) => (
              <Cell key={entry.label} fill={COLORS[index % COLORS.length]} />
            ))}
          </Pie>
        </PieChart>''',
            }[chart_type],
        })
        path = rel_path(project, "components", "%sChart.tsx" % name)
        wiring = 'import { %sChart } from "@/components/%sChart";' % (name, name)
        deps = {"recharts": "^2.15.0"}

    elif framework == "vue":
        content = render('''<script setup lang="ts">
import { onMounted, ref } from "vue";
import {
  BarElement, CategoryScale, Chart as ChartJS, Legend, LinearScale,
  LineElement, PointElement, ArcElement, Tooltip,
} from "chart.js";
import { Bar, Line, Pie } from "vue-chartjs";

ChartJS.register(CategoryScale, LinearScale, BarElement, LineElement, PointElement, ArcElement, Legend, Tooltip);

type DataPoint = { label: string; value: number };

const data = ref<DataPoint[] | null>(null);
const error = ref<string | null>(null);

onMounted(async () => {
  try {
    const payload = await $fetch<DataPoint[] | { items?: DataPoint[] }>(@@ENDPOINT@@);
    data.value = Array.isArray(payload) ? payload : (payload.items ?? []);
  } catch {
    error.value = "Failed to load chart data";
  }
});

const chartData = () => ({
  labels: (data.value ?? []).map((d) => d.label),
  datasets: [{ label: @@TITLE@@, data: (data.value ?? []).map((d) => d.value), backgroundColor: "#2563eb" }],
});

const options = { responsive: true, maintainAspectRatio: false };
</script>

<template>
  <figure style="height: 320px">
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <p v-else-if="!data" role="status">Loading chart…</p>
    <Bar v-else-if="@@IS_BAR@@" :data="chartData()" :options="options" :aria-label="@@TITLE@@" />
    <Line v-else-if="@@IS_LINE@@" :data="chartData()" :options="options" :aria-label="@@TITLE@@" />
    <Pie v-else :data="chartData()" :options="options" :aria-label="@@TITLE@@" />
    <figcaption>@@TITLE_HTML@@</figcaption>
  </figure>
</template>
''', {
            "ENDPOINT": safe.js_str(endpoint),
            "TITLE": safe.js_str(title),
            "TITLE_HTML": safe.html_text(title),
            "IS_BAR": "true" if chart_type == "bar" else "false",
            "IS_LINE": "true" if chart_type == "line" else "false",
        })
        path = rel_path(project, "components", "%sChart.vue" % name)
        wiring = "Auto-imported by Nuxt; render <%sChart />." % name
        deps = {"chart.js": "^4.4.7", "vue-chartjs": "^5.3.1"}

    elif framework == "svelte":
        content = render('''<script lang="ts">
  import { onMount } from "svelte";
  import {
    ArcElement, BarController, BarElement, CategoryScale, Chart, Legend,
    LineController, LineElement, LinearScale, PointElement, Tooltip,
  } from "chart.js";

  Chart.register(CategoryScale, LinearScale, BarController, BarElement, LineController, LineElement, PointElement, ArcElement, Tooltip);

  type DataPoint = { label: string; value: number };
  type Kind = "@@TYPE@@";

  let { kind = "@@TYPE@@" as Kind }: { kind?: Kind } = $props();

  let canvas: HTMLCanvasElement;
  let data: DataPoint[] | null = $state(null);
  let error: string | null = $state(null);
  let chart: Chart | null = null;

  $effect(() => {
    if (!data || !canvas) return;
    chart?.destroy();
    chart = new Chart(canvas, {
      type: kind === "line" ? "line" : kind === "pie" ? "pie" : "bar",
      data: {
        labels: data.map((d) => d.label),
        datasets: [{ label: @@TITLE@@, data: data.map((d) => d.value), backgroundColor: "#2563eb" }],
      },
      options: { responsive: true, maintainAspectRatio: false },
    });
  });

  $effect(() => {
    void (async () => {
      try {
        const res = await fetch(@@ENDPOINT@@);
        if (!res.ok) throw new Error("status " + res.status);
        const payload = await res.json();
        data = Array.isArray(payload) ? payload : (payload.items ?? []);
      } catch {
        error = "Failed to load chart data";
      }
    })();
  });
</script>

<figure style="height: 320px" aria-label={@@TITLE@@}>
  {#if error}<p class="error" role="alert">{error}</p>{:else if !data}<p role="status">Loading chart…</p>{:else}
    <canvas bind:this={canvas}></canvas>
  {/if}
  <figcaption>@@TITLE_HTML@@</figcaption>
</figure>
''', {
            "ENDPOINT": safe.js_str(endpoint),
            "TITLE": safe.js_str(title),
            "TITLE_HTML": safe.html_text(title),
            "TYPE": chart_type,
        })
        path = rel_path(project, "src", "lib", "components", "%sChart.svelte" % name)
        wiring = 'import %sChart from "$lib/components/%sChart.svelte";' % (name, name)
        deps = {"chart.js": "^4.4.7"}

    else:
        script = render('''const KIND = @@KIND@@;

async function load() {
  try {
    const res = await fetch(@@ENDPOINT@@);
    if (!res.ok) throw new Error("status " + res.status);
    const payload = await res.json();
    const items = Array.isArray(payload) ? payload : payload.items || [];
    if (items.some((d) => typeof d.label !== "string" || typeof d.value !== "number")) {
      throw new Error("invalid data shape");
    }
    document.getElementById("status").hidden = true;
    const ctx = document.getElementById("chart");
    new Chart(ctx, {
      type: KIND === "line" ? "line" : KIND === "pie" ? "pie" : "bar",
      data: {
        labels: items.map((d) => d.label),
        datasets: [{ label: @@TITLE@@, data: items.map((d) => d.value), backgroundColor: "#2563eb" }],
      },
      options: { responsive: true, maintainAspectRatio: false },
    });
  } catch {
    document.getElementById("status").textContent = "Failed to load chart data";
  }
}

load();
''', {
            "ENDPOINT": safe.js_str(endpoint),
            "TITLE": safe.js_str(title),
            "KIND": safe.js_str(chart_type),
        })
        body = render('''<div class="wrap">
  <h1>@@TITLE_HTML@@</h1>
  <p id="status" role="status">Loading chart…</p>
  <div style="height: 320px"><canvas id="chart" aria-label="@@TITLE_HTML@@"></canvas></div>
</div>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"></script>
''', {"TITLE_HTML": safe.html_text(title)})
        content = _html_shell(title, body, script=script)
        path = rel_path(project, "site", "%s-chart.html" % safe.snake(args["name"], "name").replace("_", "-"))
        wiring = "Static page: open %s (requires internet for the Chart.js CDN)." % path.rsplit("/", 1)[-1]
        deps = {"chart.js@4.4.7 (CDN)": "https://cdn.jsdelivr.net/npm/chart.js@4.4.7/dist/chart.umd.min.js"}

    data = {
        "framework": framework,
        "component": name,
        "path": path,
        "chart_type": chart_type,
        "endpoint": endpoint,
        "expected_payload": '{"items":[{"label":"string","value":"number"}, ...]} or a bare array',
        "dependencies": deps,
        "wiring": wiring,
    }
    limitations = [LIMIT_NO_BUILD] + ([LIMIT_CHART_DEP] if framework != "html" else [LIMIT_NO_BUILD])
    return {"data": data, "writes": [(path, content)], "limitations": limitations}


_register("fe.gen.chart", 62, "Generate a chart component (bar/line/pie) fed by a JSON endpoint", _obj({
    "project": _SLUG_S,
    "framework": _FRAMEWORK_S,
    "name": {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_-]*$", "minLength": 2, "maxLength": 48, "default": "sales"},
    "chart_type": {"type": "string", "enum": ["bar", "line", "pie"], "default": "bar"},
    "endpoint": {"type": "string", "pattern": r"^/", "minLength": 1, "maxLength": 200, "default": "/api/stats"},
    "title": {"type": "string", "minLength": 1, "maxLength": 100, "default": "Overview"},
}, ["project"]), _chart)


# ---------------------------------------------------------------------------
# 63 fe.gen.modal
# ---------------------------------------------------------------------------

def _modal(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    framework = args["framework"]
    name = safe.pascal(args["name"], "name")
    title = _bounded(args["title"], "title", 100)

    if framework == "react":
        content = render('''"use client";

import { ReactNode, useEffect, useRef } from "react";

export function @@NAME@@Modal({
  open,
  onClose,
  title,
  children,
}: {
  open: boolean;
  onClose: () => void;
  title: string;
  children: ReactNode;
}) {
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    closeRef.current?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      className="modal-backdrop"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div role="dialog" aria-modal="true" aria-label={title} className="modal">
        <header className="modal-header">
          <h2 id="@@KEBAB@@-title">{title}</h2>
          <button ref={closeRef} type="button" className="modal-close" onClick={onClose} aria-label="Close dialog">
            ×
          </button>
        </header>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}
''', {"NAME": name, "KEBAB": safe.snake(args["name"], "name").replace("_", "-")})
        path = rel_path(project, "components", "%sModal.tsx" % name)
        usage = render('import { @@NAME@@Modal } from "@/components/@@NAME@@Modal"; — control with open/onClose state.',
                       {"NAME": name})
        css_path: str | None = rel_path(project, "styles", "modal.css")
        css = MODAL_CSS
    elif framework == "vue":
        content = render('''<script setup lang="ts">
const props = defineProps<{ open: boolean; title: string }>();
const emit = defineEmits<{ close: [] }>();

function onKey(event: KeyboardEvent) {
  if (event.key === "Escape") emit("close");
}
</script>

<template>
  <Teleport to="body">
    <div v-if="props.open" class="modal-backdrop" @click.self="emit('close')" @keydown="onKey">
      <div role="dialog" aria-modal="true" :aria-label="props.title" class="modal">
        <header class="modal-header">
          <h2>{{ props.title }}</h2>
          <button type="button" class="modal-close" aria-label="Close dialog" @click="emit('close')">×</button>
        </header>
        <div class="modal-body">
          <slot />
        </div>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.modal-backdrop { position: fixed; inset: 0; background: rgb(0 0 0 / .4);
  display: grid; place-items: center; z-index: 50; }
.modal { background: #fff; border-radius: .5rem; max-width: 28rem; width: calc(100vw - 2rem);
  max-height: 85vh; overflow: auto; padding: 1.25rem; }
.modal-header { display: flex; justify-content: space-between; align-items: center; }
.modal-close { border: none; background: none; font-size: 1.5rem; cursor: pointer; line-height: 1; }
</style>
''', {})
        path = rel_path(project, "components", "%sModal.vue" % name)
        usage = "Auto-imported by Nuxt: <%sModal :open=\"open\" title=\"…\" @close=\"open = false\">…</%sModal>." % (name, name)
        css_path = None
        css = ""
    elif framework == "svelte":
        content = render('''<script lang="ts">
  let { open, title, onclose, children }: {
    open: boolean;
    title: string;
    onclose?: () => void;
    children?: import("svelte").Snippet;
  } = $props();

  function onKey(event: KeyboardEvent) {
    if (event.key === "Escape" && onclose) onclose();
  }
</script>

<svelte:window onkeydown={onKey} />

{#if open}
  <div
    class="modal-backdrop"
    onclick={(event) => {
      if (event.target === event.currentTarget && onclose) onclose();
    }}
  >
    <div role="dialog" aria-modal="true" aria-label={title} class="modal">
      <header class="modal-header">
        <h2>{title}</h2>
        <button type="button" class="modal-close" aria-label="Close dialog" onclick={() => onclose?.()}>×</button>
      </header>
      <div class="modal-body">
        {@render children?.()}
      </div>
    </div>
  </div>
{/if}

<style>
  .modal-backdrop { position: fixed; inset: 0; background: rgb(0 0 0 / .4);
    display: grid; place-items: center; z-index: 50; }
  .modal { background: #fff; border-radius: .5rem; max-width: 28rem; width: calc(100vw - 2rem);
    max-height: 85vh; overflow: auto; padding: 1.25rem; }
  .modal-header { display: flex; justify-content: space-between; align-items: center; }
  .modal-close { border: none; background: none; font-size: 1.5rem; cursor: pointer; line-height: 1; }
</style>
''', {})
        path = rel_path(project, "src", "lib", "components", "%sModal.svelte" % name)
        usage = 'import %sModal from "$lib/components/%sModal.svelte";' % (name, name)
        css_path = None
        css = ""
    else:
        content = _html_shell(
            "%s modal" % name,
            render('''<div class="wrap">
  <button type="button" class="primary" id="open">Open dialog</button>
  <dialog id="dialog" aria-labelledby="dialog-title">
    <h2 id="dialog-title">@@T_HTML@@</h2>
    <p>Dialog content goes here.</p>
    <form method="dialog"><button type="submit">Close</button></form>
  </dialog>
</div>
<script>
  const dialog = document.getElementById("dialog");
  document.getElementById("open").addEventListener("click", () => dialog.showModal());
</script>
''', {"T_HTML": safe.html_text(title)}),
        )
        path = rel_path(project, "site", "%s-modal.html" % safe.snake(args["name"], "name").replace("_", "-"))
        usage = "Static demo using the native <dialog> element (focus management included)."
        css_path = None
        css = ""

    writes: List[Pair] = [(path, content)]
    if css_path and css:
        writes.append((css_path, render(css, {"NAME": name})))

    data = {
        "framework": framework,
        "component": name,
        "paths": [w[0] for w in writes],
        "wiring": usage,
        "accessibility": "role=dialog, aria-modal, Escape/backdrop close, close button focused on open (react variant).",
    }
    return {"data": data, "writes": writes, "limitations": [LIMIT_NO_BUILD]}


MODAL_CSS = '''/* Modal primitives (pair with @@NAME@@Modal component). */
.modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgb(0 0 0 / 0.4);
  display: grid;
  place-items: center;
  z-index: 50;
}

.modal {
  background: #fff;
  border-radius: 0.5rem;
  max-width: 28rem;
  width: calc(100vw - 2rem);
  max-height: 85vh;
  overflow: auto;
  padding: 1.25rem;
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
}

.modal-close {
  border: none;
  background: none;
  font-size: 1.5rem;
  cursor: pointer;
  line-height: 1;
}
'''


_register("fe.gen.modal", 63, "Generate an accessible modal/dialog component", _obj({
    "project": _SLUG_S,
    "framework": _FRAMEWORK_S,
    "name": {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_-]*$", "minLength": 2, "maxLength": 48, "default": "confirm"},
    "title": {"type": "string", "minLength": 1, "maxLength": 100, "default": "Confirm action"},
}, ["project"]), _modal)


# ---------------------------------------------------------------------------
# 64 fe.gen.auth_page
# ---------------------------------------------------------------------------

def _auth_page(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    framework = args["framework"]
    login_endpoint = _endpoint(args["login_endpoint"])
    register_endpoint = _endpoint(args["register_endpoint"])
    redirect_to = _href(args["redirect_to"])

    note = ("Sessions are server-side: expect the API to set an httpOnly, Secure, "
            "SameSite cookie. The client never stores tokens in localStorage.")

    if framework == "react":
        content = render('''"use client";

import { FormEvent, useState } from "react";

type Mode = "login" | "register";

export function @@NAME@@() {
  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [confirm, setConfirm] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  function validate(): string | null {
    if (!/^[^@\\s]+@[^@\\s]+\\.[^@\\s]{2,}$/.test(email)) return "Enter a valid email address.";
    if (password.length < 8) return "Password must be at least 8 characters.";
    if (mode === "register" && password !== confirm) return "Passwords do not match.";
    return null;
  }

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const problem = validate();
    if (problem) {
      setError(problem);
      return;
    }
    setError(null);
    setBusy(true);
    try {
      const res = await fetch(mode === "login" ? @@LOGIN@@ : @@REGISTER@@, {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        const data = (await res.json().catch(() => null)) as { error?: string } | null;
        setError(data?.error ?? "Authentication failed (" + res.status + ").");
        return;
      }
      // Session cookie is set by the server; navigate to the app.
      window.location.href = @@REDIRECT@@;
    } catch {
      setError("Network error; please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="auth">
      <h1>{mode === "login" ? "Sign in" : "Create account"}</h1>
      <form onSubmit={submit} noValidate>
        <label htmlFor="auth-email">Email</label>
        <input id="auth-email" type="email" autoComplete="email" value={email}
               onChange={(e) => setEmail(e.target.value)} required />
        <label htmlFor="auth-password">Password</label>
        <input id="auth-password" type="password" autoComplete={mode === "login" ? "current-password" : "new-password"}
               value={password} onChange={(e) => setPassword(e.target.value)} required minLength={8} />
        {mode === "register" ? (
          <>
            <label htmlFor="auth-confirm">Confirm password</label>
            <input id="auth-confirm" type="password" autoComplete="new-password"
                   value={confirm} onChange={(e) => setConfirm(e.target.value)} required minLength={8} />
          </>
        ) : null}
        <button type="submit" disabled={busy}>
          {busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}
        </button>
        {error ? <p className="error" role="alert">{error}</p> : null}
      </form>
      <p>
        {mode === "login" ? "No account yet? " : "Already registered? "}
        <button type="button" className="link" onClick={() => { setMode(mode === "login" ? "register" : "login"); setError(null); }}>
          {mode === "login" ? "Create one" : "Sign in"}
        </button>
      </p>
    </main>
  );
}
''', {
            "NAME": safe.pascal(args.get("name", "auth"), "name"),
            "LOGIN": safe.js_str(login_endpoint),
            "REGISTER": safe.js_str(register_endpoint),
            "REDIRECT": safe.js_str(redirect_to),
        })
        path = rel_path(project, "components", "AuthPanel.tsx")
        wiring = 'Import { AuthPanel } from "@/components/AuthPanel" and render it on your /login page.'
    elif framework == "vue":
        content = render('''<script setup lang="ts">
import { ref } from "vue";

const mode = ref<"login" | "register">("login");
const email = ref("");
const password = ref("");
const confirm = ref("");
const error = ref<string | null>(null);
const busy = ref(false);

function validate(): string | null {
  if (!/^[^@\\s]+@[^@\\s]+\\.[^@\\s]{2,}$/.test(email.value)) return "Enter a valid email address.";
  if (password.value.length < 8) return "Password must be at least 8 characters.";
  if (mode.value === "register" && password.value !== confirm.value) return "Passwords do not match.";
  return null;
}

async function submit() {
  const problem = validate();
  if (problem) { error.value = problem; return; }
  error.value = null;
  busy.value = true;
  try {
    await $fetch(mode.value === "login" ? @@LOGIN@@ : @@REGISTER@@, {
      method: "POST",
      credentials: "same-origin",
      body: { email: email.value, password: password.value },
    });
    window.location.href = @@REDIRECT@@;
  } catch (err: any) {
    error.value = err?.data?.message ?? "Authentication failed; please try again.";
  } finally {
    busy.value = false;
  }
}
</script>

<template>
  <main class="auth">
    <h1>{{ mode === 'login' ? 'Sign in' : 'Create account' }}</h1>
    <form @submit.prevent="submit">
      <label for="email">Email</label>
      <input id="email" v-model="email" type="email" autocomplete="email" required />
      <label for="password">Password</label>
      <input id="password" v-model="password" type="password" required minlength="8"
             :autocomplete="mode === 'login' ? 'current-password' : 'new-password'" />
      <template v-if="mode === 'register'">
        <label for="confirm">Confirm password</label>
        <input id="confirm" v-model="confirm" type="password" required minlength="8" autocomplete="new-password" />
      </template>
      <button type="submit" :disabled="busy">{{ busy ? 'Please wait…' : (mode === 'login' ? 'Sign in' : 'Create account') }}</button>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
    </form>
    <p>
      {{ mode === 'login' ? 'No account yet?' : 'Already registered?' }}
      <button type="button" class="link" @click="mode = mode === 'login' ? 'register' : 'login'">
        {{ mode === 'login' ? 'Create one' : 'Sign in' }}
      </button>
    </p>
  </main>
</template>
''', {
            "LOGIN": safe.js_str(login_endpoint),
            "REGISTER": safe.js_str(register_endpoint),
            "REDIRECT": safe.js_str(redirect_to),
        })
        path = rel_path(project, "pages", "auth.vue")
        wiring = "Route /auth is live via Nuxt file routing."
    elif framework == "svelte":
        content = render('''<script lang="ts">
  let mode: "login" | "register" = $state("login");
  let email = $state("");
  let password = $state("");
  let confirm = $state("");
  let error: string | null = $state(null);
  let busy = $state(false);

  function validate(): string | null {
    if (!/^[^@\\s]+@[^@\\s]+\\.[^@\\s]{2,}$/.test(email)) return "Enter a valid email address.";
    if (password.length < 8) return "Password must be at least 8 characters.";
    if (mode === "register" && password !== confirm) return "Passwords do not match.";
    return null;
  }

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    const problem = validate();
    if (problem) { error = problem; return; }
    error = null;
    busy = true;
    try {
      const res = await fetch(mode === "login" ? @@LOGIN@@ : @@REGISTER@@, {
        method: "POST",
        headers: { "content-type": "application/json" },
        credentials: "same-origin",
        body: JSON.stringify({ email, password }),
      });
      if (!res.ok) {
        const data = await res.json().catch(() => null);
        error = data?.error ?? "Authentication failed (" + res.status + ").";
        return;
      }
      window.location.href = @@REDIRECT@@;
    } catch {
      error = "Network error; please try again.";
    } finally {
      busy = false;
    }
  }
</script>

<main class="auth">
  <h1>{mode === "login" ? "Sign in" : "Create account"}</h1>
  <form onsubmit={submit}>
    <label for="email">Email</label>
    <input id="email" type="email" autocomplete="email" bind:value={email} required />
    <label for="password">Password</label>
    <input id="password" type="password" minlength={8} required
           autocomplete={mode === "login" ? "current-password" : "new-password"}
           bind:value={password} />
    {#if mode === "register"}
      <label for="confirm">Confirm password</label>
      <input id="confirm" type="password" minlength={8} autocomplete="new-password" bind:value={confirm} required />
    {/if}
    <button type="submit" disabled={busy}>{busy ? "Please wait…" : mode === "login" ? "Sign in" : "Create account"}</button>
    {#if error}<p class="error" role="alert">{error}</p>{/if}
  </form>
  <p>
    {mode === "login" ? "No account yet?" : "Already registered?"}
    <button type="button" class="link" onclick={() => { mode = mode === "login" ? "register" : "login"; error = null; }}>
      {mode === "login" ? "Create one" : "Sign in"}
    </button>
  </p>
</main>
''', {
            "LOGIN": safe.js_str(login_endpoint),
            "REGISTER": safe.js_str(register_endpoint),
            "REDIRECT": safe.js_str(redirect_to),
        })
        path = rel_path(project, "src", "routes", "auth", "+page.svelte")
        wiring = "Route /auth is live via SvelteKit file routing."
    else:
        script = render('''const LOGIN = @@LOGIN@@;
const REGISTER = @@REGISTER@@;
const REDIRECT = @@REDIRECT@@;

const form = document.getElementById("auth-form");
const modeLabel = document.getElementById("mode-label");
const confirmRow = document.getElementById("confirm-row");
const errorBox = document.getElementById("auth-error");
const submitButton = document.getElementById("submit");
let mode = "login";

document.getElementById("toggle").addEventListener("click", () => {
  mode = mode === "login" ? "register" : "login";
  modeLabel.textContent = mode === "login" ? "Sign in" : "Create account";
  confirmRow.hidden = mode === "login";
  errorBox.hidden = true;
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  errorBox.hidden = true;
  const email = document.getElementById("email").value.trim();
  const password = document.getElementById("password").value;
  const confirm = document.getElementById("confirm").value;
  if (!/^[^@\\s]+@[^@\\s]+\\.[^@\\s]{2,}$/.test(email)) return showError("Enter a valid email address.");
  if (password.length < 8) return showError("Password must be at least 8 characters.");
  if (mode === "register" && password !== confirm) return showError("Passwords do not match.");

  submitButton.disabled = true;
  try {
    const res = await fetch(mode === "login" ? LOGIN : REGISTER, {
      method: "POST",
      headers: { "content-type": "application/json" },
      credentials: "same-origin",
      body: JSON.stringify({ email, password }),
    });
    if (!res.ok) {
      const data = await res.json().catch(() => null);
      throw new Error((data && data.error) || "Authentication failed (" + res.status + ").");
    }
    window.location.href = REDIRECT;
  } catch (err) {
    showError(err.message);
  } finally {
    submitButton.disabled = false;
  }
});

function showError(message) {
  errorBox.textContent = message;
  errorBox.hidden = false;
}
''', {
            "LOGIN": safe.js_str(login_endpoint),
            "REGISTER": safe.js_str(register_endpoint),
            "REDIRECT": safe.js_str(redirect_to),
        })
        body = render('''<div class="wrap">
  <main class="auth">
    <h1 id="mode-label">Sign in</h1>
    <form id="auth-form" novalidate>
      <label for="email">Email</label>
      <input id="email" type="email" autocomplete="email" required />
      <label for="password">Password</label>
      <input id="password" type="password" minlength="8" autocomplete="current-password" required />
      <div id="confirm-row" hidden>
        <label for="confirm">Confirm password</label>
        <input id="confirm" type="password" minlength="8" autocomplete="new-password" />
      </div>
      <button type="submit" class="primary" id="submit">Sign in</button>
      <p id="auth-error" class="error" role="alert" hidden></p>
    </form>
    <p><button type="button" class="link" id="toggle">Create account instead</button></p>
  </main>
</div>
''', {})
        content = _html_shell("Sign in", body, script=script)
        path = rel_path(project, "site", "auth.html")
        wiring = "Static page: open site/auth.html; posts JSON to the configured endpoints."

    data = {
        "framework": framework,
        "path": path,
        "login_endpoint": login_endpoint,
        "register_endpoint": register_endpoint,
        "wiring": wiring,
        "security_note": note,
        "requires_configuration": [
            "The login/register endpoints must exist server-side and set an httpOnly session cookie; "
            "see be.gen.auth.jwt / be.gen.auth.oauth for compatible server implementations.",
        ],
    }
    return {"data": data, "writes": [(path, content)], "limitations": [LIMIT_NO_BUILD, note]}


_register("fe.gen.auth_page", 64, "Generate login/register UI posting to real auth endpoints (httpOnly-cookie based)", _obj({
    "project": _SLUG_S,
    "framework": _FRAMEWORK_S,
    "name": {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_-]*$", "minLength": 2, "maxLength": 48, "default": "auth"},
    "login_endpoint": {"type": "string", "pattern": r"^/", "minLength": 1, "maxLength": 200, "default": "/api/auth/login"},
    "register_endpoint": {"type": "string", "pattern": r"^/", "minLength": 1, "maxLength": 200, "default": "/api/auth/register"},
    "redirect_to": {"type": "string", "pattern": r"^/", "minLength": 1, "maxLength": 200, "default": "/"},
}, ["project"]), _auth_page)


# ---------------------------------------------------------------------------
# 65 fe.gen.dashboard
# ---------------------------------------------------------------------------

def _dashboard(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    framework = args["framework"]
    title = _bounded(args["title"], "title", 100)
    stats_endpoint = _endpoint(args["stats_endpoint"])
    recent_endpoint = _endpoint(args["recent_endpoint"])

    if framework == "react":
        content = render('''"use client";

import { useCallback, useEffect, useState } from "react";

type Stat = { label: string; value: string | number };
type RecentRow = Record<string, unknown> & { id?: string | number };

const RECENT_COLUMNS: { key: string; label: string }[] = [
  { key: "title", label: "Item" },
  { key: "status", label: "Status" },
  { key: "createdAt", label: "Created" },
];

export function @@NAME@@Dashboard() {
  const [stats, setStats] = useState<Stat[] | null>(null);
  const [recent, setRecent] = useState<RecentRow[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setError(null);
    try {
      const [statsRes, recentRes] = await Promise.all([
        fetch(@@STATS@@),
        fetch(@@RECENT@@),
      ]);
      if (!statsRes.ok || !recentRes.ok) throw new Error("upstream error");
      const statsData = await statsRes.json();
      const recentData = await recentRes.json();
      setStats(Array.isArray(statsData) ? statsData : (statsData.items ?? []));
      setRecent(Array.isArray(recentData) ? recentData : (recentData.items ?? []));
    } catch {
      setError("Failed to load dashboard data");
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  return (
    <main className="dashboard">
      <header className="dashboard-header">
        <h1>@@TITLE_HTML@@</h1>
        <button type="button" onClick={() => void refresh()}>Refresh</button>
      </header>
      {error ? <p className="error" role="alert">{error}</p> : null}
      <section className="stat-grid" aria-label="Key metrics">
        {(stats ?? []).map((stat) => (
          <div key={stat.label} className="stat">
            <span className="stat-label">{stat.label}</span>
            <span className="stat-value">{stat.value}</span>
          </div>
        ))}
      </section>
      <section aria-label="Recent activity">
        <h2>Recent activity</h2>
        <table>
          <thead>
            <tr>
              {RECENT_COLUMNS.map((col) => (
                <th key={col.key} scope="col">{col.label}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {(recent ?? []).slice(0, 10).map((row, index) => (
              <tr key={String(row.id ?? index)}>
                {RECENT_COLUMNS.map((col) => (
                  <td key={col.key}>{String(row[col.key] ?? "—")}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </main>
  );
}
''', {
            "NAME": safe.pascal(args.get("name", "admin"), "name"),
            "TITLE_HTML": safe.html_text(title),
            "STATS": safe.js_str(stats_endpoint),
            "RECENT": safe.js_str(recent_endpoint),
        })
        path = rel_path(project, "components", "%sDashboard.tsx" % safe.pascal(args.get("name", "admin"), "name"))
        wiring = 'import { %sDashboard } from "@/components/%sDashboard";' % (
            safe.pascal(args.get("name", "admin"), "name"), safe.pascal(args.get("name", "admin"), "name"))
    elif framework == "vue":
        content = render('''<script setup lang="ts">
import { onMounted, ref } from "vue";

type Stat = { label: string; value: string | number };
type Row = Record<string, unknown>;

const columns = [
  { key: "title", label: "Item" },
  { key: "status", label: "Status" },
  { key: "createdAt", label: "Created" },
];

const stats = ref<Stat[]>([]);
const recent = ref<Row[]>([]);
const error = ref<string | null>(null);

async function refresh() {
  error.value = null;
  try {
    const [statsData, recentData] = await Promise.all([
      $fetch<Stat[] | { items?: Stat[] }>(@@STATS@@),
      $fetch<Row[] | { items?: Row[] }>(@@RECENT@@),
    ]);
    stats.value = Array.isArray(statsData) ? statsData : (statsData.items ?? []);
    recent.value = Array.isArray(recentData) ? recentData : (recentData.items ?? []);
  } catch {
    error.value = "Failed to load dashboard data";
  }
}

onMounted(refresh);
</script>

<template>
  <main class="dashboard">
    <header>
      <h1>@@TITLE_HTML@@</h1>
      <button type="button" @click="refresh">Refresh</button>
    </header>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <section class="stat-grid" aria-label="Key metrics">
      <div v-for="stat in stats" :key="stat.label" class="stat">
        <span class="stat-label">{{ stat.label }}</span>
        <span class="stat-value">{{ stat.value }}</span>
      </div>
    </section>
    <section aria-label="Recent activity">
      <h2>Recent activity</h2>
      <table>
        <thead><tr><th v-for="col in columns" :key="col.key" scope="col">{{ col.label }}</th></tr></thead>
        <tbody>
          <tr v-for="(row, index) in recent.slice(0, 10)" :key="String(row.id ?? index)">
            <td v-for="col in columns" :key="col.key">{{ row[col.key] ?? '—' }}</td>
          </tr>
        </tbody>
      </table>
    </section>
  </main>
</template>
''', {
            "TITLE_HTML": safe.html_text(title),
            "STATS": safe.js_str(stats_endpoint),
            "RECENT": safe.js_str(recent_endpoint),
        })
        path = rel_path(project, "pages", "admin.vue")
        wiring = "Route /admin is live via Nuxt file routing."
    elif framework == "svelte":
        content = render('''<script lang="ts">
  type Stat = { label: string; value: string | number };
  type Row = Record<string, unknown>;
  const columns = [
    { key: "title", label: "Item" },
    { key: "status", label: "Status" },
    { key: "createdAt", label: "Created" },
  ];

  let stats: Stat[] | null = $state(null);
  let recent: Row[] | null = $state(null);
  let error: string | null = $state(null);

  async function refresh() {
    error = null;
    try {
      const [statsRes, recentRes] = await Promise.all([fetch(@@STATS@@), fetch(@@RECENT@@)]);
      if (!statsRes.ok || !recentRes.ok) throw new Error("upstream error");
      const statsData = await statsRes.json();
      const recentData = await recentRes.json();
      stats = Array.isArray(statsData) ? statsData : (statsData.items ?? []);
      recent = Array.isArray(recentData) ? recentData : (recentData.items ?? []);
    } catch {
      error = "Failed to load dashboard data";
    }
  }

  $effect(() => { void refresh(); });
</script>

<main class="dashboard">
  <header>
    <h1>@@TITLE_HTML@@</h1>
    <button type="button" onclick={refresh}>Refresh</button>
  </header>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  <section class="stat-grid" aria-label="Key metrics">
    {#each stats ?? [] as stat (stat.label)}
      <div class="stat">
        <span class="stat-label">{stat.label}</span>
        <span class="stat-value">{stat.value}</span>
      </div>
    {/each}
  </section>
  <section aria-label="Recent activity">
    <h2>Recent activity</h2>
    <table>
      <thead><tr>{#each columns as col (col.key)}<th scope="col">{col.label}</th>{/each}</tr></thead>
      <tbody>
        {#each (recent ?? []).slice(0, 10) as row, index (String(row.id ?? index))}
          <tr>
            {#each columns as col (col.key)}<td>{String(row[col.key] ?? "—")}</td>{/each}
          </tr>
        {/each}
      </tbody>
    </table>
  </section>
</main>
''', {
            "TITLE_HTML": safe.html_text(title),
            "STATS": safe.js_str(stats_endpoint),
            "RECENT": safe.js_str(recent_endpoint),
        })
        path = rel_path(project, "src", "routes", "admin", "+page.svelte")
        wiring = "Route /admin is live via SvelteKit file routing."
    else:
        script = render('''const STATS = @@STATS@@;
const RECENT = @@RECENT@@;
const COLUMNS = ["title", "status", "createdAt"];

async function load() {
  try {
    const [statsRes, recentRes] = await Promise.all([fetch(STATS), fetch(RECENT)]);
    if (!statsRes.ok || !recentRes.ok) throw new Error("upstream error");
    const statsData = await statsRes.json();
    const recentData = await recentRes.json();
    const stats = Array.isArray(statsData) ? statsData : statsData.items || [];
    const recent = Array.isArray(recentData) ? recentData : recentData.items || [];
    document.getElementById("error").hidden = true;

    const grid = document.getElementById("stats");
    grid.innerHTML = "";
    for (const stat of stats) {
      const cell = document.createElement("div");
      cell.className = "stat";
      const label = document.createElement("span");
      label.className = "stat-label";
      label.textContent = String(stat.label);
      const value = document.createElement("span");
      value.className = "stat-value";
      value.textContent = String(stat.value);
      cell.append(label, value);
      grid.append(cell);
    }

    const tbody = document.querySelector("#recent tbody");
    tbody.innerHTML = "";
    for (const row of recent.slice(0, 10)) {
      const tr = document.createElement("tr");
      for (const key of COLUMNS) {
        const td = document.createElement("td");
        td.textContent = row[key] == null ? "—" : String(row[key]);
        tr.append(td);
      }
      tbody.append(tr);
    }
  } catch {
    document.getElementById("error").hidden = false;
  }
}

document.getElementById("refresh").addEventListener("click", load);
load();
''', {
            "STATS": safe.js_str(stats_endpoint),
            "RECENT": safe.js_str(recent_endpoint),
        })
        body = render('''<div class="wrap" style="max-width:56rem">
  <header style="display:flex;justify-content:space-between;align-items:center">
    <h1>@@TITLE_HTML@@</h1>
    <button type="button" id="refresh">Refresh</button>
  </header>
  <p id="error" class="error" role="alert" hidden>Failed to load dashboard data</p>
  <section id="stats" class="cards" aria-label="Key metrics"></section>
  <section aria-label="Recent activity">
    <h2>Recent activity</h2>
    <table id="recent">
      <thead><tr><th>Item</th><th>Status</th><th>Created</th></tr></thead>
      <tbody></tbody>
    </table>
  </section>
</div>
''', {"TITLE_HTML": safe.html_text(title)})
        content = _html_shell(title, body, script=script)
        path = rel_path(project, "site", "dashboard.html")
        wiring = "Static page: open site/dashboard.html."

    css = ('''.stat-grid { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fill, minmax(10rem, 1fr)); margin: 1rem 0; }
.stat { border: 1px solid #e5e7eb; border-radius: .5rem; padding: 1rem; display: flex; flex-direction: column; }
.stat-value { font-size: 1.75rem; font-weight: 700; }
.dashboard-header { display: flex; justify-content: space-between; align-items: center; }''')
    writes = [(path, content), (rel_path(project, "styles", "dashboard.css"), css)]

    data = {
        "framework": framework,
        "path": path,
        "stats_endpoint": stats_endpoint,
        "recent_endpoint": recent_endpoint,
        "expected_payload": {
            "stats": '{"items":[{"label":"Users","value":123}, ...]} or a bare array',
            "recent": '{"items":[{"id":1,"title":"...","status":"...","createdAt":"..."}, ...]}',
        },
        "wiring": wiring,
    }
    return {"data": data, "writes": writes, "limitations": [LIMIT_NO_BUILD]}


_register("fe.gen.dashboard", 65, "Generate an admin dashboard (stat cards + recent activity) fed by two JSON endpoints", _obj({
    "project": _SLUG_S,
    "framework": _FRAMEWORK_S,
    "name": {"type": "string", "pattern": r"^[A-Za-z][A-Za-z0-9_-]*$", "minLength": 2, "maxLength": 48, "default": "admin"},
    "title": {"type": "string", "minLength": 1, "maxLength": 100, "default": "Admin dashboard"},
    "stats_endpoint": {"type": "string", "pattern": r"^/", "minLength": 1, "maxLength": 200, "default": "/api/stats"},
    "recent_endpoint": {"type": "string", "pattern": r"^/", "minLength": 1, "maxLength": 200, "default": "/api/items"},
}, ["project"]), _dashboard)


# ---------------------------------------------------------------------------
# 66 fe.gen.landing_page
# ---------------------------------------------------------------------------

def _landing(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    framework = args["framework"]
    site = _bounded(args["site_name"], "site_name", 80)
    tagline = _bounded(args["tagline"], "tagline", 160)
    hero = _bounded(args["hero_body"], "hero_body", 400)
    cta_label = _bounded(args["cta_label"], "cta_label", 40)
    cta_href = _href(args["cta_href"])
    features = _features_spec(args["features"])

    html_cards = "\n".join(
        render('''        <article class="card">
          <h3>@@T_HTML@@</h3>
          <p>@@B_HTML@@</p>
        </article>''', {"T_HTML": safe.html_text(f["title"]), "B_HTML": safe.html_text(f["body"])})
        for f in features
    )

    if framework == "react":
        cards = "\n".join(
            render('''          <article className="card" key={@@K@@}>
            <h3>{@@T@@}</h3>
            <p>{@@B@@}</p>
          </article>''', {
                "K": safe.js_str(f["title"]),
                "T": safe.js_str(f["title"]),
                "B": safe.js_str(f["body"]),
            })
            for f in features
        )
        content = render('''import Link from "next/link";
import type { Metadata } from "next";

export const metadata: Metadata = {
  title: @@SITE@@,
  description: @@TAGLINE@@,
};

const FEATURES = [
@@FEATURES@@
];

export default function LandingPage() {
  return (
    <main className="landing">
      <section className="hero">
        <h1>@@SITE@@</h1>
        <p className="tagline">{@@TAGLINE@@}</p>
        <p className="hero-body">{@@HERO@@}</p>
        <Link href={@@CTA_HREF@@} className="cta">
          {@@CTA_LABEL@@}
        </Link>
      </section>
      <section className="features" aria-label="Features">
        <h2>Why @@SITE@@</h2>
        <div className="cards">
          {FEATURES.map((feature) => (
            <article className="card" key={feature.title}>
              <h3>{feature.title}</h3>
              <p>{feature.body}</p>
            </article>
          ))}
        </div>
      </section>
      <footer>
        <p>© {new Date().getFullYear()} @@SITE@@</p>
      </footer>
    </main>
  );
}
''', {
            "SITE": safe.js_str(site),
            "TAGLINE": safe.js_str(tagline),
            "HERO": safe.js_str(hero),
            "CTA_HREF": safe.js_str(cta_href),
            "CTA_LABEL": safe.js_str(cta_label),
            "FEATURES": "\n".join(
                '  { title: %s, body: %s },' % (safe.js_str(f["title"]), safe.js_str(f["body"]))
                for f in features
            ),
        })
        path = rel_path(project, "app", "page.tsx")
        wiring = "Replaces the home route (app/page.tsx)."
    elif framework == "vue":
        content = render('''<script setup lang="ts">
const features = JSON.parse(@@FEATURES@@);
const year = new Date().getFullYear();
</script>

<template>
  <main class="landing">
    <section class="hero">
      <h1>@@SITE_HTML@@</h1>
      <p class="tagline">@@TAGLINE_HTML@@</p>
      <p class="hero-body">@@HERO_HTML@@</p>
      <NuxtLink :to="@@CTA_HREF@@" class="cta">@@CTA_HTML@@</NuxtLink>
    </section>
    <section class="features" aria-label="Features">
      <h2>Why @@SITE_HTML@@</h2>
      <div class="cards">
        <article v-for="feature in features" :key="feature.title" class="card">
          <h3>{{ feature.title }}</h3>
          <p>{{ feature.body }}</p>
        </article>
      </div>
    </section>
    <footer><p>© {{ year }} @@SITE_HTML@@</p></footer>
  </main>
</template>

<style scoped>
.hero { text-align: center; padding: 3rem 0; }
.tagline { font-size: 1.25rem; color: #374151; }
.cta { display: inline-block; margin-top: 1rem; padding: .625rem 1.25rem; border-radius: .375rem;
  background: #2563eb; color: #fff; text-decoration: none; }
.cards { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr)); }
.card { border: 1px solid #e5e7eb; border-radius: .5rem; padding: 1rem; }
</style>
''', {
            "SITE_HTML": safe.html_text(site),
            "TAGLINE_HTML": safe.html_text(tagline),
            "HERO_HTML": safe.html_text(hero),
            "CTA_HREF": safe.js_str(cta_href),
            "CTA_HTML": safe.html_text(cta_label),
            "FEATURES": safe.js_str(json.dumps([{"title": f["title"], "body": f["body"]} for f in features], ensure_ascii=False)),
        })
        path = rel_path(project, "pages", "index.vue")
        wiring = "Replaces the Nuxt home route (pages/index.vue)."
    elif framework == "svelte":
        content = render('''<script lang="ts">
  const features = JSON.parse(@@FEATURES@@);
</script>

<main class="landing">
  <section class="hero">
    <h1>@@SITE_HTML@@</h1>
    <p class="tagline">@@TAGLINE_HTML@@</p>
    <p class="hero-body">@@HERO_HTML@@</p>
    <a class="cta" href={@@CTA_HREF@@}>@@CTA_HTML@@</a>
  </section>
  <section class="features" aria-label="Features">
    <h2>Why @@SITE_HTML@@</h2>
    <div class="cards">
      {#each features as feature (feature.title)}
        <article class="card">
          <h3>{feature.title}</h3>
          <p>{feature.body}</p>
        </article>
      {/each}
    </div>
  </section>
  <footer><p>© {new Date().getFullYear()} @@SITE_HTML@@</p></footer>
</main>

<style>
  .hero { text-align: center; padding: 3rem 0; }
  .tagline { font-size: 1.25rem; color: #374151; }
  .cta { display: inline-block; margin-top: 1rem; padding: .625rem 1.25rem; border-radius: .375rem;
    background: #2563eb; color: #fff; text-decoration: none; }
  .cards { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr)); }
  .card { border: 1px solid #e5e7eb; border-radius: .5rem; padding: 1rem; }
</style>
''', {
            "SITE_HTML": safe.html_text(site),
            "TAGLINE_HTML": safe.html_text(tagline),
            "HERO_HTML": safe.html_text(hero),
            "CTA_HREF": safe.js_str(cta_href),
            "CTA_HTML": safe.html_text(cta_label),
            "FEATURES": safe.js_str(json.dumps([{"title": f["title"], "body": f["body"]} for f in features], ensure_ascii=False)),
        })
        path = rel_path(project, "src", "routes", "+page.svelte")
        wiring = "Replaces the SvelteKit home route (+page.svelte)."
    else:
        body = render('''<div class="wrap">
  <main class="landing">
    <section class="hero">
      <h1>@@SITE_HTML@@</h1>
      <p class="tagline">@@TAGLINE_HTML@@</p>
      <p class="hero-body">@@HERO_HTML@@</p>
      <a class="cta" href="@@CTA_HREF@@">@@CTA_HTML@@</a>
    </section>
    <section class="features" aria-label="Features">
      <h2>Why @@SITE_HTML@@</h2>
      <div class="cards">
@@CARDS@@
      </div>
    </section>
    <footer><p>© <span class="js-year"></span> @@SITE_HTML@@</p></footer>
  </main>
</div>
<script>document.querySelectorAll(".js-year").forEach((el) => { el.textContent = String(new Date().getFullYear()); });</script>
''', {
            "SITE_HTML": safe.html_text(site),
            "TAGLINE_HTML": safe.html_text(tagline),
            "HERO_HTML": safe.html_text(hero),
            "CTA_HREF": safe.html_text(cta_href),
            "CTA_HTML": safe.html_text(cta_label),
            "CARDS": html_cards,
        })
        css = BASE_HTML_CSS + '''
    .landing { text-align: left; }
    .hero { text-align: center; padding: 3rem 0; }
    .tagline { font-size: 1.25rem; color: #374151; }
    .cta { display: inline-block; margin-top: 1rem; padding: .625rem 1.25rem; border-radius: .375rem;
      background: #2563eb; color: #fff; text-decoration: none; }
    .cards { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr)); }
    .card { border: 1px solid #e5e7eb; border-radius: .5rem; padding: 1rem; }'''
        content = _html_shell(site, body, extra_css=css)
        path = rel_path(project, "site", "index.html")
        wiring = "Static landing page at site/index.html."

    data = {
        "framework": framework,
        "path": path,
        "wiring": wiring,
    }
    return {"data": data, "writes": [(path, content)], "limitations": [LIMIT_NO_BUILD]}


_register("fe.gen.landing_page", 66, "Generate a complete landing page (hero, features, CTA, footer)", _obj({
    "project": _SLUG_S,
    "framework": _FRAMEWORK_S,
    "site_name": {"type": "string", "minLength": 1, "maxLength": 80},
    "tagline": {"type": "string", "minLength": 1, "maxLength": 160},
    "hero_body": {"type": "string", "minLength": 1, "maxLength": 400},
    "cta_label": {"type": "string", "minLength": 1, "maxLength": 40, "default": "Get started"},
    "cta_href": {"type": "string", "minLength": 1, "maxLength": 300, "default": "/signup"},
    "features": {
        "type": "array",
        "minItems": 1,
        "maxItems": 8,
        "items": _obj({
            "title": {"type": "string", "minLength": 1, "maxLength": 100},
            "body": {"type": "string", "minLength": 1, "maxLength": 400},
        }, ["title", "body"]),
    },
}, ["project", "site_name", "tagline", "hero_body", "features"]), _landing)


# ---------------------------------------------------------------------------
# 67 fe.gen.ecommerce
# ---------------------------------------------------------------------------

def _ecommerce(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    framework = args["framework"]
    currency = args["currency"]
    catalog = _endpoint(args["catalog_endpoint"])
    checkout = _endpoint(args["checkout_endpoint"])

    if framework != "html":
        limitations = [LIMIT_NO_BUILD]
        note = ("Cart is client-side (persists via localStorage). The checkout endpoint MUST "
                "recompute prices server-side; never trust client totals.")
    else:
        limitations = [LIMIT_NO_BUILD]
        note = ("Demo store persists the cart in localStorage. The checkout endpoint MUST "
                "recompute prices server-side; never trust client totals.")

    if framework == "react":
        store_ts = render('''"use client";

import { createContext, ReactNode, useContext, useEffect, useMemo, useState } from "react";

export type Product = {
  id: string | number;
  title: string;
  price: number;
  image?: string;
};

export type CartLine = { product: Product; quantity: number };

type CartContextValue = {
  lines: CartLine[];
  add: (product: Product, quantity?: number) => void;
  remove: (productId: Product["id"]) => void;
  setQuantity: (productId: Product["id"], quantity: number) => void;
  clear: () => void;
  total: number;
};

const CartContext = createContext<CartContextValue | null>(null);
const STORAGE_KEY = "@@PROJECT@@.cart";

export function CartProvider({ children }: { children: ReactNode }) {
  const [lines, setLines] = useState<CartLine[]>([]);

  useEffect(() => {
    try {
      const raw = window.localStorage.getItem(STORAGE_KEY);
      if (raw) setLines(JSON.parse(raw) as CartLine[]);
    } catch {
      // Corrupt cart: start empty instead of crashing.
    }
  }, []);

  useEffect(() => {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(lines));
  }, [lines]);

  const value = useMemo<CartContextValue>(() => {
    const total = lines.reduce((sum, line) => sum + line.product.price * line.quantity, 0);
    return {
      lines,
      total,
      add: (product, quantity = 1) =>
        setLines((current) => {
          const existing = current.find((line) => line.product.id === product.id);
          if (existing) {
            return current.map((line) =>
              line.product.id === product.id
                ? { ...line, quantity: line.quantity + quantity }
                : line,
            );
          }
          return [...current, { product, quantity }];
        }),
      remove: (productId) =>
        setLines((current) => current.filter((line) => line.product.id !== productId)),
      setQuantity: (productId, quantity) =>
        setLines((current) =>
          quantity <= 0
            ? current.filter((line) => line.product.id !== productId)
            : current.map((line) =>
                line.product.id === productId ? { ...line, quantity } : line,
              ),
        ),
      clear: () => setLines([]),
    };
  }, [lines]);

  return <CartContext.Provider value={value}>{children}</CartContext.Provider>;
}

export function useCart(): CartContextValue {
  const context = useContext(CartContext);
  if (!context) {
    throw new Error("useCart must be used inside <CartProvider>");
  }
  return context;
}
''', {"PROJECT": project})
        product_card = render('''"use client";

import { useCart, type Product } from "./CartProvider";

export function ProductCard({ product }: { product: Product }) {
  const cart = useCart();
  return (
    <article className="product-card">
      {product.image ? <img src={product.image} alt={product.title} width={200} height={200} /> : null}
      <h3>{product.title}</h3>
      <p className="price">{new Intl.NumberFormat("@@LOCALE@@", { style: "currency", currency: "@@CURRENCY@@" }).format(product.price)}</p>
      <button type="button" onClick={() => cart.add(product)}>Add to cart</button>
    </article>
  );
}
''', {"LOCALE": "id-ID" if currency == "IDR" else "en-US", "CURRENCY": currency})
        products_page = render('''"use client";

import { useEffect, useState } from "react";
import { useCart, type Product } from "../../components/CartProvider";
import { ProductCard } from "../../components/ProductCard";

export default function ProductsPage() {
  const cart = useCart();
  const [products, setProducts] = useState<Product[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const res = await fetch(@@CATALOG@@);
        if (!res.ok) throw new Error("status " + res.status);
        const data = await res.json();
        setProducts(Array.isArray(data) ? data : (data.items ?? []));
      } catch {
        setError("Failed to load the catalog");
      }
    })();
  }, []);

  return (
    <main className="products">
      <h1>Products</h1>
      <p>
        <a href="/cart">Cart: {cart.lines.length} item(s)</a>
      </p>
      {error ? <p className="error" role="alert">{error}</p> : null}
      <div className="cards">
        {(products ?? []).map((product) => (
          <ProductCard key={String(product.id)} product={product} />
        ))}
      </div>
    </main>
  );
}
''', {"CATALOG": safe.js_str(catalog)})
        cart_page = render('''"use client";

import { useCart } from "../../components/CartProvider";

export default function CartPage() {
  const cart = useCart();
  const format = (value: number) =>
    new Intl.NumberFormat("@@LOCALE@@", { style: "currency", currency: "@@CURRENCY@@" }).format(value);

  return (
    <main className="cart">
      <h1>Cart</h1>
      {cart.lines.length === 0 ? (
        <p>Your cart is empty.</p>
      ) : (
        <>
          <table>
            <thead>
              <tr><th scope="col">Product</th><th scope="col">Qty</th><th scope="col">Subtotal</th><th scope="col"></th></tr>
            </thead>
            <tbody>
              {cart.lines.map((line) => (
                <tr key={String(line.product.id)}>
                  <td>{line.product.title}</td>
                  <td>
                    <input
                      type="number"
                      min={1}
                      value={line.quantity}
                      aria-label={"Quantity of " + line.product.title}
                      onChange={(e) => cart.setQuantity(line.product.id, Number(e.target.value) || 0)}
                    />
                  </td>
                  <td>{format(line.product.price * line.quantity)}</td>
                  <td>
                    <button type="button" onClick={() => cart.remove(line.product.id)}>Remove</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <p className="total">Total: {format(cart.total)}</p>
          <a href="/checkout" className="cta">Proceed to checkout</a>
        </>
      )}
    </main>
  );
}
''', {"LOCALE": "id-ID" if currency == "IDR" else "en-US", "CURRENCY": currency})
        checkout_page = render('''"use client";

import { FormEvent, useState } from "react";
import { useCart } from "../../components/CartProvider";

export default function CheckoutPage() {
  const cart = useCart();
  const [status, setStatus] = useState<"idle" | "submitting" | "error">("idle");
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (cart.lines.length === 0) {
      setError("Your cart is empty.");
      return;
    }
    setStatus("submitting");
    setError(null);
    const data = new FormData(event.currentTarget);
    try {
      const res = await fetch(@@CHECKOUT@@, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          customer: {
            name: String(data.get("name") ?? ""),
            email: String(data.get("email") ?? ""),
            address: String(data.get("address") ?? ""),
          },
          items: cart.lines.map((line) => ({
            productId: line.product.id,
            quantity: line.quantity,
          })),
        }),
      });
      if (!res.ok) {
        const body = (await res.json().catch(() => null)) as { error?: string } | null;
        throw new Error(body?.error ?? "Checkout failed (" + res.status + ")");
      }
      cart.clear();
      window.location.href = "/checkout?status=placed";
    } catch (err) {
      setStatus("error");
      setError(err instanceof Error ? err.message : "Checkout failed");
    }
  }

  return (
    <main className="checkout">
      <h1>Checkout</h1>
      {new URLSearchParams(window.location.search).get("status") === "placed" ? (
        <p className="ok" role="status">Order placed! A confirmation email is on its way.</p>
      ) : null}
      <form onSubmit={submit}>
        <label htmlFor="name">Full name</label>
        <input id="name" name="name" required autoComplete="name" />
        <label htmlFor="email">Email</label>
        <input id="email" name="email" type="email" required autoComplete="email" />
        <label htmlFor="address">Shipping address</label>
        <textarea id="address" name="address" required autoComplete="street-address" rows={3} />
        <button type="submit" disabled={status === "submitting"}>
          {status === "submitting" ? "Placing order…" : "Place order"}
        </button>
        {error ? <p className="error" role="alert">{error}</p> : null}
      </form>
    </main>
  );
}
''', {"CHECKOUT": safe.js_str(checkout)})
        commerce_css = '''/* E-commerce primitives */
.products .cards { display: grid; gap: 1rem; grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr)); }
.product-card { border: 1px solid #e5e7eb; border-radius: .5rem; padding: 1rem; display: flex; flex-direction: column; gap: .5rem; }
.product-card img { width: 100%; height: auto; border-radius: .375rem; }
.price { font-weight: 700; }
table { border-collapse: collapse; width: 100%; }
th, td { text-align: left; padding: .5rem; border-bottom: 1px solid #e5e7eb; }
input[type=number] { width: 4.5rem; }
.cta { display: inline-block; margin-top: 1rem; padding: .625rem 1.25rem; border-radius: .375rem;
  background: #2563eb; color: #fff; text-decoration: none; }'''
        writes = [
            (rel_path(project, "components", "CartProvider.tsx"), store_ts),
            (rel_path(project, "components", "ProductCard.tsx"), product_card),
            (rel_path(project, "app", "products", "page.tsx"), products_page),
            (rel_path(project, "app", "cart", "page.tsx"), cart_page),
            (rel_path(project, "app", "checkout", "page.tsx"), checkout_page),
            (rel_path(project, "styles", "commerce.css"), commerce_css),
            (rel_path(project, "app", "layout.tsx"),
             "NOTE: wrap your existing app/layout.tsx children with <CartProvider> from "
             "@/components/CartProvider and import styles/commerce.css. See data.wiring.\n"),
        ]
        wiring = {
            "provider": 'Wrap the app in <CartProvider> (app/layout.tsx): '
                        'import { CartProvider } from "@/components/CartProvider";',
            "css": 'import "./styles/commerce.css"; in app/layout.tsx (or your global stylesheet)',
            "routes": ["/products", "/cart", "/checkout"],
            "checkout_payload": '{"customer":{"name","email","address"},"items":[{"productId","quantity"}]}',
        }
        paths_note = "components/CartProvider.tsx + ProductCard.tsx, app/products|cart|checkout pages"

    elif framework == "vue":
        content_store = render('''// composables/useCart.ts — Nuxt composable with localStorage persistence.
import { computed, ref } from "vue";

export type Product = { id: string | number; title: string; price: number; image?: string };
export type CartLine = { product: Product; quantity: number };

const STORAGE_KEY = "@@PROJECT@@.cart";
const lines = ref<CartLine[]>([]);

export function useCart() {
  const total = computed(() => lines.value.reduce((sum, l) => sum + l.product.price * l.quantity, 0));

  function persist() {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(lines.value));
  }

  function restore() {
    try {
      const raw = localStorage.getItem(STORAGE_KEY);
      if (raw) lines.value = JSON.parse(raw) as CartLine[];
    } catch {
      lines.value = [];
    }
  }

  function add(product: Product, quantity = 1) {
    const existing = lines.value.find((l) => l.product.id === product.id);
    if (existing) {
      existing.quantity += quantity;
    } else {
      lines.value.push({ product, quantity });
    }
    persist();
  }

  function remove(productId: Product["id"]) {
    lines.value = lines.value.filter((l) => l.product.id !== productId);
    persist();
  }

  function setQuantity(productId: Product["id"], quantity: number) {
    lines.value = lines.value
      .map((l) => (l.product.id === productId ? { ...l, quantity } : l))
      .filter((l) => l.quantity > 0);
    persist();
  }

  function clear() {
    lines.value = [];
    persist();
  }

  return { lines, total, add, remove, setQuantity, clear, restore };
}
''', {"PROJECT": project})
        products_page = render('''<script setup lang="ts">
import { onMounted, ref } from "vue";
import { useCart, type Product } from "~/composables/useCart";

const cart = useCart();
const products = ref<Product[]>([]);
const error = ref<string | null>(null);
const format = (v: number) => new Intl.NumberFormat("@@LOCALE@@", { style: "currency", currency: "@@CURRENCY@@" }).format(v);

onMounted(async () => {
  cart.restore();
  try {
    const data = await $fetch<Product[] | { items?: Product[] }>(@@CATALOG@@);
    products.value = Array.isArray(data) ? data : (data.items ?? []);
  } catch {
    error.value = "Failed to load the catalog";
  }
});
</script>

<template>
  <main class="products">
    <h1>Products</h1>
    <p><NuxtLink to="/cart">Cart: {{ cart.lines.value.length }} item(s)</NuxtLink></p>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <div class="cards">
      <article v-for="product in products" :key="String(product.id)" class="product-card">
        <img v-if="product.image" :src="product.image" :alt="product.title" width="200" />
        <h3>{{ product.title }}</h3>
        <p class="price">{{ format(product.price) }}</p>
        <button type="button" @click="cart.add(product)">Add to cart</button>
      </article>
    </div>
  </main>
</template>
''', {"CATALOG": safe.js_str(catalog), "LOCALE": "id-ID" if currency == "IDR" else "en-US", "CURRENCY": currency})
        cart_page = render('''<script setup lang="ts">
import { onMounted } from "vue";
import { useCart } from "~/composables/useCart";

const cart = useCart();
const format = (v: number) => new Intl.NumberFormat("@@LOCALE@@", { style: "currency", currency: "@@CURRENCY@@" }).format(v);
onMounted(() => cart.restore());
</script>

<template>
  <main>
    <h1>Cart</h1>
    <p v-if="cart.lines.value.length === 0">Your cart is empty.</p>
    <template v-else>
      <table>
        <thead><tr><th>Product</th><th>Qty</th><th>Subtotal</th><th></th></tr></thead>
        <tbody>
          <tr v-for="line in cart.lines.value" :key="String(line.product.id)">
            <td>{{ line.product.title }}</td>
            <td><input type="number" min="1" :value="line.quantity"
                       :aria-label="'Quantity of ' + line.product.title"
                       @change="cart.setQuantity(line.product.id, Number(($event.target as HTMLInputElement).value) || 0)" /></td>
            <td>{{ format(line.product.price * line.quantity) }}</td>
            <td><button type="button" @click="cart.remove(line.product.id)">Remove</button></td>
          </tr>
        </tbody>
      </table>
      <p>Total: {{ format(cart.total.value) }}</p>
      <NuxtLink to="/checkout" class="cta">Proceed to checkout</NuxtLink>
    </template>
  </main>
</template>
''', {"LOCALE": "id-ID" if currency == "IDR" else "en-US", "CURRENCY": currency})
        checkout_page = render('''<script setup lang="ts">
import { ref } from "vue";
import { useCart } from "~/composables/useCart";

const cart = useCart();
const status = ref<"idle" | "submitting" | "error">("idle");
const error = ref<string | null>(null);

async function submit() {
  status.value = "submitting";
  error.value = null;
  try {
    await $fetch(@@CHECKOUT@@, {
      method: "POST",
      body: {
        customer: { name: customerName.value, email: customerEmail.value, address: customerAddress.value },
        items: cart.lines.value.map((l) => ({ productId: l.product.id, quantity: l.quantity })),
      },
    });
    cart.clear();
    navigateTo("/checkout?status=placed");
  } catch (err: any) {
    status.value = "error";
    error.value = err?.data?.message ?? "Checkout failed; please try again.";
  }
}

const customerName = ref("");
const customerEmail = ref("");
const customerAddress = ref("");
</script>

<template>
  <main>
    <h1>Checkout</h1>
    <form @submit.prevent="submit">
      <label for="name">Full name</label><input id="name" v-model="customerName" required autocomplete="name" />
      <label for="email">Email</label><input id="email" v-model="customerEmail" type="email" required autocomplete="email" />
      <label for="address">Shipping address</label><textarea id="address" v-model="customerAddress" rows="3" required autocomplete="street-address"></textarea>
      <button type="submit" :disabled="status === 'submitting'">
        {{ status === 'submitting' ? 'Placing order…' : 'Place order' }}
      </button>
      <p v-if="error" class="error" role="alert">{{ error }}</p>
    </form>
  </main>
</template>
''', {"CHECKOUT": safe.js_str(checkout)})
        writes = [
            (rel_path(project, "composables", "useCart.ts"), content_store),
            (rel_path(project, "pages", "products", "index.vue"), products_page),
            (rel_path(project, "pages", "cart", "index.vue"), cart_page),
            (rel_path(project, "pages", "checkout", "index.vue"), checkout_page),
        ]
        wiring = {
            "composable": "useCart() from composables/useCart.ts (auto-imported by Nuxt).",
            "routes": ["/products", "/cart", "/checkout"],
            "checkout_payload": '{"customer":{...},"items":[{"productId","quantity"}]}',
        }
        paths_note = "composables/useCart.ts + pages/products|cart|checkout"

    elif framework == "svelte":
        store_ts = render('''// src/lib/cart.svelte.ts — Svelte 5 runes-based cart store.
export type Product = { id: string | number; title: string; price: number; image?: string };
export type CartLine = { product: Product; quantity: number };

const STORAGE_KEY = "@@PROJECT@@.cart";

class CartStore {
  lines = $state<CartLine[]>([]);

  constructor() {
    if (typeof window !== "undefined") {
      try {
        const raw = window.localStorage.getItem(STORAGE_KEY);
        if (raw) this.lines = JSON.parse(raw) as CartLine[];
      } catch {
        this.lines = [];
      }
    }
  }

  get total(): number {
    return this.lines.reduce((sum, l) => sum + l.product.price * l.quantity, 0);
  }

  #persist() {
    if (typeof window !== "undefined") {
      window.localStorage.setItem(STORAGE_KEY, JSON.stringify(this.lines));
    }
  }

  add(product: Product, quantity = 1) {
    const existing = this.lines.find((l) => l.product.id === product.id);
    if (existing) {
      existing.quantity += quantity;
    } else {
      this.lines.push({ product, quantity });
    }
    this.#persist();
  }

  remove(productId: Product["id"]) {
    this.lines = this.lines.filter((l) => l.product.id !== productId);
    this.#persist();
  }

  setQuantity(productId: Product["id"], quantity: number) {
    this.lines = this.lines
      .map((l) => (l.product.id === productId ? { ...l, quantity } : l))
      .filter((l) => l.quantity > 0);
    this.#persist();
  }

  clear() {
    this.lines = [];
    this.#persist();
  }
}

export const cart = new CartStore();
''', {"PROJECT": project})
        products_page = render('''<script lang="ts">
  import { cart, type Product } from "$lib/cart.svelte";

  let products: Product[] | null = $state(null);
  let error: string | null = $state(null);
  const format = (v: number) => new Intl.NumberFormat("@@LOCALE@@", { style: "currency", currency: "@@CURRENCY@@" }).format(v);

  $effect(() => {
    void (async () => {
      try {
        const res = await fetch(@@CATALOG@@);
        if (!res.ok) throw new Error("status " + res.status);
        const data = await res.json();
        products = Array.isArray(data) ? data : (data.items ?? []);
      } catch {
        error = "Failed to load the catalog";
      }
    })();
  });
</script>

<main>
  <h1>Products</h1>
  <p><a href="/cart">Cart: {cart.lines.length} item(s)</a></p>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  <div class="cards">
    {#each products ?? [] as product (String(product.id))}
      <article class="product-card">
        {#if product.image}<img src={product.image} alt={product.title} width="200" />{/if}
        <h3>{product.title}</h3>
        <p class="price">{format(product.price)}</p>
        <button type="button" onclick={() => cart.add(product)}>Add to cart</button>
      </article>
    {/each}
  </div>
</main>
''', {"CATALOG": safe.js_str(catalog), "LOCALE": "id-ID" if currency == "IDR" else "en-US", "CURRENCY": currency})
        cart_page = render('''<script lang="ts">
  import { cart } from "$lib/cart.svelte";
  const format = (v: number) => new Intl.NumberFormat("@@LOCALE@@", { style: "currency", currency: "@@CURRENCY@@" }).format(v);
</script>

<main>
  <h1>Cart</h1>
  {#if cart.lines.length === 0}
    <p>Your cart is empty.</p>
  {:else}
    <table>
      <thead><tr><th>Product</th><th>Qty</th><th>Subtotal</th><th></th></tr></thead>
      <tbody>
        {#each cart.lines as line (String(line.product.id))}
          <tr>
            <td>{line.product.title}</td>
            <td><input type="number" min="1" value={line.quantity} aria-label={"Quantity of " + line.product.title}
                       onchange={(e) => cart.setQuantity(line.product.id, Number(e.currentTarget.value) || 0)} /></td>
            <td>{format(line.product.price * line.quantity)}</td>
            <td><button type="button" onclick={() => cart.remove(line.product.id)}>Remove</button></td>
          </tr>
        {/each}
      </tbody>
    </table>
    <p>Total: {format(cart.total)}</p>
    <a href="/checkout" class="cta">Proceed to checkout</a>
  {/if}
</main>
''', {"LOCALE": "id-ID" if currency == "IDR" else "en-US", "CURRENCY": currency})
        checkout_page = render('''<script lang="ts">
  import { cart } from "$lib/cart.svelte";

  let name = $state("");
  let email = $state("");
  let address = $state("");
  let status: "idle" | "submitting" | "error" = $state("idle");
  let error: string | null = $state(null);

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    status = "submitting";
    error = null;
    try {
      const res = await fetch(@@CHECKOUT@@, {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({
          customer: { name, email, address },
          items: cart.lines.map((l) => ({ productId: l.product.id, quantity: l.quantity })),
        }),
      });
      if (!res.ok) {
        const body = await res.json().catch(() => null);
        throw new Error(body?.error ?? "Checkout failed (" + res.status + ")");
      }
      cart.clear();
      window.location.href = "/checkout?status=placed";
    } catch (err) {
      status = "error";
      error = err instanceof Error ? err.message : "Checkout failed";
    }
  }
</script>

<main>
  <h1>Checkout</h1>
  <form onsubmit={submit}>
    <label for="name">Full name</label><input id="name" bind:value={name} required autocomplete="name" />
    <label for="email">Email</label><input id="email" type="email" bind:value={email} required autocomplete="email" />
    <label for="address">Shipping address</label><textarea id="address" rows="3" bind:value={address} required autocomplete="street-address"></textarea>
    <button type="submit" disabled={status === "submitting"}>
      {status === "submitting" ? "Placing order…" : "Place order"}
    </button>
    {#if error}<p class="error" role="alert">{error}</p>{/if}
  </form>
</main>
''', {"CHECKOUT": safe.js_str(checkout)})
        writes = [
            (rel_path(project, "src", "lib", "cart.svelte.ts"), store_ts),
            (rel_path(project, "src", "routes", "products", "+page.svelte"), products_page),
            (rel_path(project, "src", "routes", "cart", "+page.svelte"), cart_page),
            (rel_path(project, "src", "routes", "checkout", "+page.svelte"), checkout_page),
        ]
        wiring = {
            "store": "cart from $lib/cart.svelte.ts (runes-based singleton).",
            "routes": ["/products", "/cart", "/checkout"],
        }
        paths_note = "src/lib/cart.svelte.ts + routes products|cart|checkout"

    else:
        script = render('''const CATALOG = @@CATALOG@@;
const CHECKOUT = @@CHECKOUT@@;
const STORAGE_KEY = "@@PROJECT@@.cart";
const format = (v) => new Intl.NumberFormat("@@LOCALE@@", { style: "currency", currency: "@@CURRENCY@@" }).format(v);

let cart = [];
let products = [];

function persist() { localStorage.setItem(STORAGE_KEY, JSON.stringify(cart)); renderCart(); }
function restore() {
  try { cart = JSON.parse(localStorage.getItem(STORAGE_KEY) || "[]"); } catch { cart = []; }
}
function addToCart(id) {
  const product = products.find((p) => String(p.id) === String(id));
  if (!product) return;
  const line = cart.find((l) => String(l.product.id) === String(id));
  if (line) line.quantity += 1; else cart.push({ product, quantity: 1 });
  persist();
}
function setQty(id, qty) {
  cart = cart.map((l) => (String(l.product.id) === String(id) ? { ...l, quantity: qty } : l)).filter((l) => l.quantity > 0);
  persist();
}
function removeLine(id) {
  cart = cart.filter((l) => String(l.product.id) !== String(id));
  persist();
}

function renderProducts() {
  const grid = document.getElementById("products");
  grid.innerHTML = "";
  for (const product of products) {
    const card = document.createElement("article");
    card.className = "product-card";
    const title = document.createElement("h3");
    title.textContent = product.title;
    const price = document.createElement("p");
    price.className = "price";
    price.textContent = format(product.price);
    const button = document.createElement("button");
    button.type = "button";
    button.textContent = "Add to cart";
    button.addEventListener("click", () => addToCart(product.id));
    card.append(title, price, button);
    grid.append(card);
  }
}

function renderCart() {
  const tbody = document.querySelector("#cart-table tbody");
  tbody.innerHTML = "";
  let total = 0;
  for (const line of cart) {
    total += line.product.price * line.quantity;
    const tr = document.createElement("tr");
    const tdName = document.createElement("td");
    tdName.textContent = line.product.title;
    const tdQty = document.createElement("td");
    const input = document.createElement("input");
    input.type = "number";
    input.min = "1";
    input.value = String(line.quantity);
    input.setAttribute("aria-label", "Quantity of " + line.product.title);
    input.addEventListener("change", (e) => setQty(line.product.id, Number(e.target.value) || 0));
    tdQty.append(input);
    const tdSub = document.createElement("td");
    tdSub.textContent = format(line.product.price * line.quantity);
    const tdRemove = document.createElement("td");
    const removeButton = document.createElement("button");
    removeButton.type = "button";
    removeButton.textContent = "Remove";
    removeButton.addEventListener("click", () => removeLine(line.product.id));
    tdRemove.append(removeButton);
    tr.append(tdName, tdQty, tdSub, tdRemove);
    tbody.append(tr);
  }
  document.getElementById("cart-total").textContent = format(total);
  document.getElementById("cart-section").hidden = cart.length === 0;
  document.getElementById("cart-empty").hidden = cart.length !== 0;
}

async function load() {
  try {
    const res = await fetch(CATALOG);
    if (!res.ok) throw new Error("status " + res.status);
    const data = await res.json();
    products = Array.isArray(data) ? data : data.items || [];
    document.getElementById("error").hidden = true;
  } catch {
    document.getElementById("error").hidden = false;
    return;
  }
  restore();
  renderProducts();
  renderCart();
}

document.getElementById("checkout-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  if (cart.length === 0) { alert("Your cart is empty."); return; }
  const data = new FormData(event.target);
  const button = event.target.querySelector("button[type=submit]");
  button.disabled = true;
  try {
    const res = await fetch(CHECKOUT, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        customer: {
          name: String(data.get("name") || ""),
          email: String(data.get("email") || ""),
          address: String(data.get("address") || ""),
        },
        items: cart.map((l) => ({ productId: l.product.id, quantity: l.quantity })),
      }),
    });
    if (!res.ok) {
      const body = await res.json().catch(() => null);
      throw new Error((body && body.error) || "Checkout failed (" + res.status + ")");
    }
    cart = [];
    persist();
    document.getElementById("placed").hidden = false;
  } catch (err) {
    alert(err.message);
  } finally {
    button.disabled = false;
  }
});

load();
''', {
            "CATALOG": safe.js_str(catalog),
            "CHECKOUT": safe.js_str(checkout),
            "PROJECT": project,
            "LOCALE": "id-ID" if currency == "IDR" else "en-US",
            "CURRENCY": currency,
        })
        body = '''<div class="wrap" style="max-width:56rem">
  <h1>Store demo</h1>
  <p id="error" class="error" role="alert" hidden>Failed to load the catalog</p>
  <section aria-label="Products"><div id="products" class="cards"></div></section>
  <section id="cart-section" aria-label="Cart" hidden>
    <h2>Cart</h2>
    <table id="cart-table">
      <thead><tr><th>Product</th><th>Qty</th><th>Subtotal</th><th></th></tr></thead>
      <tbody></tbody>
    </table>
    <p>Total: <strong id="cart-total"></strong></p>
  </section>
  <p id="cart-empty">Your cart is empty.</p>
  <section aria-label="Checkout">
    <h2>Checkout</h2>
    <p id="placed" class="ok" role="status" hidden>Order placed!</p>
    <form id="checkout-form">
      <label for="name">Full name</label><input id="name" name="name" required autocomplete="name" />
      <label for="email">Email</label><input id="email" name="email" type="email" required autocomplete="email" />
      <label for="address">Shipping address</label><textarea id="address" name="address" rows="3" required autocomplete="street-address"></textarea>
      <button type="submit" class="primary">Place order</button>
    </form>
  </section>
</div>'''
        content = _html_shell("Store demo", body, script=script, extra_css='''
    .product-card { border: 1px solid #e5e7eb; border-radius: .5rem; padding: 1rem; }''')
        writes = [(rel_path(project, "site", "store.html"), content)]
        wiring = {"route": "site/store.html", "checkout_payload": '{"customer":{...},"items":[{"productId","quantity"}]}'}
        paths_note = "site/store.html (single-page demo store)"

    data = {
        "framework": framework,
        "paths_note": paths_note,
        "currency": currency,
        "catalog_endpoint": catalog,
        "checkout_endpoint": checkout,
        "wiring": wiring,
        "expected_payload": '{"items":[{"id":1,"title":"...","price":199000,"image":"https://..."}]}',
        "note": note,
    }
    return {"data": data, "writes": writes, "limitations": limitations}


_register("fe.gen.ecommerce", 67, "Generate catalog/cart/checkout commerce UI (client cart, server checkout)", _obj({
    "project": _SLUG_S,
    "framework": {"type": "string", "enum": FE_FRAMEWORKS, "default": "react"},
    "currency": {"type": "string", "pattern": r"^[A-Z]{3}$", "default": "IDR"},
    "catalog_endpoint": {"type": "string", "pattern": r"^/", "minLength": 1, "maxLength": 200, "default": "/api/products"},
    "checkout_endpoint": {"type": "string", "pattern": r"^/", "minLength": 1, "maxLength": 200, "default": "/api/checkout"},
}, ["project"]), _ecommerce)


# ---------------------------------------------------------------------------
# 68 fe.gen.blog
# ---------------------------------------------------------------------------

def _blog(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    framework = args["framework"]
    endpoint = _endpoint(args["posts_endpoint"])

    if framework == "react":
        list_page = render('''"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

type Post = { slug: string; title: string; excerpt?: string; publishedAt?: string };

export default function BlogListPage() {
  const [posts, setPosts] = useState<Post[] | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const res = await fetch(@@ENDPOINT@@);
        if (!res.ok) throw new Error("status " + res.status);
        const data = await res.json();
        setPosts(Array.isArray(data) ? data : (data.items ?? []));
      } catch {
        setError("Failed to load posts");
      }
    })();
  }, []);

  return (
    <main>
      <h1>Blog</h1>
      {error ? <p className="error" role="alert">{error}</p> : null}
      <ul className="post-list">
        {(posts ?? []).map((post) => (
          <li key={post.slug}>
            <Link href={"/blog/" + encodeURIComponent(post.slug)}>
              <h2>{post.title}</h2>
            </Link>
            {post.excerpt ? <p>{post.excerpt}</p> : null}
          </li>
        ))}
      </ul>
    </main>
  );
}
''', {"ENDPOINT": safe.js_str(endpoint)})
        detail_page = render('''"use client";

import { useParams } from "next/navigation";
import { useEffect, useState } from "react";

type Post = { slug: string; title: string; body: string; publishedAt?: string };

export default function BlogPostPage() {
  const params = useParams<{ slug: string }>();
  const [post, setPost] = useState<Post | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    void (async () => {
      try {
        const res = await fetch(@@ENDPOINT@@ + "/" + encodeURIComponent(String(params.slug)));
        if (res.status === 404) {
          setError("Post not found");
          return;
        }
        if (!res.ok) throw new Error("status " + res.status);
        setPost((await res.json()) as Post);
      } catch {
        setError("Failed to load the post");
      }
    })();
  }, [params.slug]);

  if (error) return <main><p className="error" role="alert">{error}</p></main>;
  if (!post) return <main><p role="status">Loading…</p></main>;

  return (
    <main>
      <article>
        <h1>{post.title}</h1>
        {post.publishedAt ? <p><time dateTime={post.publishedAt}>{post.publishedAt}</time></p> : null}
        {post.body.split("\\n\\n").map((paragraph, index) => (
          <p key={index}>{paragraph}</p>
        ))}
      </article>
    </main>
  );
}
''', {"ENDPOINT": safe.js_str(endpoint)})
        writes = [
            (rel_path(project, "app", "blog", "page.tsx"), list_page),
            (rel_path(project, "app", "blog", "[slug]", "page.tsx"), detail_page),
        ]
        wiring = "Routes: /blog (list) and /blog/[slug] (detail)."
    elif framework == "vue":
        list_page = render('''<script setup lang="ts">
import { onMounted, ref } from "vue";

type Post = { slug: string; title: string; excerpt?: string };

const posts = ref<Post[]>([]);
const error = ref<string | null>(null);

onMounted(async () => {
  try {
    const data = await $fetch<Post[] | { items?: Post[] }>(@@ENDPOINT@@);
    posts.value = Array.isArray(data) ? data : (data.items ?? []);
  } catch {
    error.value = "Failed to load posts";
  }
});
</script>

<template>
  <main>
    <h1>Blog</h1>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <ul class="post-list">
      <li v-for="post in posts" :key="post.slug">
        <NuxtLink :to="'/blog/' + encodeURIComponent(post.slug)"><h2>{{ post.title }}</h2></NuxtLink>
        <p v-if="post.excerpt">{{ post.excerpt }}</p>
      </li>
    </ul>
  </main>
</template>
''', {"ENDPOINT": safe.js_str(endpoint)})
        detail_page = render('''<script setup lang="ts">
import { onMounted, ref } from "vue";

type Post = { slug: string; title: string; body: string; publishedAt?: string };

const route = useRoute();
const post = ref<Post | null>(null);
const error = ref<string | null>(null);

onMounted(async () => {
  try {
    post.value = await $fetch<Post>(@@ENDPOINT@@ + "/" + encodeURIComponent(String(route.params.slug)));
  } catch (err: any) {
    error.value = err?.status === 404 ? "Post not found" : "Failed to load the post";
  }
});
</script>

<template>
  <main>
    <p v-if="error" class="error" role="alert">{{ error }}</p>
    <article v-else-if="post">
      <h1>{{ post.title }}</h1>
      <p v-if="post.publishedAt"><time :datetime="post.publishedAt">{{ post.publishedAt }}</time></p>
      <p v-for="(paragraph, index) in post.body.split('\\n\\n')" :key="index">{{ paragraph }}</p>
    </article>
    <p v-else role="status">Loading…</p>
  </main>
</template>
''', {"ENDPOINT": safe.js_str(endpoint)})
        writes = [
            (rel_path(project, "pages", "blog", "index.vue"), list_page),
            (rel_path(project, "pages", "blog", "[slug].vue"), detail_page),
        ]
        wiring = "Routes: /blog (list) and /blog/:slug (detail)."
    elif framework == "svelte":
        list_page = render('''<script lang="ts">
  type Post = { slug: string; title: string; excerpt?: string };
  let posts: Post[] | null = $state(null);
  let error: string | null = $state(null);

  $effect(() => {
    void (async () => {
      try {
        const res = await fetch(@@ENDPOINT@@);
        if (!res.ok) throw new Error("status " + res.status);
        const data = await res.json();
        posts = Array.isArray(data) ? data : (data.items ?? []);
      } catch {
        error = "Failed to load posts";
      }
    })();
  });
</script>

<main>
  <h1>Blog</h1>
  {#if error}<p class="error" role="alert">{error}</p>{/if}
  <ul class="post-list">
    {#each posts ?? [] as post (post.slug)}
      <li>
        <a href={"/blog/" + encodeURIComponent(post.slug)}><h2>{post.title}</h2></a>
        {#if post.excerpt}<p>{post.excerpt}</p>{/if}
      </li>
    {/each}
  </ul>
</main>
''', {"ENDPOINT": safe.js_str(endpoint)})
        detail_page = render('''<script lang="ts">
  import { page } from "$app/state";

  type Post = { slug: string; title: string; body: string; publishedAt?: string };
  let post: Post | null = $state(null);
  let error: string | null = $state(null);

  $effect(() => {
    void (async () => {
      try {
        const res = await fetch(@@ENDPOINT@@ + "/" + encodeURIComponent(String(page.params.slug)));
        if (res.status === 404) { error = "Post not found"; return; }
        if (!res.ok) throw new Error("status " + res.status);
        post = await res.json();
      } catch {
        error = "Failed to load the post";
      }
    })();
  });
</script>

<main>
  {#if error}<p class="error" role="alert">{error}</p>{:else if !post}<p role="status">Loading…</p>{:else}
    <article>
      <h1>{post.title}</h1>
      {#if post.publishedAt}<p><time datetime={post.publishedAt}>{post.publishedAt}</time></p>{/if}
      {#each post.body.split("\\n\\n") as paragraph, index (index)}<p>{paragraph}</p>{/each}
    </article>
  {/if}
</main>
''', {"ENDPOINT": safe.js_str(endpoint)})
        writes = [
            (rel_path(project, "src", "routes", "blog", "+page.svelte"), list_page),
            (rel_path(project, "src", "routes", "blog", "[slug]", "+page.svelte"), detail_page),
        ]
        wiring = "Routes: /blog (list) and /blog/[slug] (detail)."
    else:
        script = render('''const ENDPOINT = @@ENDPOINT@@;

function renderList(posts) {
  const list = document.getElementById("posts");
  list.innerHTML = "";
  for (const post of posts) {
    const li = document.createElement("li");
    const link = document.createElement("a");
    link.href = "#";
    link.textContent = post.title;
    link.addEventListener("click", (event) => {
      event.preventDefault();
      showPost(post);
    });
    li.append(link);
    if (post.excerpt) {
      const p = document.createElement("p");
      p.textContent = post.excerpt;
      li.append(p);
    }
    list.append(li);
  }
}

function showPost(post) {
  document.getElementById("list-view").hidden = true;
  const view = document.getElementById("post-view");
  view.hidden = false;
  document.getElementById("post-title").textContent = post.title;
  const body = document.getElementById("post-body");
  body.innerHTML = "";
  for (const paragraph of String(post.body || "").split("\\n\\n")) {
    const p = document.createElement("p");
    p.textContent = paragraph;
    body.append(p);
  }
}

async function load() {
  try {
    const res = await fetch(ENDPOINT);
    if (!res.ok) throw new Error("status " + res.status);
    const data = await res.json();
    const posts = Array.isArray(data) ? data : data.items || [];
    renderList(posts);
    const slug = new URLSearchParams(window.location.search).get("slug");
    if (slug) {
      const match = posts.find((p) => p.slug === slug);
      if (match) showPost(match);
    }
    document.getElementById("error").hidden = true;
  } catch {
    document.getElementById("error").hidden = false;
  }
}

document.getElementById("back").addEventListener("click", () => {
  document.getElementById("post-view").hidden = true;
  document.getElementById("list-view").hidden = false;
});
load();
''', {"ENDPOINT": safe.js_str(endpoint)})
        body = '''<div class="wrap">
  <p id="error" class="error" role="alert" hidden>Failed to load posts</p>
  <div id="list-view">
    <h1>Blog</h1>
    <ul id="posts" class="post-list"></ul>
  </div>
  <div id="post-view" hidden>
    <p><button type="button" id="back">← All posts</button></p>
    <article>
      <h1 id="post-title"></h1>
      <div id="post-body"></div>
    </article>
  </div>
</div>'''
        content = _html_shell("Blog", body, script=script)
        writes = [(rel_path(project, "site", "blog.html"), content)]
        wiring = "Static page: site/blog.html (list + inline detail view)."

    data = {
        "framework": framework,
        "paths": [w[0] for w in writes],
        "posts_endpoint": endpoint,
        "expected_payload": '[{"slug":"hello-world","title":"Hello","excerpt":"...","body":"..."}] '
                            'or {"items":[...]}; detail: GET endpoint + "/" + slug',
        "wiring": wiring,
    }
    return {"data": data, "writes": writes, "limitations": [LIMIT_NO_BUILD]}


_register("fe.gen.blog", 68, "Generate blog list + detail pages consuming a posts API", _obj({
    "project": _SLUG_S,
    "framework": _FRAMEWORK_S,
    "posts_endpoint": {"type": "string", "pattern": r"^/", "minLength": 1, "maxLength": 200, "default": "/api/posts"},
}, ["project"]), _blog)


# ---------------------------------------------------------------------------
# 69 fe.style.tailwind
# ---------------------------------------------------------------------------

def _tailwind(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    framework = args["framework"]
    brand = args["brand_color"]
    if not re.match(r"^#[0-9a-fA-F]{6}$", brand or ""):
        raise SkillInputError("brand_color: expected a hex color like #2563EB")
    brand = brand.lower()

    def shade(mix: str) -> str:
        """Blend the brand color toward white (mix in hex)."""
        r = int(brand[1:3], 16)
        g = int(brand[3:5], 16)
        b = int(brand[5:7], 16)
        f = int(mix, 16) / 255
        return "#%02x%02x%02x" % (round(r * f + 255 * (1 - f)), round(g * f + 255 * (1 - f)), round(b * f + 255 * (1 - f)))

    palette = {level: shade(level_hex) for level, level_hex in
               [("50", "f2"), ("100", "e0"), ("200", "c0"), ("300", "9a"), ("400", "66"),
                ("500", "00"), ("600", "e8"), ("700", "c8"), ("800", "a0"), ("900", "78")]}
    # 600/700/800/900 darken toward black instead of lighten.
    def darken(mix: str) -> str:
        r = int(brand[1:3], 16)
        g = int(brand[3:5], 16)
        b = int(brand[5:7], 16)
        f = int(mix, 16) / 255
        return "#%02x%02x%02x" % (round(r * f), round(g * f), round(b * f))
    palette["600"] = darken("e8")
    palette["700"] = darken("c8")
    palette["800"] = darken("a0")
    palette["900"] = darken("78")

    content_css = render('''@tailwind base;
@tailwind components;
@tailwind utilities;

:root {
  --color-brand: @@BRAND@@;
  --color-brand-dark: @@BRAND_DARK@@;
}
''', {"BRAND": brand, "BRAND_DARK": palette["700"]})

    config = render('''import type { Config } from "tailwindcss";

const config: Config = {
  content: @@CONTENT@@,
  theme: {
    extend: {
      colors: {
        brand: {
          50: "@@C50@@",
          100: "@@C100@@",
          200: "@@C200@@",
          300: "@@C300@@",
          400: "@@C400@@",
          500: "@@C500@@",
          600: "@@C600@@",
          700: "@@C700@@",
          800: "@@C800@@",
          900: "@@C900@@",
        },
      },
    },
  },
  plugins: [],
};

export default config;
''', {
        "CONTENT": safe.js_str(json.dumps({
            "react": ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
            "vue": ["./components/**/*.{vue,ts}", "./pages/**/*.vue", "./app.vue"],
            "svelte": ["./src/**/*.{svelte,ts}", "./static/**/*.html"],
            "html": ["./site/**/*.html"],
        }[framework])),
        **{"C" + k.upper(): v for k, v in palette.items()},
    })

    postcss = '''module.exports = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};
'''

    css_path = {
        "react": rel_path(project, "app", "tailwind.css"),
        "vue": rel_path(project, "assets", "css", "tailwind.css"),
        "svelte": rel_path(project, "src", "app-tailwind.css"),
        "html": rel_path(project, "site", "tailwind.css"),
    }[framework]

    wiring = {
        "react": 'npm install -D tailwindcss@^3.4.17 postcss@^8.5.1 autoprefixer@^10.4.20; '
                 'import "./app/tailwind.css" inside app/layout.tsx.',
        "vue": 'npm install -D tailwindcss@^3.4.17 postcss@^8.5.1 autoprefixer@^10.4.20; '
               'add css: ["@@CSS@@"] (or import in app.vue).'.replace("@@CSS@@", css_path),
        "svelte": 'npm install -D tailwindcss@^3.4.17 postcss@^8.5.1 autoprefixer@^10.4.20; '
                  'import "./app-tailwind.css" in src/routes/+layout.svelte.',
        "html": 'Build CSS with: npx tailwindcss -i site/tailwind.css -o site/tailwind.out.css '
                'and link it from your pages; or use the Tailwind CDN play script for prototyping.',
    }[framework]

    writes = [
        (rel_path(project, "tailwind.config.ts"), config),
        (rel_path(project, "postcss.config.js"), postcss),
        (css_path, content_css),
    ]
    data = {
        "framework": framework,
        "brand_color": brand,
        "paths": [w[0] for w in writes],
        "dependencies": {"tailwindcss": "^3.4.17", "postcss": "^8.5.1", "autoprefixer": "^10.4.20"},
        "wiring": wiring,
    }
    return {
        "data": data,
        "writes": writes,
        "limitations": [
            "Config files are generated but Tailwind is NOT installed and CSS is NOT built; "
            "run the npm install and build steps in data.wiring.",
            "If the project already has tailwind.config/postcss.config, merge instead of overwrite.",
        ],
    }


_register("fe.style.tailwind", 69, "Generate Tailwind CSS v3 setup with a brand color palette", _obj({
    "project": _SLUG_S,
    "framework": _FRAMEWORK_S,
    "brand_color": {"type": "string", "pattern": r"^#[0-9a-fA-F]{6}$", "default": "#2563eb"},
}, ["project"]), _tailwind)


# ---------------------------------------------------------------------------
# 70 fe.style.shadcn
# ---------------------------------------------------------------------------

def _shadcn(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    typescript = bool(args["typescript"])
    ext = "tsx" if typescript else "jsx"

    components_json = {
        "$schema": "https://ui.shadcn.com/schema.json",
        "style": "new-york",
        "rsc": typescript,
        "tsx": typescript,
        "tailwind": {
            "config": "tailwind.config.ts",
            "css": "app/globals.css" if typescript else "styles/globals.css",
            "baseColor": "neutral",
            "cssVariables": True,
            "prefix": "",
        },
        "aliases": {
            "components": "@/components",
            "utils": "@/lib/utils",
            "ui": "@/components/ui",
            "lib": "@/lib",
            "hooks": "@/hooks",
        },
        "iconLibrary": "lucide",
    }

    utils_ts = '''import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

/** Merge conditional class names with Tailwind conflict resolution. */
export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}
'''

    button_tsx = '''import * as React from "react";
import { Slot } from "@radix-ui/react-slot";
import { cva, type VariantProps } from "class-variance-authority";

import { cn } from "@/lib/utils";

const buttonVariants = cva(
  "inline-flex items-center justify-center gap-2 whitespace-nowrap rounded-md text-sm font-medium transition-colors focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring disabled:pointer-events-none disabled:opacity-50",
  {
    variants: {
      variant: {
        default: "bg-primary text-primary-foreground shadow hover:bg-primary/90",
        destructive: "bg-destructive text-destructive-foreground shadow-sm hover:bg-destructive/90",
        outline: "border border-input bg-background shadow-sm hover:bg-accent hover:text-accent-foreground",
        secondary: "bg-secondary text-secondary-foreground shadow-sm hover:bg-secondary/80",
        ghost: "hover:bg-accent hover:text-accent-foreground",
        link: "text-primary underline-offset-4 hover:underline",
      },
      size: {
        default: "h-9 px-4 py-2",
        sm: "h-8 rounded-md px-3 text-xs",
        lg: "h-10 rounded-md px-8",
        icon: "h-9 w-9",
      },
    },
    defaultVariants: {
      variant: "default",
      size: "default",
    },
  },
);

export interface ButtonProps
  extends React.ButtonHTMLAttributes<HTMLButtonElement>,
    VariantProps<typeof buttonVariants> {
  asChild?: boolean;
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
  ({ className, variant, size, asChild = false, ...props }, ref) => {
    const Comp = asChild ? Slot : "button";
    return <Comp className={cn(buttonVariants({ variant, size, className }))} ref={ref} {...props} />;
  },
);
Button.displayName = "Button";

export { Button, buttonVariants };
'''

    globals_css = '''@tailwind base;
@tailwind components;
@tailwind utilities;

@layer base {
  :root {
    --background: 0 0% 100%;
    --foreground: 0 0% 3.9%;
    --primary: 0 0% 9%;
    --primary-foreground: 0 0% 98%;
    --secondary: 0 0% 96.1%;
    --secondary-foreground: 0 0% 9%;
    --destructive: 0 84.2% 60.2%;
    --destructive-foreground: 0 0% 98%;
    --accent: 0 0% 96.1%;
    --accent-foreground: 0 0% 9%;
    --ring: 0 0% 63.9%;
    --radius: 0.5rem;
  }

  .dark {
    --background: 0 0% 3.9%;
    --foreground: 0 0% 98%;
    --primary: 0 0% 98%;
    --primary-foreground: 0 0% 9%;
    --secondary: 0 0% 14.9%;
    --secondary-foreground: 0 0% 98%;
    --destructive: 0 62.8% 30.6%;
    --destructive-foreground: 0 0% 98%;
    --accent: 0 0% 14.9%;
    --accent-foreground: 0 0% 98%;
    --ring: 0 0% 83.1%;
  }
}

@layer base {
  * {
    @apply border-border;
  }
  body {
    @apply bg-background text-foreground;
  }
}
'''

    writes = [
        (rel_path(project, "components.json"), json.dumps(components_json, indent=2) + "\n"),
        (rel_path(project, "lib", "utils.ts"), utils_ts),
        (rel_path(project, "components", "ui", "button.%s" % ext), button_tsx),
        (rel_path(project, "styles", "globals.css"), globals_css),
    ]
    data = {
        "project": project,
        "typescript": typescript,
        "paths": [w[0] for w in writes],
        "dependencies": {
            "clsx": "^2.1.1",
            "tailwind-merge": "^2.6.0",
            "class-variance-authority": "^0.7.1",
            "@radix-ui/react-slot": "^1.1.1",
        },
        "instructions": [
            "1. Configure Tailwind first (fe.style.tailwind) — shadcn/ui requires it.",
            "2. npm install clsx tailwind-merge class-variance-authority @radix-ui/react-slot",
            "3. Merge styles/globals.css into your app's global stylesheet (Tailwind v3 syntax).",
            "4. Add `@tailwindcss/forms`? Not required. Ensure `tailwindcss-animate` if you add more components: npm i -D tailwindcss-animate and register it in tailwind.config.ts plugins.",
            "5. Scaffold more components with: npx shadcn@latest add <component> (uses components.json).",
        ],
    }
    return {
        "data": data,
        "writes": writes,
        "limitations": [
            "shadcn/ui is copy-in components, not an npm package: files are generated, but dependencies "
            "are NOT installed and `npx shadcn` has NOT been run.",
            "button.tsx uses the new-york style with CSS variables and expects Tailwind v3 + the "
            "globals.css variables to be wired into the app build.",
        ],
    }


_register("fe.style.shadcn", 70, "Generate shadcn/ui-ready config, cn() util, Button component, and CSS variables", _obj({
    "project": _SLUG_S,
    "typescript": {"type": "boolean", "default": True},
}, ["project"]), _shadcn)


# ---------------------------------------------------------------------------
# 71 fe.responsive.optimize
# ---------------------------------------------------------------------------

def _responsive(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    framework = args["framework"]

    responsive_css = '''/* Responsive foundation generated by fe.responsive.optimize.
   Mobile-first; import from your global stylesheet. */

/* Fluid typography: 1rem at 360px wide up to 1.25rem at 1200px. */
:root {
  font-size: clamp(1rem, 0.96rem + 0.35vw, 1.25rem);
}

/* Prevent unintended horizontal scroll. */
html, body {
  overflow-x: clip;
}

/* Comfortable tap targets and spacing on touch devices. */
button, a, input, select, textarea {
  touch-action: manipulation;
}

@media (pointer: coarse) {
  button, a[role="button"] {
    min-height: 44px;
    min-width: 44px;
  }
}

/* Breakpoints (Tailwind-compatible):
   sm 640px | md 768px | lg 1024px | xl 1280px | 2xl 1536px */

/* Tables become scrollable containers on small screens. */
.table-scroll {
  overflow-x: auto;
  -webkit-overflow-scrolling: touch;
}

/* Respect reduced-motion preferences. */
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
    transition-duration: 0.01ms !important;
    scroll-behavior: auto !important;
  }
}

/* Images never overflow their container. */
img, video {
  max-width: 100%;
  height: auto;
}
'''

    writes: List[Pair] = [(rel_path(project, "styles", "responsive.css"), responsive_css)]

    if framework == "react":
        grid_tsx = '''"use client";

import { CSSProperties, ReactNode } from "react";

type ResponsiveGridProps = {
  children: ReactNode;
  /** Minimum column width in px; the browser fits as many as possible. */
  minColumnWidth?: number;
  gap?: string;
  className?: string;
  style?: CSSProperties;
};

/** Auto-responsive grid: no media queries needed (CSS Grid auto-fit). */
export function ResponsiveGrid({
  children,
  minColumnWidth = 240,
  gap = "1rem",
  className,
  style,
}: ResponsiveGridProps) {
  return (
    <div
      className={className}
      style={{
        display: "grid",
        gridTemplateColumns: `repeat(auto-fit, minmax(min(${minColumnWidth}px, 100%), 1fr))`,
        gap,
        ...style,
      }}
    >
      {children}
    </div>
  );
}
'''
        writes.append((rel_path(project, "components", "ResponsiveGrid.tsx"), grid_tsx))
        usage = 'import "./styles/responsive.css"; in app/layout.tsx; use <ResponsiveGrid> for card layouts.'
    elif framework == "vue":
        usage = "Import styles/responsive.css in app.vue or nuxt.config css array: css: ['~/styles/responsive.css']."
    elif framework == "svelte":
        usage = 'Import "$lib/styles/responsive.css"? Place it in src/routes/+layout.svelte via import "../styles/responsive.css".'
    else:
        usage = 'Add <link rel="stylesheet" href="responsive.css"> (same folder) to every site/*.html page.'

    checklist = [
        "viewport meta present: <meta name='viewport' content='width=device-width, initial-scale=1' />",
        "no horizontal scrolling at 320px, 390px, 768px, 1024px, 1440px widths",
        "tap targets >= 44x44 px on touch devices (enforced in CSS for pointer:coarse)",
        "base font >= 16px on mobile (fluid clamp in CSS)",
        "images use max-width:100% and width/height attributes to avoid layout shift",
        "navigation is usable with one hand: primary actions within thumb reach on mobile",
        "test with prefers-reduced-motion enabled (respected in CSS)",
        "form inputs >= 16px font-size so iOS does not zoom on focus",
    ]

    data = {
        "framework": framework,
        "paths": [w[0] for w in writes],
        "breakpoints": {"sm": 640, "md": 768, "lg": 1024, "xl": 1280, "2xl": 1536},
        "wiring": usage,
        "checklist": checklist,
    }
    return {
        "data": data,
        "writes": writes,
        "limitations": [
            "CSS and component are generated but NOT tested in a browser; verify with real viewports "
            "(e.g. browser QA at 390x844 and 1440x900).",
        ],
    }


_register("fe.responsive.optimize", 71, "Generate a responsive foundation (fluid type, tap targets, grid) + audit checklist", _obj({
    "project": _SLUG_S,
    "framework": _FRAMEWORK_S,
}, ["project"]), _responsive)
