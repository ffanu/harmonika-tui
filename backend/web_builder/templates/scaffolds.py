"""Framework scaffolds: real runnable project skeletons (skills 47-56).

Every scaffold is emitted as static source files. The generator does not
install dependencies, run builds, or contact package registries; each
README states the exact commands the operator must run. Dependency
versions are pinned to known-compatible ranges from the respective
official projects (Next.js 16, Nuxt 3, SvelteKit 2, Remix 2, NestJS 10,
FastAPI 0.162, Django 5.2 LTS, Laravel 11, Turborepo 2).
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Tuple

from . import safe
from .safe import rel_path, render

Schema = Dict[str, Any]
Pair = Tuple[str, str]

LIMIT_RUN = (
    "Static files only: dependencies are not installed and no install, build, "
    "migration, or test run has been executed by this generator. Run the "
    "commands in the generated README inside your workspace."
)

_SLUG_S = {"type": "string", "pattern": safe.SLUG_RE.pattern, "minLength": 2, "maxLength": 50}
_ROUTE_S = {"type": "string", "pattern": safe.ROUTE_RE.pattern, "minLength": 1, "maxLength": 64}
_PORT_S = {"type": "integer", "minimum": 1024, "maximum": 65535, "default": 3000}


def npm(**deps: str) -> Dict[str, str]:
    return dict(deps)


def dump_json(obj: Any) -> str:
    return json.dumps(obj, indent=2, ensure_ascii=False) + "\n"


def _pkg(name: str, scripts: Dict[str, str], deps: Dict[str, str], dev: Dict[str, str],
         extra: Dict[str, Any] = None) -> Dict[str, Any]:
    pkg: Dict[str, Any] = {
        "name": name,
        "version": "0.1.0",
        "private": True,
        "scripts": scripts,
    }
    if extra:
        pkg.update(extra)
    if deps:
        pkg["dependencies"] = deps
    if dev:
        pkg["devDependencies"] = dev
    return pkg


# ---------------------------------------------------------------------------
# Shared TS store used by several Node scaffolds
# ---------------------------------------------------------------------------

STORE_TS = '''export interface @@MODEL@@ {
  id: string;
  title: string;
  done: boolean;
  createdAt: string;
}

export function createMemoryStore() {
  const items = new Map<string, @@MODEL@@>();
  return {
    list(): @@MODEL@@[] {
      return [...items.values()].sort((a, b) => a.createdAt.localeCompare(b.createdAt));
    },
    get(id: string): @@MODEL@@ | undefined {
      return items.get(id);
    },
    create(title: string): @@MODEL@@ {
      const item: @@MODEL@@ = {
        id: crypto.randomUUID(),
        title,
        done: false,
        createdAt: new Date().toISOString(),
      };
      items.set(item.id, item);
      return item;
    },
    update(id: string, patch: Partial<Pick<@@MODEL@@, "title" | "done">>): @@MODEL@@ | undefined {
      const current = items.get(id);
      if (!current) return undefined;
      const next: @@MODEL@@ = { ...current, ...patch };
      items.set(id, next);
      return next;
    },
    remove(id: string): boolean {
      return items.delete(id);
    },
  };
}

/**
 * In-process store kept for the sample API. Swap for a real database
 * adapter before relying on persistence across restarts.
 */
export const store = createMemoryStore();
'''

STORE_TEST_TS = '''import { describe, expect, it } from "vitest";
import { createMemoryStore } from "../lib/store";

describe("memory store", () => {
  it("creates and lists items in insertion order", () => {
    const store = createMemoryStore();
    store.create("first");
    store.create("second");
    expect(store.list().map((i) => i.title)).toEqual(["first", "second"]);
    expect(store.list()[0].done).toBe(false);
  });

  it("updates only existing items", () => {
    const store = createMemoryStore();
    const created = store.create("draft");
    expect(store.update(created.id, { done: true, title: "final" }))
      .toMatchObject({ title: "final", done: true });
    expect(store.update("missing", { done: true })).toBeUndefined();
  });

  it("removes items", () => {
    const store = createMemoryStore();
    const created = store.create("temp");
    expect(store.remove(created.id)).toBe(true);
    expect(store.get(created.id)).toBeUndefined();
  });
});
'''

FORMAT_TS = '''/** Format an ISO timestamp as a short human-readable UTC label. */
export function formatDate(iso: string): string {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) {
    throw new Error("invalid ISO timestamp");
  }
  return date.toISOString().slice(0, 16).replace("T", " ") + " UTC";
}
'''

FORMAT_TEST_TS = '''import { describe, expect, it } from "vitest";
import { formatDate } from "../utils/format";

describe("formatDate", () => {
  it("formats ISO timestamps", () => {
    expect(formatDate("2026-01-02T03:04:05.000Z")).toBe("2026-01-02 03:04 UTC");
  });

  it("rejects invalid input", () => {
    expect(() => formatDate("not-a-date")).toThrow("invalid ISO timestamp");
  });
});
'''

VITEST_CONFIG_TS = '''import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
  },
});
'''

GITIGNORE_NODE = '''node_modules/
dist/
build/
coverage/
.env
.env.*
!.env.example
*.tsbuildinfo
.next/
.nuxt/
.svelte-kit/
'''


# ---------------------------------------------------------------------------
# 47 scaffold.nextjs
# ---------------------------------------------------------------------------

_NEXT_TSRESOURCE_ROUTE = '''import { NextResponse } from "next/server";
import { store } from "@/lib/store";

export const dynamic = "force-dynamic";

export async function GET() {
  return NextResponse.json({ items: store.list() });
}

export async function POST(request: Request) {
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }
  const title = (body as { title?: unknown } | null)?.title;
  if (typeof title !== "string" || title.trim().length === 0 || title.length > 200) {
    return NextResponse.json(
      { error: "title must be a string of 1..200 characters" },
      { status: 400 },
    );
  }
  return NextResponse.json({ item: store.create(title.trim()) }, { status: 201 });
}
'''

_NEXT_TSRESOURCE_DETAIL = '''import { NextResponse } from "next/server";
import { store } from "@/lib/store";

export const dynamic = "force-dynamic";

type Context = { params: Promise<{ id: string }> };

export async function GET(_request: Request, context: Context) {
  const { id } = await context.params;
  const item = store.get(id);
  if (!item) {
    return NextResponse.json({ error: "not found" }, { status: 404 });
  }
  return NextResponse.json({ item });
}

export async function PATCH(request: Request, context: Context) {
  const { id } = await context.params;
  let body: unknown;
  try {
    body = await request.json();
  } catch {
    return NextResponse.json({ error: "invalid JSON body" }, { status: 400 });
  }
  const patch = body as { title?: unknown; done?: unknown };
  if (patch.title !== undefined && (typeof patch.title !== "string" || patch.title.trim().length === 0)) {
    return NextResponse.json({ error: "title must be a non-empty string" }, { status: 400 });
  }
  if (patch.done !== undefined && typeof patch.done !== "boolean") {
    return NextResponse.json({ error: "done must be a boolean" }, { status: 400 });
  }
  const clean: { title?: string; done?: boolean } = {};
  if (typeof patch.title === "string") clean.title = patch.title.trim();
  if (typeof patch.done === "boolean") clean.done = patch.done;
  const item = store.update(id, clean);
  if (!item) {
    return NextResponse.json({ error: "not found" }, { status: 404 });
  }
  return NextResponse.json({ item });
}

export async function DELETE(_request: Request, context: Context) {
  const { id } = await context.params;
  if (!store.remove(id)) {
    return NextResponse.json({ error: "not found" }, { status: 404 });
  }
  return new NextResponse(null, { status: 204 });
}
'''


def _nextjs(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    resource = args["resource"]
    port = args["port"]
    tailwind = bool(args["tailwind"])
    model = safe.pascal(resource, "resource")

    pkg = _pkg(
        project,
        {
            "dev": "next dev -p %d" % port,
            "build": "next build",
            "start": "next start -p %d" % port,
            "lint": "eslint .",
            "test": "vitest run",
        },
        npm(**{"next": "16.4.3", "react": "19.3.1", "react-dom": "19.3.1"}),
        npm(**{
            "@eslint/eslintrc": "^3.2.0",
            "@types/node": "^22.10.10",
            "@types/react": "^19.0.8",
            "@types/react-dom": "^19.0.3",
            "eslint": "^9.19.0",
            "eslint-config-next": "16.4.3",
            "typescript": "^5.7.3",
            "vitest": "4.1.12",
        }),
    )
    if tailwind:
        pkg["devDependencies"].update(npm(**{
            "autoprefixer": "^10.4.20",
            "postcss": "^8.5.1",
            "tailwindcss": "^3.4.17",
        }))

    globals_css = (
        '@tailwind base;\n@tailwind components;\n@tailwind utilities;\n'
        if tailwind
        else ''':root {
  color-scheme: light dark;
  font-family: system-ui, -apple-system, "Segoe UI", sans-serif;
  line-height: 1.6;
}

* {
  box-sizing: border-box;
}

body {
  margin: 0;
  padding: 2rem;
  max-width: 48rem;
}

button {
  cursor: pointer;
}
'''
    )

    pairs: List[Pair] = [
        (rel_path(project, "package.json"), dump_json(pkg)),
        (rel_path(project, "tsconfig.json"), dump_json({
            "compilerOptions": {
                "target": "ES2017",
                "lib": ["dom", "dom.iterable", "esnext"],
                "allowJs": True,
                "skipLibCheck": True,
                "strict": True,
                "noEmit": True,
                "esModuleInterop": True,
                "module": "esnext",
                "moduleResolution": "bundler",
                "resolveJsonModule": True,
                "isolatedModules": True,
                "jsx": "preserve",
                "incremental": True,
                "plugins": [{"name": "next"}],
                "paths": {"@/*": ["./*"]},
            },
            "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
            "exclude": ["node_modules"],
        })),
        (rel_path(project, "next.config.ts"), '''import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  reactStrictMode: true,
};

export default nextConfig;
'''),
        (rel_path(project, ".gitignore"), GITIGNORE_NODE),
        (rel_path(project, ".env.example"), "# Copy to .env.local. Add credentials here only in private config.\n"),
        (rel_path(project, "eslint.config.mjs"), '''import nextVitals from "eslint-config-next/core-web-vitals";
import nextTs from "eslint-config-next/typescript";
const config = [...nextVitals,...nextTs,{ignores:["node_modules/**",".next/**"]}];
export default config;
'''),
        (rel_path(project, "app", "layout.tsx"), render('''import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "@@PROJECT@@",
  description: "Generated Next.js scaffold for @@PROJECT@@.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>{children}</body>
    </html>
  );
}
''', {"PROJECT": project})),
        (rel_path(project, "app", "page.tsx"), render('''"use client";

import { FormEvent, useCallback, useEffect, useState } from "react";

type Item = {
  id: string;
  title: string;
  done: boolean;
  createdAt: string;
};

export default function Home() {
  const [items, setItems] = useState<Item[]>([]);
  const [title, setTitle] = useState("");
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    const res = await fetch("/api/@@RESOURCE@@");
    if (!res.ok) {
      setError("failed to load items");
      return;
    }
    const data = (await res.json()) as { items: Item[] };
    setItems(data.items);
  }, []);

  useEffect(() => {
    const timer = setTimeout(() => { void refresh(); }, 0);
    return () => clearTimeout(timer);
  }, [refresh]);

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    const res = await fetch("/api/@@RESOURCE@@", {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ title }),
    });
    if (!res.ok) {
      const data = (await res.json().catch(() => null)) as { error?: string } | null;
      setError(data?.error ?? "request failed");
      return;
    }
    setTitle("");
    await refresh();
  }

  return (
    <main>
      <h1>@@PROJECT@@</h1>
      <form onSubmit={submit}>
        <input
          value={title}
          onChange={(e) => setTitle(e.target.value)}
          placeholder="Add @@RESOURCE@@ item"
          aria-label="Title"
          maxLength={200}
          required
        />
        <button type="submit">Add</button>
      </form>
      {error ? <p role="alert">{error}</p> : null}
      <ul>
        {items.map((item) => (
          <li key={item.id}>{item.title}</li>
        ))}
      </ul>
    </main>
  );
}
''', {"PROJECT": project, "RESOURCE": resource})),
        (rel_path(project, "app", "globals.css"), globals_css),
        (rel_path(project, "app", "api", "health", "route.ts"), render('''import { NextResponse } from "next/server";

export const dynamic = "force-dynamic";

export function GET() {
  return NextResponse.json({
    status: "ok",
    service: "@@PROJECT@@",
    time: new Date().toISOString(),
  });
}
''', {"PROJECT": project})),
        (rel_path(project, "app", "api", resource, "route.ts"), render(_NEXT_TSRESOURCE_ROUTE, {"MODEL": model, "RESOURCE": resource})),
        (rel_path(project, "app", "api", resource, "[id]", "route.ts"), render(_NEXT_TSRESOURCE_DETAIL, {"MODEL": model, "RESOURCE": resource})),
        (rel_path(project, "lib", "store.ts"), render(STORE_TS, {"MODEL": model})),
        (rel_path(project, "lib", "format.ts"), FORMAT_TS),
        (rel_path(project, "tests", "store.test.ts"), render(STORE_TEST_TS, {"MODEL": model})),
        (rel_path(project, "tests", "format.test.ts"), '''import { describe, expect, it } from "vitest";
import { formatDate } from "../lib/format";

describe("formatDate", () => {
  it("formats ISO timestamps", () => {
    expect(formatDate("2026-01-02T03:04:05.000Z")).toBe("2026-01-02 03:04 UTC");
  });

  it("rejects invalid input", () => {
    expect(() => formatDate("not-a-date")).toThrow("invalid ISO timestamp");
  });
});
'''),
        (rel_path(project, "vitest.config.ts"), VITEST_CONFIG_TS),
        (rel_path(project, "README.md"), render('''# @@PROJECT@@ (Next.js)

Generated scaffold — static source files. Nothing has been installed,
built, or executed yet.

## Requirements

- Node.js >= 18.18 (Node 20+ recommended), npm >= 9

## Run

```sh
npm install
npm run dev        # http://localhost:@@PORT@@
```

## Build & production

```sh
npm run build
npm run start
```

## Tests

```sh
npm run test       # vitest unit tests
npm run lint       # eslint
```

## What is inside

- App Router (`app/`) with TypeScript strict mode.
- `GET /api/health` liveness endpoint.
- Sample REST API `/api/@@RESOURCE@@` (list/create, get/update/delete)
  backed by the in-process store in `lib/store.ts`. Replace the store
  with a real database adapter before production use.
- Client page `app/page.tsx` consuming the sample API.
@@TW@@## Environment

Copy `.env.example` to `.env.local`. No secrets are required for the
sample; never commit real secrets.
''', {"PROJECT": project, "PORT": str(port), "RESOURCE": resource,
      "TW": "Tailwind CSS v3 is preconfigured (`postcss.config.mjs`, `tailwind.config.ts`).\n" if tailwind else ""})),
    ]
    if tailwind:
        pairs.extend([
            (rel_path(project, "postcss.config.mjs"), '''const config = {
  plugins: {
    tailwindcss: {},
    autoprefixer: {},
  },
};

export default config;
'''),
            (rel_path(project, "tailwind.config.ts"), '''import type { Config } from "tailwindcss";

const config: Config = {
  content: ["./app/**/*.{ts,tsx}", "./components/**/*.{ts,tsx}"],
  theme: {
    extend: {},
  },
  plugins: [],
};

export default config;
'''),
        ])

    data = {
        "framework": "nextjs",
        "language": "typescript",
        "package_manager": "npm",
        "entrypoints": ["app/page.tsx", "app/api/health/route.ts"],
        "sample_api": {
            "list": "GET /api/%s" % resource,
            "create": "POST /api/%s" % resource,
            "detail": "GET|PATCH|DELETE /api/%s/{id}" % resource,
        },
        "commands": {"install": "npm install", "dev": "npm run dev",
                     "build": "npm run build", "test": "npm run test"},
        "dependencies": pkg.get("dependencies", {}),
        "dev_dependencies": pkg.get("devDependencies", {}),
    }
    return {"data": data, "writes": pairs, "limitations": [LIMIT_RUN]}


# ---------------------------------------------------------------------------
# 48 scaffold.nuxt
# ---------------------------------------------------------------------------

def _nuxt(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    resource = args["resource"]
    port = args["port"]

    pkg = _pkg(
        project,
        {
            "dev": "nuxt dev --port %d" % port,
            "build": "nuxt build",
            "generate": "nuxt generate",
            "preview": "nuxt preview",
            "postinstall": "nuxt prepare",
            "test": "vitest run",
        },
        npm(**{"nuxt": "^3.15.4", "vue": "^3.5.13", "vue-router": "^4.5.0"}),
        npm(**{"typescript": "^5.7.3", "vitest": "4.1.12"}),
    )

    pairs: List[Pair] = [
        (rel_path(project, "package.json"), dump_json(pkg)),
        (rel_path(project, "nuxt.config.ts"), render('''export default defineNuxtConfig({
  compatibilityDate: "2025-01-01",
  devtools: { enabled: false },
  runtimeConfig: {
    public: {
      appName: "@@PROJECT@@",
    },
  },
});
''', {"PROJECT": project})),
        (rel_path(project, "tsconfig.json"), '''{
  "extends": "./.nuxt/tsconfig.json"
}
'''),
        (rel_path(project, ".gitignore"), GITIGNORE_NODE),
        (rel_path(project, ".env.example"), "# Copy to .env. NUXT_PUBLIC_APP_NAME is picked up automatically.\n"),
        (rel_path(project, "app.vue"), '''<template>
  <NuxtPage />
</template>
'''),
        (rel_path(project, "types", "item.ts"), '''export interface Item {
  id: string;
  title: string;
  done: boolean;
  createdAt: string;
}
'''),
        (rel_path(project, "pages", "index.vue"), render('''<script setup lang="ts">
import type { Item } from "~/types/item";

const items = ref<Item[]>([]);
const title = ref("");
const error = ref<string | null>(null);

async function refresh() {
  try {
    const data = await $fetch<{ items: Item[] }>("/api/@@RESOURCE@@");
    items.value = data.items;
    error.value = null;
  } catch {
    error.value = "failed to load items";
  }
}

async function submit() {
  try {
    await $fetch("/api/@@RESOURCE@@", {
      method: "POST",
      body: { title: title.value },
    });
    title.value = "";
    await refresh();
  } catch (err: any) {
    error.value = err?.data?.statusMessage ?? "request failed";
  }
}

onMounted(refresh);
</script>

<template>
  <main>
    <h1>@@PROJECT@@</h1>
    <form @submit.prevent="submit">
      <input v-model="title" type="text" maxlength="200" required aria-label="Title" />
      <button type="submit">Add</button>
    </form>
    <p v-if="error" role="alert">{{ error }}</p>
    <ul>
      <li v-for="item in items" :key="item.id">{{ item.title }}</li>
    </ul>
  </main>
</template>
''', {"PROJECT": project, "RESOURCE": resource})),
        (rel_path(project, "server", "api", "health.get.ts"), '''export default defineEventHandler(() => {
  const config = useRuntimeConfig();
  return {
    status: "ok",
    service: config.public.appName,
    time: new Date().toISOString(),
  };
});
'''),
        (rel_path(project, "server", "utils", "store.ts"), render('''export interface @@MODEL@@ {
  id: string;
  title: string;
  done: boolean;
  createdAt: string;
}

const items = new Map<string, @@MODEL@@>();

export function listItems(): @@MODEL@@[] {
  return [...items.values()].sort((a, b) => a.createdAt.localeCompare(b.createdAt));
}

export function getItem(id: string): @@MODEL@@ | undefined {
  return items.get(id);
}

export function createItem(title: string): @@MODEL@@ {
  const item: @@MODEL@@ = {
    id: crypto.randomUUID(),
    title,
    done: false,
    createdAt: new Date().toISOString(),
  };
  items.set(item.id, item);
  return item;
}

export function updateItem(
  id: string,
  patch: Partial<Pick<@@MODEL@@, "title" | "done">>,
): @@MODEL@@ | undefined {
  const current = items.get(id);
  if (!current) return undefined;
  const next: @@MODEL@@ = { ...current, ...patch };
  items.set(id, next);
  return next;
}

export function removeItem(id: string): boolean {
  return items.delete(id);
}
''', {"MODEL": "Item"})),
        (rel_path(project, "server", "api", resource, "index.get.ts"), render('''import { listItems } from "../../utils/store";

export default defineEventHandler(() => {
  return { items: listItems() };
});
''', {"MODEL": "Item", "RESOURCE": resource})),
        (rel_path(project, "server", "api", resource, "index.post.ts"), render('''import { createItem } from "../../utils/store";

export default defineEventHandler(async (event) => {
  const body = await readBody<{ title?: unknown }>(event);
  const title = body?.title;
  if (typeof title !== "string" || title.trim().length === 0 || title.length > 200) {
    throw createError({
      statusCode: 400,
      statusMessage: "title must be a string of 1..200 characters",
    });
  }
  setResponseStatus(event, 201);
  return { item: createItem(title.trim()) };
});
''', {"MODEL": "Item", "RESOURCE": resource})),
        (rel_path(project, "utils", "format.ts"), FORMAT_TS),
        (rel_path(project, "tests", "format.test.ts"), '''import { describe, expect, it } from "vitest";
import { formatDate } from "../utils/format";

describe("formatDate", () => {
  it("formats ISO timestamps", () => {
    expect(formatDate("2026-01-02T03:04:05.000Z")).toBe("2026-01-02 03:04 UTC");
  });

  it("rejects invalid input", () => {
    expect(() => formatDate("nope")).toThrow("invalid ISO timestamp");
  });
});
'''),
        (rel_path(project, "vitest.config.ts"), VITEST_CONFIG_TS),
        (rel_path(project, "README.md"), render('''# @@PROJECT@@ (Nuxt 3)

Generated scaffold — static source files. Nothing has been installed,
built, or executed yet.

## Requirements

- Node.js >= 18.12 (Node 20+ recommended), npm >= 9

## Run

```sh
npm install
npm run dev        # http://localhost:@@PORT@@
```

## Build & production

```sh
npm run build
npm run preview    # serves the production build (Node server)
```

## Tests

```sh
npm run test       # vitest unit tests
```

## What is inside

- File-based routing (`pages/`) with the sample page consuming
  `/api/@@RESOURCE@@`.
- Nitro server API: `GET /api/health`, `GET|POST /api/@@RESOURCE@@`
  backed by `server/utils/store.ts` (in-process memory store; swap for
  a real database before production).
- Auto-imports: `ref`, `onMounted`, `$fetch`, `defineEventHandler`,
  `readBody`, `createError` are provided by Nuxt.
''', {"PROJECT": project, "PORT": str(port), "RESOURCE": resource})),
    ]

    data = {
        "framework": "nuxt",
        "language": "typescript",
        "package_manager": "npm",
        "entrypoints": ["app.vue", "pages/index.vue", "server/api/health.get.ts"],
        "sample_api": {
            "list": "GET /api/%s" % resource,
            "create": "POST /api/%s" % resource,
            "health": "GET /api/health",
        },
        "commands": {"install": "npm install", "dev": "npm run dev",
                     "build": "npm run build", "test": "npm run test"},
        "dependencies": pkg.get("dependencies", {}),
        "dev_dependencies": pkg.get("devDependencies", {}),
    }
    return {"data": data, "writes": pairs, "limitations": [LIMIT_RUN]}


# ---------------------------------------------------------------------------
# 49 scaffold.sveltekit
# ---------------------------------------------------------------------------

def _sveltekit(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    resource = args["resource"]
    port = args["port"]

    pkg = _pkg(
        project,
        {
            "dev": "vite dev --port %d" % port,
            "build": "vite build",
            "preview": "vite preview",
            "prepare": "svelte-kit sync",
            "check": "svelte-kit sync && svelte-check --tsconfig ./tsconfig.json",
            "test": "vitest run",
        },
        {},
        npm(**{
            "@sveltejs/adapter-node": "^5.2.11",
            "@sveltejs/kit": "^2.17.1",
            "@sveltejs/vite-plugin-svelte": "^5.0.3",
            "svelte": "^5.19.3",
            "svelte-check": "^4.1.4",
            "typescript": "^5.7.3",
            "vite": "^6.0.11",
            "vitest": "4.1.12",
        }),
    )

    pairs: List[Pair] = [
        (rel_path(project, "package.json"), dump_json(pkg)),
        (rel_path(project, "svelte.config.js"), '''import adapter from "@sveltejs/adapter-node";
import { vitePreprocess } from "@sveltejs/vite-plugin-svelte";

/** @type {import('@sveltejs/kit').Config} */
const config = {
  preprocess: vitePreprocess(),
  kit: {
    adapter: adapter(),
  },
};

export default config;
'''),
        (rel_path(project, "vite.config.ts"), '''import { sveltekit } from "@sveltejs/kit/vite";
import { defineConfig } from "vite";

export default defineConfig({
  plugins: [sveltekit()],
});
'''),
        (rel_path(project, "tsconfig.json"), '''{
  "extends": "./.svelte-kit/tsconfig.json",
  "compilerOptions": {
    "allowJs": true,
    "checkJs": true,
    "esModuleInterop": true,
    "forceConsistentCasingInFileNames": true,
    "resolveJsonModule": true,
    "skipLibCheck": true,
    "sourceMap": true,
    "strict": true,
    "moduleResolution": "bundler"
  }
}
'''),
        (rel_path(project, ".gitignore"), GITIGNORE_NODE + ".environment/\n"),
        (rel_path(project, ".env.example"), "# Copy to .env. No secrets required for the sample.\n"),
        (rel_path(project, "src", "app.html"), '''<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>@@PROJECT@@</title>
    %sveltekit.head%
  </head>
  <body data-sveltekit-preload-data="hover">
    <div style="display: contents">%sveltekit.body%</div>
  </body>
</html>
'''.replace("@@PROJECT@@", project)),
        (rel_path(project, "src", "app.d.ts"), '''declare global {
  namespace App {
    // interface Error {}
    // interface Locals {}
    // interface PageData {}
    // interface PageState {}
    // interface Platform {}
  }
}

export {};
'''),
        (rel_path(project, "src", "routes", "+layout.svelte"), '''<script lang="ts">
  let { children } = $props();
</script>

{@render children()}
'''),
        (rel_path(project, "src", "routes", "+page.svelte"), render('''<script lang="ts">
  import { onMount } from "svelte";

  type Item = {
    id: string;
    title: string;
    done: boolean;
    createdAt: string;
  };

  let items: Item[] = $state([]);
  let title = $state("");
  let error = $state<string | null>(null);

  async function refresh() {
    try {
      const res = await fetch("/api/@@RESOURCE@@");
      if (!res.ok) throw new Error("bad status");
      items = (await res.json()).items;
      error = null;
    } catch {
      error = "failed to load items";
    }
  }

  async function submit(event: SubmitEvent) {
    event.preventDefault();
    try {
      const res = await fetch("/api/@@RESOURCE@@", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ title }),
      });
      if (!res.ok) throw new Error("bad status");
      title = "";
      await refresh();
    } catch {
      error = "request failed";
    }
  }

  onMount(refresh);
</script>

<main>
  <h1>@@PROJECT@@</h1>
  <form onsubmit={submit}>
    <input type="text" bind:value={title} maxlength="200" required aria-label="Title" />
    <button type="submit">Add</button>
  </form>
  {#if error}
    <p role="alert">{error}</p>
  {/if}
  <ul>
    {#each items as item (item.id)}
      <li>{item.title}</li>
    {/each}
  </ul>
</main>
''', {"PROJECT": project, "RESOURCE": resource})),
        (rel_path(project, "src", "routes", "api", "health", "+server.ts"), render('''import { json } from "@sveltejs/kit";
import type { RequestHandler } from "./$types";

export const GET: RequestHandler = async () => {
  return json({
    status: "ok",
    service: "@@PROJECT@@",
    time: new Date().toISOString(),
  });
};
''', {"PROJECT": project})),
        (rel_path(project, "src", "lib", "server", "store.ts"), render('''import type { Item } from "$lib/types";

const items = new Map<string, Item>();

export function listItems(): Item[] {
  return [...items.values()].sort((a, b) => a.createdAt.localeCompare(b.createdAt));
}

export function getItem(id: string): Item | undefined {
  return items.get(id);
}

export function createItem(title: string): Item {
  const item: Item = {
    id: crypto.randomUUID(),
    title,
    done: false,
    createdAt: new Date().toISOString(),
  };
  items.set(item.id, item);
  return item;
}

export function updateItem(
  id: string,
  patch: Partial<Pick<Item, "title" | "done">>,
): Item | undefined {
  const current = items.get(id);
  if (!current) return undefined;
  const next: Item = { ...current, ...patch };
  items.set(id, next);
  return next;
}

export function removeItem(id: string): boolean {
  return items.delete(id);
}
''', {"MODEL": "Item"})),
        (rel_path(project, "src", "lib", "types", "index.ts"), '''export interface Item {
  id: string;
  title: string;
  done: boolean;
  createdAt: string;
}
'''),
        (rel_path(project, "src", "routes", "api", resource, "+server.ts"), render('''import { error, json } from "@sveltejs/kit";
import type { RequestHandler } from "./$types";
import { createItem, listItems } from "$lib/server/store";

export const GET: RequestHandler = async () => {
  return json({ items: listItems() });
};

export const POST: RequestHandler = async ({ request }) => {
  let body: { title?: unknown };
  try {
    body = await request.json();
  } catch {
    throw error(400, "invalid JSON body");
  }
  const title = body?.title;
  if (typeof title !== "string" || title.trim().length === 0 || title.length > 200) {
    throw error(400, "title must be a string of 1..200 characters");
  }
  return json({ item: createItem(title.trim()) }, { status: 201 });
};
''', {"MODEL": "Item", "RESOURCE": resource})),
        (rel_path(project, "src", "lib", "utils", "format.ts"), FORMAT_TS),
        (rel_path(project, "tests", "format.test.ts"), '''import { describe, expect, it } from "vitest";
import { formatDate } from "$lib/utils/format";

describe("formatDate", () => {
  it("formats ISO timestamps", () => {
    expect(formatDate("2026-01-02T03:04:05.000Z")).toBe("2026-01-02 03:04 UTC");
  });

  it("rejects invalid input", () => {
    expect(() => formatDate("nope")).toThrow("invalid ISO timestamp");
  });
});
'''),
        (rel_path(project, "vitest.config.ts"), '''import { defineConfig } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    include: ["tests/**/*.test.ts"],
  },
  resolve: {
    alias: {
      "$lib": new URL("./src/lib", import.meta.url).pathname,
    },
  },
});
'''),
        (rel_path(project, "README.md"), render('''# @@PROJECT@@ (SvelteKit)

Generated scaffold — static source files. Nothing has been installed,
built, or executed yet.

## Requirements

- Node.js >= 18.13 (Node 20+ recommended), npm >= 9

## Run

```sh
npm install
npm run dev        # http://localhost:@@PORT@@
```

## Build & production (Node adapter)

```sh
npm run build
node build         # adapter-node output; PORT env controls the port
```

## Tests

```sh
npm run test       # vitest unit tests
npm run check      # svelte-check type checking
```

## What is inside

- Svelte 5 runes (`$state`, `$props`) and `@sveltejs/adapter-node`.
- `GET /api/health` and `GET|POST /api/@@RESOURCE@@` (server-only store
  under `src/lib/server` — swap for a real database before production).
- `+page.svelte` consuming the sample API in the browser.
''', {"PROJECT": project, "PORT": str(port), "RESOURCE": resource})),
    ]

    data = {
        "framework": "sveltekit",
        "language": "typescript",
        "package_manager": "npm",
        "entrypoints": ["src/routes/+page.svelte", "src/routes/api/health/+server.ts"],
        "sample_api": {
            "list": "GET /api/%s" % resource,
            "create": "POST /api/%s" % resource,
            "health": "GET /api/health",
        },
        "commands": {"install": "npm install", "dev": "npm run dev",
                     "build": "npm run build", "test": "npm run test"},
        "dependencies": pkg.get("dependencies", {}),
        "dev_dependencies": pkg.get("devDependencies", {}),
    }
    return {"data": data, "writes": pairs, "limitations": [LIMIT_RUN]}


# ---------------------------------------------------------------------------
# 50 scaffold.remix
# ---------------------------------------------------------------------------

def _remix(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    resource = args["resource"]
    port = args["port"]

    pkg = _pkg(
        project,
        {
            "dev": "remix vite:dev --port %d" % port,
            "build": "remix vite:build",
            "start": "remix-serve ./build/server/index.js",
            "typecheck": "tsc",
            "test": "vitest run",
        },
        npm(**{
            "@remix-run/node": "^2.15.2",
            "@remix-run/react": "^2.15.2",
            "@remix-run/serve": "^2.15.2",
            "isbot": "^4.4.0",
            "react": "^18.3.1",
            "react-dom": "^18.3.1",
        }),
        npm(**{
            "@remix-run/dev": "^2.15.2",
            "@types/react": "^18.3.18",
            "@types/react-dom": "^18.3.5",
            "typescript": "^5.7.3",
            "vite": "^5.4.14",
            "vite-tsconfig-paths": "^4.3.2",
            "vitest": "4.1.12",
        }),
    )

    pairs: List[Pair] = [
        (rel_path(project, "package.json"), dump_json(pkg)),
        (rel_path(project, "vite.config.ts"), '''import { vitePlugin as remix } from "@remix-run/dev";
import { defineConfig } from "vite";
import tsconfigPaths from "vite-tsconfig-paths";

export default defineConfig({
  plugins: [
    remix({
      future: {
        v3_fetcherPersist: true,
        v3_relativeSplatPath: true,
        v3_throwAbortReason: true,
      },
    }),
    tsconfigPaths(),
  ],
});
'''),
        (rel_path(project, "tsconfig.json"), '''{
  "include": ["**/*.ts", "**/*.tsx"],
  "compilerOptions": {
    "lib": ["DOM", "DOM.Iterable", "ES2022"],
    "types": ["@remix-run/node", "vite/client"],
    "isolatedModules": true,
    "esModuleInterop": true,
    "jsx": "react-jsx",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "resolveJsonModule": true,
    "target": "ES2022",
    "strict": true,
    "allowJs": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "baseUrl": ".",
    "paths": {
      "~/*": ["./app/*"]
    },
    "noEmit": true
  }
}
'''),
        (rel_path(project, ".gitignore"), GITIGNORE_NODE),
        (rel_path(project, ".env.example"), "# Copy to .env. SESSION_SECRET is required before production.\n"),
        (rel_path(project, "app", "root.tsx"), render('''import { Links, Meta, Outlet, Scripts, ScrollRestoration } from "@remix-run/react";
import type { LinksFunction } from "@remix-run/node";

export const links: LinksFunction = [];

export function Layout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <head>
        <meta charSet="utf-8" />
        <meta name="viewport" content="width=device-width, initial-scale=1" />
        <Meta />
        <Links />
      </head>
      <body>
        {children}
        <ScrollRestoration />
        <Scripts />
      </body>
    </html>
  );
}

export default function App() {
  return <Outlet />;
}
''', {"PROJECT": project})),
        (rel_path(project, "app", "routes", "_index.tsx"), render('''import { useFetcher } from "@remix-run/react";
import { json } from "@remix-run/node";
import type { ActionFunctionArgs, LoaderFunctionArgs } from "@remix-run/node";
import { useEffect } from "react";
import { store } from "~/store.server";

export async function loader(_: LoaderFunctionArgs) {
  return json({ items: store.list() });
}

export async function action({ request }: ActionFunctionArgs) {
  const form = await request.formData();
  const title = form.get("title");
  if (typeof title !== "string" || title.trim().length === 0 || title.length > 200) {
    return json({ error: "title must be a string of 1..200 characters" }, { status: 400 });
  }
  return json({ item: store.create(title.trim()) }, { status: 201 });
}

export default function Index() {
  const fetcher = useFetcher<typeof loader>();

  useEffect(() => {
    if (fetcher.state === "idle" && !fetcher.data) {
      void fetcher.load("/?index");
    }
  }, [fetcher]);

  const items = fetcher.data?.items ?? [];

  return (
    <main>
      <h1>@@PROJECT@@</h1>
      <fetcher.Form method="post">
        <input type="text" name="title" maxLength={200} required aria-label="Title" />
        <button type="submit" disabled={fetcher.state !== "idle"}>
          Add
        </button>
      </fetcher.Form>
      <ul>
        {items.map((item) => (
          <li key={item.id}>{item.title}</li>
        ))}
      </ul>
    </main>
  );
}
''', {"PROJECT": project, "RESOURCE": resource})),
        (rel_path(project, "app", "routes", "api.health.tsx"), render('''import { json } from "@remix-run/node";
import type { LoaderFunctionArgs } from "@remix-run/node";

export async function loader(_: LoaderFunctionArgs) {
  return json({
    status: "ok",
    service: "@@PROJECT@@",
    time: new Date().toISOString(),
  });
}
''', {"PROJECT": project})),
        (rel_path(project, "app", "routes", "api.%s.tsx" % resource), render('''import { json } from "@remix-run/node";
import type { ActionFunctionArgs, LoaderFunctionArgs } from "@remix-run/node";
import { createItem, listItems } from "~/store.server";

export async function loader(_: LoaderFunctionArgs) {
  return json({ items: listItems() });
}

export async function action({ request }: ActionFunctionArgs) {
  const body = (await request.json().catch(() => null)) as { title?: unknown } | null;
  const title = body?.title;
  if (typeof title !== "string" || title.trim().length === 0 || title.length > 200) {
    return json({ error: "title must be a string of 1..200 characters" }, { status: 400 });
  }
  return json({ item: createItem(title.trim()) }, { status: 201 });
}
''', {"MODEL": "Item", "RESOURCE": resource})),
        (rel_path(project, "app", "store.server.ts"), render('''import type { Item } from "~/types";

const items = new Map<string, Item>();

/**
 * Server-only in-process store (.server.ts guarantees it never ships to
 * the browser bundle). Swap for a real database before production.
 */
export const store = {
  list(): Item[] {
    return [...items.values()].sort((a, b) => a.createdAt.localeCompare(b.createdAt));
  },
  get(id: string): Item | undefined {
    return items.get(id);
  },
  create(title: string): Item {
    const item: Item = {
      id: crypto.randomUUID(),
      title,
      done: false,
      createdAt: new Date().toISOString(),
    };
    items.set(item.id, item);
    return item;
  },
  update(id: string, patch: Partial<Pick<Item, "title" | "done">>): Item | undefined {
    const current = items.get(id);
    if (!current) return undefined;
    const next: Item = { ...current, ...patch };
    items.set(id, next);
    return next;
  },
  remove(id: string): boolean {
    return items.delete(id);
  },
};

export const listItems = store.list.bind(store);
export const createItem = store.create.bind(store);
''', {"MODEL": "Item"})),
        (rel_path(project, "app", "types.ts"), '''export interface Item {
  id: string;
  title: string;
  done: boolean;
  createdAt: string;
}
'''),
        (rel_path(project, "app", "utils", "format.ts"), FORMAT_TS),
        (rel_path(project, "tests", "format.test.ts"), '''import { describe, expect, it } from "vitest";
import { formatDate } from "../app/utils/format";

describe("formatDate", () => {
  it("formats ISO timestamps", () => {
    expect(formatDate("2026-01-02T03:04:05.000Z")).toBe("2026-01-02 03:04 UTC");
  });
});
'''),
        (rel_path(project, "vitest.config.ts"), VITEST_CONFIG_TS),
        (rel_path(project, "README.md"), render('''# @@PROJECT@@ (Remix)

Generated scaffold — static source files. Nothing has been installed,
built, or executed yet.

## Requirements

- Node.js >= 20, npm >= 9

## Run

```sh
npm install
npm run dev        # http://localhost:@@PORT@@
```

## Build & production

```sh
npm run build
npm run start      # remix-serve on ./build/server/index.js
```

## Tests

```sh
npm run test       # vitest unit tests
npm run typecheck
```

## What is inside

- Remix v2 with the Vite plugin and React 18.
- `GET /api/health` and `GET|POST /api/@@RESOURCE@@` loaders/actions.
- Server-only store in `app/store.server.ts` (in-process; swap for a
  real database before production).
- Set `SESSION_SECRET` before enabling sessions in production.
''', {"PROJECT": project, "PORT": str(port), "RESOURCE": resource})),
    ]

    data = {
        "framework": "remix",
        "language": "typescript",
        "package_manager": "npm",
        "entrypoints": ["app/root.tsx", "app/routes/_index.tsx", "app/routes/api.health.tsx"],
        "sample_api": {
            "list": "GET /api/%s" % resource,
            "create": "POST /api/%s" % resource,
            "health": "GET /api/health",
        },
        "commands": {"install": "npm install", "dev": "npm run dev",
                     "build": "npm run build", "test": "npm run test"},
        "dependencies": pkg.get("dependencies", {}),
        "dev_dependencies": pkg.get("devDependencies", {}),
    }
    return {"data": data, "writes": pairs, "limitations": [LIMIT_RUN]}


# ---------------------------------------------------------------------------
# 51 scaffold.express
# ---------------------------------------------------------------------------

_EXPRESS_RESOURCE = '''import { store } from "../lib/store.js";

const TITLE_MAX = 200;

function readTitle(body) {
  const title = body?.title;
  if (typeof title !== "string" || title.trim().length === 0 || title.length > TITLE_MAX) {
    return null;
  }
  return title.trim();
}

export function createResourceRouter(logger = console) {
  const router = Router();

  router.get("/", (_req, res) => {
    res.json({ items: store.list() });
  });

  router.post("/", (req, res) => {
    const title = readTitle(req.body);
    if (title === null) {
      return res.status(400).json({ error: "title must be a string of 1..200 characters" });
    }
    res.status(201).json({ item: store.create(title) });
  });

  router.get("/:id", (req, res) => {
    const item = store.get(req.params.id);
    if (!item) {
      return res.status(404).json({ error: "not found" });
    }
    res.json({ item });
  });

  router.patch("/:id", (req, res) => {
    const patch = {};
    if (req.body?.title !== undefined) {
      const title = readTitle(req.body);
      if (title === null) {
        return res.status(400).json({ error: "title must be a non-empty string" });
      }
      patch.title = title;
    }
    if (req.body?.done !== undefined) {
      if (typeof req.body.done !== "boolean") {
        return res.status(400).json({ error: "done must be a boolean" });
      }
      patch.done = req.body.done;
    }
    const item = store.update(req.params.id, patch);
    if (!item) {
      return res.status(404).json({ error: "not found" });
    }
    res.json({ item });
  });

  router.delete("/:id", (req, res) => {
    if (!store.remove(req.params.id)) {
      return res.status(404).json({ error: "not found" });
    }
    res.status(204).end();
  });

  return router;
}
'''

_EXPRESS_RESOURCE_TEST = '''import assert from "node:assert/strict";
import { after, before, describe, it } from "node:test";
import { createApp } from "../src/app.js";
import { startServer } from "./helpers/server.js";

describe("@@RESOURCE@@ API", () => {
  const server = startServer(createApp());
  const base = `http://127.0.0.1:${server.port}`;

  before(async () => server.start());
  after(() => server.stop());

  it("health endpoint reports ok", async () => {
    const res = await fetch(`${base}/api/health`);
    assert.equal(res.status, 200);
    const body = await res.json();
    assert.equal(body.status, "ok");
  });

  it("rejects invalid create payloads", async () => {
    const res = await fetch(`${base}/api/@@RESOURCE@@`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ title: "" }),
    });
    assert.equal(res.status, 400);
  });

  it("creates, reads, updates, and deletes an item", async () => {
    const create = await fetch(`${base}/api/@@RESOURCE@@`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ title: "sample" }),
    });
    assert.equal(create.status, 201);
    const { item } = await create.json();

    const got = await fetch(`${base}/api/@@RESOURCE@@/${item.id}`);
    assert.equal((await got.json()).item.title, "sample");

    const patch = await fetch(`${base}/api/@@RESOURCE@@/${item.id}`, {
      method: "PATCH",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ done: true }),
    });
    assert.equal((await patch.json()).item.done, true);

    const del = await fetch(`${base}/api/@@RESOURCE@@/${item.id}`, { method: "DELETE" });
    assert.equal(del.status, 204);
    assert.equal((await fetch(`${base}/api/@@RESOURCE@@/${item.id}`)).status, 404);
  });

  it("returns 404 with JSON body for unknown routes", async () => {
    const res = await fetch(`${base}/definitely-missing`);
    assert.equal(res.status, 404);
    assert.ok((await res.json()).error);
  });
});
'''

_EXPRESS_SERVER_HELPER = '''import { once } from "node:events";

/** Start an app on an ephemeral port and expose its base URL for tests. */
export function startServer(app) {
  let server = null;
  let port = 0;
  return {
    get port() {
      return port;
    },
    async start() {
      server = app.listen(0, "127.0.0.1");
      await once(server, "listening");
      port = server.address().port;
    },
    async stop() {
      if (!server) return;
      server.close();
      await once(server, "close");
      server = null;
    },
  };
}
'''


def _express(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    resource = args["resource"]
    port = args["port"]

    pkg = _pkg(
        project,
        {
            "start": "node src/server.js",
            "dev": "node --watch src/server.js",
            "test": "node --test test/",
        },
        npm(**{"express": "^4.21.2"}),
        {},
        {"type": "module", "engines": {"node": ">=18.18"}},
    )

    pairs: List[Pair] = [
        (rel_path(project, "package.json"), dump_json(pkg)),
        (rel_path(project, ".gitignore"), GITIGNORE_NODE),
        (rel_path(project, ".env.example"), '''# Copy to .env. PORT is optional (defaults to @@PORT@@).
'''.replace("@@PORT@@", str(port))),
        (rel_path(project, "src", "config.js"), render('''const port = Number.parseInt(process.env.PORT ?? "@@PORT@@", 10);
if (!Number.isInteger(port) || port < 1024 || port > 65535) {
  throw new Error("PORT must be an integer between 1024 and 65535");
}

export const config = { port };
''', {"PORT": str(port)})),
        (rel_path(project, "src", "lib", "store.js"), render('''/**
 * In-process store kept for the sample API. Swap for a database adapter
 * (better-sqlite3, Prisma, Postgres) before relying on persistence.
 */
export function createMemoryStore() {
  const items = new Map();
  return {
    list() {
      return [...items.values()].sort((a, b) => a.createdAt.localeCompare(b.createdAt));
    },
    get(id) {
      return items.get(id);
    },
    create(title) {
      const item = {
        id: crypto.randomUUID(),
        title,
        done: false,
        createdAt: new Date().toISOString(),
      };
      items.set(item.id, item);
      return item;
    },
    update(id, patch) {
      const current = items.get(id);
      if (!current) return undefined;
      const next = { ...current, ...patch };
      items.set(id, next);
      return next;
    },
    remove(id) {
      return items.delete(id);
    },
  };
}

export const store = createMemoryStore();
''', {"MODEL": "Item"})),
        (rel_path(project, "src", "middleware", "requestLogger.js"), '''/** Minimal structured request logger (no external dependency). */
export function requestLogger(req, res, next) {
  const startedAt = process.hrtime.bigint();
  res.on("finish", () => {
    const elapsedMs = Number(process.hrtime.bigint() - startedAt) / 1e6;
    const line = {
      level: "info",
      msg: "request",
      method: req.method,
      path: req.originalUrl,
      status: res.statusCode,
      ms: Math.round(elapsedMs * 100) / 100,
    };
    console.log(JSON.stringify(line));
  });
  next();
}
'''),
        (rel_path(project, "src", "middleware", "security.js"), '''/** Conservative security headers for API responses. */
export function securityHeaders(_req, res, next) {
  res.setHeader("X-Content-Type-Options", "nosniff");
  res.setHeader("X-Frame-Options", "DENY");
  res.setHeader("Referrer-Policy", "no-referrer");
  res.setHeader("Content-Security-Policy", "default-src 'none'; frame-ancestors 'none'");
  next();
}
'''),
        (rel_path(project, "src", "middleware", "errors.js"), '''export function notFound(req, res) {
  res.status(404).json({ error: "not found", path: req.originalUrl });
}

// eslint-disable-next-line no-unused-vars
export function errorHandler(err, req, res, _next) {
  if (err.type === "entity.parse.failed" || err.type === "entity.too.large") {
    return res.status(400).json({ error: "invalid request body" });
  }
  console.error(JSON.stringify({ level: "error", msg: err.message }));
  res.status(500).json({ error: "internal server error" });
}
'''),
        (rel_path(project, "src", "routes", "health.js"), render('''import { Router } from "express";

export function createHealthRouter(serviceName) {
  const router = Router();
  router.get("/", (_req, res) => {
    res.json({
      status: "ok",
      service: serviceName,
      time: new Date().toISOString(),
    });
  });
  return router;
}
''', {"PROJECT": project})),
        (rel_path(project, "src", "routes", "%s.router.js" % resource), _EXPRESS_RESOURCE),
        (rel_path(project, "src", "app.js"), render('''import express from "express";
import { config } from "./config.js";
import { securityHeaders } from "./middleware/security.js";
import { requestLogger } from "./middleware/requestLogger.js";
import { errorHandler, notFound } from "./middleware/errors.js";
import { createHealthRouter } from "./routes/health.js";
import { createResourceRouter } from "./routes/@@RESOURCE@@.router.js";

export function createApp() {
  const app = express();
  app.disable("x-powered-by");
  app.use(securityHeaders);
  app.use(requestLogger);
  app.use(express.json({ limit: "64kb" }));

  app.use("/api/health", createHealthRouter("@@PROJECT@@"));
  app.use("/api/@@RESOURCE@@", createResourceRouter());

  app.use(notFound);
  app.use(errorHandler);
  return app;
}
''', {"PROJECT": project, "RESOURCE": resource})),
        (rel_path(project, "src", "server.js"), render('''import { config } from "./config.js";
import { createApp } from "./app.js";

const app = createApp();
const server = app.listen(config.port, () => {
  console.log(JSON.stringify({ level: "info", msg: "listening", port: config.port }));
});

function shutdown(signal) {
  console.log(JSON.stringify({ level: "info", msg: "shutting down", signal }));
  server.close(() => process.exit(0));
  setTimeout(() => process.exit(1), 10_000).unref();
}

process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));
''', {"PROJECT": project, "PORT": str(port)})),
        (rel_path(project, "test", "helpers", "server.js"), _EXPRESS_SERVER_HELPER),
        (rel_path(project, "test", "%s.test.js" % resource), render(_EXPRESS_RESOURCE_TEST, {"PROJECT": project, "RESOURCE": resource})),
        (rel_path(project, "README.md"), render('''# @@PROJECT@@ (Express)

Generated scaffold — static source files. Nothing has been installed,
built, or executed yet.

## Requirements

- Node.js >= 18.18 (built-in test runner and `fetch` are used)

## Run

```sh
npm install
npm start          # http://localhost:@@PORT@@
npm run dev        # restart on file change (node --watch)
```

## Tests

```sh
npm test           # node:test suite; starts the app on an ephemeral port
```

## What is inside

- `src/app.js` composes middleware: security headers, request logging,
  JSON body limit (64 kb), routers, 404 + error handlers.
- `GET /api/health` liveness endpoint.
- CRUD router for `@@RESOURCE@@` at `/api/@@RESOURCE@@` backed by the
  in-process store in `src/lib/store.js` (swap for a database adapter).
- `POST` bodies are validated (title 1..200 chars); invalid bodies get
  a 400 with a JSON error.
''', {"PROJECT": project, "PORT": str(port), "RESOURCE": resource})),
    ]

    data = {
        "framework": "express",
        "language": "javascript-esm",
        "package_manager": "npm",
        "entrypoints": ["src/server.js", "src/app.js"],
        "sample_api": {
            "list": "GET /api/%s" % resource,
            "create": "POST /api/%s" % resource,
            "detail": "GET|PATCH|DELETE /api/%s/:id" % resource,
            "health": "GET /api/health",
        },
        "commands": {"install": "npm install", "start": "npm start", "test": "npm test"},
        "dependencies": pkg.get("dependencies", {}),
        "dev_dependencies": pkg.get("devDependencies", {}),
    }
    return {"data": data, "writes": pairs, "limitations": [LIMIT_RUN]}


# ---------------------------------------------------------------------------
# 52 scaffold.nestjs
# ---------------------------------------------------------------------------

def _nestjs(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    resource = args["resource"]
    port = args["port"]
    model = safe.pascal(resource, "resource")
    prop = safe.camel_if_needed(resource) if hasattr(safe, "camel_if_needed") else resource

    pkg = _pkg(
        project,
        {
            "build": "nest build",
            "start": "nest start",
            "start:dev": "nest start --watch",
            "start:prod": "node dist/main",
            "test": "jest",
            "test:watch": "jest --watch",
            "test:e2e": "jest --config ./test/jest-e2e.json",
        },
        npm(**{
            "@nestjs/common": "^10.4.15",
            "@nestjs/core": "^10.4.15",
            "@nestjs/platform-express": "^10.4.15",
            "class-transformer": "^0.5.1",
            "class-validator": "^0.14.1",
            "reflect-metadata": "^0.2.2",
            "rxjs": "^7.8.1",
        }),
        npm(**{
            "@nestjs/cli": "^10.4.9",
            "@nestjs/schematics": "^10.2.3",
            "@nestjs/testing": "^10.4.15",
            "@types/express": "^5.0.0",
            "@types/jest": "^29.5.14",
            "@types/node": "^22.10.10",
            "@types/supertest": "^6.0.2",
            "jest": "^29.7.0",
            "supertest": "^7.0.0",
            "ts-jest": "^29.2.5",
            "ts-loader": "^9.5.2",
            "ts-node": "^10.9.2",
            "tsconfig-paths": "^4.2.0",
            "typescript": "^5.7.3",
        }),
        {"jest": {
            "moduleFileExtensions": ["js", "json", "ts"],
            "rootDir": "src",
            "testRegex": ".*\\.spec\\.ts$",
            "transform": {"^.+\\.(t|j)s$": "ts-jest"},
            "collectCoverageFrom": ["**/*.(t|j)s"],
            "coverageDirectory": "../coverage",
            "testEnvironment": "node",
        }},
    )

    dto_create = '''import { IsBoolean, IsOptional, IsString, Length } from "class-validator";

export class Create@@MODEL@@Dto {
  @IsString()
  @Length(1, 200)
  title!: string;

  @IsOptional()
  @IsBoolean()
  done?: boolean;
}
'''

    dto_update = '''import { IsBoolean, IsOptional, IsString, Length } from "class-validator";

export class Update@@MODEL@@Dto {
  @IsOptional()
  @IsString()
  @Length(1, 200)
  title?: string;

  @IsOptional()
  @IsBoolean()
  done?: boolean;
}
'''

    service = '''import { Injectable, NotFoundException } from "@nestjs/common";
import { Create@@MODEL@@Dto } from "./dto/create-@@RESOURCE@@.dto";
import { Update@@MODEL@@Dto } from "./dto/update-@@RESOURCE@@.dto";

export interface @@MODEL@@ {
  id: string;
  title: string;
  done: boolean;
  createdAt: string;
}

/**
 * In-memory store for the sample module. Replace with a real repository
 * (Prisma, TypeORM, Drizzle) before relying on persistence.
 */
@Injectable()
export class @@MODEL@@Service {
  private readonly items = new Map<string, @@MODEL@@>();

  create(dto: Create@@MODEL@@Dto): @@MODEL@@ {
    const item: @@MODEL@@ = {
      id: crypto.randomUUID(),
      title: dto.title.trim(),
      done: dto.done ?? false,
      createdAt: new Date().toISOString(),
    };
    this.items.set(item.id, item);
    return item;
  }

  list(): @@MODEL@@[] {
    return [...this.items.values()].sort((a, b) => a.createdAt.localeCompare(b.createdAt));
  }

  findOne(id: string): @@MODEL@@ {
    const item = this.items.get(id);
    if (!item) {
      throw new NotFoundException("@@MODEL@@ not found");
    }
    return item;
  }

  update(id: string, dto: Update@@MODEL@@Dto): @@MODEL@@ {
    const current = this.findOne(id);
    const next: @@MODEL@@ = {
      ...current,
      ...(dto.title !== undefined ? { title: dto.title.trim() } : {}),
      ...(dto.done !== undefined ? { done: dto.done } : {}),
    };
    this.items.set(id, next);
    return next;
  }

  remove(id: string): void {
    this.findOne(id);
    this.items.delete(id);
  }
}
'''

    controller = '''import { Body, Controller, Delete, Get, HttpCode, Param, Patch, Post } from "@nestjs/common";
import { Create@@MODEL@@Dto } from "./dto/create-@@RESOURCE@@.dto";
import { Update@@MODEL@@Dto } from "./dto/update-@@RESOURCE@@.dto";
import { @@MODEL@@Service } from "./@@RESOURCE@@.service";

@Controller("@@RESOURCE@@")
export class @@MODEL@@Controller {
  constructor(private readonly service: @@MODEL@@Service) {}

  @Post()
  create(@Body() dto: Create@@MODEL@@Dto) {
    return this.service.create(dto);
  }

  @Get()
  list() {
    return { items: this.service.list() };
  }

  @Get(":id")
  detail(@Param("id") id: string) {
    return { item: this.service.findOne(id) };
  }

  @Patch(":id")
  update(@Param("id") id: string, @Body() dto: Update@@MODEL@@Dto) {
    return { item: this.service.update(id, dto) };
  }

  @Delete(":id")
  @HttpCode(204)
  remove(@Param("id") id: string): void {
    this.service.remove(id);
  }
}
'''

    module = '''import { Module } from "@nestjs/common";
import { @@MODEL@@Controller } from "./@@RESOURCE@@.controller";
import { @@MODEL@@Service } from "./@@RESOURCE@@.service";

@Module({
  controllers: [@@MODEL@@Controller],
  providers: [@@MODEL@@Service],
})
export class @@MODEL@@Module {}
'''

    spec = '''import { Test } from "@nestjs/testing";
import { @@MODEL@@Controller } from "./@@RESOURCE@@.controller";
import { @@MODEL@@Service } from "./@@RESOURCE@@.service";

describe("@@MODEL@@Controller", () => {
  let controller: @@MODEL@@Controller;
  let service: @@MODEL@@Service;

  beforeEach(async () => {
    const moduleRef = await Test.createTestingModule({
      controllers: [@@MODEL@@Controller],
      providers: [@@MODEL@@Service],
    }).compile();
    controller = moduleRef.get(@@MODEL@@Controller);
    service = moduleRef.get(@@MODEL@@Service);
  });

  it("creates and lists items", () => {
    controller.create({ title: "sample" });
    expect(controller.list().items).toHaveLength(1);
  });

  it("throws NotFound for unknown ids", () => {
    expect(() => controller.detail("missing")).toThrow("@@MODEL@@ not found");
  });

  it("removes items", () => {
    const { item } = controller.create({ title: "temp" });
    controller.remove(item.id);
    expect(service.list()).toHaveLength(0);
  });
});
'''

    e2e = '''import { INestApplication, ValidationPipe } from "@nestjs/common";
import { Test } from "@nestjs/testing";
import request from "supertest";
import { AppModule } from "./../src/app.module";

describe("App (e2e)", () => {
  let app: INestApplication;

  beforeAll(async () => {
    const moduleRef = await Test.createTestingModule({
      imports: [AppModule],
    }).compile();
    app = moduleRef.createNestApplication();
    app.useGlobalPipes(new ValidationPipe({ whitelist: true, transform: true }));
    await app.init();
  });

  afterAll(async () => {
    await app.close();
  });

  it("GET /api/health", () => {
    return request(app.getHttpServer())
      .get("/api/health")
      .expect(200)
      .expect((res) => expect(res.body.status).toBe("ok"));
  });

  it("rejects invalid @@RESOURCE@@ payloads", () => {
    return request(app.getHttpServer())
      .post("/api/@@RESOURCE@@")
      .send({ title: "" })
      .expect(400);
  });

  it("creates and deletes an @@RESOURCE@@ item", async () => {
    const created = await request(app.getHttpServer())
      .post("/api/@@RESOURCE@@")
      .send({ title: "sample" })
      .expect(201);
    const id = created.body.id;
    await request(app.getHttpServer()).delete(`/api/@@RESOURCE@@/${id}`).expect(204);
  });
});
'''

    pairs: List[Pair] = [
        (rel_path(project, "package.json"), dump_json(pkg)),
        (rel_path(project, "nest-cli.json"), '''{
  "$schema": "https://json.schemastore.org/nest-cli",
  "collection": "@nestjs/schematics",
  "sourceRoot": "src",
  "compilerOptions": {
    "deleteOutDir": true
  }
}
'''),
        (rel_path(project, "tsconfig.json"), '''{
  "compilerOptions": {
    "module": "commonjs",
    "declaration": true,
    "removeComments": true,
    "emitDecoratorMetadata": true,
    "experimentalDecorators": true,
    "allowSyntheticDefaultImports": true,
    "target": "ES2021",
    "sourceMap": true,
    "outDir": "./dist",
    "baseUrl": "./",
    "incremental": true,
    "skipLibCheck": true,
    "strictNullChecks": true,
    "forceConsistentCasingInFileNames": true,
    "noImplicitAny": false,
    "strictBindCallApply": false,
    "esModuleInterop": true
  }
}
'''),
        (rel_path(project, "tsconfig.build.json"), '''{
  "extends": "./tsconfig.json",
  "exclude": ["node_modules", "test", "dist", "**/*spec.ts"]
}
'''),
        (rel_path(project, ".gitignore"), GITIGNORE_NODE),
        (rel_path(project, ".env.example"), "# Copy to .env. PORT is optional (defaults to @@PORT@@).\n".replace("@@PORT@@", str(port))),
        (rel_path(project, "src", "main.ts"), '''import { ValidationPipe } from "@nestjs/common";
import { NestFactory } from "@nestjs/core";
import { AppModule } from "./app.module";

async function bootstrap(): Promise<void> {
  const app = await NestFactory.create(AppModule);
  const port = Number.parseInt(process.env.PORT ?? "@@PORT@@", 10);
  app.useGlobalPipes(
    new ValidationPipe({ whitelist: true, transform: true, forbidNonWhitelisted: true }),
  );
  await app.listen(port);
  console.log(`Application is running on: ${await app.getUrl()}`);
}

void bootstrap();
'''.replace("@@PORT@@", str(port))),
        (rel_path(project, "src", "app.module.ts"), render('''import { Module } from "@nestjs/common";
import { AppController } from "./app.controller";
import { AppService } from "./app.service";
import { @@MODEL@@Module } from "./@@RESOURCE@@/@@RESOURCE@@.module";

@Module({
  imports: [@@MODEL@@Module],
  controllers: [AppController],
  providers: [AppService],
})
export class AppModule {}
''', {"MODEL": model, "RESOURCE": resource})),
        (rel_path(project, "src", "app.controller.ts"), render('''import { Controller, Get } from "@nestjs/common";
import { AppService } from "./app.service";

@Controller()
export class AppController {
  constructor(private readonly appService: AppService) {}

  @Get("api/health")
  health() {
    return this.appService.health();
  }
}
''', {"PROJECT": project})),
        (rel_path(project, "src", "app.service.ts"), render('''import { Injectable } from "@nestjs/common";

@Injectable()
export class AppService {
  health() {
    return {
      status: "ok",
      service: "@@PROJECT@@",
      time: new Date().toISOString(),
    };
  }
}
''', {"PROJECT": project})),
        (rel_path(project, "src", "app.controller.spec.ts"), render('''import { Test } from "@nestjs/testing";
import { AppController } from "./app.controller";
import { AppService } from "./app.service";

describe("AppController", () => {
  it("reports health", async () => {
    const moduleRef = await Test.createTestingModule({
      controllers: [AppController],
      providers: [AppService],
    }).compile();
    const controller = moduleRef.get(AppController);
    expect(controller.health().status).toBe("ok");
  });
});
''', {"PROJECT": project})),
        (rel_path(project, "src", resource, "%s.module.ts" % resource), render(module, {"MODEL": model, "RESOURCE": resource})),
        (rel_path(project, "src", resource, "%s.controller.ts" % resource), render(controller, {"MODEL": model, "RESOURCE": resource})),
        (rel_path(project, "src", resource, "%s.service.ts" % resource), render(service, {"MODEL": model, "RESOURCE": resource})),
        (rel_path(project, "src", resource, "dto", "create-%s.dto.ts" % resource), render(dto_create, {"MODEL": model})),
        (rel_path(project, "src", resource, "dto", "update-%s.dto.ts" % resource), render(dto_update, {"MODEL": model})),
        (rel_path(project, "src", resource, "%s.service.spec.ts" % resource), render(spec, {"MODEL": model, "RESOURCE": resource})),
        (rel_path(project, "test", "app.e2e-spec.ts"), render(e2e, {"MODEL": model, "RESOURCE": resource})),
        (rel_path(project, "test", "jest-e2e.json"), '''{
  "moduleFileExtensions": ["js", "json", "ts"],
  "rootDir": ".",
  "testEnvironment": "node",
  "testRegex": ".e2e-spec.ts$",
  "transform": {"^.+\\\\.(t|j)s$": "ts-jest"}
}
'''),
        (rel_path(project, "README.md"), render('''# @@PROJECT@@ (NestJS)

Generated scaffold — static source files. Nothing has been installed,
built, or executed yet.

## Requirements

- Node.js >= 20, npm >= 9

## Run

```sh
npm install
npm run start:dev     # http://localhost:@@PORT@@
npm run build && npm run start:prod
```

## Tests

```sh
npm test              # unit tests (jest)
npm run test:e2e      # supertest e2e against the HTTP server
```

## What is inside

- NestJS 10 with global `ValidationPipe` (whitelist + transform).
- `GET /api/health` liveness endpoint (AppController).
- Sample `@@RESOURCE@@` module with class-validator DTOs, service with
  in-memory storage (swap for a real repository), controller, unit spec,
  and e2e tests.
''', {"PROJECT": project, "PORT": str(port), "RESOURCE": resource, "MODEL": model})),
    ]

    data = {
        "framework": "nestjs",
        "language": "typescript",
        "package_manager": "npm",
        "entrypoints": ["src/main.ts", "src/app.module.ts"],
        "sample_api": {
            "list": "GET /api/%s" % resource,
            "create": "POST /api/%s" % resource,
            "detail": "GET|PATCH|DELETE /api/%s/:id" % resource,
            "health": "GET /api/health",
        },
        "commands": {"install": "npm install", "dev": "npm run start:dev",
                     "build": "npm run build", "test": "npm test",
                     "test_e2e": "npm run test:e2e"},
        "dependencies": pkg.get("dependencies", {}),
        "dev_dependencies": pkg.get("devDependencies", {}),
    }
    return {"data": data, "writes": pairs, "limitations": [LIMIT_RUN]}


# ---------------------------------------------------------------------------
# 53 scaffold.fastapi
# ---------------------------------------------------------------------------

_FASTAPI_MAIN = '''"""FastAPI application factory."""
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config import settings
from .database import init_db
from .routers import health, @@PLURAL@@


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


def create_app() -> FastAPI:
    app = FastAPI(title=settings.app_name, lifespan=lifespan)
    app.include_router(health.router)
    app.include_router(@@PLURAL@@.router, prefix="/api/@@PLURAL@@", tags=["@@PLURAL@@"])

    static_dir = Path(__file__).parent / "static"
    app.mount("/static", StaticFiles(directory=static_dir), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        """Serve the bundled single-page frontend."""
        return FileResponse(static_dir / "index.html")

    return app


app = create_app()
'''

_FASTAPI_DATABASE = '''"""SQLite access helpers (stdlib sqlite3, parameterized queries)."""
import sqlite3
from pathlib import Path

from .config import settings

SCHEMA = """
CREATE TABLE IF NOT EXISTS @@PLURAL@@ (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL,
    done INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_@@PLURAL@@_created_at ON @@PLURAL@@ (created_at);
"""


def connect() -> sqlite3.Connection:
    """Open a new connection per call; FastAPI sync routes run in a
    threadpool, so sharing a single connection is not safe."""
    path = Path(settings.db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
'''

_FASTAPI_MODELS = '''"""Pydantic request/response models."""
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field


class @@MODEL@@Create(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    done: bool = False


class @@MODEL@@Update(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=200)
    done: Optional[bool] = None


class @@MODEL@@Read(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    done: bool
    created_at: datetime
'''

_FASTAPI_ROUTER = '''"""CRUD router backed by SQLite (parameterized SQL only)."""
import sqlite3
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status

from ..database import connect
from ..models import @@MODEL@@Create, @@MODEL@@Read, @@MODEL@@Update

router = APIRouter()


def _to_read(row: sqlite3.Row) -> @@MODEL@@Read:
    return @@MODEL@@Read(
        id=row["id"],
        title=row["title"],
        done=bool(row["done"]),
        created_at=row["created_at"],
    )


@router.get("", response_model=list[@@MODEL@@Read])
def list_@@PLURAL@@(limit: int = 50, offset: int = 0) -> list[@@MODEL@@Read]:
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    with connect() as conn:
        rows = conn.execute(
            "SELECT id, title, done, created_at FROM @@PLURAL@@ ORDER BY created_at, id LIMIT ? OFFSET ?",
            (limit, offset),
        ).fetchall()
    return [_to_read(row) for row in rows]


@router.post("", response_model=@@MODEL@@Read, status_code=status.HTTP_201_CREATED)
def create_@@SINGULAR@@(payload: @@MODEL@@Create) -> @@MODEL@@Read:
    created_at = datetime.now(timezone.utc).isoformat()
    with connect() as conn:
        cursor = conn.execute(
            "INSERT INTO @@PLURAL@@ (title, done, created_at) VALUES (?, ?, ?)",
            (payload.title.strip(), int(payload.done), created_at),
        )
        row = conn.execute(
            "SELECT id, title, done, created_at FROM @@PLURAL@@ WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return _to_read(row)


@router.get("/{item_id}", response_model=@@MODEL@@Read)
def get_@@SINGULAR@@(item_id: int) -> @@MODEL@@Read:
    with connect() as conn:
        row = conn.execute(
            "SELECT id, title, done, created_at FROM @@PLURAL@@ WHERE id = ?", (item_id,)
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="not found")
    return _to_read(row)


@router.patch("/{item_id}", response_model=@@MODEL@@Read)
def update_@@SINGULAR@@(item_id: int, payload: @@MODEL@@Update) -> @@MODEL@@Read:
    fields = {k: v for k, v in payload.model_dump().items() if v is not None}
    if not fields:
        raise HTTPException(status_code=400, detail="nothing to update")
    if "title" in fields:
        fields["title"] = fields["title"].strip()
    assignments = ", ".join(f"{name} = ?" for name in fields)
    values = [*fields.values(), item_id]
    with connect() as conn:
        cursor = conn.execute(f"UPDATE @@PLURAL@@ SET {assignments} WHERE id = ?", values)
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="not found")
        row = conn.execute(
            "SELECT id, title, done, created_at FROM @@PLURAL@@ WHERE id = ?", (item_id,)
        ).fetchone()
    return _to_read(row)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_@@SINGULAR@@(item_id: int) -> None:
    with connect() as conn:
        cursor = conn.execute("DELETE FROM @@PLURAL@@ WHERE id = ?", (item_id,))
        if cursor.rowcount == 0:
            raise HTTPException(status_code=404, detail="not found")
'''

_FASTAPI_HEALTH = '''from datetime import datetime, timezone

from fastapi import APIRouter

from ..config import settings

router = APIRouter(prefix="/api/health", tags=["health"])


@router.get("")
def health() -> dict:
    return {
        "status": "ok",
        "service": settings.app_name,
        "time": datetime.now(timezone.utc).isoformat(),
    }
'''

_FASTAPI_CONFIG = '''"""Environment-driven settings (no third-party dependency)."""
import os


class Settings:
    app_name: str = os.environ.get("APP_NAME", "@@PROJECT@@")
    db_path: str = os.environ.get("APP_DB_PATH", "data/@@PROJECT@@.db")


settings = Settings()
'''

_FASTAPI_STATIC = '''<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>@@PROJECT@@</title>
  <style>
    :root { font-family: system-ui, sans-serif; line-height: 1.5; }
    body { max-width: 42rem; margin: 0 auto; padding: 2rem 1rem; }
    form { display: flex; gap: .5rem; margin-bottom: 1rem; }
    input[type=text] { flex: 1; padding: .5rem; border: 1px solid #ccc; border-radius: .375rem; }
    button { padding: .5rem 1rem; border: none; border-radius: .375rem; background: #2563eb; color: #fff; cursor: pointer; }
    button.secondary { background: #6b7280; }
    li { display: flex; align-items: center; gap: .5rem; padding: .375rem 0; border-bottom: 1px solid #eee; }
    li.done span { text-decoration: line-through; color: #6b7280; }
    [role=alert] { color: #b91c1c; }
  </style>
</head>
<body>
  <h1>@@PROJECT@@</h1>
  <p>Sample frontend served by FastAPI, calling <code>/api/@@PLURAL@@</code>.</p>
  <form id="add-form">
    <input type="text" id="title" maxlength="200" required placeholder="Add @@SINGULAR@@ title" aria-label="Title" />
    <button type="submit">Add</button>
  </form>
  <p id="error" role="alert" hidden></p>
  <ul id="list"></ul>
  <script>
    const list = document.getElementById("list");
    const errorBox = document.getElementById("error");
    let items = [];

    function showError(message) {
      errorBox.textContent = message;
      errorBox.hidden = false;
    }

    function clearError() {
      errorBox.hidden = true;
    }

    function render() {
      list.innerHTML = "";
      for (const item of items) {
        const li = document.createElement("li");
        if (item.done) li.className = "done";
        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = item.done;
        checkbox.addEventListener("change", () => toggle(item));
        const label = document.createElement("span");
        label.textContent = item.title;
        const remove = document.createElement("button");
        remove.className = "secondary";
        remove.textContent = "Delete";
        remove.setAttribute("aria-label", "Delete " + item.title);
        remove.addEventListener("click", () => removeItem(item));
        li.append(checkbox, label, remove);
        list.append(li);
      }
    }

    async function refresh() {
      clearError();
      try {
        const res = await fetch("/api/@@PLURAL@@");
        if (!res.ok) throw new Error("bad status " + res.status);
        items = (await res.json()).map((item) => ({ ...item, done: !!item.done }));
        render();
      } catch {
        showError("failed to load items");
      }
    }

    async function toggle(item) {
      clearError();
      const res = await fetch("/api/@@PLURAL@@/" + item.id, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ done: !item.done }),
      });
      if (!res.ok) return showError("update failed");
      await refresh();
    }

    async function removeItem(item) {
      clearError();
      const res = await fetch("/api/@@PLURAL@@/" + item.id, { method: "DELETE" });
      if (!res.ok && res.status !== 204) return showError("delete failed");
      await refresh();
    }

    document.getElementById("add-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      clearError();
      const input = document.getElementById("title");
      const res = await fetch("/api/@@PLURAL@@", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ title: input.value }),
      });
      if (!res.ok) return showError("create failed");
      input.value = "";
      await refresh();
    });

    refresh();
  </script>
</body>
</html>
'''

_FASTAPI_TEST_API = '''"""End-to-end API tests using the bundled TestClient (httpx)."""
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture()
def client():
    with TestClient(app) as test_client:
        yield test_client


def test_health(client):
    res = client.get("/api/health")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"


def test_index_serves_frontend(client):
    res = client.get("/")
    assert res.status_code == 200
    assert "text/html" in res.headers["content-type"]


def test_create_requires_title(client):
    res = client.post("/api/@@PLURAL@@", json={"title": ""})
    assert res.status_code == 422


def test_full_crud_cycle(client):
    created = client.post("/api/@@PLURAL@@", json={"title": "sample"})
    assert created.status_code == 201
    item = created.json()
    assert item["title"] == "sample"
    assert item["done"] is False

    listed = client.get("/api/@@PLURAL@@")
    assert any(row["id"] == item["id"] for row in listed.json())

    updated = client.patch(f"/api/@@PLURAL@@/{item['id']}", json={"done": True})
    assert updated.json()["done"] is True

    assert client.delete(f"/api/@@PLURAL@@/{item['id']}").status_code == 204
    assert client.get(f"/api/@@PLURAL@@/{item['id']}").status_code == 404


def test_update_unknown_item_returns_404(client):
    assert client.patch("/api/@@PLURAL@@/999999", json={"done": True}).status_code == 404
'''

_FASTAPI_CONFTEST = '''"""Isolate the database for every test run (set before app import)."""
import os
import tempfile

_DB, _PATH = tempfile.mkstemp(prefix="harmonika-test-", suffix=".db")
os.close(_DB)
os.environ["APP_DB_PATH"] = _PATH
'''

_FASTAPI_README = '''# @@PROJECT@@ (FastAPI + SQLite)

Generated scaffold — static source files. Nothing has been installed
and no test has been executed yet.

## Requirements

- Python 3.9+ (3.11+ recommended)

## Install

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt
```

## Run

```sh
uvicorn app.main:app --reload   # http://localhost:8000
```

- `GET /api/health` — liveness endpoint.
- `GET|POST /api/@@PLURAL@@`, `GET|PATCH|DELETE /api/@@PLURAL@@/{id}` — CRUD
  over SQLite with parameterized queries.
- `/` serves the bundled single-page frontend (`app/static/index.html`)
  that talks to the API end to end.
- Interactive API docs: `/docs`.

## Tests

```sh
pytest    # tests/conftest.py points APP_DB_PATH at a temp database
```

## Notes

- The database file defaults to `data/@@PROJECT@@.db`; override with
  `APP_DB_PATH`. The directory is created automatically.
- Copy `.env.example` to `.env` only for private configuration.
'''

_FASTAPI_REQUIREMENTS = '''fastapi==0.162.0
uvicorn[standard]==0.54.0
pydantic==2.14.1
'''

_FASTAPI_REQUIREMENTS_DEV = '''pytest==8.3.4
httpx==0.28.1
'''

_FASTAPI_GITIGNORE = '''.venv/
__pycache__/
*.pyc
.pytest_cache/
data/
.env
'''


def _fastapi(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    resource = args["resource"]
    port = args["port"]
    plural = resource
    singular = safe.singularize(resource) if hasattr(safe, "singularize") else resource.rstrip("s") or resource
    model = safe.pascal(plural, "resource")

    pairs: List[Pair] = [
        (rel_path(project, "requirements.txt"), _FASTAPI_REQUIREMENTS),
        (rel_path(project, "requirements-dev.txt"), _FASTAPI_REQUIREMENTS_DEV),
        (rel_path(project, "pytest.ini"), '''[pytest]
testpaths = tests
addopts = -q
'''),
        (rel_path(project, ".gitignore"), _FASTAPI_GITIGNORE),
        (rel_path(project, ".env.example"), '''# Copy to .env for private configuration.
APP_NAME=@@PROJECT@@
APP_DB_PATH=data/@@PROJECT@@.db
'''.replace("@@PROJECT@@", project)),
        (rel_path(project, "app", "__init__.py"), '"""@@PROJECT@@ application package."""\n'.replace("@@PROJECT@@", project)),
        (rel_path(project, "app", "config.py"), _FASTAPI_CONFIG.replace("@@PROJECT@@", project)),
        (rel_path(project, "app", "database.py"), _FASTAPI_DATABASE),
        (rel_path(project, "app", "models.py"), _FASTAPI_MODELS),
        (rel_path(project, "app", "routers", "__init__.py"), '"""API routers."""\n'),
        (rel_path(project, "app", "routers", "health.py"), _FASTAPI_HEALTH),
        (rel_path(project, "app", "routers", "%s.py" % plural), _FASTAPI_ROUTER),
        (rel_path(project, "app", "static", "index.html"), _FASTAPI_STATIC),
        (rel_path(project, "app", "main.py"), _FASTAPI_MAIN),
        (rel_path(project, "tests", "conftest.py"), _FASTAPI_CONFTEST),
        (rel_path(project, "tests", "test_api.py"), _FASTAPI_TEST_API),
        (rel_path(project, "README.md"), _FASTAPI_README),
    ]
    mapping = {
        "PROJECT": project,
        "PLURAL": plural,
        "SINGULAR": singular,
        "MODEL": model,
    }
    writes = [
        (path, render(content, mapping) if "@@" in content else content)
        for path, content in pairs
    ]

    data = {
        "framework": "fastapi",
        "language": "python",
        "python_min": "3.9",
        "entrypoints": ["app/main.py", "app/routers/%s.py" % plural],
        "sample_api": {
            "list": "GET /api/%s" % plural,
            "create": "POST /api/%s" % plural,
            "detail": "GET|PATCH|DELETE /api/%s/{item_id}" % plural,
            "health": "GET /api/health",
            "docs": "/docs",
            "frontend": "/ (serves app/static/index.html)",
        },
        "commands": {
            "install": "pip install -r requirements.txt -r requirements-dev.txt",
            "dev": "uvicorn app.main:app --reload",
            "test": "pytest",
        },
        "dependencies": {"fastapi": "0.162.0", "uvicorn": "0.54.0", "pydantic": "2.14.1"},
        "dev_dependencies": {"pytest": "8.3.4", "httpx": "0.28.1"},
        "database": {"engine": "sqlite", "schema": "app/database.py SCHEMA (auto-created at startup)"},
    }
    return {"data": data, "writes": writes, "limitations": [LIMIT_RUN]}


# ---------------------------------------------------------------------------
# 54 scaffold.django
# ---------------------------------------------------------------------------

def _django(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    resource = args["resource"]
    proj_mod = safe.snake(project, "project")
    app_mod = safe.snake(resource, "app name")
    model = safe.pascal(resource, "resource")

    settings_py = '''"""
Django settings for @@PROJ@@ (generated).

Fail closed: DJANGO_SECRET_KEY must be provided in the environment;
without it the application refuses to start.
"""
from pathlib import Path
import os

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY")
if not SECRET_KEY:
    raise ImproperlyConfigured(
        "DJANGO_SECRET_KEY is not set. Generate one with: "
        "python -c \\"import secrets; print(secrets.token_urlsafe(50))\\""
    )

DEBUG = os.environ.get("DJANGO_DEBUG", "0") == "1"

ALLOWED_HOSTS = [
    host.strip()
    for host in os.environ.get("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "@@APP@@",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "@@PROJ@@.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "@@PROJ@@.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / os.environ.get("DJANGO_DB_PATH", "db.sqlite3"),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
'''

    manage_py = '''#!/usr/bin/env python
"""Django's command-line utility for administrative tasks."""
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "@@PROJ@@.settings")
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
'''

    views_py = '''"""JSON API for @@MODEL@@ (no DRF dependency)."""
import json

from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from .models import @@MODEL@@

MAX_TITLE = 200


def _serialize(task: @@MODEL@@) -> dict:
    return {
        "id": task.id,
        "title": task.title,
        "done": task.done,
        "created_at": task.created_at.isoformat(),
    }


def _parse_body(request):
    try:
        data = json.loads(request.body or b"{}")
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _validate_title(value) -> str | None:
    if not isinstance(value, str) or not value.strip() or len(value) > MAX_TITLE:
        return None
    return value.strip()


@require_http_methods(["GET", "POST"])
@csrf_exempt
def @@SING@@_collection(request):
    if request.method == "GET":
        items = [ _serialize(t) for t in @@MODEL@@.objects.order_by("created_at")[:100] ]
        return JsonResponse({"items": items})

    data = _parse_body(request)
    title = _validate_title(data.get("title")) if data else None
    if title is None:
        return JsonResponse({"error": "title must be a string of 1..200 characters"}, status=400)
    task = @@MODEL@@.objects.create(title=title, done=bool(data.get("done", False)))
    return JsonResponse(_serialize(task), status=201)


@require_http_methods(["GET", "PATCH", "DELETE"])
@csrf_exempt
def @@SING@@_detail(request, pk: int):
    task = get_object_or_404(@@MODEL@@, pk=pk)

    if request.method == "GET":
        return JsonResponse(_serialize(task))

    if request.method == "PATCH":
        data = _parse_body(request)
        if data is None:
            return JsonResponse({"error": "invalid JSON body"}, status=400)
        if "title" in data:
            title = _validate_title(data["title"])
            if title is None:
                return JsonResponse({"error": "title must be a string of 1..200 characters"}, status=400)
            task.title = title
        if "done" in data:
            if not isinstance(data["done"], bool):
                return JsonResponse({"error": "done must be a boolean"}, status=400)
            task.done = data["done"]
        task.save()
        return JsonResponse(_serialize(task))

    task.delete()
    return HttpResponse(status=204)
'''

    urls_app = '''from django.urls import path

from . import views

app_name = "@@APP@@"


urlpatterns = [
    path("@@PLURAL@@/", views.@@SING@@_collection, name="collection"),
    path("@@PLURAL@@/<int:pk>/", views.@@SING@@_detail, name="detail"),
]
'''

    urls_proj = '''from django.contrib import admin
from django.http import JsonResponse
from django.urls import include, path


def health(_request):
    return JsonResponse({"status": "ok", "service": "@@PROJ@@"})


urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/health", health, name="health"),
    path("api/", include("@@APP@@.urls")),
]
'''

    models_py = '''from django.db import models


class @@MODEL@@(models.Model):
    title = models.CharField(max_length=200)
    done = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return self.title
'''

    migration = '''from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="@@MODEL@@",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=200)),
                ("done", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "ordering": ["created_at", "id"],
            },
        ),
    ]
'''

    tests_py = '''from django.test import TestCase


class @@MODEL@@ApiTests(TestCase):
    def test_health(self):
        res = self.client.get("/api/health")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.json()["status"], "ok")

    def test_create_requires_title(self):
        res = self.client.post(
            "/api/@@PLURAL@@/", data='{"title": ""}', content_type="application/json"
        )
        self.assertEqual(res.status_code, 400)

    def test_full_crud_cycle(self):
        res = self.client.post(
            "/api/@@PLURAL@@/", data='{"title": "sample"}', content_type="application/json"
        )
        self.assertEqual(res.status_code, 201)
        item = res.json()

        res = self.client.get(f"/api/@@PLURAL@@/{item['id']}/")
        self.assertEqual(res.json()["title"], "sample")

        res = self.client.patch(
            f"/api/@@PLURAL@@/{item['id']}/", data='{"done": true}', content_type="application/json"
        )
        self.assertTrue(res.json()["done"])

        res = self.client.delete(f"/api/@@PLURAL@@/{item['id']}/")
        self.assertEqual(res.status_code, 204)
        self.assertEqual(self.client.get(f"/api/@@PLURAL@@/{item['id']}/").status_code, 404)
'''

    admin_py = '''from django.contrib import admin

from .models import @@MODEL@@


@admin.register(@@MODEL@@)
class @@MODEL@@Admin(admin.ModelAdmin):
    list_display = ("id", "title", "done", "created_at")
    list_filter = ("done",)
    search_fields = ("title",)
'''

    apps_py = '''from django.apps import AppConfig


class @@APPCLASS@@Config(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "@@APP@@"
'''

    pairs: List[Pair] = [
        (rel_path(project, "requirements.txt"), "django==5.2.18\n"),
        (rel_path(project, "manage.py"), manage_py),
        (rel_path(project, proj_mod, "__init__.py"), ""),
        (rel_path(project, proj_mod, "settings.py"), settings_py),
        (rel_path(project, proj_mod, "urls.py"), urls_proj),
        (rel_path(project, proj_mod, "wsgi.py"), '''import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "@@PROJ@@.settings")

application = get_wsgi_application()
'''),
        (rel_path(project, proj_mod, "asgi.py"), '''import os

from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "@@PROJ@@.settings")

application = get_asgi_application()
'''),
        (rel_path(project, app_mod, "apps.py"), apps_py),
        (rel_path(project, app_mod, "__init__.py"), ""),
        (rel_path(project, app_mod, "models.py"), models_py),
        (rel_path(project, app_mod, "views.py"), views_py),
        (rel_path(project, app_mod, "urls.py"), urls_app),
        (rel_path(project, app_mod, "admin.py"), admin_py),
        (rel_path(project, app_mod, "migrations", "__init__.py"), ""),
        (rel_path(project, app_mod, "migrations", "0001_initial.py"), migration),
        (rel_path(project, app_mod, "tests.py"), tests_py),
        (rel_path(project, ".gitignore"), '''db.sqlite3
__pycache__/
*.pyc
.venv/
.env
staticfiles/
'''),
        (rel_path(project, ".env.example"), '''# Copy to .env. DJANGO_SECRET_KEY is REQUIRED (the app fails closed without it).
DJANGO_SECRET_KEY=
DJANGO_DEBUG=0
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
'''),
        (rel_path(project, "README.md"), '''# @@PROJECT@@ (Django)

Generated scaffold — static source files. Nothing has been installed,
built, and no migration or test has been executed yet.

## Requirements

- Python 3.10+ (Django 5.2 LTS requirement)

## Setup

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Required (fail closed): the app refuses to start without it
export DJANGO_SECRET_KEY=$(python -c 'import secrets; print(secrets.token_urlsafe(50))')
```

## Migrate & run

```sh
python manage.py migrate
python manage.py runserver   # http://localhost:8000
```

## Tests

```sh
python manage.py test
```

## What is inside

- Project `@@PROJ@@` with fail-closed `DJANGO_SECRET_KEY` (settings.py
  raises ImproperlyConfigured when it is missing) and DEBUG=0 default.
- App `@@APP@@`: `@@MODEL@@` model, initial migration, JSON API at
  `/api/@@PLURAL@@/` and `/api/@@PLURAL@@/<id>/`, admin registration,
  and Django TestCase coverage of the API.
- `GET /api/health` liveness endpoint.
- The JSON API is csrf_exempt for machine clients; add authentication,
  throttling, and CSRF/session handling before exposing it publicly.
'''),
    ]
    mapping = {
        "PROJECT": project,
        "PROJ": proj_mod,
        "APP": app_mod,
        "APPCLASS": app_mod.replace("_", " ").title().replace(" ", ""),
        "MODEL": model,
        "PLURAL": app_mod,
        "SING": safe.singularize(app_mod) if hasattr(safe, "singularize") else app_mod.rstrip("s") or app_mod,
    }
    writes = [
        (path, render(content, mapping) if "@@" in content else content)
        for path, content in pairs
    ]

    data = {
        "framework": "django",
        "language": "python",
        "python_min": "3.10",
        "entrypoints": ["manage.py", "%s/urls.py" % proj_mod, "%s/views.py" % app_mod],
        "sample_api": {
            "list": "GET /api/%s/" % app_mod,
            "create": "POST /api/%s/" % app_mod,
            "detail": "GET|PATCH|DELETE /api/%s/<id>/" % app_mod,
            "health": "GET /api/health",
            "admin": "/admin/ (requires python manage.py createsuperuser)",
        },
        "commands": {
            "install": "pip install -r requirements.txt",
            "migrate": "python manage.py migrate",
            "dev": "python manage.py runserver",
            "test": "python manage.py test",
        },
        "dependencies": {"django": "5.2.18"},
        "requires_configuration": [
            "DJANGO_SECRET_KEY must be exported before running (fail-closed).",
        ],
        "database": {"engine": "sqlite", "path": "db.sqlite3 (override DJANGO_DB_PATH)"},
    }
    return {"data": data, "writes": writes, "limitations": [LIMIT_RUN]}


# ---------------------------------------------------------------------------
# 55 scaffold.laravel
# ---------------------------------------------------------------------------

_LARAVEL_ARTISAN = '''#!/usr/bin/env php
<?php

use Symfony\\Component\\Console\\Application;

define('LARAVEL_START', microtime(true));

require __DIR__.'/vendor/autoload.php';

$app = require_once __DIR__.'/bootstrap/app.php';

$status = $app->handleCommand(new Application);

exit($status);
'''

_LARAVEL_BOOTSTRAP_APP = '''<?php

use Illuminate\\Foundation\\Application;
use Illuminate\\Foundation\\Configuration\\Exceptions;
use Illuminate\\Foundation\\Configuration\\Middleware;

return Application::configure(basePath: dirname(__DIR__))
    ->withRouting(
        web: __DIR__.'/../routes/web.php',
        api: __DIR__.'/../routes/api.php',
        commands: __DIR__.'/../routes/console.php',
        health: '/up',
    )
    ->withMiddleware(function (Middleware $middleware) {
        //
    })
    ->withExceptions(function (Exceptions $exceptions) {
        //
    })->create();
'''

_LARAVEL_PUBLIC_INDEX = '''<?php

use Illuminate\\Http\\Request;

define('LARAVEL_START', microtime(true));

require __DIR__.'/../vendor/autoload.php';

$app = require_once __DIR__.'/../bootstrap/app.php';

$app->handleRequest(Request::capture());
'''

_LARAVEL_CONTROLLER = '''<?php

namespace App\\Http\\Controllers;

use App\\Models\\@@MODEL@@;
use Illuminate\\Http\\JsonResponse;
use Illuminate\\Http\\Request;

class @@MODEL@@Controller extends Controller
{
    public function index(): JsonResponse
    {
        return response()->json([
            'items' => @@MODEL@@::query()->orderBy('created_at')->limit(100)->get(),
        ]);
    }

    public function store(Request $request): JsonResponse
    {
        $validated = $request->validate([
            'title' => ['required', 'string', 'min:1', 'max:200'],
            'done' => ['sometimes', 'boolean'],
        ]);

        $item = @@MODEL@@::create([
            'title' => trim($validated['title']),
            'done' => (bool) ($validated['done'] ?? false),
        ]);

        return response()->json(['item' => $item], 201);
    }

    public function show(@@MODEL@@ $@@SING@@): JsonResponse
    {
        return response()->json(['item' => $@@SING@@]);
    }

    public function update(Request $request, @@MODEL@@ $@@SING@@): JsonResponse
    {
        $validated = $request->validate([
            'title' => ['sometimes', 'string', 'min:1', 'max:200'],
            'done' => ['sometimes', 'boolean'],
        ]);

        $@@SING@@->fill($validated)->save();

        return response()->json(['item' => $@@SING@@->fresh()]);
    }

    public function destroy(@@MODEL@@ $@@SING@@)
    {
        $@@SING@@->delete();

        return response()->noContent();
    }
}
'''

_LARAVEL_MODEL = '''<?php

namespace App\\Models;

use Illuminate\\Database\\Eloquent\\Factories\\HasFactory;
use Illuminate\\Database\\Eloquent\\Model;

class @@MODEL@@ extends Model
{
    use HasFactory;

    protected $fillable = ['title', 'done'];

    protected $casts = [
        'done' => 'boolean',
    ];
}
'''

_LARAVEL_FACTORY = '''<?php

namespace Database\\Factories;

use App\\Models\\@@MODEL@@;
use Illuminate\\Database\\Eloquent\\Factories\\Factory;

class @@MODEL@@Factory extends Factory
{
    protected $model = @@MODEL@@::class;

    public function definition(): array
    {
        return [
            'title' => $this->faker->sentence(3),
            'done' => $this->faker->boolean(20),
        ];
    }
}
'''

_LARAVEL_MIGRATION = '''<?php

use Illuminate\\Database\\Migrations\\Migration;
use Illuminate\\Database\\Schema\\Blueprint;
use Illuminate\\Support\\Facades\\Schema;

return new class extends Migration
{
    public function up(): void
    {
        Schema::create('@@PLURAL@@', function (Blueprint $table) {
            $table->id();
            $table->string('title', 200);
            $table->boolean('done')->default(false);
            $table->timestamps();
            $table->index(['done', 'created_at']);
        });
    }

    public function down(): void
    {
        Schema::dropIfExists('@@PLURAL@@');
    }
};
'''

_LARAVEL_SEEDER = '''<?php

namespace Database\\Seeders;

use App\\Models\\@@MODEL@@;
use Illuminate\\Database\\Seeder;

class DatabaseSeeder extends Seeder
{
    public function run(): void
    {
        @@MODEL@@::factory()->count(3)->create();
    }
}
'''

_LARAVEL_FEATURE_TEST = '''<?php

namespace Tests\\Feature;

use Illuminate\\Foundation\\Testing\\RefreshDatabase;
use Tests\\TestCase;

class @@MODEL@@ApiTest extends TestCase
{
    use RefreshDatabase;

    public function test_health_endpoint(): void
    {
        $response = $this->get('/api/health');
        $response->assertOk()->assertJson(['status' => 'ok']);
    }

    public function test_validation_rejects_empty_title(): void
    {
        $response = $this->postJson('/api/@@PLURAL@@', ['title' => '']);
        $response->assertStatus(422)->assertJsonValidationErrors(['title']);
    }

    public function test_crud_cycle(): void
    {
        $created = $this->postJson('/api/@@PLURAL@@', ['title' => 'sample']);
        $created->assertStatus(201)->assertJsonPath('item.title', 'sample');
        $id = $created->json('item.id');

        $this->getJson("/api/@@PLURAL@@/{$id}")->assertJsonPath('item.title', 'sample');

        $this->patchJson("/api/@@PLURAL@@/{$id}", ['done' => true])
            ->assertJsonPath('item.done', true);

        $this->deleteJson("/api/@@PLURAL@@/{$id}")->assertNoContent();
        $this->getJson("/api/@@PLURAL@@/{$id}")->assertNotFound();
    }
}
'''

_LARAVEL_PHPUNIT = '''<?xml version="1.0" encoding="UTF-8"?>
<phpunit xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
         xsi:noNamespaceSchemaLocation="vendor/phpunit/phpunit/phpunit.xsd"
         bootstrap="vendor/autoload.php"
         colors="true"
>
    <testsuites>
        <testsuite name="Feature">
            <directory>tests/Feature</directory>
        </testsuite>
    </testsuites>
    <source>
        <include>app</include>
    </source>
    <php>
        <env name="APP_ENV" value="testing"/>
        <env name="APP_KEY" value="base64:2fl+Ktvkfl+Fuz4Qp/A75G2RTiWVA/ZoKZvp6fiiM10="/>
        <env name="DB_CONNECTION" value="sqlite"/>
        <env name="DB_DATABASE" value=":memory:"/>
        <env name="SESSION_DRIVER" value="array"/>
        <env name="QUEUE_CONNECTION" value="sync"/>
        <env name="CACHE_STORE" value="array"/>
    </php>
</phpunit>
'''


def _laravel(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    resource = args["resource"]
    model = safe.pascal(resource, "resource")
    plural = safe.snake(resource, "resource")
    singular = safe.singularize(plural) if hasattr(safe, "singularize") else (plural[:-1] if plural.endswith("s") and len(plural) > 1 else plural)

    composer = {
        "name": "%s/app" % project,
        "type": "project",
        "description": "Generated Laravel 11 scaffold for %s." % project,
        "require": {
            "php": "^8.2",
            "laravel/framework": "^11.41",
            "laravel/tinker": "^2.10",
        },
        "require-dev": {
            "fakerphp/faker": "^1.23",
            "laravel/pint": "^1.13",
            "phpunit/phpunit": "^11.0.1",
        },
        "autoload": {
            "psr-4": {
                "App\\": "app/",
                "Database\\Factories\\": "database/factories/",
                "Database\\Seeders\\": "database/seeders/",
            }
        },
        "autoload-dev": {"psr-4": {"Tests\\": "tests/"}},
        "scripts": {
            "post-autoload-dump": [
                "Illuminate\\Foundation\\ComposerScripts::postAutoloadDump",
                "@php artisan package:discover --ansi",
            ],
            "test": ["@php artisan test"],
        },
        "config": {
            "optimize-autoloader": True,
            "preferred-install": "dist",
            "sort-packages": True,
        },
        "minimum-stability": "stable",
        "prefer-stable": True,
    }

    pairs: List[Pair] = [
        (rel_path(project, "composer.json"), dump_json(composer)),
        (rel_path(project, "artisan"), _LARAVEL_ARTISAN),
        (rel_path(project, "bootstrap", "app.php"), _LARAVEL_BOOTSTRAP_APP),
        (rel_path(project, "bootstrap", "providers.php"), "<?php\n\nreturn [\n    App\\Providers\\AppServiceProvider::class,\n];\n"),
        (rel_path(project, "public", "index.php"), _LARAVEL_PUBLIC_INDEX),
        (rel_path(project, "app", "Providers", "AppServiceProvider.php"), '''<?php

namespace App\\Providers;

use Illuminate\\Support\\Providers\\AppServiceProvider as ServiceProvider;

class AppServiceProvider extends ServiceProvider
{
    public function register(): void
    {
        //
    }

    public function boot(): void
    {
        //
    }
}
'''),
        (rel_path(project, "routes", "web.php"), '''<?php

use Illuminate\\Support\\Facades\\Route;

Route::get('/', function () {
    return response()->json([
        'app' => '@@PROJECT@@',
        'framework' => 'laravel',
    ]);
});
'''),
        (rel_path(project, "routes", "console.php"), "<?php\n\nuse Illuminate\\Foundation\\Inspiring;\nuse Illuminate\\Support\\Facades\\Artisan;\n\nArtisan::command('inspire', function () {\n    $this->comment(Inspiring::quote());\n})->purpose('Display an inspiring quote');\n"),
        (rel_path(project, "routes", "api.php"), '''<?php

use App\\Http\\Controllers\\@@MODEL@@Controller;
use Illuminate\\Support\\Facades\\Route;

Route::get('/health', function () {
    return response()->json(['status' => 'ok', 'service' => '@@PROJECT@@', 'time' => now()->toIso8601String()]);
});

Route::apiResource('@@PLURAL@@', @@MODEL@@Controller::class);
'''),
        (rel_path(project, "app", "Models", "%s.php" % model), _LARAVEL_MODEL),
        (rel_path(project, "app", "Http", "Controllers", "%sController.php" % model), _LARAVEL_CONTROLLER),
        (rel_path(project, "database", "factories", "%sFactory.php" % model), _LARAVEL_FACTORY),
        (rel_path(project, "database", "seeders", "DatabaseSeeder.php"), _LARAVEL_SEEDER),
        (rel_path(project, "database", "migrations", "2025_01_01_000000_create_%s_table.php" % plural), _LARAVEL_MIGRATION),
        (rel_path(project, "tests", "TestCase.php"), '''<?php

namespace Tests;

use Illuminate\\Foundation\\Testing\\TestCase as BaseTestCase;

abstract class TestCase extends BaseTestCase
{
    //
}
'''),
        (rel_path(project, "tests", "Feature", "%sApiTest.php" % model), _LARAVEL_FEATURE_TEST),
        (rel_path(project, "phpunit.xml"), _LARAVEL_PHPUNIT),
        (rel_path(project, "bootstrap", "cache", ".gitkeep"), ""),
        (rel_path(project, "storage", "app", ".gitkeep"), ""),
        (rel_path(project, "storage", "framework", "cache", "data", ".gitkeep"), ""),
        (rel_path(project, "storage", "framework", "sessions", ".gitkeep"), ""),
        (rel_path(project, "storage", "framework", "views", ".gitkeep"), ""),
        (rel_path(project, "storage", "logs", ".gitkeep"), ""),
        (rel_path(project, ".gitignore"), '''/.phpunit.cache
/node_modules
/public/build
/public/hot
/storage/*.key
/vendor
.env
.env.backup
.env.production
.phpactor.json
.phpunit.result.cache
Homestead.json
Homestead.yaml
auth.json
npm-debug.log
yarn-error.log
/.fleet
/.idea
/.vscode
'''),
        (rel_path(project, ".env.example"), '''APP_NAME=@@PROJECT@@
APP_ENV=local
APP_KEY=
APP_DEBUG=true
APP_URL=http://localhost

DB_CONNECTION=sqlite
DB_DATABASE=database/database.sqlite

SESSION_DRIVER=file
QUEUE_CONNECTION=database
CACHE_STORE=database
'''),
        (rel_path(project, "README.md"), '''# @@PROJECT@@ (Laravel 11)

Generated scaffold — static source files. Composer dependencies are NOT
installed; no artisan command, migration, or test has been executed yet.

## Requirements

- PHP >= 8.2 with extensions: ctype, curl, dom, fileinfo, filter, hash,
  mbstring, openssl, pcre, pdo, session, tokenizer, xml
- Composer >= 2

## Setup

```sh
composer install

# Required: Laravel refuses to run without an APP_KEY
php artisan key:generate

touch database/database.sqlite
php artisan migrate --seed
```

## Run

```sh
php artisan serve    # http://localhost:8000
```

- `GET /api/health` liveness endpoint
- `GET|POST /api/@@PLURAL@@` and `GET|PATCH|DELETE /api/@@PLURAL@@/{id}`
  (apiResource controller with validation)

## Tests

```sh
composer test       # php artisan test (sqlite :memory:)
```

## Notes

- `bootstrap/cache/` and `storage/` are pre-created with `.gitkeep`.
- `phpunit.xml` uses an in-memory sqlite database and a testing APP_KEY
  placeholder; keep real keys only in `.env` (never committed).
'''),
    ]
    mapping = {"PROJECT": project, "MODEL": model, "PLURAL": plural, "SING": singular}
    writes = [
        (path, render(content, mapping) if "@@" in content else content)
        for path, content in pairs
    ]

    data = {
        "framework": "laravel",
        "language": "php",
        "php_min": "8.2",
        "entrypoints": ["public/index.php", "routes/api.php", "app/Http/Controllers/%sController.php" % model],
        "sample_api": {
            "list": "GET /api/%s" % plural,
            "create": "POST /api/%s" % plural,
            "detail": "GET|PATCH|DELETE /api/%s/{id}" % plural,
            "health": "GET /api/health",
        },
        "commands": {
            "install": "composer install",
            "key": "php artisan key:generate",
            "migrate": "php artisan migrate --seed",
            "dev": "php artisan serve",
            "test": "composer test",
        },
        "dependencies": composer["require"],
        "requires_configuration": ["APP_KEY must be generated via `php artisan key:generate`."],
        "database": {"engine": "sqlite (default)", "path": "database/database.sqlite"},
    }
    return {"data": data, "writes": writes, "limitations": [LIMIT_RUN]}


# ---------------------------------------------------------------------------
# 56 scaffold.monorepo
# ---------------------------------------------------------------------------

def _monorepo(args: Dict[str, Any]) -> Dict[str, Any]:
    project = args["project"]
    scope = "@%s" % project

    root_pkg = {
        "name": project,
        "private": True,
        "packageManager": "pnpm@9.15.4",
        "scripts": {
            "build": "turbo run build",
            "dev": "turbo run dev",
            "lint": "turbo run lint",
            "test": "turbo run test",
        },
        "devDependencies": npm(**{"turbo": "^2.3.3"}),
    }

    pairs: List[Pair] = [
        (rel_path(project, "package.json"), dump_json(root_pkg)),
        (rel_path(project, "turbo.json"), '''{
  "$schema": "https://turbo.build/schema.json",
  "tasks": {
    "build": {
      "dependsOn": ["^build"],
      "outputs": ["dist/**", ".next/**", "!.next/cache/**"]
    },
    "dev": {
      "cache": false,
      "persistent": true
    },
    "lint": {},
    "test": {
      "dependsOn": ["^build"]
    }
  }
}
'''),
        (rel_path(project, "pnpm-workspace.yaml"), '''packages:
  - "apps/*"
  - "packages/*"
'''),
        (rel_path(project, ".gitignore"), GITIGNORE_NODE + ".turbo/\n"),
        (rel_path(project, "README.md"), '''# @@PROJECT@@ (Turborepo monorepo)

Generated scaffold — static source files. Nothing has been installed or
built yet.

## Requirements

- Node.js >= 20
- pnpm >= 9 (enabled via `packageManager`; corepack is preinstalled on
  Node 20: `corepack enable`)

## Run

```sh
pnpm install
pnpm dev            # runs apps/web (Next.js) and apps/api (Express) in parallel
pnpm build
pnpm test
```

## Layout

- `apps/web` — Next.js 16 frontend (fetches the API at runtime).
- `apps/api` — Express 4 JSON API (`/api/health`, `/api/items`).
- `packages/shared` — TypeScript types and helpers shared by both apps
  (`@@SCOPE@@/shared`).

The web app proxies `/api/*` to the API during development via
`rewrites()` in `apps/web/next.config.ts` (API must run on port 4000).
'''),
        # packages/shared
        (rel_path(project, "packages", "shared", "package.json"), dump_json({
            "name": "%s/shared" % scope,
            "version": "0.1.0",
            "private": True,
            "main": "./src/index.ts",
            "types": "./src/index.ts",
            "scripts": {"build": "tsc --project tsconfig.json", "lint": "tsc --noEmit"},
            "devDependencies": npm(**{"typescript": "^5.7.3"}),
        })),
        (rel_path(project, "packages", "shared", "tsconfig.json"), '''{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "Bundler",
    "strict": true,
    "declaration": true,
    "outDir": "dist",
    "skipLibCheck": true
  },
  "include": ["src"]
}
'''),
        (rel_path(project, "packages", "shared", "src", "index.ts"), '''export interface Item {
  id: string;
  title: string;
  done: boolean;
  createdAt: string;
}

export interface HealthReport {
  status: "ok";
  service: string;
  time: string;
}

export const API_BASE = "/api";

export function normalizeTitle(input: string): string {
  return input.trim();
}

export function isValidTitle(input: unknown): input is string {
  return typeof input === "string" && input.trim().length > 0 && input.length <= 200;
}
'''),
        # apps/api
        (rel_path(project, "apps", "api", "package.json"), dump_json(_pkg(
            "%s/api" % scope,
            {
                "dev": "tsx watch src/index.ts",
                "build": "tsc --project tsconfig.json",
                "start": "node dist/index.js",
                "lint": "tsc --noEmit",
                "test": "vitest run",
            },
            npm(**{"%s/shared" % scope: "workspace:*", "express": "^4.21.2"}),
            npm(**{"@types/express": "^5.0.0", "@types/node": "^22.10.10",
                   "tsx": "^4.19.2", "typescript": "^5.7.3", "vitest": "4.1.12"}),
        ))),
        (rel_path(project, "apps", "api", "tsconfig.json"), '''{
  "compilerOptions": {
    "target": "ES2022",
    "module": "NodeNext",
    "moduleResolution": "NodeNext",
    "strict": true,
    "outDir": "dist",
    "rootDir": "src",
    "skipLibCheck": true,
    "types": ["node"]
  },
  "include": ["src"]
}
'''),
        (rel_path(project, "apps", "api", "src", "index.ts"), render('''import express from "express";
import type { HealthReport, Item } from "@@SCOPE@@/shared";

const PORT = Number.parseInt(process.env.PORT ?? "4000", 10);
const app = express();
app.use(express.json({ limit: "64kb" }));

const items = new Map<string, Item>();

app.get("/api/health", (_req, res): void => {
  const report: HealthReport = {
    status: "ok",
    service: "@@PROJECT@@-api",
    time: new Date().toISOString(),
  };
  res.json(report);
});

app.get("/api/items", (_req, res) => {
  res.json({ items: [...items.values()] });
});

app.post("/api/items", (req, res) => {
  const title = req.body?.title;
  if (title !== undefined && typeof title !== "string") {
    res.status(400).json({ error: "title must be a string" });
    return;
  }
  const item: Item = {
    id: crypto.randomUUID(),
    title: String(title ?? "").trim(),
    done: false,
    createdAt: new Date().toISOString(),
  };
  items.set(item.id, item);
  res.status(201).json({ item });
});

app.listen(PORT, () => {
  console.log(JSON.stringify({ level: "info", msg: "api listening", port: PORT }));
});
''', {"PROJECT": project, "SCOPE": scope})),
        (rel_path(project, "apps", "api", "test", "api.test.ts"), '''import { describe, expect, it } from "vitest";
import { isValidTitle } from "@@SCOPE@@/shared";

describe("shared validation", () => {
  it("accepts valid titles", () => {
    expect(isValidTitle("hello")).toBe(true);
  });

  it("rejects empty and oversized titles", () => {
    expect(isValidTitle("")).toBe(false);
    expect(isValidTitle("x".repeat(201))).toBe(false);
    expect(isValidTitle(42)).toBe(false);
  });
});
'''.replace("@@SCOPE@@", scope)),
        (rel_path(project, "apps", "api", "vitest.config.ts"), VITEST_CONFIG_TS),
        # apps/web
        (rel_path(project, "apps", "web", "package.json"), dump_json(_pkg(
            "%s/web" % scope,
            {
                "dev": "next dev -p 3000",
                "build": "next build",
                "start": "next start",
                "lint": "tsc --noEmit",
            },
            npm(**{"%s/shared" % scope: "workspace:*", "next": "16.4.3",
                   "react": "19.3.1", "react-dom": "19.3.1"}),
            npm(**{"@types/node": "^22.10.10", "@types/react": "^19.0.8",
                   "@types/react-dom": "^19.0.3", "typescript": "^5.7.3"}),
        ))),
        (rel_path(project, "apps", "web", "next.config.ts"), render('''import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  transpilePackages: ["@@SCOPE@@/shared"],
  async rewrites() {
    // Forward API calls to apps/api during development.
    const apiPort = process.env.API_PORT ?? "4000";
    return [{ source: "/api/:path*", destination: `http://localhost:${apiPort}/api/:path*` }];
  },
};

export default nextConfig;
''', {"SCOPE": scope})),
        (rel_path(project, "apps", "web", "tsconfig.json"), '''{
  "compilerOptions": {
    "target": "ES2017",
    "lib": ["dom", "dom.iterable", "esnext"],
    "allowJs": true,
    "skipLibCheck": true,
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "module": "esnext",
    "moduleResolution": "bundler",
    "resolveJsonModule": true,
    "isolatedModules": true,
    "jsx": "preserve",
    "incremental": true,
    "plugins": [{"name": "next"}]
  },
  "include": ["next-env.d.ts", "**/*.ts", "**/*.tsx", ".next/types/**/*.ts"],
  "exclude": ["node_modules"]
}
'''),
        (rel_path(project, "apps", "web", "app", "layout.tsx"), '''import type { Metadata } from "next";

export const metadata: Metadata = {
  title: "@@PROJECT@@",
  description: "Monorepo web app for @@PROJECT@@.",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body style={{ fontFamily: "system-ui, sans-serif", margin: "2rem", maxWidth: "48rem" }}>
        {children}
      </body>
    </html>
  );
}
'''),
        (rel_path(project, "apps", "web", "app", "page.tsx"), render('''import { HealthCheck } from "../components/HealthCheck";

export default function Home() {
  return (
    <main>
      <h1>@@PROJECT@@</h1>
      <p>Monorepo web app. The API runs in <code>apps/api</code>.</p>
      <HealthCheck />
    </main>
  );
}
''', {"PROJECT": project})),
        (rel_path(project, "apps", "web", "components", "HealthCheck.tsx"), '''"use client";

import { useCallback, useEffect, useState } from "react";
import type { HealthReport } from "@@SCOPE@@/shared";

export function HealthCheck() {
  const [report, setReport] = useState<HealthReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  const check = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/health");
      if (!res.ok) throw new Error("bad status");
      setReport((await res.json()) as HealthReport);
    } catch {
      setError("API unreachable (is apps/api running on :4000?)");
    }
  }, []);

  useEffect(() => {
    void check();
  }, [check]);

  return (
    <section>
      {error ? <p role="alert">{error}</p> : null}
      {report ? <p>API status: {report.status} at {report.time}</p> : <p>checking API…</p>}
    </section>
  );
}
'''.replace("@@SCOPE@@", scope)),
    ]
    mapping = {"PROJECT": project, "SCOPE": scope}
    writes = [
        (path, render(content, mapping) if "@@" in content else content)
        for path, content in pairs
    ]

    data = {
        "framework": "monorepo-turborepo",
        "language": "typescript",
        "package_manager": "pnpm",
        "apps": ["apps/web (Next.js 16)", "apps/api (Express 4)"],
        "packages": ["packages/shared (@%s/shared)" % project],
        "commands": {
            "install": "corepack enable && pnpm install",
            "dev": "pnpm dev",
            "build": "pnpm build",
            "test": "pnpm test",
        },
        "dependencies": root_pkg["devDependencies"],
    }
    return {"data": data, "writes": writes, "limitations": [LIMIT_RUN]}


SKILLS: Dict[str, Dict[str, Any]] = {}


def _register(skill_id: str, number: int, summary: str, schema: Schema,
              handler: Any) -> None:
    SKILLS[skill_id] = {
        "number": number,
        "summary": summary,
        "schema": schema,
        "handler": handler,
    }


_register("scaffold.nextjs", 47, "Scaffold a runnable Next.js (App Router, TypeScript) project", {
    "type": "object",
    "additionalProperties": False,
    "required": ["project"],
    "properties": {
        "project": _SLUG_S,
        "resource": dict(_ROUTE_S, **{"default": "tasks"}),
        "port": _PORT_S,
        "tailwind": {"type": "boolean", "default": False},
    },
}, _nextjs)

_register("scaffold.nuxt", 48, "Scaffold a runnable Nuxt 3 project", {
    "type": "object",
    "additionalProperties": False,
    "required": ["project"],
    "properties": {
        "project": _SLUG_S,
        "resource": dict(_ROUTE_S, **{"default": "tasks"}),
        "port": _PORT_S,
    },
}, _nuxt)

_register("scaffold.sveltekit", 49, "Scaffold a runnable SvelteKit 2 (Svelte 5, node adapter) project", {
    "type": "object",
    "additionalProperties": False,
    "required": ["project"],
    "properties": {
        "project": _SLUG_S,
        "resource": dict(_ROUTE_S, **{"default": "tasks"}),
        "port": _PORT_S,
    },
}, _sveltekit)

_register("scaffold.remix", 50, "Scaffold a runnable Remix 2 (Vite) project", {
    "type": "object",
    "additionalProperties": False,
    "required": ["project"],
    "properties": {
        "project": _SLUG_S,
        "resource": dict(_ROUTE_S, **{"default": "tasks"}),
        "port": _PORT_S,
    },
}, _remix)

_register("scaffold.express", 51, "Scaffold a runnable Express 4 API (ESM, node:test)", {
    "type": "object",
    "additionalProperties": False,
    "required": ["project"],
    "properties": {
        "project": _SLUG_S,
        "resource": dict(_ROUTE_S, **{"default": "tasks"}),
        "port": _PORT_S,
    },
}, _express)

_register("scaffold.nestjs", 52, "Scaffold a runnable NestJS 10 project with class-validator DTOs", {
    "type": "object",
    "additionalProperties": False,
    "required": ["project"],
    "properties": {
        "project": _SLUG_S,
        "resource": dict(_ROUTE_S, **{"default": "tasks"}),
        "port": _PORT_S,
    },
}, _nestjs)

_register("scaffold.fastapi", 53, "Scaffold a runnable FastAPI + SQLite API with bundled frontend and tests", {
    "type": "object",
    "additionalProperties": False,
    "required": ["project"],
    "properties": {
        "project": _SLUG_S,
        "resource": dict(_ROUTE_S, **{"default": "tasks"}),
        "port": _PORT_S,
    },
}, _fastapi)

_register("scaffold.django", 54, "Scaffold a runnable Django 5.2 LTS project with JSON API and tests", {
    "type": "object",
    "additionalProperties": False,
    "required": ["project"],
    "properties": {
        "project": _SLUG_S,
        "resource": dict(_ROUTE_S, **{"default": "tasks"}),
        "port": _PORT_S,
    },
}, _django)

_register("scaffold.laravel", 55, "Scaffold a runnable Laravel 11 project with apiResource controller", {
    "type": "object",
    "additionalProperties": False,
    "required": ["project"],
    "properties": {
        "project": _SLUG_S,
        "resource": dict(_ROUTE_S, **{"default": "tasks"}),
        "port": _PORT_S,
    },
}, _laravel)

_register("scaffold.monorepo", 56, "Scaffold a runnable Turborepo monorepo (Next.js web + Express api + shared package)", {
    "type": "object",
    "additionalProperties": False,
    "required": ["project"],
    "properties": {
        "project": _SLUG_S,
        "resource": dict(_ROUTE_S, **{"default": "tasks"}),
        "port": _PORT_S,
    },
}, _monorepo)
