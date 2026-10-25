"""Harmonika mobile engine: bounded offline SOURCE generators.

This module is a pure, offline source generator for mobile app starter
projects and modules. It writes nothing to disk, opens no sockets, and runs
no subprocesses. ``dispatch()`` returns generated file contents in memory.

Honest scope (read before use):

- Mode is always ``source_generation`` with ``runtime_verified: false``.
  Nothing generated here has been compiled, installed, or executed.
- Expo core uses a verified pinned baseline. Other dependency ranges must
  be resolved and locked; README/warnings delegate native setup to
  the official tools (``npx expo install --fix``, ``flutter create .``,
  Android Studio / ``gradle wrapper``, ``xcodegen generate``).
- Native artifacts (``.xcodeproj``, Gradle wrapper jar, platform folders of
  Flutter, keystores, provisioning profiles, IPA/APK/AAB binaries) are NOT
  and CANNOT be produced here; the matching skills say so explicitly.
- No secrets, tokens, or credentials are embedded in generated output.
- ``scaffold.react_native_cli`` is registered as explicitly UNSUPPORTED:
  a bare React Native project requires the official CLI template for its
  native ``ios/`` + ``android/`` folders, which cannot be faked.

Public interface:
    SUPPORTED_FRAMEWORKS         tuple of framework keys
    descriptors()                dict skill_id -> {description, input_schema}
    support(skill_id)            bool, True when dispatch can complete it
    dispatch(skill_id, input)    {status, mode, files, warnings, runtime_verified}
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlparse

__all__ = [
    "SUPPORTED_FRAMEWORKS",
    "descriptors",
    "dispatch",
    "support",
    "GeneratorError",
    "UnknownSkillError",
    "UnsupportedSkillError",
    "ValidationError",
]

SUPPORTED_FRAMEWORKS: Tuple[str, ...] = ("expo", "flutter", "kotlin", "swift", "ionic")

DEFAULT_NAME = "Harmonika App"
DEFAULT_IDENTIFIER = "id.harmonika.app"
DEFAULT_API_BASE_URL = "https://api.example.com"

_BASE_NOTE = (
    "Source generation only: nothing was compiled, installed, or run "
    "(runtime_verified=false). Review generated code before production use."
)


class GeneratorError(ValueError):
    """Base class for all generator failures."""


class UnknownSkillError(GeneratorError):
    """Raised when the skill id is not in the registry."""


class UnsupportedSkillError(GeneratorError):
    """Raised when a registered skill cannot produce complete output."""


class ValidationError(GeneratorError):
    """Raised when user input fails strict validation."""


# ---------------------------------------------------------------------------
# Text validation helpers
# ---------------------------------------------------------------------------

_FORBIDDEN_NAME_CHARS = set('/\\<>`"$;&|\x00')

_HUMPS_RE = re.compile(r"[A-Z][a-z0-9]*|[a-z0-9]+")
_PASCAL_RE = re.compile(r"[A-Z][A-Za-z0-9]{0,63}")
_SNAKE_RE = re.compile(r"[a-z][a-z0-9_]{0,31}")
_IDENTIFIER_RE = re.compile(r"[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+")
_FIELD_TYPES = ("text", "email", "password", "number", "multiline")
_PERMISSION_KEYS = ("camera", "media_library", "location", "notifications")


def _no_control(value: str) -> bool:
    return all(ord(ch) >= 32 for ch in value)


def _split_humps(value: str) -> List[str]:
    return _HUMPS_RE.findall(value)


def _snake(value: str) -> str:
    return "_".join(w.lower() for w in _split_humps(value))


def _kebab(value: str) -> str:
    return "-".join(w.lower() for w in _split_humps(value))


def _pascal_words(value: str) -> str:
    return "".join(w.capitalize() for w in _split_humps(value))


def _clean_text(value: Any, field: str, min_len: int = 1, max_len: int = 200) -> str:
    if not isinstance(value, str):
        raise ValidationError("%s must be a string" % field)
    stripped = value.strip()
    if len(stripped) < min_len:
        raise ValidationError("%s must be at least %d character(s)" % (field, min_len))
    if len(value) > max_len:
        raise ValidationError("%s must be at most %d characters" % (field, max_len))
    if any(ch in "\\{}$`" for ch in stripped):
        raise ValidationError("unsafe text metacharacter")
    if not _no_control(stripped):
        raise ValidationError("%s must not contain control characters" % field)
    return stripped


def _validate_name(value: Any) -> str:
    name = _clean_text(value, "name", 1, 64)
    if name in (".", ".."):
        raise ValidationError("name must not be '.' or '..'")
    if any(not (ch.isalnum() or ch in " ._-()") for ch in name):
        raise ValidationError("name must not contain any of: / \\ < > ` \" or control characters")
    if not any(ch.isalnum() for ch in name):
        raise ValidationError("name must contain at least one letter or digit")
    return name


def _validate_identifier(value: Any) -> str:
    ident = _clean_text(value, "identifier", 3, 100)
    if not _IDENTIFIER_RE.fullmatch(ident):
        raise ValidationError(
            "identifier must be reverse-DNS like 'id.harmonika.app' (lowercase "
            "letters and digits; segments of 2+ dot-separated labels)"
        )
    segments = ident.split(".")
    if any(x in {"class","when","object","interface","package","import","public","private","protected","static","void","int","boolean","switch","case","default","new","return","this","super","null","true","false","val","var","fun","typealias"} for x in segments):
        raise ValidationError("identifier contains a reserved language keyword")
    if len(segments) > 8:
        raise ValidationError("identifier must have at most 8 dot-separated segments")
    return ident


def _validate_pascal(value: Any, field: str) -> str:
    text = _clean_text(value, field, 1, 64)
    if not _PASCAL_RE.fullmatch(text):
        raise ValidationError("%s must be PascalCase (e.g. 'HomeScreen'): uppercase first letter, letters/digits only" % field)
    return text


def _validate_https_url(value: Any, field: str = "api_base_url") -> str:
    raw = _clean_text(value, field, 1, 2048)
    if any(ch.isspace() or ch in "\"'`$\\{}<>" for ch in raw):
        raise ValidationError("%s must not contain whitespace" % field)
    parsed = urlparse(raw)
    if parsed.scheme != "https":
        raise ValidationError("%s must use the https scheme" % field)
    if not parsed.netloc or not parsed.hostname:
        raise ValidationError("%s must include a host" % field)
    if parsed.username or parsed.password or "@" in raw:
        raise ValidationError("%s must not embed credentials (user:pass@host)" % field)
    try:
        if parsed.port is not None and not 1 <= parsed.port <= 65535:raise ValidationError("invalid port")
    except ValueError:raise ValidationError("invalid port")
    if parsed.query or parsed.fragment:
        raise ValidationError("%s must not include a query string or fragment" % field)
    if any(ord(ch) < 32 for ch in raw):
        raise ValidationError("%s must not contain control characters" % field)
    return raw.rstrip("/")


def _validate_framework(value: Any, allowed: Tuple[str, ...]) -> str:
    if not isinstance(value, str):
        raise ValidationError("framework must be a string")
    fw = value.strip().lower()
    if fw not in SUPPORTED_FRAMEWORKS:
        raise ValidationError(
            "unsupported framework %r; supported frameworks: %s"
            % (value, ", ".join(SUPPORTED_FRAMEWORKS))
        )
    if fw not in allowed:
        raise ValidationError(
            "this skill does not support framework %r (supported: %s)"
            % (fw, ", ".join(allowed))
        )
    return fw


def _validate_fields(value: Any) -> List[Dict[str, Any]]:
    if not isinstance(value, list):
        raise ValidationError("fields must be a list")
    if not 1 <= len(value) <= 12:
        raise ValidationError("fields must contain between 1 and 12 entries")
    out: List[Dict[str, Any]] = []
    seen = set()
    for idx, item in enumerate(value):
        if not isinstance(item, dict):
            raise ValidationError("fields[%d] must be an object" % idx)
        unknown = sorted(set(item) - {"name", "label", "type", "required"})
        if unknown:
            raise ValidationError(
                "fields[%d] has unknown key(s): %s (allowed: label, name, required, type)"
                % (idx, ", ".join(unknown))
            )
        fname = _clean_text(item.get("name"), "fields[%d].name" % idx, 1, 32)
        if not _SNAKE_RE.fullmatch(fname):
            raise ValidationError(
                "fields[%d].name must be snake_case (lowercase letters, digits, underscores; starts with a letter)" % idx
            )
        if fname in seen:
            raise ValidationError("fields[%d].name %r is duplicated" % (idx, fname))
        seen.add(fname)
        label = _clean_text(
            item.get("label", _pascal_words(fname) if not fname.isupper() else fname.capitalize()),
            "fields[%d].label" % idx, 1, 80,
        )
        ftype = item.get("type", "text")
        if ftype not in _FIELD_TYPES:
            raise ValidationError(
                "fields[%d].type must be one of: %s" % (idx, ", ".join(_FIELD_TYPES))
            )
        required = item.get("required", True)
        if not isinstance(required, bool):
            raise ValidationError("fields[%d].required must be a boolean" % idx)
        out.append({"name": fname, "label": label, "type": ftype, "required": required})
    return out


def _validate_pascal_list(value: Any, field: str, low: int, high: int) -> List[str]:
    if not isinstance(value, list):
        raise ValidationError("%s must be a list" % field)
    if not low <= len(value) <= high:
        raise ValidationError("%s must contain between %d and %d entries" % (field, low, high))
    out = []
    for idx, item in enumerate(value):
        out.append(_validate_pascal(item, "%s[%d]" % (field, idx)))
    if len(set(out)) != len(out):
        raise ValidationError("%s must not contain duplicates" % field)
    return out


def _validate_permissions(value: Any) -> List[str]:
    if not isinstance(value, list):
        raise ValidationError("permissions must be a list")
    if not value:
        raise ValidationError("permissions must not be empty")
    out = []
    for item in value:
        if item not in _PERMISSION_KEYS:
            raise ValidationError(
                "permissions entries must be one of: %s" % ", ".join(_PERMISSION_KEYS)
            )
        if item not in out:
            out.append(item)
    return out


def _pascal_sfx(pascal: str, suffix: str) -> str:
    """PascalCase name with a suffix, collapsing the overlapping words.

    'Catalog' + 'ListScreen' -> 'CatalogListScreen';
    'CatalogList' + 'ListScreen' -> 'CatalogListScreen';
    'HomeScreen' + 'Screen' -> 'HomeScreen'.
    """

    if pascal.endswith(suffix):
        return pascal
    p_words = _split_humps(pascal)
    s_words = _split_humps(suffix)
    overlap = 0
    for k in range(min(len(p_words), len(s_words)), 0, -1):
        if p_words[-k:] == s_words[:k]:
            overlap = k
            break
    return "".join(p_words[: len(p_words) - overlap] + s_words) or pascal


def _snake_sfx(snake: str, suffix: str) -> str:
    """snake_case file base with a suffix, collapsing the overlapping words."""

    parts = snake.split("_")
    s_parts = suffix.split("_")
    overlap = 0
    for k in range(min(len(parts), len(s_parts)), 0, -1):
        if parts[-k:] == s_parts[:k]:
            overlap = k
            break
    return "_".join(parts[: len(parts) - overlap] + s_parts) or snake


# ---------------------------------------------------------------------------
# Derived names / shared context
# ---------------------------------------------------------------------------


def _derive_slug(name: str) -> str:
    lowered = name.lower()
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    slug = re.sub(r"-{2,}", "-", slug)[:40].strip("-")
    if not slug:
        slug = "harmonika-app"
    if slug[0].isdigit():
        slug = "app-" + slug
    return slug


def _derive_app_pascal(name: str) -> str:
    words = _split_humps(name)
    if not words:
        words = ["harmonika", "app"]
    pascal = "".join(w.capitalize() for w in words if w.isalnum() or w.isalpha())
    pascal = re.sub(r"[^A-Za-z0-9]", "", pascal)[:40]
    if not pascal or not pascal[0].isalpha() or not pascal[0].isupper():
        pascal = "HarmonikaApp"
    return pascal


class _Ctx(object):
    """Validated generation context shared by all builders."""

    def __init__(
        self,
        skill_id: str,
        name: str,
        identifier: str,
        framework: str,
        screen_name: Optional[str],
        api_base_url: str,
        extras: Dict[str, Any],
    ) -> None:
        self.skill_id = skill_id
        self.name = name
        self.slug = _derive_slug(name)
        self.dart_name = self.slug.replace("-", "_")
        if self.dart_name in {"class","enum","void","int","double","if","else","for","switch","case","default","return","import","export","library","part","final","var","const","abstract","extends","implements","async","await","yield","null","true","false","flutter","dart","test"}:
            self.dart_name = "app_" + self.dart_name
        self.app_pascal = _derive_app_pascal(name)
        self.identifier = identifier
        self.framework = framework
        self.screen_name = screen_name or "Home"
        self.screen_snake = _snake(self.screen_name)
        self.screen_kebab = _kebab(self.screen_name)
        self.screen_words = " ".join(w.capitalize() for w in _split_humps(self.screen_name))
        self.api_base_url = api_base_url or DEFAULT_API_BASE_URL
        self.scheme = self.slug.replace("_", "-")
        self.extras = extras


_BASE_PROPERTIES: Dict[str, Dict[str, Any]] = {
    "name": {
        "type": "string",
        "description": "Application display name.",
        "default": DEFAULT_NAME,
        "minLength": 1,
        "maxLength": 64,
    },
    "identifier": {
        "type": "string",
        "description": "Reverse-DNS application identifier used as Android package / iOS bundle id.",
        "default": DEFAULT_IDENTIFIER,
        "pattern": "^[a-z][a-z0-9_]*(\\.[a-z][a-z0-9_]*)+$",
    },
    "framework": {
        "type": "string",
        "description": "Target framework. Skills reject frameworks they cannot generate.",
        "enum": list(SUPPORTED_FRAMEWORKS),
        "default": "expo",
    },
    "screen_name": {
        "type": "string",
        "description": "PascalCase screen/component name (letters and digits, first letter uppercase).",
        "pattern": "^[A-Z][A-Za-z0-9]{0,63}$",
    },
    "api_base_url": {
        "type": "string",
        "description": "HTTPS base URL embedded in generated source only; this generator never fetches it.",
        "pattern": "^https://",
    },
}

_BASE_PROPERTIES['identifier']['pattern'] = r'^[a-z][a-z0-9]*(?:\.[a-z][a-z0-9]*)+$'
_BASE_PROPERTIES['identifier']['maxLength'] = 100

_BASE_KEYS = {"name", "identifier", "framework", "screen_name", "api_base_url"}

_EXTRA_VALIDATORS: Dict[str, Callable[[Any], Any]] = {
    "fields": _validate_fields,
    "tabs": lambda v: _validate_pascal_list(v, "tabs", 2, 5),
    "items": lambda v: _validate_pascal_list(v, "items", 2, 6),
    "message": lambda v: _clean_text(v, "message", 1, 200),
    "permissions": _validate_permissions,
}


def _obj_schema(description: str, properties: Dict[str, Dict[str, Any]], required: Optional[List[str]] = None) -> Dict[str, Any]:
    schema: Dict[str, Any] = {
        "type": "object",
        "description": description,
        "properties": properties,
        "additionalProperties": False,
    }
    if required:
        schema["required"] = required
    return schema


def _prop(key: str) -> Dict[str, Any]:
    return copy.deepcopy(_BASE_PROPERTIES[key])


def _fill(template: str, mapping: Dict[str, str]) -> str:
    out = template
    for token, value in mapping.items():
        out = out.replace(token, value)
    if "@@" in out:
        leftover = sorted(set(re.findall(r"@@[A-Z0-9_]+@@", out)))
        raise GeneratorError("internal: unresolved placeholder(s) %s" % ", ".join(leftover))
    return out


# ---------------------------------------------------------------------------
# Shared TypeScript theme kit (Expo / React Native)
# ---------------------------------------------------------------------------

_TS_HEADER = "// Generated by Harmonika mobile source generators (offline).\n// Source only: this file has NOT been compiled or executed. Review before use.\n"

_TS_THEME = _TS_HEADER + r"""import { createContext, useContext, useMemo } from 'react';
import { ColorSchemeName, useColorScheme } from 'react-native';

/**
 * Central design tokens for the app. Colors follow the system color scheme
 * by default; `mobile.theme.dark_mode` adds a persisted manual override.
 */
export const palette = {
  light: {
    background: '#FFFFFF',
    surface: '#F5F7FB',
    text: '#0F172A',
    textMuted: '#5B6472',
    primary: '#2563EB',
    onPrimary: '#FFFFFF',
    border: '#E2E8F0',
    danger: '#DC2626',
    success: '#16A34A',
  },
  dark: {
    background: '#0B1220',
    surface: '#151E2E',
    text: '#F1F5F9',
    textMuted: '#94A3B8',
    primary: '#60A5FA',
    onPrimary: '#0B1220',
    border: '#243044',
    danger: '#F87171',
    success: '#4ADE80',
  },
} as const;

export type ThemeColors = { [K in keyof typeof palette.light]: string };

export const spacing = { xs: 4, sm: 8, md: 16, lg: 24, xl: 32 } as const;
export const radius = { sm: 8, md: 12, lg: 20, full: 999 } as const;
export const typography = {
  title: { fontSize: 22, fontWeight: '700' as const },
  heading: { fontSize: 18, fontWeight: '600' as const },
  body: { fontSize: 16, fontWeight: '400' as const },
  caption: { fontSize: 13, fontWeight: '500' as const },
};

export interface ThemeValue {
  colors: ThemeColors;
  scheme: 'light' | 'dark';
  spacing: typeof spacing;
  radius: typeof radius;
  typography: typeof typography;
}

const ThemeContext = createContext<ThemeValue | null>(null);

export function themeForScheme(scheme: ColorSchemeName | null): ThemeValue {
  const dark = scheme === 'dark';
  return {
    colors: dark ? palette.dark : palette.light,
    scheme: dark ? 'dark' : 'light',
    spacing,
    radius,
    typography,
  };
}

/**
 * Reads the nearest ThemeProvider. Falls back to the OS color scheme so
 * every generated module works standalone before the provider is mounted.
 */
export function useTheme(): ThemeValue {
  const provided = useContext(ThemeContext);
  const systemScheme = useColorScheme();
  const fallback = useMemo(() => themeForScheme(systemScheme), [systemScheme]);
  return provided ?? fallback;
}

export { ThemeContext };
"""

# ---------------------------------------------------------------------------
# Expo scaffold builder
# ---------------------------------------------------------------------------


def _build_expo_scaffold(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    package_json = {
        "name": ctx.slug,
        "version": "0.1.0",
        "private": True,
        "main": "index.ts",
        "scripts": {
            "start": "expo start",
            "android": "expo start --android",
            "ios": "expo start --ios",
            "web": "expo start --web",
        },
        "dependencies": {
            "expo": "58.0.19",
            "expo-status-bar": "58.0.4",
            "react": "19.3.0",
            "react-native": "0.88.0",
        },
        "devDependencies": {
            "@types/react": "19.3.0",
            "typescript": "5.9.3",
        },
    }
    app_json = {
        "expo": {
            "name": ctx.name,
            "slug": ctx.slug,
            "version": "1.0.0",
            "orientation": "portrait",
            "scheme": ctx.scheme,
            "userInterfaceStyle": "automatic",
            "ios": {"supportsTablet": True, "bundleIdentifier": ctx.identifier},
            "android": {"package": ctx.identifier},
            "web": {"bundler": "metro", "output": "single"},
        }
    }
    import json as _json

    app_tsx = _fill(
        _TS_HEADER
            + r"""import { StatusBar } from 'expo-status-bar';
import { Text, View, StyleSheet } from 'react-native';
import { useTheme, spacing, typography } from './src/theme/theme';

export default function App() {
  const { colors, typography: type } = useTheme();
  return (
    <View style={[styles.root, { backgroundColor: colors.background }]}>
      <StatusBar style="auto" />
      <Text style={[type.title, { color: colors.text }]}>@@NAME@@</Text>
      <Text style={[styles.subtitle, { color: colors.textMuted }]}>
        Expo starter generated by Harmonika. Edit src/ to build your app.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: spacing.lg,
  },
  subtitle: {
    marginTop: spacing.sm,
    textAlign: 'center',
    fontSize: typography.body.fontSize,
  },
});
""",
        {"@@NAME@@": ctx.name},
    )

    readme = _fill(
        r"""# @@NAME@@ (Expo)

Expo starter source generated offline by Harmonika mobile source generators.
**No command below has been run for you; nothing was compiled or installed.**

## Prerequisites

- Node.js LTS (see https://nodejs.org)
- Expo Go app on a device, or Android Studio / Xcode for native builds

## Install

```sh
npm install
npx expo install --fix
```

Expo 58.0.19 / React 19.3.0 / React Native 0.88.0 are pinned from the official
Expo bundledNativeModules baseline. Review dependency updates and commit the
lockfile after installation. No native build has been performed.

## Run

```sh
npx expo start            # dev server + Expo Go
npx expo run:android      # native debug build (requires Android SDK)
npx expo run:ios          # native debug build (requires macOS + Xcode)
```

## Build for stores

```sh
npx eas build --platform android --profile preview
npx eas build --platform all --profile production
```

EAS requires an Expo account and project configuration (`eas init`).

## Tests

None are included. This generator does not create or run test suites.

## Notes

- App identifier: `@@IDENT@@` (set in `app.json`).
- Generated files: `App.tsx`, `index.ts`, `src/theme/theme.ts`.
- Expo documentation: https://docs.expo.dev
""",
        {"@@NAME@@": ctx.name, "@@IDENT@@": ctx.identifier},
    )

    index_ts = _TS_HEADER + r"""import { registerRootComponent } from 'expo';

import App from './App';

// Registers the root component for both Expo Go and native builds.
registerRootComponent(App);
"""

    gitignore = r"""node_modules/
.expo/
dist/
web-build/
*.orig.*
*.jks
*.p8
*.p12
*.key
*.mobileprovision
android/
ios/
"""

    files = {
        "App.tsx": app_tsx,
        "index.ts": index_ts,
        "app.json": _json.dumps(app_json, indent=2, sort_keys=True) + "\n",
        "package.json": _json.dumps(package_json, indent=2, sort_keys=True) + "\n",
        "tsconfig.json": r"""{
  "extends": "expo/tsconfig.base",
  "compilerOptions": {
    "strict": true
  },
  "include": ["**/*.ts", "**/*.tsx"]
}
""",
        "src/theme/theme.ts": _TS_THEME,
        "README.md": readme,
        ".gitignore": gitignore,
    }
    warnings = [
        "Expo baseline is pinned from official package metadata; validate native builds and keep a reviewed lockfile.",
        "The ios/ and android/ native folders are created by 'npx expo run:<platform>' "
        "or 'npx expo prebuild'; they were NOT generated here.",
        "Store builds require EAS CLI and an Expo account; no build was executed.",
    ]
    return files, warnings


# ---------------------------------------------------------------------------
# Flutter scaffold builder
# ---------------------------------------------------------------------------


def _build_flutter_scaffold(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    import json as _json

    pubspec = _fill(
        r"""# Generated by Harmonika mobile source generators (offline).
# Source only: this project has NOT been built. Platform folders must be
# materialized with 'flutter create .' (see README).
name: @@DART@@
description: @@NAME@@ - Flutter starter generated by Harmonika.
publish_to: "none"
version: 1.0.0+1

environment:
  sdk: ">=3.0.0 <4.0.0"

dependencies:
  flutter:
    sdk: flutter

dev_dependencies:
  flutter_test:
    sdk: flutter

flutter:
  uses-material-design: true
""",
        {"@@DART@@": ctx.dart_name, "@@NAME@@": ctx.name},
    )

    theme_dart = _fill(
        r"""// Generated by Harmonika mobile source generators (offline).
// Source only: not compiled or executed. Review before use.
import 'package:flutter/material.dart';

/// Material 3 themes for @@NAME@@. Seeds use Harmonika blue (#2563EB).
class AppThemes {
  AppThemes._();

  static const Color seed = Color(0xFF2563EB);

  static ThemeData light() => _theme(Brightness.light);

  static ThemeData dark() => _theme(Brightness.dark);

  static ThemeData _theme(Brightness brightness) {
    final ColorScheme scheme = ColorScheme.fromSeed(
      seedColor: seed,
      brightness: brightness,
    );
    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      scaffoldBackgroundColor: scheme.surface,
      appBarTheme: AppBarTheme(
        backgroundColor: scheme.surface,
        foregroundColor: scheme.onSurface,
        centerTitle: false,
        elevation: 0,
        scrolledUnderElevation: 1,
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: scheme.surfaceContainerHighest.withOpacity(0.4),
        border: const OutlineInputBorder().copyWith(
          borderRadius: BorderRadius.circular(12),
        ),
      ),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
        ),
      ),
    );
  }
}
""",
        {"@@NAME@@": ctx.name},
    )

    main_dart = _fill(
        r"""// Generated by Harmonika mobile source generators (offline).
// Source only: not compiled or executed. Review before use.
import 'package:flutter/material.dart';

import 'theme/app_themes.dart';

void main() => runApp(const @@PASCAL@@App());

class @@PASCAL@@App extends StatelessWidget {
  const @@PASCAL@@App({super.key});

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: '@@NAME@@',
      theme: AppThemes.light(),
      darkTheme: AppThemes.dark(),
      themeMode: ThemeMode.system,
      home: const HomePage(),
    );
  }
}

class HomePage extends StatelessWidget {
  const HomePage({super.key});

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    final TextTheme text = Theme.of(context).textTheme;
    return Scaffold(
      appBar: AppBar(title: const Text('@@NAME@@')),
      // Keyboard-safe: the bottom inset keeps content above the IME.
      body: SafeArea(
        child: SingleChildScrollView(
          padding: EdgeInsets.only(
            left: 16,
            right: 16,
            top: 16,
            bottom: 16 + MediaQuery.viewInsetsOf(context).bottom,
          ),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 640),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: <Widget>[
                  Text(
                    'Welcome',
                    style: text.headlineMedium?.copyWith(color: scheme.onSurface),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    'Flutter starter generated by Harmonika. Replace this '
                    'screen with your first feature.',
                    style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
                  ),
                  const SizedBox(height: 24),
                  FilledButton.icon(
                    onPressed: () {},
                    icon: const Icon(Icons.rocket_launch_outlined),
                    label: const Text('Get started'),
                  ),
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
""",
        {"@@NAME@@": ctx.name, "@@PASCAL@@": ctx.app_pascal},
    )

    widget_test = _fill(
        r"""// Generated by Harmonika mobile source generators (offline).
// Source only: not executed. Run 'flutter test' locally.
import 'package:flutter_test/flutter_test.dart';

import 'package:@@DART@@/main.dart';

void main() {
  testWidgets('home renders welcome text', (WidgetTester tester) async {
    await tester.pumpWidget(const @@PASCAL@@App());
    expect(find.text('Welcome'), findsOneWidget);
  });
}
""",
        {"@@DART@@": ctx.dart_name, "@@PASCAL@@": ctx.app_pascal},
    )

    readme = _fill(
        r"""# @@NAME@@ (Flutter)

Flutter starter source generated offline by Harmonika. **The platform
folders (`android/`, `ios/`) were NOT generated**; a pubspec alone cannot
be built. Materialize them with the official tool:

```sh
flutter create . --org @@ORG@@ --project-name @@DART@@ --platforms android,ios
```

After that step the application id becomes `@@ORG@@.@@DART@@`. If that does
not match your intended identifier (`@@IDENT@@`), adjust
`android/app/build.gradle` (`applicationId`) and the iOS bundle identifier
in Xcode.

## Prerequisites

- Flutter SDK (stable channel): https://docs.flutter.dev/get-started/install
- Android Studio and/or Xcode for native toolchains

## Install / run

```sh
flutter pub get
flutter run
```

## Tests

```sh
flutter test
```

`test/widget_test.dart` is included; run it locally. It has not been run
by the generator.

## Notes

- Material 3 is enabled (`useMaterial3: true`) with light + dark themes.
- Generated files: `pubspec.yaml`, `lib/main.dart`, `lib/theme/app_themes.dart`,
  `test/widget_test.dart`.
""",
        {
            "@@NAME@@": ctx.name,
            "@@DART@@": ctx.dart_name,
            "@@IDENT@@": ctx.identifier,
            "@@ORG@@": ".".join(ctx.identifier.split(".")[:-1]),
        },
    )

    gitignore = r"""# Flutter / Dart
.dart_tool/
.packages
build/
.flutter-plugins
.flutter-plugins-dependencies
*.iml
.idea/
"""

    analysis = r"""# Basic analyzer configuration; `flutter create .` may replace this file.
# No lint package is pinned in pubspec, so nothing is `include:`d here.
analyzer:
  language:
    strict-casts: true
linter:
  rules:
    - prefer_const_constructors
    - prefer_final_locals
    - always_declare_return_types
"""

    files = {
        "pubspec.yaml": pubspec,
        "lib/main.dart": main_dart,
        "lib/theme/app_themes.dart": theme_dart,
        "test/widget_test.dart": widget_test,
        "analysis_options.yaml": analysis,
        "README.md": readme,
        ".gitignore": gitignore,
    }
    warnings = [
        "Run 'flutter create . --org %s --project-name %s --platforms android,ios' to "
        "materialize android/ and ios/ folders before any build; they were not generated."
        % (".".join(ctx.identifier.split(".")[:-1]), ctx.dart_name),
        "If %s.%s differs from your intended identifier %s, fix applicationId and the "
        "iOS bundle id after materializing platform folders."
        % (".".join(ctx.identifier.split(".")[:-1]), ctx.dart_name, ctx.identifier),
        "Dart/Flutter SDK versions are left as a wide range; no toolchain was invoked.",
    ]
    return files, warnings


# ---------------------------------------------------------------------------
# Kotlin (Jetpack Compose) scaffold builder
# ---------------------------------------------------------------------------


def _build_kotlin_scaffold(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    pkg_path = ctx.identifier.replace(".", "/")

    settings_kts = _fill(
        r"""// Generated by Harmonika mobile source generators (offline).
pluginManagement {
    repositories {
        google()
        mavenCentral()
        gradlePluginPortal()
    }
}

dependencyResolutionManagement {
    repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
    repositories {
        google()
        mavenCentral()
    }
}

rootProject.name = "@@SLUG@@"
include(":app")
""",
        {"@@SLUG@@": ctx.slug},
    )

    root_build_kts = r"""// Generated by Harmonika mobile source generators (offline).
// Version pins live in gradle/libs.versions.toml; verify latest stable
// versions before release. Nothing here has been built.
plugins {
    alias(libs.plugins.android.application) apply false
    alias(libs.plugins.kotlin.android) apply false
    alias(libs.plugins.kotlin.compose) apply false
}
"""

    libs_toml = r"""# Generated by Harmonika mobile source generators (offline).
# Pinned as historical sample pins; not runtime-verified; check for newer stable versions.
[versions]
agp = "8.5.2"
kotlin = "2.0.20"
coreKtx = "1.13.1"
activityCompose = "1.9.2"
lifecycleRuntime = "2.8.6"
composeBom = "2024.09.03"

[libraries]
androidx-core-ktx = { module = "androidx.core:core-ktx", version.ref = "coreKtx" }
androidx-activity-compose = { module = "androidx.activity:activity-compose", version.ref = "activityCompose" }
androidx-lifecycle-viewmodel-compose = { module = "androidx.lifecycle:lifecycle-viewmodel-compose", version.ref = "lifecycleRuntime" }
androidx-compose-bom = { module = "androidx.compose:compose-bom", version.ref = "composeBom" }
androidx-ui = { module = "androidx.compose.ui:ui" }
androidx-ui-tooling = { module = "androidx.compose.ui:ui-tooling" }
androidx-ui-tooling-preview = { module = "androidx.compose.ui:ui-tooling-preview" }
androidx-material3 = { module = "androidx.compose.material3:material3" }

[plugins]
android-application = { id = "com.android.application", version.ref = "agp" }
kotlin-android = { id = "org.jetbrains.kotlin.android", version.ref = "kotlin" }
kotlin-compose = { id = "org.jetbrains.kotlin.plugin.compose", version.ref = "kotlin" }
"""

    app_build_kts = _fill(
        r"""// Generated by Harmonika mobile source generators (offline).
plugins {
    alias(libs.plugins.android.application)
    alias(libs.plugins.kotlin.android)
    alias(libs.plugins.kotlin.compose)
}

android {
    namespace = "@@IDENT@@"
    compileSdk = 35

    defaultConfig {
        applicationId = "@@IDENT@@"
        minSdk = 24
        targetSdk = 35
        versionCode = 1
        versionName = "1.0.0"
    }

    buildTypes {
        release {
            isMinifyEnabled = true
            proguardFiles(
                getDefaultProguardFile("proguard-android-optimize.txt"),
                "proguard-rules.pro",
            )
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }

    kotlinOptions {
        jvmTarget = "17"
    }

    buildFeatures {
        compose = true
    }
}

dependencies {
    implementation(platform(libs.androidx.compose.bom))
    implementation(libs.androidx.core.ktx)
    implementation(libs.androidx.activity.compose)
    implementation(libs.androidx.lifecycle.viewmodel.compose)
    implementation(libs.androidx.ui)
    implementation(libs.androidx.ui.tooling.preview)
    implementation(libs.androidx.material3)
    debugImplementation(libs.androidx.ui.tooling)
}
""",
        {"@@IDENT@@": ctx.identifier},
    )

    gradle_props = r"""# Generated by Harmonika mobile source generators (offline).
org.gradle.jvmargs=-Xmx2048m -Dfile.encoding=UTF-8
android.useAndroidX=true
android.nonTransitiveRClass=true
kotlin.code.style=official
"""

    manifest = _fill(
        r"""<?xml version="1.0" encoding="utf-8"?>
<!-- Generated by Harmonika mobile source generators (offline). -->
<manifest xmlns:android="http://schemas.android.com/apk/res/android">

    <application
        android:label="@string/app_name"
        android:supportsRtl="true"
        android:theme="@android:style/Theme.Material.Light.NoActionBar">

        <activity
            android:name=".MainActivity"
            android:exported="true"
            android:windowSoftInputMode="adjustResize">
            <intent-filter>
                <action android:name="android.intent.action.MAIN" />
                <category android:name="android.intent.category.LAUNCHER" />
            </intent-filter>
        </activity>
    </application>

</manifest>
""",
        {},
    )

    strings_xml = _fill(
        r"""<?xml version="1.0" encoding="utf-8"?>
<!-- Generated by Harmonika mobile source generators (offline). -->
<resources>
    <string name="app_name">@@NAME@@</string>
</resources>
""",
        {"@@NAME@@": ctx.name},
    )

    theme_kt = r"""// Generated by Harmonika mobile source generators (offline).
// Source only: not compiled or executed. Review before use.
package @@PKG@@.ui.theme

import android.os.Build
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.material3.dynamicLightColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext

private val Seed = Color(0xFF2563EB)

private val LightColors = lightColorScheme(
    primary = Color(0xFF2563EB),
    onPrimary = Color.White,
    primaryContainer = Color(0xFFD8E2FF),
    onPrimaryContainer = Color(0xFF001A46),
    secondary = Color(0xFF575E71),
    surface = Color(0xFFFBF8FF),
    onSurface = Color(0xFF1B1B21),
)

private val DarkColors = darkColorScheme(
    primary = Color(0xFFADC6FF),
    onPrimary = Color(0xFF002D6F),
    primaryContainer = Color(0xFF00419B),
    onPrimaryContainer = Color(0xFFD8E2FF),
    secondary = Color(0xFFBFC6DC),
    surface = Color(0xFF131318),
    onSurface = Color(0xFFE4E1E9),
)

@Composable
fun @@PASCAL@@Theme(
    darkTheme: Boolean = isSystemInDarkTheme(),
    // Dynamic color (Material You) on Android 12+; set to false to force brand colors.
    dynamicColor: Boolean = true,
    content: @Composable () -> Unit,
) {
    val colorScheme = when {
        dynamicColor && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S -> {
            val context = LocalContext.current
            if (darkTheme) dynamicDarkColorScheme(context) else dynamicLightColorScheme(context)
        }
        darkTheme -> DarkColors
        else -> LightColors
    }
    MaterialTheme(
        colorScheme = colorScheme,
        content = content,
    )
}
""".replace("@@PASCAL@@", ctx.app_pascal).replace("@@PKG@@", ctx.identifier)

    viewmodel_kt = _fill(
        r"""// Generated by Harmonika mobile source generators (offline).
// Source only: not compiled or executed. Review before use.
package @@PKG@@.ui

import androidx.lifecycle.ViewModel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update

/** Immutable UI state rendered by the home screen. */
data class HomeUiState(
    val title: String = "@@NAME@@",
    val greetingCount: Int = 0,
    val items: List<String> = listOf("Plan", "Build", "Ship"),
)

class HomeViewModel : ViewModel() {

    private val _uiState = MutableStateFlow(HomeUiState())
    val uiState: StateFlow<HomeUiState> = _uiState.asStateFlow()

    fun onGreet() = _uiState.update { it.copy(greetingCount = it.greetingCount + 1) }
}
""",
        {"@@PKG@@": ctx.identifier, "@@NAME@@": ctx.name},
    )

    activity_kt = _fill(
        r"""// Generated by Harmonika mobile source generators (offline).
// Source only: not compiled or executed. Review before use.
package @@PKG@@

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.BoxWithConstraints
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.WindowInsets
import androidx.compose.foundation.layout.asPaddingValues
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawing
import androidx.compose.foundation.layout.widthIn
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.collectAsState
import androidx.compose.runtime.getValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.unit.dp
import @@PKG@@.ui.HomeViewModel
import @@PKG@@.ui.theme.@@PASCAL@@Theme

class MainActivity : ComponentActivity() {

    private val viewModel: HomeViewModel by viewModels()

    override fun onCreate(savedInstanceState: Bundle?) {
        // Edge-to-edge: draws behind system bars on Android 15+ and older.
        enableEdgeToEdge()
        super.onCreate(savedInstanceState)
        setContent {
            @@PASCAL@@Theme {
                HomeScreen(viewModel = viewModel)
            }
        }
    }
}

@Composable
private fun HomeScreen(viewModel: HomeViewModel) {
    val state by viewModel.uiState.collectAsState()

    Scaffold(
        containerColor = MaterialTheme.colorScheme.surface,
        // Safe drawing handles status bar + navigation bar + display cutouts.
        contentWindowInsets = WindowInsets.safeDrawing,
    ) { innerPadding ->
        BoxWithConstraints(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding),
        ) {
            // Responsive: single centered column on compact widths, two panes
            // when expanded (tablets / unfolded foldables).
            val expanded = maxWidth >= 840.dp
            if (expanded) {
                Row(modifier = Modifier.fillMaxSize()) {
                    HomeContent(
                        state = state,
                        onGreet = viewModel::onGreet,
                        modifier = Modifier
                            .weight(1f)
                            .imePadding()
                            .navigationBarsPadding(),
                    )
                    DetailPane(modifier = Modifier.weight(1f))
                }
            } else {
                HomeContent(
                    state = state,
                    onGreet = viewModel::onGreet,
                    modifier = Modifier
                        .fillMaxSize()
                        .imePadding()
                        .navigationBarsPadding(),
                )
            }
        }
    }
}

@Composable
private fun HomeContent(
    state: @@PKG_DOT@@.ui.HomeUiState,
    onGreet: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Column(
        modifier = modifier
            .widthIn(max = 640.dp)
            .padding(16.dp),
        verticalArrangement = Arrangement.spacedBy(12.dp),
    ) {
        Text(text = state.title, style = MaterialTheme.typography.headlineMedium)
        Text(
            text = "Greeted ${state.greetingCount}x",
            style = MaterialTheme.typography.bodyMedium,
        )
        Button(onClick = onGreet) {
            Text(text = "Say hello")
        }
        LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
            items(state.items) { item ->
                Card(modifier = Modifier.fillMaxWidth()) {
                    Text(text = item, modifier = Modifier.padding(16.dp))
                }
            }
        }
    }
}

@Composable
private fun DetailPane(modifier: Modifier = Modifier) {
    val safePadding = WindowInsets.safeDrawing.asPaddingValues()
    Column(
        modifier = modifier.padding(top = safePadding.calculateTopPadding()),
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text(
            text = "Expanded layout",
            style = MaterialTheme.typography.titleMedium,
        )
    }
}
""",
        {"@@PKG@@": ctx.identifier, "@@PKG_DOT@@": ctx.identifier, "@@PASCAL@@": ctx.app_pascal, "@@NAME@@": ctx.name},
    )

    proguard = r"""# Generated by Harmonika mobile source generators (offline).
# Add project-specific keep rules here. R8 runs on release builds
# (isMinifyEnabled = true in app/build.gradle.kts).
"""

    readme = _fill(
        r"""# @@NAME@@ (Kotlin + Jetpack Compose)

Native Android starter generated offline by Harmonika. **No Gradle build,
unit test, or APK assembly has been executed.**

## Prerequisites

- JDK 17
- Android Studio (Koala / 2024.1 or newer recommended) with SDK 35
- The Gradle wrapper jar is intentionally not committed; Android Studio
  generates it on first sync, or run `gradle wrapper --gradle-version 8.7`
  with a local Gradle install.

## Open / build

```sh
# In Android Studio: File > Open > this folder, then let Gradle sync.
./gradlew assembleDebug     # after the wrapper exists
./gradlew testDebugUnitTest # no unit tests are included yet
```

## Included structure

- `gradle/libs.versions.toml` - all version pins in one place; verify latest
  stable versions before release.
- `app/src/main/java/@@PKG_PATH@@/MainActivity.kt` - Compose entry with
  edge-to-edge, IME-safe and responsive layout.
- `MainActivity` + `HomeViewModel` (StateFlow) - unidirectional state.
- Material 3 themes with dynamic color on Android 12+.

## Notes

- applicationId / namespace: `@@IDENT@@`, minSdk 24, targetSdk 35.
- No app icon assets are included; add `mipmap` resources before release.
- Versions pinned (AGP 8.5.2, Kotlin 2.0.20, Compose BOM 2024.09.03) were
  historical sample pins; not runtime-verified - re-check them.
""",
        {
            "@@NAME@@": ctx.name,
            "@@IDENT@@": ctx.identifier,
            "@@PKG_PATH@@": pkg_path,
        },
    )

    gitignore = r"""*.iml
.gradle/
local.properties
.idea/
.DS_Store
build/
captures/
.externalNativeBuild/
.cxx/
*.jks
*.keystore
"""

    files = {
        "settings.gradle.kts": settings_kts,
        "build.gradle.kts": root_build_kts,
        "gradle/libs.versions.toml": libs_toml,
        "gradle.properties": gradle_props,
        "app/build.gradle.kts": app_build_kts,
        "app/proguard-rules.pro": proguard,
        "app/src/main/AndroidManifest.xml": manifest,
        "app/src/main/res/values/strings.xml": strings_xml,
        "app/src/main/java/%s/MainActivity.kt" % pkg_path: activity_kt,
        "app/src/main/java/%s/ui/HomeViewModel.kt" % pkg_path: viewmodel_kt,
        "app/src/main/java/%s/ui/theme/Theme.kt" % pkg_path: theme_kt,
        "README.md": readme,
        ".gitignore": gitignore,
    }
    warnings = [
        "Gradle wrapper jar is not included; open in Android Studio or run "
        "'gradle wrapper --gradle-version 8.7' before ./gradlew commands.",
        "Version pins (AGP/Kotlin/Compose BOM/AndroidX) are historical examples "
        "only; verify the latest stable versions before release.",
        "No APK/AAB was built and no unit tests were run (runtime_verified=false).",
        "Add launcher icon resources (mipmap) before publishing.",
    ]
    return files, warnings


# ---------------------------------------------------------------------------
# Swift (SwiftUI) scaffold builder
# ---------------------------------------------------------------------------


def _build_swift_scaffold(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    app_pascal = ctx.app_pascal
    org = ".".join(ctx.identifier.split(".")[:-1])
    leaf = ctx.identifier.split(".")[-1]

    project_yml = _fill(
        r"""# Generated by Harmonika mobile source generators (offline).
# Xcode project generation requires xcodegen (https://github.com/yonaskolb/XcodeGen).
# Run:  xcodegen generate
name: @@PASCAL@@
options:
  bundleIdPrefix: @@ORG@@
  deploymentTarget:
    iOS: "16.0"
settings:
  base:
    SWIFT_VERSION: "5.0"
targets:
  App:
    type: application
    platform: iOS
    sources:
      - Sources
    info:
      path: Info.plist
      properties:
        CFBundleDisplayName: @@NAME@@
        UILaunchScreen: {}
        UISupportedInterfaceOrientations:
          - UIInterfaceOrientationPortrait
    settings:
      base:
        PRODUCT_BUNDLE_IDENTIFIER: @@IDENT@@
        TARGETED_DEVICE_FAMILY: "1,2"
        INFOPLIST_KEY_UILaunchScreen_Generation: YES
""",
        {"@@PASCAL@@": app_pascal, "@@NAME@@": ctx.name, "@@ORG@@": org, "@@IDENT@@": ctx.identifier},
    )

    app_swift = _fill(
        r"""// Generated by Harmonika mobile source generators (offline).
// Source only: not compiled or executed. Review before use.
import SwiftUI

@main
struct @@PASCAL@@App: App {
    var body: some Scene {
        WindowGroup {
            RootView()
        }
    }
}
""",
        {"@@PASCAL@@": app_pascal},
    )

    root_swift = r"""// Generated by Harmonika mobile source generators (offline).
// Source only: not compiled or executed. Review before use.
import SwiftUI

struct RootView: View {
    var body: some View {
        TabView {
            NavigationStack {
                HomeView()
            }
            .tabItem {
                Label("Home", systemImage: "house")
            }

            NavigationStack {
                SettingsView()
            }
            .tabItem {
                Label("Settings", systemImage: "gearshape")
            }
        }
    }
}

#Preview {
    RootView()
}
"""

    home_swift = r"""// Generated by Harmonika mobile source generators (offline).
// Source only: not compiled or executed. Review before use.
import SwiftUI

struct Item: Identifiable, Hashable {
    let id: UUID
    let title: String
    let subtitle: String

    init(title: String, subtitle: String) {
        self.id = UUID()
        self.title = title
        self.subtitle = subtitle
    }
}

/// Sample data for UI structure only. Replace with your real data source;
/// this scaffold deliberately contains no network or auth placeholders.
enum SampleData {
    static let items: [Item] = [
        Item(title: "First", subtitle: "Generated starter row"),
        Item(title: "Second", subtitle: "NavigationStack demo row"),
        Item(title: "Third", subtitle: "Replace with real content"),
    ]
}

struct HomeView: View {
    var body: some View {
        List(SampleData.items) { item in
            NavigationLink(value: item) {
                VStack(alignment: .leading, spacing: 4) {
                    Text(item.title)
                        .font(.headline)
                    Text(item.subtitle)
                        .font(.subheadline)
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 2)
            }
        }
        .navigationTitle("@@NAME@@")
        .navigationDestination(for: Item.self) { item in
            DetailView(item: item)
        }
    }
}

#Preview {
    NavigationStack {
        HomeView()
    }
}
""".replace("@@NAME@@", ctx.name)

    detail_swift = r"""// Generated by Harmonika mobile source generators (offline).
// Source only: not compiled or executed. Review before use.
import SwiftUI

struct DetailView: View {
    let item: Item

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            Text(item.title)
                .font(.largeTitle.bold())
            Text(item.subtitle)
                .font(.body)
                .foregroundStyle(.secondary)
            Spacer()
        }
        .padding()
        .navigationTitle(item.title)
        .navigationBarTitleDisplayMode(.inline)
    }
}

#Preview {
    NavigationStack {
        DetailView(item: SampleData.items[0])
    }
}
"""

    settings_swift = r"""// Generated by Harmonika mobile source generators (offline).
// Source only: not compiled or executed. Review before use.
import SwiftUI

struct SettingsView: View {
    @State private var darkModePreferred = false

    var body: some View {
        Form {
            Section("Appearance") {
                Toggle("Prefer dark appearance", isOn: $darkModePreferred)
            }
            Section("About") {
                LabeledContent("App", value: "@@NAME@@")
                LabeledContent("Bundle", value: "@@IDENT@@")
                LabeledContent("Version", value: "1.0.0")
            }
        }
        .navigationTitle("Settings")
        .preferredColorScheme(darkModePreferred ? .dark : nil)
    }
}

#Preview {
    NavigationStack {
        SettingsView()
    }
}
""".replace("@@NAME@@", ctx.name).replace("@@IDENT@@", ctx.identifier)

    readme = _fill(
        r"""# @@NAME@@ (Swift + SwiftUI)

Native iOS **source kit** generated offline by Harmonika.

**No Xcode project file is included.** A `.xcodeproj` cannot be hand-written
reliably, so this kit ships SwiftUI sources plus an `xcodegen` spec. Nothing
has been compiled; no IPA exists.

## Create the Xcode project (pick one)

Option A - XcodeGen (recommended, spec included):

```sh
brew install xcodegen
xcodegen generate
open @@PASCAL@@.xcodeproj
```

Option B - Xcode only: File > New > Project > iOS App, Interface = SwiftUI,
then add the files from `Sources/` to the new project and set the bundle
identifier to `@@IDENT@@` (deployment target iOS 16.0).

## Build / run

- Xcode 15+ and an iOS 16+ simulator or device.
- Cmd+R builds and runs. Signing requires your own Apple Developer team in
  Xcode > Signing & Capabilities.

## Included sources

- `Sources/App.swift` - `@main` entry.
- `Sources/RootView.swift` - TabView + NavigationStack shell.
- `Sources/HomeView.swift` + `Sources/DetailView.swift` - typed navigation
  with sample data clearly labeled as sample only (no mock auth, no fake API).
- `Sources/SettingsView.swift` - form + preference toggle.

## Notes

- Bundle id: `@@IDENT@@` (org prefix `@@ORG@@`, leaf `@@LEAF@@`).
- Push notifications, Sign in with Apple, Keychain and other capabilities
  must be added in Xcode; none are configured here.
""",
        {
            "@@NAME@@": ctx.name,
            "@@PASCAL@@": app_pascal,
            "@@IDENT@@": ctx.identifier,
            "@@ORG@@": org,
            "@@LEAF@@": leaf,
        },
    )

    gitignore = r"""*.xcodeproj
*.xcworkspace
xcuserdata/
DerivedData/
.DS_Store
*.ipa
*.dSYM.zip
"""

    files = {
        "project.yml": project_yml,
        "Sources/App.swift": app_swift,
        "Sources/RootView.swift": root_swift,
        "Sources/HomeView.swift": home_swift,
        "Sources/DetailView.swift": detail_swift,
        "Sources/SettingsView.swift": settings_swift,
        "README.md": readme,
        ".gitignore": gitignore,
    }
    warnings = [
        "Source kit only: no .xcodeproj was generated. Run 'xcodegen generate' "
        "(spec in project.yml) or create the project in Xcode, then build there.",
        "No IPA was produced and no compiler was invoked (runtime_verified=false).",
        "Signing, capabilities (push, Keychain, Sign in with Apple) and app icons "
        "must be configured in Xcode with your own team.",
        "Sample data in HomeView.swift is UI structure only - no auth or API calls.",
    ]
    return files, warnings


# ---------------------------------------------------------------------------
# Ionic + Capacitor scaffold builder
# ---------------------------------------------------------------------------


def _build_ionic_scaffold(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    import json as _json

    package_json = {
        "name": ctx.slug,
        "version": "0.1.0",
        "private": True,
        "type": "module",
        "scripts": {
            "dev": "vite",
            "build": "tsc && vite build",
            "preview": "vite preview",
            "cap:sync": "cap sync",
            "cap:android": "cap open android",
            "cap:ios": "cap open ios",
        },
        "dependencies": {"@capacitor/core": "*", "@capacitor/android": "*", "@capacitor/ios": "*", "@ionic/core": "*"},
        "devDependencies": {
            "@capacitor/cli": "*",
            "typescript": "5.9.3",
            "vite": "*",
        },
    }

    cap_config = _fill(
        r"""// Generated by Harmonika mobile source generators (offline).
// Source only. Native projects are added later with `npx cap add android|ios`.
import type { CapacitorConfig } from '@capacitor/cli';

const config: CapacitorConfig = {
  appId: '@@IDENT@@',
  appName: '@@NAME@@',
  webDir: 'dist',
  // Keep Android/iOS projects in sync with the built web assets.
};

export default config;
""",
        {"@@IDENT@@": ctx.identifier, "@@NAME@@": ctx.name},
    )

    index_html = _fill(
        r"""<!DOCTYPE html>
<!-- Generated by Harmonika mobile source generators (offline). -->
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0, viewport-fit=cover" />
    <title>@@NAME@@</title>
    <link rel="stylesheet" href="/src/style.css" />
  </head>
  <body>
    <ion-app><ion-content><main id="app" class="shell">
      <h1>@@NAME@@</h1>
      <p id="status">Web shell ready. Native shells are added with Capacitor.</p>
      <button id="action" type="button">Get started</button>
    </main></ion-content></ion-app>
    <script type="module" src="/src/main.ts"></script>
  </body>
</html>
""",
        {"@@NAME@@": ctx.name},
    )

    main_ts = _TS_HEADER + r"""import '@ionic/core/css/ionic.bundle.css';
import { defineCustomElements } from '@ionic/core/loader';
defineCustomElements(window);

const status = document.querySelector<HTMLParagraphElement>('#status');
const action = document.querySelector<HTMLButtonElement>('#action');
let started = false;

function render(): void {
  if (!status || !action) return;
  if (started) {
    status.textContent = 'Starter interaction wired. Build your UI here.';
    action.textContent = 'Reset';
  } else {
    status.textContent = 'Web shell ready. Native shells are added with Capacitor.';
    action.textContent = 'Get started';
  }
}

action?.addEventListener('click', () => {
  started = !started;
  render();
});

render();
"""

    style_css = r"""/* Generated by Harmonika mobile source generators (offline). */
:root {
  color-scheme: light dark;
  --bg: #ffffff;
  --fg: #0f172a;
  --muted: #5b6472;
  --primary: #2563eb;
}

@media (prefers-color-scheme: dark) {
  :root {
    --bg: #0b1220;
    --fg: #f1f5f9;
    --muted: #94a3b8;
    --primary: #60a5fa;
  }
}

* { box-sizing: border-box; }

body {
  margin: 0;
  font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
  background: var(--bg);
  color: var(--fg);
}

.shell {
  max-width: 640px;
  margin: 0 auto;
  padding: max(24px, env(safe-area-inset-top)) 24px
    max(24px, env(safe-area-inset-bottom));
  display: grid;
  gap: 16px;
  align-content: center;
  min-height: 100dvh;
}

.shell p { color: var(--muted); margin: 0; }

#action {
  justify-self: start;
  padding: 12px 20px;
  border-radius: 12px;
  border: none;
  background: var(--primary);
  color: #ffffff;
  font-size: 16px;
}

#action:active { opacity: 0.85; }
"""

    vite_config = r"""// Generated by Harmonika mobile source generators (offline).
import { defineConfig } from 'vite';

export default defineConfig({
  // Relative base keeps assets resolvable inside Capacitor WebView.
  base: './',
  build: {
    outDir: 'dist',
  },
});
"""

    tsconfig = r"""{
  "compilerOptions": {
    "target": "ES2020",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "noEmit": true,
    "lib": ["ES2020", "DOM", "DOM.Iterable"]
  },
  "include": ["src"]
}
"""

    readme = _fill(
        r"""# @@NAME@@ (Ionic-style web shell + Capacitor)

This scaffold uses Ionic Core web components with a TypeScript shell and Capacitor config.
The Ionic Framework component library itself is **not** included; if you
want the full Ionic UI kit, prefer `ionic start` and port these files.

## Web shell

```sh
npm install
npm run dev        # local dev server
npm run build      # outputs dist/ (webDir for Capacitor)
```

## Native shells

Native projects need the native toolchains (Android Studio / Xcode):

```sh
npx cap add android
npx cap add ios      # macOS + Xcode required
npx cap sync
```

Full Ionic tooling alternative: `npx @ionic/cli start` - see
https://ionicframework.com/docs.

## Tests

None included; nothing was built or run by the generator.

## Notes

- appId: `@@IDENT@@`, appName: `@@NAME@@`, webDir: `dist`.
- Dependency versions are unpinned placeholders (`*`); review them after
  `npm install`.
""",
        {"@@NAME@@": ctx.name, "@@IDENT@@": ctx.identifier},
    )

    gitignore = r"""node_modules/
dist/
android/
ios/
.DS_Store
*.local
"""

    files = {
        "package.json": _json.dumps(package_json, indent=2, sort_keys=True) + "\n",
        "capacitor.config.ts": cap_config,
        "index.html": index_html,
        "src/main.ts": main_ts,
        "src/style.css": style_css,
        "vite.config.ts": vite_config,
        "tsconfig.json": tsconfig,
        "README.md": readme,
        ".gitignore": gitignore,
    }
    warnings = [
        "This is a web shell + Capacitor config, not the full Ionic Framework UI kit; "
        "use 'ionic start' if you need Ionic components.",
        "Run 'npx cap add android' / 'npx cap add ios' to create native shells; "
        "they require Android Studio / Xcode and were NOT generated.",
        "Dependency versions are unpinned ('*'); review after 'npm install'.",
    ]
    return files, warnings


# ---------------------------------------------------------------------------
# Expo UI module builders (React Navigation / plain TypeScript)
# ---------------------------------------------------------------------------

_NAV_DEPS_WARNING = (
    "Dependencies (unpinned; resolve inside the project): "
    "npm install @react-navigation/native && npx expo install "
    "react-native-screens react-native-safe-area-context. Add stack/tab/drawer "
    "packages as listed per file."
)


def _expo_screen(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    snake = ctx.screen_snake
    code = _fill(
        _TS_HEADER + r"""import React from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  ScrollView,
  StatusBar,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTheme, spacing, typography } from '../theme/theme';

export interface @@PSFX@@Props {
  /** Header title; defaults to the generated screen name. */
  title?: string;
  /** Optional one-line description rendered under the title. */
  subtitle?: string;
  /** Screen body content. */
  children?: React.ReactNode;
}

/**
 * @@WORDS@@ screen.
 * Keyboard-safe (KeyboardAvoidingView + keyboardShouldPersistTaps) and
 * inset-aware on notched devices.
 */
export function @@PSFX@@({
  title = '@@WORDS@@',
  subtitle,
  children,
}: @@PSFX@@Props): React.ReactElement {
  const { colors, scheme, typography: type } = useTheme();
  const insets = useSafeAreaInsets();

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <StatusBar barStyle={scheme === 'dark' ? 'light-content' : 'dark-content'} />
      <ScrollView
        style={styles.flex}
        contentContainerStyle={[
          styles.content,
          {
            backgroundColor: colors.background,
            paddingTop: insets.top + spacing.md,
            paddingBottom: insets.bottom + spacing.xl,
          },
        ]}
        keyboardShouldPersistTaps="handled"
      >
        <View style={styles.header}>
          <Text accessibilityRole="header" style={[type.title, { color: colors.text }]}>
            {title}
          </Text>
          {subtitle ? (
            <Text style={[styles.subtitle, type.body, { color: colors.textMuted }]}>
              {subtitle}
            </Text>
          ) : null}
        </View>
        {children}
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  content: {
    flexGrow: 1,
    paddingHorizontal: spacing.lg,
  },
  header: {
    marginBottom: spacing.lg,
    gap: spacing.sm,
  },
  subtitle: {
    fontSize: typography.body.fontSize,
  },
});
""", {
            "@@PASCAL@@": ctx.screen_name,
            "@@PSFX@@": _pascal_sfx(ctx.screen_name, "Screen"),
            "@@WORDS@@": ctx.screen_words,
        })
    files = {
        "src/screens/%s.tsx" % _pascal_sfx(ctx.screen_name, "Screen"): code,
        "src/theme/theme.ts": _TS_THEME,
    }
    warnings = [
        "Dependency (unpinned): npx expo install react-native-safe-area-context",
    ]
    return files, warnings


def _expo_component(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _TS_HEADER + r"""import React from 'react';
import {
  ActivityIndicator,
  Pressable,
  StyleSheet,
  Text,
  ViewStyle,
} from 'react-native';
import { useTheme, radius, spacing } from '../theme/theme';

export type @@PASCAL@@Variant = 'primary' | 'secondary' | 'danger';

export interface @@PASCAL@@Props {
  label: string;
  onPress: () => void;
  variant?: @@PASCAL@@Variant;
  disabled?: boolean;
  loading?: boolean;
  /** Optional extra container styles. */
  style?: ViewStyle;
}

/**
 * Reusable themed button with press feedback, busy state and
 * accessibility wiring.
 */
export function @@PASCAL@@({
  label,
  onPress,
  variant = 'primary',
  disabled = false,
  loading = false,
  style,
}: @@PASCAL@@Props): React.ReactElement {
  const { colors } = useTheme();
  const blocked = disabled || loading;

  const paletteByVariant = {
    primary: { bg: colors.primary, fg: colors.onPrimary },
    secondary: { bg: colors.surface, fg: colors.text },
    danger: { bg: colors.danger, fg: '#FFFFFF' },
  }[variant];

  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={label}
      accessibilityState={{ disabled: blocked, busy: loading }}
      disabled={blocked}
      onPress={onPress}
      style={({ pressed }) => [
        styles.base,
        { backgroundColor: paletteByVariant.bg, opacity: pressed ? 0.7 : 1 },
        blocked && styles.blocked,
        style,
      ]}
      android_ripple={{ color: 'rgba(0,0,0,0.15)' }}
    >
      {loading ? (
        <ActivityIndicator color={paletteByVariant.fg} />
      ) : (
        <Text style={[styles.label, { color: paletteByVariant.fg }]}>{label}</Text>
      )}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  base: {
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 48,
    paddingHorizontal: spacing.lg,
    borderRadius: radius.md,
  },
  blocked: { opacity: 0.5 },
  label: { fontSize: 16, fontWeight: '600' },
});
""", {"@@PASCAL@@": ctx.screen_name})
    files = {
        "src/components/%s.tsx" % ctx.screen_name: code,
        "src/theme/theme.ts": _TS_THEME,
    }
    warnings = ["No external dependencies: uses React Native primitives only."]
    return files, warnings


def _expo_form(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    fields = ctx.extras["fields"]
    field_lines = []
    for f in fields:
        field_lines.append(
            "  { name: '%s', label: %s, type: '%s', required: %s },"
            % (f["name"], json.dumps(f["label"]), f["type"], "true" if f["required"] else "false")
        )
    fields_block = "\n".join(field_lines) if field_lines else "  { name: 'email', label: 'Email', type: 'email', required: true },"
    snake = ctx.screen_snake
    code = _fill(
        _TS_HEADER + r"""import React, { useState } from 'react';
import {
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useTheme, radius, spacing, typography } from '../theme/theme';

export type @@PASCAL@@FieldType = 'text' | 'email' | 'password' | 'number' | 'multiline';

export interface @@PASCAL@@FieldSpec {
  name: string;
  label: string;
  type: @@PASCAL@@FieldType;
  required: boolean;
}

export type @@PASCAL@@Values = Record<string, string>;
export type @@PASCAL@@Errors = Record<string, string>;

const EMAIL_PATTERN = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const NUMBER_PATTERN = /^-?\d+(?:\.\d+)?$/;

/** Default fields; pass your own via the `fields` prop. */
export const DEFAULT_@@UPPER@@_FIELDS: readonly @@PASCAL@@FieldSpec[] = [
@@FIELDS@@
];

export function validate@@PASCAL@@Field(
  field: @@PASCAL@@FieldSpec,
  value: string,
): string | null {
  const trimmed = value.trim();
  if (field.required && trimmed.length === 0) {
    return 'This field is required';
  }
  if (trimmed.length === 0) return null;
  switch (field.type) {
    case 'email':
      return EMAIL_PATTERN.test(trimmed) ? null : 'Enter a valid email address';
    case 'number':
      return NUMBER_PATTERN.test(trimmed) ? null : 'Enter a valid number';
    case 'password':
      return trimmed.length >= 8 ? null : 'Use at least 8 characters';
    default:
      return trimmed.length <= 2000 ? null : 'Too long (max 2000 characters)';
  }
}

export interface @@PASCAL@@FormProps {
  /** Field definitions rendered by the form. */
  fields?: readonly @@PASCAL@@FieldSpec[];
  /** Submit button label. */
  submitLabel?: string;
  /** Called with the collected values after client-side validation passes. */
  onSubmit: (values: @@PASCAL@@Values) => Promise<void> | void;
}

/**
 * Validated, keyboard-safe form. Values are plain strings; wire `onSubmit`
 * to your API layer. No request is made by this component itself.
 */
export function @@PASCAL@@Form({
  fields = DEFAULT_@@UPPER@@_FIELDS,
  submitLabel = 'Submit',
  onSubmit,
}: @@PASCAL@@FormProps): React.ReactElement {
  const { colors, radius: rad, spacing: sp, typography: type } = useTheme();
  const [values, setValues] = useState<@@PASCAL@@Values>(() =>
    Object.fromEntries(fields.map((f) => [f.name, ''])),
  );
  const [errors, setErrors] = useState<@@PASCAL@@Errors>({});
  const [submitting, setSubmitting] = useState(false);

  const setValue = (name: string, next: string) => {
    setValues((prev) => ({ ...prev, [name]: next }));
    if (errors[name]) {
      setErrors((prev) => {
        const nextErrors = { ...prev };
        delete nextErrors[name];
        return nextErrors;
      });
    }
  };

  const handleSubmit = async () => {
    const nextErrors: @@PASCAL@@Errors = {};
    for (const field of fields) {
      const error = validate@@PASCAL@@Field(field, values[field.name] ?? '');
      if (error) nextErrors[field.name] = error;
    }
    setErrors(nextErrors);
    if (Object.keys(nextErrors).length > 0 || submitting) return;
    try {
      setSubmitting(true);
      await onSubmit(values);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView
        style={styles.flex}
        contentContainerStyle={[styles.content, { padding: sp.lg }]}
        keyboardShouldPersistTaps="handled"
      >
        {fields.map((field) => {
          const error = errors[field.name];
          const isMultiline = field.type === 'multiline';
          return (
            <View key={field.name} style={styles.field}>
              <Text style={[styles.label, type.caption, { color: colors.text }]}>
                {field.label}
                {field.required ? ' *' : ''}
              </Text>
              <TextInput
                accessibilityLabel={field.label}
                value={values[field.name] ?? ''}
                onChangeText={(next) => setValue(field.name, next)}
                placeholder={field.label}
                placeholderTextColor={colors.textMuted}
                secureTextEntry={field.type === 'password'}
                keyboardType={
                  field.type === 'email'
                    ? 'email-address'
                    : field.type === 'number'
                      ? 'numeric'
                      : 'default'
                }
                autoCapitalize={field.type === 'email' || field.type === 'password' ? 'none' : 'sentences'}
                autoCorrect={field.type !== 'email' && field.type !== 'password'}
                multiline={isMultiline}
                numberOfLines={isMultiline ? 4 : undefined}
                maxLength={2000}
                style={[
                  styles.input,
                  {
                    borderColor: error ? colors.danger : colors.border,
                    color: colors.text,
                    backgroundColor: colors.surface,
                    borderRadius: rad.sm,
                    minHeight: isMultiline ? 96 : 48,
                    textAlignVertical: isMultiline ? 'top' : 'center',
                  },
                ]}
              />
              {error ? (
                <Text accessibilityLiveRegion="polite" style={[styles.error, { color: colors.danger }]}>
                  {error}
                </Text>
              ) : null}
            </View>
          );
        })}

        <Pressable
          accessibilityRole="button"
          accessibilityState={{ busy: submitting }}
          disabled={submitting}
          onPress={handleSubmit}
          android_ripple={{ color: 'rgba(0,0,0,0.15)' }}
          style={({ pressed }) => [
            styles.submit,
            { backgroundColor: colors.primary, borderRadius: rad.md, opacity: pressed || submitting ? 0.7 : 1 },
          ]}
        >
          <Text style={[styles.submitLabel, { color: colors.onPrimary }]}>
            {submitting ? 'Submitting…' : submitLabel}
          </Text>
        </Pressable>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  content: { paddingBottom: 32, gap: 4 },
  field: { marginBottom: 12 },
  label: { marginBottom: 6 },
  input: {
    borderWidth: 1,
    paddingHorizontal: 12,
    paddingVertical: 10,
    fontSize: typography.body.fontSize,
  },
  error: { marginTop: 4, fontSize: 13 },
  submit: {
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 48,
    marginTop: 8,
  },
  submitLabel: { fontSize: 16, fontWeight: '600' },
});
""", {
            "@@PASCAL@@": ctx.screen_name,
            "@@UPPER@@": ctx.screen_snake.upper(),
            "@@FIELDS@@": fields_block,
        })
    files = {
        "src/forms/%s.tsx" % _snake_sfx(snake, "form"): code,
        "src/theme/theme.ts": _TS_THEME,
    }
    warnings = [
        "No external dependencies: uses React Native primitives only.",
        "onSubmit receives validated values; wire it to your API layer (no network call is made here).",
    ]
    return files, warnings


def _expo_list(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _TS_HEADER + r"""import React from 'react';
import {
  FlatList,
  RefreshControl,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTheme, radius, spacing, typography } from '../theme/theme';

export interface @@PASCAL@@Item {
  id: string;
  title: string;
  subtitle?: string;
  meta?: string;
}

export interface @@PSFX@@Props {
  items: readonly @@PASCAL@@Item[];
  refreshing?: boolean;
  onRefresh?: () => void;
  onPressItem?: (item: @@PASCAL@@Item) => void;
  emptyTitle?: string;
  emptyMessage?: string;
}

function @@PSFX@@EmptyState({ title, message }: { title: string; message: string }) {
  const { colors, typography: type } = useTheme();
  return (
    <View style={styles.empty}>
      <Text style={[type.heading, { color: colors.text }]}>{title}</Text>
      <Text style={[styles.emptyMessage, type.body, { color: colors.textMuted }]}>{message}</Text>
    </View>
  );
}

/**
 * Typed FlatList screen with pull-to-refresh, separators and an inline
 * empty state. Data and refresh handling are owned by the caller.
 */
export function @@PSFX@@({
  items,
  refreshing = false,
  onRefresh,
  onPressItem,
  emptyTitle = 'Nothing here yet',
  emptyMessage = 'Items will appear in this list.',
}: @@PSFX@@Props): React.ReactElement {
  const { colors, radius: rad, spacing: sp, typography: type } = useTheme();
  const insets = useSafeAreaInsets();

  return (
    <View style={[styles.flex, { backgroundColor: colors.background }]}>
      <FlatList
        data={items}
        keyExtractor={(item) => item.id}
        renderItem={({ item }) => (
          <View style={[styles.card, { backgroundColor: colors.surface, borderRadius: rad.md }]}>
            <Text style={[type.heading, { color: colors.text }]}>{item.title}</Text>
            {item.subtitle ? (
              <Text style={[styles.subtitle, type.body, { color: colors.textMuted }]}>
                {item.subtitle}
              </Text>
            ) : null}
            {item.meta ? (
              <Text style={[styles.meta, type.caption, { color: colors.textMuted }]}>{item.meta}</Text>
            ) : null}
          </View>
        )}
        ItemSeparatorComponent={() => <View style={{ height: sp.sm }} />}
        contentContainerStyle={[
          styles.content,
          {
            padding: sp.lg,
            paddingBottom: insets.bottom + sp.xl,
          },
        ]}
        ListEmptyComponent={
          <@@PSFX@@EmptyState title={emptyTitle} message={emptyMessage} />
        }
        refreshControl={
          onRefresh ? (
            <RefreshControl refreshing={refreshing} onRefresh={onRefresh} tintColor={colors.primary} />
          ) : undefined
        }
      />
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  content: { flexGrow: 1 },
  card: {
    padding: spacing.md,
  },
  subtitle: { marginTop: spacing.xs },
  meta: { marginTop: spacing.xs },
  empty: { alignItems: 'center', padding: spacing.xl, gap: spacing.xs },
  emptyMessage: { textAlign: 'center' },
});
""", {"@@PASCAL@@": ctx.screen_name,
          "@@PSFX@@": _pascal_sfx(ctx.screen_name, "ListScreen")})
    # Route taps through a Pressable card instead of a FlatList prop.
    code = code.replace(
        "          <View style={[styles.card, { backgroundColor: colors.surface, borderRadius: rad.md }]}>",
        "          <Pressable\n            accessibilityRole=\"button\"\n            onPress={onPressItem ? () => onPressItem(item) : undefined}\n            style={[styles.card, { backgroundColor: colors.surface, borderRadius: rad.md }]}>",
    )
    code = code.replace(
        "  FlatList,\n  RefreshControl,",
        "  FlatList,\n  Pressable,\n  RefreshControl,",
    )
    code = code.replace(
        "import { useTheme, radius, spacing, typography } from '../theme/theme';",
        "import { useTheme, radius, spacing, typography } from '../theme/theme';",
    )
    code = code.replace("          </View>\n        )}", "          </Pressable>\n        )}")
    files = {
        "src/screens/%s.tsx" % _pascal_sfx(ctx.screen_name, "ListScreen"): code,
        "src/theme/theme.ts": _TS_THEME,
    }
    warnings = [
        "No external dependencies beyond react-native-safe-area-context (npx expo install react-native-safe-area-context).",
    ]
    return files, warnings


def _expo_card(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _TS_HEADER + r"""import React from 'react';
import {
  Image,
  ImageSourcePropType,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useTheme, radius, spacing, typography } from '../theme/theme';

export interface @@PSFX@@Props {
  title: string;
  subtitle?: string;
  /** Optional local image source rendered above the content. */
  image?: ImageSourcePropType;
  onPress?: () => void;
  /** Trailing right-aligned content (e.g. a price or badge). */
  trailing?: React.ReactNode;
}

/**
 * Themed media card with platform-correct shadow/elevation and an optional
 * image slot. Falls back to a tinted placeholder block when no image given.
 */
export function @@PSFX@@({
  title,
  subtitle,
  image,
  onPress,
  trailing,
}: @@PSFX@@Props): React.ReactElement {
  const { colors, radius: rad, spacing: sp, typography: type } = useTheme();
  const body = (
    <>
      {image ? (
        <Image source={image} style={styles.image} resizeMode="cover" />
      ) : (
        <View style={[styles.imagePlaceholder, { backgroundColor: colors.border }]}>
          <Text style={[styles.placeholderInitial, type.title, { color: colors.textMuted }]}>
            {title.slice(0, 1).toUpperCase()}
          </Text>
        </View>
      )}
      <View style={styles.content}>
        <View style={styles.titleRow}>
          <Text style={[type.heading, { color: colors.text, flexShrink: 1 }]} numberOfLines={2}>
            {title}
          </Text>
          {trailing}
        </View>
        {subtitle ? (
          <Text style={[type.body, { color: colors.textMuted, marginTop: sp.xs }]} numberOfLines={2}>
            {subtitle}
          </Text>
        ) : null}
      </View>
    </>
  );

  const cardStyle = [styles.card, { backgroundColor: colors.surface, borderRadius: rad.lg }];

  if (!onPress) {
    return <View style={cardStyle}>{body}</View>;
  }
  return (
    <Pressable
      accessibilityRole="button"
      accessibilityLabel={title}
      onPress={onPress}
      android_ripple={{ color: 'rgba(0,0,0,0.12)' }}
      style={({ pressed }) => [cardStyle, { opacity: pressed ? 0.85 : 1 }]}
    >
      {body}
    </Pressable>
  );
}

const styles = StyleSheet.create({
  card: {
    backgroundColor: '#FFFFFF',
    overflow: 'hidden',
    // iOS shadow
    shadowColor: '#000000',
    shadowOffset: { width: 0, height: 2 },
    shadowOpacity: 0.1,
    shadowRadius: 6,
    // Android elevation
    elevation: 2,
  },
  image: { width: '100%', height: 160 },
  imagePlaceholder: {
    height: 120,
    alignItems: 'center',
    justifyContent: 'center',
  },
  placeholderInitial: { opacity: 0.6 },
  content: { padding: spacing.md },
  titleRow: { flexDirection: 'row', alignItems: 'center', gap: spacing.sm },
});
""", {"@@PASCAL@@": ctx.screen_name,
          "@@PSFX@@": _pascal_sfx(ctx.screen_name, "Card")})
    # Dark-aware card background without extra dependency: the two replacement
    # passes above already moved backgroundColor into component scope.
    files = {
        "src/components/%s.tsx" % _pascal_sfx(ctx.screen_name, "Card"): code,
        "src/theme/theme.ts": _TS_THEME,
    }
    warnings = ["No external dependencies: uses React Native primitives only."]
    return files, warnings


def _expo_modal(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _TS_HEADER + r"""import React from 'react';
import {
  Modal,
  Pressable,
  StyleSheet,
  Text,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { useTheme, radius, spacing, typography } from '../theme/theme';

export interface @@PASCAL@@Props {
  visible: boolean;
  onClose: () => void;
  title?: string;
  children: React.ReactNode;
  /** Hide the drag handle when the sheet hosts critical content. */
  hideHandle?: boolean;
}

/**
 * Bottom-sheet modal: backdrop tap and Android back button both request
 * close through `onClose`. Insets-aware on notched devices.
 */
export function @@PASCAL@@({
  visible,
  onClose,
  title,
  children,
  hideHandle = false,
}: @@PASCAL@@Props): React.ReactElement {
  const { colors, radius: rad, spacing: sp, typography: type } = useTheme();
  const insets = useSafeAreaInsets();

  return (
    <Modal
      visible={visible}
      transparent
      animationType="slide"
      onRequestClose={onClose}
      accessibilityViewIsModal
    >
      <View style={styles.backdrop}>
        <Pressable
          accessibilityLabel="Close"
          accessibilityRole="button"
          onPress={onClose}
          style={StyleSheet.absoluteFill}
        />
        <View
          style={[
            styles.sheet,
            {
              backgroundColor: colors.background,
              borderTopLeftRadius: rad.lg,
              borderTopRightRadius: rad.lg,
              paddingBottom: insets.bottom + sp.md,
            },
          ]}
        >
          {!hideHandle ? <View style={[styles.handle, { backgroundColor: colors.border }]} /> : null}
          {title ? (
            <Text accessibilityRole="header" style={[type.heading, { color: colors.text, marginBottom: sp.sm }]}>
              {title}
            </Text>
          ) : null}
          {children}
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  backdrop: {
    flex: 1,
    backgroundColor: 'rgba(0, 0, 0, 0.45)',
    justifyContent: 'flex-end',
  },
  sheet: {
    maxHeight: '88%',
    paddingHorizontal: spacing.lg,
    paddingTop: spacing.sm,
  },
  handle: {
    alignSelf: 'center',
    width: 40,
    height: 4,
    borderRadius: radius.full,
    marginBottom: spacing.md,
  },
});
""", {"@@PASCAL@@": ctx.screen_name})
    files = {
        "src/components/%s.tsx" % ctx.screen_name: code,
        "src/theme/theme.ts": _TS_THEME,
    }
    warnings = ["No external dependencies beyond react-native-safe-area-context (npx expo install react-native-safe-area-context)."]
    return files, warnings


def _expo_skeleton(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _TS_HEADER + r"""import React, { useEffect, useRef } from 'react';
import { Animated, Easing, StyleSheet, View, ViewStyle } from 'react-native';
import { useTheme, radius, spacing } from '../theme/theme';

export interface @@PASCAL@@Props {
  /** Number of skeleton rows to render. */
  rows?: number;
  /** Row height in dp. */
  rowHeight?: number;
  /** Show a circular avatar block on each row. */
  withAvatar?: boolean;
  style?: ViewStyle;
}

/**
 * Pulsing skeleton loader built on the React Native Animated API
 * (no external animation dependency). Stops cleanly on unmount.
 */
export function @@PASCAL@@({
  rows = 3,
  rowHeight = 64,
  withAvatar = false,
  style,
}: @@PASCAL@@Props): React.ReactElement {
  const { colors, radius: rad, spacing: sp } = useTheme();
  const pulse = useRef(new Animated.Value(0.45)).current;

  useEffect(() => {
    const loop = Animated.loop(
      Animated.sequence([
        Animated.timing(pulse, {
          toValue: 1,
          duration: 700,
          easing: Easing.inOut(Easing.quad),
          useNativeDriver: true,
        }),
        Animated.timing(pulse, {
          toValue: 0.45,
          duration: 700,
          easing: Easing.inOut(Easing.quad),
          useNativeDriver: true,
        }),
      ]),
    );
    loop.start();
    return () => loop.stop();
  }, [pulse]);

  return (
    <View style={style} accessibilityElementsHidden accessibilityLabel="Loading content">
      {Array.from({ length: rows }, (_, index) => (
        <Animated.View key={index} style={[styles.row, { opacity: pulse, marginBottom: sp.md }]}>
          {withAvatar ? (
            <View style={[styles.avatar, { backgroundColor: colors.border }]} />
          ) : null}
          <View style={styles.lines}>
            <View style={[styles.line, { backgroundColor: colors.border, width: '72%' }]} />
            <View
              style={[
                styles.line,
                { backgroundColor: colors.border, width: '48%', borderRadius: rad.sm, height: rowHeight / 3 },
              ]}
            />
          </View>
        </Animated.View>
      ))}
    </View>
  );
}

const styles = StyleSheet.create({
  row: { flexDirection: 'row', alignItems: 'center' },
  avatar: {
    width: 44,
    height: 44,
    borderRadius: 22,
    marginRight: 12,
  },
  lines: { flex: 1, gap: 8 },
  line: {
    height: 12,
    borderRadius: 6,
  },
});
""", {"@@PASCAL@@": ctx.screen_name})
    files = {
        "src/components/%s.tsx" % ctx.screen_name: code,
        "src/theme/theme.ts": _TS_THEME,
    }
    warnings = ["No external dependencies: built on the built-in Animated API."]
    return files, warnings


def _expo_empty_state(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _TS_HEADER + r"""import React from 'react';
import { Pressable, StyleSheet, Text, View, ViewStyle } from 'react-native';
import { useTheme, radius, spacing, typography } from '../theme/theme';

export interface @@PASCAL@@Props {
  /** Short heading, e.g. 'No results'. */
  title: string;
  /** One or two sentences explaining the empty state. */
  message?: string;
  /** Optional icon slot: pass any element (icon font, image, symbol). */
  icon?: React.ReactNode;
  /** When set, renders a call-to-action button under the message. */
  actionLabel?: string;
  onAction?: () => void;
  style?: ViewStyle;
}

/** Themed empty state with optional icon and call-to-action. */
export function @@PASCAL@@({
  title,
  message,
  icon,
  actionLabel,
  onAction,
  style,
}: @@PASCAL@@Props): React.ReactElement {
  const { colors, radius: rad, spacing: sp, typography: type } = useTheme();
  return (
    <View style={[styles.container, style]} accessibilityRole="text" accessible>
      {icon}
      <Text style={[type.heading, { color: colors.text, marginTop: icon ? sp.md : 0 }]}>
        {title}
      </Text>
      {message ? (
        <Text style={[styles.message, type.body, { color: colors.textMuted }]}>{message}</Text>
      ) : null}
      {actionLabel && onAction ? (
        <Pressable
          accessibilityRole="button"
          onPress={onAction}
          android_ripple={{ color: 'rgba(0,0,0,0.15)' }}
          style={({ pressed }) => [
            styles.action,
            { backgroundColor: colors.primary, borderRadius: rad.md, opacity: pressed ? 0.7 : 1 },
          ]}
        >
          <Text style={[styles.actionLabel, { color: colors.onPrimary }]}>{actionLabel}</Text>
        </Pressable>
      ) : null}
    </View>
  );
}

const styles = StyleSheet.create({
  container: {
    alignItems: 'center',
    justifyContent: 'center',
    padding: spacing.xl,
    gap: spacing.xs,
  },
  message: {
    textAlign: 'center',
    marginTop: spacing.xs,
  },
  action: {
    marginTop: spacing.md,
    paddingHorizontal: spacing.lg,
    minHeight: 44,
    alignItems: 'center',
    justifyContent: 'center',
  },
  actionLabel: { fontWeight: '600', fontSize: typography.body.fontSize },
});
""", {"@@PASCAL@@": ctx.screen_name})
    files = {
        "src/components/%s.tsx" % ctx.screen_name: code,
        "src/theme/theme.ts": _TS_THEME,
    }
    warnings = ["No external dependencies: uses React Native primitives only."]
    return files, warnings


def _expo_tab_bar(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    tabs = ctx.extras["tabs"]
    param_list_lines = []
    screen_imports = []
    screen_files: Dict[str, str] = {}
    screen_registrations = []
    tab_bar_items = []
    for tab in tabs:
        route = tab + "Tab"
        param_list_lines.append("  %s: undefined;" % route)
        screen_imports.append("import { %sScreen } from '../screens/%sScreen';" % (tab, tab))
        screen_registrations.append(
            '        <Tab.Screen name="%s" component={%sScreen} options={{ title: \'%s\' }} />'
            % (route, tab, tab)
        )
        tab_bar_items.append(
            "    { route: '%s', label: '%s' }," % (route, tab)
        )
        screen_files["src/screens/%s.tsx" % _pascal_sfx(tab, "Screen")] = (
            _TS_HEADER
            + r"""import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { useTheme, spacing, typography } from '../theme/theme';

/** Placeholder content for the @@TAB@@ tab. Replace with your feature. */
export function @@TAB@@Screen(): React.ReactElement {
  const { colors, typography: type } = useTheme();
  return (
    <View style={[styles.root, { backgroundColor: colors.background }]}>
      <Text style={[type.title, { color: colors.text }]} accessibilityRole="header">
        @@TAB@@
      </Text>
      <Text style={[type.body, { color: colors.textMuted }]}>
        Replace this placeholder with the @@TAB@@ feature screen.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: spacing.lg,
    gap: spacing.sm,
  },
});
""".replace("@@TAB@@", tab)
        )

    tab_navigator = _fill(
        _TS_HEADER
            + r"""import React from 'react';
import { NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
@@IMPORTS@@
import { themeForScheme } from '../theme/theme';
import { useColorScheme } from 'react-native';
import { AppTabBar } from './AppTabBar';

export type TabParamList = {
@@PARAMS@@
};

const Tab = createBottomTabNavigator<TabParamList>();

/** Bottom tabs wired with the custom AppTabBar and themed options. */
export function TabNavigator(): React.ReactElement {
  const scheme = useColorScheme();
  const theme = themeForScheme(scheme);
  const { colors } = theme;

  return (
    <NavigationContainer theme={toNavigationTheme(theme)}>
      <Tab.Navigator
        tabBar={(props) => <AppTabBar {...props} />}
        screenOptions={{
          headerShown: true,
          headerStyle: { backgroundColor: colors.surface },
          headerTintColor: colors.text,
        }}
      >
@@SCREENS@@
      </Tab.Navigator>
    </NavigationContainer>
  );
}

/** Minimal adapter: maps our theme tokens onto a NavigationTheme. */
function toNavigationTheme(theme: ReturnType<typeof themeForScheme>) {
  const { colors, scheme } = theme;
  return {
    dark: scheme === 'dark',
    colors: {
      primary: colors.primary,
      background: colors.background,
      card: colors.surface,
      text: colors.text,
      border: colors.border,
      notification: colors.danger,
    },
    fonts: {
      regular: { fontFamily: 'System', fontWeight: '400' },
      medium: { fontFamily: 'System', fontWeight: '500' },
      bold: { fontFamily: 'System', fontWeight: '700' },
      heavy: { fontFamily: 'System', fontWeight: '800' },
    },
  } as const;
}
""", {
            "@@IMPORTS@@": "\n".join(screen_imports),
            "@@PARAMS@@": "\n".join(param_list_lines),
            "@@SCREENS@@": "\n".join(screen_registrations),
        })

    app_tab_bar = _fill(
        _TS_HEADER
            + r"""import React from 'react';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import type { BottomTabBarProps } from '@react-navigation/bottom-tabs';
import { useTheme, spacing, typography } from '../theme/theme';

const TAB_ITEMS = [
@@ITEMS@@
] as const;

/**
 * Custom bottom tab bar. Renders one pressable tab per route in the
 * navigator; icons can be added per route inside `renderIcon`.
 */
export function AppTabBar({ state, descriptors, navigation }: BottomTabBarProps): React.ReactElement {
  const { colors, spacing: sp, typography: type } = useTheme();
  const insets = useSafeAreaInsets();

  return (
    <View
      style={[
        styles.bar,
        {
          backgroundColor: colors.surface,
          borderTopColor: colors.border,
          paddingBottom: insets.bottom + sp.xs,
        },
      ]}
      accessibilityRole="tablist"
    >
      {state.routes.map((route, index) => {
        const focused = state.index === index;
        const label =
          (TAB_ITEMS as readonly { route: string; label: string }[]).find(
            (item) => item.route === route.name,
          )?.label ?? route.name;
        const options = descriptors[route.key]?.options;

        const onPress = () => {
          const event = navigation.emit({ type: 'tabPress', target: route.key, canPreventDefault: true });
          if (!focused && !event.defaultPrevented) {
            navigation.navigate(route.name, route.params);
          }
        };

        return (
          <Pressable
            key={route.key}
            accessibilityRole="tab"
            accessibilityState={{ selected: focused }}
            accessibilityLabel={options?.title ?? label}
            onPress={onPress}
            style={styles.tab}
          >
            {renderIcon(route.name, focused)}
            <Text
              style={[
                type.caption,
                { color: focused ? colors.primary : colors.textMuted, fontWeight: focused ? '700' : '500' },
              ]}
              numberOfLines={1}
            >
              {options?.title ?? label}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

/** Replace the bodies below with icon fonts or images per route. */
function renderIcon(_routeName: string, _focused: boolean): React.ReactElement | null {
  return null;
}

const styles = StyleSheet.create({
  bar: {
    flexDirection: 'row',
    borderTopWidth: StyleSheet.hairlineWidth,
    paddingTop: spacing.sm,
  },
  tab: {
    flex: 1,
    alignItems: 'center',
    gap: 2,
  },
});
""", {"@@ITEMS@@": "\n".join(tab_bar_items)})

    files = {
        "src/navigation/TabNavigator.tsx": tab_navigator,
        "src/navigation/AppTabBar.tsx": app_tab_bar,
        "src/theme/theme.ts": _TS_THEME,
    }
    files.update(screen_files)
    warnings = [
        "Dependencies (unpinned; resolve inside the project): "
        "npm install @react-navigation/native @react-navigation/bottom-tabs && "
        "npx expo install react-native-screens react-native-safe-area-context",
        "renderIcon() in AppTabBar.tsx returns null; add your icon set (icon font, "
        "SVG or images) per route.",
    ]
    return files, warnings


def _expo_drawer(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    items = ctx.extras["items"]
    param_lines = []
    imports = []
    registrations = []
    screen_files: Dict[str, str] = {}
    for item in items:
        route = item + "Drawer"
        param_lines.append("  %s: undefined;" % route)
        imports.append("import { %sScreen } from '../screens/%sScreen';" % (item, item))
        registrations.append(
            '        <Drawer.Screen name="%s" component={%sScreen} options={{ title: \'%s\' }} />'
            % (route, item, item)
        )
        screen_files["src/screens/%s.tsx" % _pascal_sfx(item, "Screen")] = (
            _TS_HEADER
            + r"""import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { useTheme, spacing, typography } from '../theme/theme';

/** Placeholder content for the @@ITEM@@ drawer entry. Replace with your feature. */
export function @@ITEM@@Screen(): React.ReactElement {
  const { colors, typography: type } = useTheme();
  return (
    <View style={[styles.root, { backgroundColor: colors.background }]}>
      <Text style={[type.title, { color: colors.text }]} accessibilityRole="header">
        @@ITEM@@
      </Text>
      <Text style={[type.body, { color: colors.textMuted }]}>
        Replace this placeholder with the @@ITEM@@ feature screen.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: spacing.lg,
    gap: spacing.sm,
  },
});
""".replace("@@ITEM@@", item)
        )

    navigator = _fill(
        _TS_HEADER
            + r"""import React from 'react';
import { NavigationContainer } from '@react-navigation/native';
import { createDrawerNavigator } from '@react-navigation/drawer';
@@IMPORTS@@
import { toNavigationTheme } from './navigationTheme';
import { useTheme, themeForScheme } from '../theme/theme';
import { useColorScheme } from 'react-native';

export type DrawerParamList = {
@@PARAMS@@
};

const Drawer = createDrawerNavigator<DrawerParamList>();

/** Drawer navigation with themed drawer content. */
export function DrawerNavigator(): React.ReactElement {
  const scheme = useColorScheme();
  const theme = themeForScheme(scheme);
  const { colors } = useTheme();

  return (
    <NavigationContainer theme={toNavigationTheme(theme)}>
      <Drawer.Navigator
        screenOptions={{
          headerShown: true,
          headerStyle: { backgroundColor: colors.surface },
          headerTintColor: colors.text,
          drawerStyle: { backgroundColor: colors.surface, width: 300 },
          drawerActiveTintColor: colors.primary,
          drawerInactiveTintColor: colors.textMuted,
        }}
      >
@@SCREENS@@
      </Drawer.Navigator>
    </NavigationContainer>
  );
}
""", {
            "@@IMPORTS@@": "\n".join(imports),
            "@@PARAMS@@": "\n".join(param_lines),
            "@@SCREENS@@": "\n".join(registrations),
        })

    files = {
        "src/navigation/DrawerNavigator.tsx": navigator,
        "src/navigation/navigationTheme.ts": _TS_NAV_THEME,
        "src/theme/theme.ts": _TS_THEME,
    }
    files.update(screen_files)
    warnings = [
        "Dependencies (unpinned; resolve inside the project): "
        "npm install @react-navigation/native @react-navigation/drawer && "
        "npx expo install react-native-screens react-native-safe-area-context "
        "react-native-gesture-handler react-native-reanimated",
        "'react-native-gesture-handler' must be imported at the very top of your "
        "app entry (index.ts): add `import 'react-native-gesture-handler';` before App import.",
        "react-native-reanimated requires the babel plugin: in babel.config.js add "
        "'react-native-reanimated/plugin' as the last plugin.",
    ]
    return files, warnings


def _expo_material(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _TS_HEADER + r"""import { useWindowDimensions } from 'react-native';
import { useTheme } from './theme';

/**
 * Material Design 3 token layer on top of the app theme: elevation levels,
 * shape scale and state-layer opacities. Pure React Native approximation -
 * for the full MD3 component set consider react-native-paper (not installed).
 */
export interface MaterialTokens {
  /** MD3 elevation levels 0-5 mapped to shadow/elevation styles. */
  elevation: (level: 0 | 1 | 2 | 3 | 4 | 5) => {
    shadowColor: string;
    shadowOpacity: number;
    shadowRadius: number;
    shadowOffset: { width: number; height: number };
    elevation: number;
  };
  /** MD3 corner radius scale. */
  shape: { small: number; medium: number; large: number; extraLarge: number };
  /** State layer opacity for pressed/focused/dragged states. */
  stateLayer: { hover: number; focus: number; pressed: number; dragged: number };
  /** Ripple color for android_ripple from the current scheme. */
  ripple: string;
}

export function useMaterialTokens(): MaterialTokens {
  const { colors, scheme } = useTheme();
  return {
    elevation: (level) => {
      const opacityTable = { 0: 0, 1: 0.12, 2: 0.16, 3: 0.2, 4: 0.24, 5: 0.28 };
      const radiusTable = { 0: 0, 1: 2, 2: 4, 3: 8, 4: 12, 5: 16 };
      const elevationTable = { 0: 0, 1: 1, 2: 3, 3: 6, 4: 8, 5: 12 };
      return {
        shadowColor: '#000000',
        shadowOpacity: scheme === 'dark' ? 0 : opacityTable[level],
        shadowRadius: radiusTable[level],
        shadowOffset: { width: 0, height: level },
        elevation: elevationTable[level],
      };
    },
    shape: { small: 8, medium: 12, large: 16, extraLarge: 28 },
    stateLayer: { hover: 0.08, focus: 0.12, pressed: 0.12, dragged: 0.16 },
    ripple: scheme === 'dark' ? 'rgba(255,255,255,0.12)' : 'rgba(0,0,0,0.12)',
  };
}

/**
 * Responsive helpers aligned with Material 3 window size classes.
 * compact < 600dp, medium 600-840dp, expanded >= 840dp.
 */
export function useWindowClass(): 'compact' | 'medium' | 'expanded' {
  const { width } = useWindowDimensions();
  if (width >= 840) return 'expanded';
  if (width >= 600) return 'medium';
  return 'compact';
}
"""
    files = {
        "src/theme/material.ts": code,
        "src/theme/theme.ts": _TS_THEME,
    }
    warnings = [
        "Token-layer approximation of Material 3 for React Native; for full MD3 "
        "widgets consider react-native-paper (not installed by this generator).",
        "No external dependencies: uses React Native primitives only.",
    ]
    return files, warnings


def _expo_cupertino(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _TS_HEADER + r"""import { Platform, PlatformColor, TextStyle, ViewStyle } from 'react-native';
import { useTheme } from './theme';

/**
 * Cupertino (iOS) token layer. On iOS real system colors are used via
 * PlatformColor; on other platforms fallback hex values are returned so the
 * tokens stay safe everywhere.
 */
function iosColor(iosName: string, fallback: string) {
  return Platform.OS === 'ios' ? PlatformColor(iosName) : fallback;
}

export interface CupertinoTokens {
  colors: {
    systemBackground: ReturnType<typeof iosColor>;
    secondarySystemBackground: ReturnType<typeof iosColor>;
    label: ReturnType<typeof iosColor>;
    secondaryLabel: ReturnType<typeof iosColor>;
    separator: ReturnType<typeof iosColor>;
    systemBlue: ReturnType<typeof iosColor>;
    systemRed: ReturnType<typeof iosColor>;
    systemGreen: ReturnType<typeof iosColor>;
  };
  typography: {
    largeTitle: TextStyle;
    title1: TextStyle;
    headline: TextStyle;
    body: TextStyle;
    footnote: TextStyle;
    caption1: TextStyle;
  };
  /** Hairline separator view style (iOS style 0.5px line). */
  separator: ViewStyle;
  /** iOS-like row height for list-like controls. */
  rowHeight: number;
}

export function useCupertinoTokens(): CupertinoTokens {
  const { colors } = useTheme();
  return {
    colors: {
      systemBackground: iosColor('systemBackground', colors.background),
      secondarySystemBackground: iosColor('secondarySystemBackground', colors.surface),
      label: iosColor('label', colors.text),
      secondaryLabel: iosColor('secondaryLabel', colors.textMuted),
      separator: iosColor('separator', colors.border),
      systemBlue: iosColor('systemBlue', colors.primary),
      systemRed: iosColor('systemRed', colors.danger),
      systemGreen: iosColor('systemGreen', colors.success),
    },
    typography: {
      largeTitle: { fontSize: 34, fontWeight: '700' },
      title1: { fontSize: 28, fontWeight: '700' },
      headline: { fontSize: 17, fontWeight: '600' },
      body: { fontSize: 17, fontWeight: '400' },
      footnote: { fontSize: 13, fontWeight: '400' },
      caption1: { fontSize: 12, fontWeight: '400' },
    },
    separator: {
      height: StyleSheet_hairline(),
      backgroundColor: iosColor('separator', colors.border),
    } as ViewStyle,
    rowHeight: 44,
  };
}

function StyleSheet_hairline(): number {
  // 1/3px mirrors UIKit hairline; keep integer-safe across densities.
  return 0.5;
}
"""
    files = {
        "src/theme/cupertino.ts": code,
        "src/theme/theme.ts": _TS_THEME,
    }
    warnings = [
        "PlatformColor names resolve on iOS only; fallbacks are provided for other platforms.",
        "No external dependencies: uses React Native primitives only.",
    ]
    return files, warnings


def _expo_adaptive(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _TS_HEADER + r"""import { Platform, PressableProps } from 'react-native';
import { useTheme } from './theme';
import { useMaterialTokens } from './material';
import { useCupertinoTokens } from './cupertino';

/**
 * Adaptive styling helpers: iOS gets Cupertino-flavored press behavior,
 * Android gets Material ripple + elevation. All hooks here are called
 * unconditionally (rules-of-hooks safe).
 */

/** Suggested press opacity per platform (iOS convention). */
export function pressedOpacity(platform: typeof Platform.OS): number {
  return platform === 'ios' ? 0.6 : 0.85;
}

/** Corner radius convention per platform (iOS squarer, Material rounder). */
export function useAdaptiveRadius(): number {
  const { radius } = useTheme();
  return Platform.OS === 'ios' ? 10 : radius.md;
}

/** Hook: Pressable props tuned per platform (ripple vs opacity). */
export function useAdaptivePressProps(accessibilityLabel?: string): PressableProps {
  const material = useMaterialTokens();
  if (Platform.OS === 'android') {
    return {
      accessibilityLabel,
      android_ripple: { color: material.ripple, borderless: false },
    };
  }
  return {
    accessibilityLabel,
    android_ripple: undefined,
  };
}

/** Merged token bundle for components that branch per platform. */
export function useAdaptiveTheme() {
  const material = useMaterialTokens();
  const cupertino = useCupertinoTokens();
  return Platform.select({
    ios: { flavor: 'cupertino' as const, tokens: cupertino },
    default: { flavor: 'material' as const, tokens: material },
  });
}
"""
    files = {
        "src/theme/adaptive.ts": code,
        "src/theme/material.ts": _expo_material(ctx)[0]["src/theme/material.ts"],
        "src/theme/cupertino.ts": _expo_cupertino(ctx)[0]["src/theme/cupertino.ts"],
        "src/theme/theme.ts": _TS_THEME,
    }
    warnings = [
        "No external dependencies: uses React Native primitives only.",
        "useAdaptivePressProps is a hook - call it from a component body, not inside callbacks.",
    ]
    return files, warnings


def _expo_tablet(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    breakpoint_code = _TS_HEADER + r"""import { ScaledSize, useWindowDimensions } from 'react-native';

export type Breakpoint = 'compact' | 'medium' | 'expanded';

/** Material 3 window width classes: <600, 600-839, >=840. */
export function breakpointForWidth(width: number): Breakpoint {
  if (width >= 840) return 'expanded';
  if (width >= 600) return 'medium';
  return 'compact';
}

/** Reactive breakpoint for the current window (updates on rotation/fold). */
export function useBreakpoint(): Breakpoint {
  const { width } = useWindowDimensions();
  return breakpointForWidth(width);
}

/** True when two-pane layouts should be used. */
export function useIsExpanded(): boolean {
  return useBreakpoint() === 'expanded';
}

/** Utility kept for tests/mocking: measure without hooks. */
export function measureBreakpoint(dimensions: ScaledSize): Breakpoint {
  return breakpointForWidth(dimensions.width);
}
"""
    two_pane_code = _TS_HEADER + r"""import React from 'react';
import { ScrollView, StyleSheet, View } from 'react-native';
import { useIsExpanded } from '../hooks/useBreakpoint';

export interface TwoPaneLayoutProps {
  /** Primary content (list column on expanded, full content on compact). */
  primary: React.ReactNode;
  /** Detail column rendered only on expanded widths. */
  secondary?: React.ReactNode;
  /** Gap between panes in dp (expanded only). */
  gap?: number;
}

/**
 * Two-pane responsive layout: side-by-side on tablets/unfolded foldables,
 * stacked single pane elsewhere. `secondary` is optional so phones simply
 * render `primary`.
 */
export function TwoPaneLayout({
  primary,
  secondary,
  gap = 16,
}: TwoPaneLayoutProps): React.ReactElement {
  const expanded = useIsExpanded();

  if (!expanded || !secondary) {
    return <View style={styles.flex}>{primary}</View>;
  }

  return (
    <View style={[styles.flex, styles.row, { gap }]}>
      <View style={styles.pane}>{primary}</View>
      <View style={[styles.divider]} />
      <View style={styles.pane}>
        <ScrollView contentContainerStyle={styles.scrollContent}>{secondary}</ScrollView>
      </View>
    </View>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  row: { flexDirection: 'row' },
  pane: { flex: 1 },
  divider: { width: StyleSheet.hairlineWidth, backgroundColor: '#94A3B8' },
  scrollContent: { padding: 16 },
});
"""
    files = {
        "src/hooks/useBreakpoint.ts": breakpoint_code,
        "src/layout/TwoPaneLayout.tsx": two_pane_code,
        "src/theme/theme.ts": _TS_THEME,
    }
    warnings = [
        "No external dependencies: uses React Native primitives only.",
        "Foldable posture (hinge-aware) needs a native library; width classes only.",
    ]
    return files, warnings


def _expo_dark_mode(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _TS_HEADER + r"""import React, { useCallback, useEffect, useMemo, useState } from 'react';
import { ColorSchemeName, Pressable, StyleSheet, Text, View } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { ThemeContext, themeForScheme, useTheme } from './theme';

export type ThemeMode = 'system' | 'light' | 'dark';

const STORAGE_KEY = '@@SLUG@@.themeMode';

function resolveScheme(mode: ThemeMode, system: ColorSchemeName | null): 'light' | 'dark' {
  if (mode === 'system') return system === 'dark' ? 'dark' : 'light';
  return mode;
}

export interface ThemePreferenceProviderProps {
  children: React.ReactNode;
}

/**
 * Persists the user's theme preference and feeds the resolved scheme into
 * the shared ThemeContext from src/theme/theme.ts. Mount ABOVE your app
 * content: <ThemePreferenceProvider><App /></ThemePreferenceProvider>.
 */
export function ThemePreferenceProvider({ children }: ThemePreferenceProviderProps): React.ReactElement {
  const [mode, setMode] = useState<ThemeMode>('system');
  const [hydrated, setHydrated] = useState(false);

  useEffect(() => {
    let mounted = true;
    AsyncStorage.getItem(STORAGE_KEY)
      .then((stored) => {
        if (mounted && (stored === 'light' || stored === 'dark' || stored === 'system')) {
          setMode(stored);
        }
      })
      .catch(() => {
        // Corrupt or missing preference falls back to 'system'.
      })
      .finally(() => {
        if (mounted) setHydrated(true);
      });
    return () => {
      mounted = false;
    };
  }, []);

  const changeMode = useCallback((next: ThemeMode) => {
    setMode(next);
    AsyncStorage.setItem(STORAGE_KEY, next).catch(() => {
      // Preference still applies for this session even if persistence fails.
    });
  }, []);

  // `hydrated` gates one render pass so the stored mode wins over the default.
  const value = useMemo(() => ({ mode, changeMode, hydrated }), [mode, changeMode, hydrated]);

  return <ThemeModeContext.Provider value={value}>{children}</ThemeModeContext.Provider>;
}

interface ThemeModeContextValue {
  mode: ThemeMode;
  changeMode: (next: ThemeMode) => void;
  hydrated: boolean;
}

const ThemeModeContext = React.createContext<ThemeModeContextValue | null>(null);

export function useThemeMode(): ThemeModeContextValue {
  const value = React.useContext(ThemeModeContext);
  if (!value) {
    throw new Error('useThemeMode must be used inside <ThemePreferenceProvider>');
  }
  return value;
}

const MODES: readonly ThemeMode[] = ['system', 'light', 'dark'];

/** Segmented control to switch between system/light/dark appearance. */
export function ThemeModeSwitch(): React.ReactElement {
  const { mode, changeMode } = useThemeMode();
  const { colors, radius: rad, spacing: sp, typography: type } = useTheme();

  return (
    <View
      style={[styles.row, { backgroundColor: colors.surface, borderRadius: rad.md, padding: sp.xs }]}
      accessibilityRole="radiogroup"
      accessibilityLabel="Appearance"
    >
      {MODES.map((option) => {
        const selected = option === mode;
        return (
          <Pressable
            key={option}
            accessibilityRole="radio"
            accessibilityState={{ selected }}
            onPress={() => changeMode(option)}
            style={[
              styles.segment,
              { borderRadius: rad.sm, backgroundColor: selected ? colors.primary : 'transparent' },
            ]}
          >
            <Text
              style={[type.caption, { color: selected ? colors.onPrimary : colors.textMuted }]}
            >
              {option[0].toUpperCase() + option.slice(1)}
            </Text>
          </Pressable>
        );
      })}
    </View>
  );
}

const styles = StyleSheet.create({
  row: {
    flexDirection: 'row',
    alignSelf: 'flex-start',
  },
  segment: {
    paddingHorizontal: 14,
    paddingVertical: 8,
  },
});

export { resolveScheme, STORAGE_KEY };
""", {"@@SLUG@@": ctx.slug})
    # Feed resolved scheme into the shared ThemeContext via a wrapper:
    code = code.replace(
        "  return <ThemeModeContext.Provider value={value}>{children}</ThemeModeContext.Provider>;",
        "  const systemScheme = useColorScheme();\n"
        "  const themeValue = useMemo(\n"
        "    () => themeForScheme(resolveScheme(mode, systemScheme)),\n"
        "    [mode, systemScheme],\n"
        "  );\n"
        "  return (\n"
        "    <ThemeModeContext.Provider value={value}>\n"
        "      <ThemeContext.Provider value={themeValue}>{children}</ThemeContext.Provider>\n"
        "    </ThemeModeContext.Provider>\n"
        "  );",
    )
    code = code.replace(
        "import { ColorSchemeName, Pressable, StyleSheet, Text, View } from 'react-native';",
        "import { ColorSchemeName, Pressable, StyleSheet, Text, useColorScheme, View } from 'react-native';",
    )
    files = {
        "src/theme/ThemePreference.tsx": code,
        "src/theme/theme.ts": _TS_THEME,
    }
    warnings = [
        "Dependency (unpinned): npx expo install @react-native-async-storage/async-storage",
        "Mount <ThemePreferenceProvider> above your app root; the system fallback in "
        "theme.ts applies until then.",
    ]
    return files, warnings


# ---------------------------------------------------------------------------
# Expo navigation / state / network / storage / auth / device / push modules
# ---------------------------------------------------------------------------

_TS_NAV_THEME = _TS_HEADER + r"""import { ThemeValue } from '../theme/theme';
import type { Theme } from '@react-navigation/native';

/**
 * Maps the app theme onto a React Navigation theme object so navigators
 * match the rest of the app. Kept in one place for every navigator.
 */
export function toNavigationTheme(theme: ThemeValue): Theme {
  const { colors, scheme } = theme;
  return {
    dark: scheme === 'dark',
    colors: {
      primary: colors.primary,
      background: colors.background,
      card: colors.surface,
      text: colors.text,
      border: colors.border,
      notification: colors.danger,
    },
    fonts: {
      regular: { fontFamily: 'System', fontWeight: '400' },
      medium: { fontFamily: 'System', fontWeight: '500' },
      bold: { fontFamily: 'System', fontWeight: '700' },
      heavy: { fontFamily: 'System', fontWeight: '800' },
    },
  };
}
"""


def _expo_nav_stack(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _TS_HEADER + r"""import React from 'react';
import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { toNavigationTheme } from './navigationTheme';
import { useTheme, themeForScheme, spacing, typography } from '../theme/theme';
import { useColorScheme } from 'react-native';

export type RootStackParamList = {
  Home: undefined;
  Detail: { id: string; title?: string };
};

const Stack = createNativeStackNavigator<RootStackParamList>();

function HomeScreen({ navigation }: { navigation: { navigate: (screen: 'Detail', params: RootStackParamList['Detail']) => void } }) {
  const { colors, typography: type } = useTheme();
  const rows = [
    { id: '1', title: 'First item' },
    { id: '2', title: 'Second item' },
    { id: '3', title: 'Third item' },
  ];
  return (
    <View style={[styles.root, { backgroundColor: colors.background }]}>
      <Text style={[type.title, { color: colors.text }]} accessibilityRole="header">
        Home
      </Text>
      {rows.map((row) => (
        <Pressable
          key={row.id}
          accessibilityRole="button"
          onPress={() => navigation.navigate('Detail', { id: row.id, title: row.title })}
          style={({ pressed }) => [styles.row, { opacity: pressed ? 0.6 : 1 }]}
        >
          <Text style={[type.body, { color: colors.text }]}>{row.title}</Text>
          <Text style={[type.caption, { color: colors.textMuted }]}>{'Open >'}</Text>
        </Pressable>
      ))}
    </View>
  );
}

function DetailScreen({
  route,
}: {
  route: { params: RootStackParamList['Detail'] };
}) {
  const { colors, typography: type } = useTheme();
  return (
    <View style={[styles.root, { backgroundColor: colors.background }]}>
      <Text style={[type.title, { color: colors.text }]} accessibilityRole="header">
        {route.params.title ?? 'Detail'}
      </Text>
      <Text style={[type.body, { color: colors.textMuted }]}>
        Item id: {route.params.id}
      </Text>
    </View>
  );
}

/** Root stack navigator with typed params. */
export function RootNavigator(): React.ReactElement {
  const scheme = useColorScheme();
  const theme = themeForScheme(scheme);
  const { colors } = useTheme();
  return (
    <NavigationContainer theme={toNavigationTheme(theme)}>
      <Stack.Navigator
        screenOptions={{
          headerStyle: { backgroundColor: colors.surface },
          headerTintColor: colors.text,
          contentStyle: { backgroundColor: colors.background },
        }}
      >
        <Stack.Screen name="Home" component={HomeScreen} options={{ title: '@@WORDS@@' }} />
        <Stack.Screen name="Detail" component={DetailScreen} />
      </Stack.Navigator>
    </NavigationContainer>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    padding: spacing.lg,
    gap: spacing.md,
  },
  row: {
    flexDirection: 'row',
    justifyContent: 'space-between',
    alignItems: 'center',
    paddingVertical: spacing.md,
  },
});
""", {"@@WORDS@@": ctx.screen_words})
    files = {
        "src/navigation/RootNavigator.tsx": code,
        "src/navigation/navigationTheme.ts": _TS_NAV_THEME,
        "src/theme/theme.ts": _TS_THEME,
    }
    warnings = [
        _NAV_DEPS_WARNING.replace("Add stack/tab/drawer packages as listed per file.",
                                  "Also: npm install @react-navigation/native-stack"),
        "Demo rows in HomeScreen are placeholders; replace with real data.",
    ]
    return files, warnings


def _expo_nav_tab(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    tabs = ctx.extras["tabs"]
    params = "\n".join("  %s: undefined;" % (t + "Tab") for t in tabs)
    imports = "\n".join(
        "import { %sTabScreen } from '../screens/%sTabScreen';" % (t, t) for t in tabs
    )
    screens = "\n".join(
        '        <Tab.Screen name="%sTab" component={%sTabScreen} options={{ title: \'%s\' }} />'
        % (t, t, t)
        for t in tabs
    )
    screen_files: Dict[str, str] = {}
    for t in tabs:
        screen_files["src/screens/%sTabScreen.tsx" % t] = (
            _TS_HEADER
            + r"""import React from 'react';
import { StyleSheet, Text, View } from 'react-native';
import { useTheme, spacing, typography } from '../theme/theme';

/** Placeholder for the @@TAB@@ tab. Replace with the real feature screen. */
export function @@TAB@@TabScreen(): React.ReactElement {
  const { colors, typography: type } = useTheme();
  return (
    <View style={[styles.root, { backgroundColor: colors.background }]}>
      <Text style={[type.title, { color: colors.text }]} accessibilityRole="header">
        @@TAB@@
      </Text>
      <Text style={[type.body, { color: colors.textMuted }]}>
        Replace this placeholder with the @@TAB@@ feature screen.
      </Text>
    </View>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    alignItems: 'center',
    justifyContent: 'center',
    padding: spacing.lg,
    gap: spacing.sm,
  },
});
""".replace("@@TAB@@", t)
        )
    code = _fill(
        _TS_HEADER + r"""import React from 'react';
import { NavigationContainer } from '@react-navigation/native';
import { createBottomTabNavigator } from '@react-navigation/bottom-tabs';
@@IMPORTS@@
import { toNavigationTheme } from './navigationTheme';
import { useTheme, themeForScheme } from '../theme/theme';
import { useColorScheme } from 'react-native';

export type TabParamList = {
@@PARAMS@@
};

export type RootTabParamList = TabParamList;

const Tab = createBottomTabNavigator<RootTabParamList>();

/**
 * Bottom tab navigation as the app root. Screen headers are per-tab; wrap
 * this inside a stack when you need pushed details above the tab bar.
 */
export function RootNavigator(): React.ReactElement {
  const scheme = useColorScheme();
  const theme = themeForScheme(scheme);
  const { colors } = useTheme();

  return (
    <NavigationContainer theme={toNavigationTheme(theme)}>
      <Tab.Navigator
        screenOptions={{
          headerStyle: { backgroundColor: colors.surface },
          headerTintColor: colors.text,
          tabBarActiveTintColor: colors.primary,
          tabBarInactiveTintColor: colors.textMuted,
          tabBarStyle: { backgroundColor: colors.surface, borderTopColor: colors.border },
        }}
      >
@@SCREENS@@
      </Tab.Navigator>
    </NavigationContainer>
  );
}
""", {"@@IMPORTS@@": imports, "@@PARAMS@@": params, "@@SCREENS@@": screens})
    files = {
        "src/navigation/RootNavigator.tsx": code,
        "src/navigation/navigationTheme.ts": _TS_NAV_THEME,
        "src/theme/theme.ts": _TS_THEME,
    }
    files.update(screen_files)
    warnings = [
        _NAV_DEPS_WARNING.replace("Add stack/tab/drawer packages as listed per file.",
                                  "Also: npm install @react-navigation/bottom-tabs"),
        "Icons: add tabBarIcon per Screen options (icon font or image).",
    ]
    return files, warnings


def _expo_nav_modal(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _TS_HEADER + r"""import React from 'react';
import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import { Pressable, StyleSheet, Text, View } from 'react-native';
import { toNavigationTheme } from './navigationTheme';
import { useTheme, themeForScheme, spacing, typography } from '../theme/theme';
import { useNavigation, StackActions, CommonActions } from '@react-navigation/native';
import { useColorScheme } from 'react-native';

export type ModalStackParamList = {
  Home: undefined;
  ComposeModal: { draft?: string } | undefined;
};

const Stack = createNativeStackNavigator<ModalStackParamList>();

function HomeScreen() {
  const navigation = useNavigation<{ navigate: (s: 'ComposeModal', p?: ModalStackParamList['ComposeModal']) => void }>();
  const { colors, typography: type } = useTheme();
  return (
    <View style={[styles.root, { backgroundColor: colors.background }]}>
      <Text style={[type.title, { color: colors.text }]} accessibilityRole="header">
        Home
      </Text>
      <Pressable
        accessibilityRole="button"
        onPress={() => navigation.navigate('ComposeModal', { draft: undefined })}
        style={({ pressed }) => [styles.openButton, { opacity: pressed ? 0.6 : 1 }]}
      >
        <Text style={[type.body, { color: colors.primary }]}>Open modal</Text>
      </Pressable>
    </View>
  );
}

function ComposeModalScreen() {
  const navigation = useNavigation<{ goBack: () => void }>();
  const { colors, typography: type } = useTheme();
  return (
    <View style={[styles.root, { backgroundColor: colors.background }]} accessibilityViewIsModal>
      <Text style={[type.title, { color: colors.text }]} accessibilityRole="header">
        Compose
      </Text>
      <Text style={[type.body, { color: colors.textMuted }]}>
        This screen is presented as a sheet (presentation: 'modal').
      </Text>
      <Pressable accessibilityRole="button" onPress={navigation.goBack}>
        <Text style={[type.body, { color: colors.primary }]}>Close</Text>
      </Pressable>
    </View>
  );
}

/** Stack where modal screens are grouped with presentation: 'modal'. */
export function RootNavigator(): React.ReactElement {
  const scheme = useColorScheme();
  const theme = themeForScheme(scheme);
  const { colors } = useTheme();
  return (
    <NavigationContainer theme={toNavigationTheme(theme)}>
      <Stack.Navigator
        screenOptions={{
          headerStyle: { backgroundColor: colors.surface },
          headerTintColor: colors.text,
        }}
      >
        <Stack.Screen name="Home" component={HomeScreen} options={{ title: '@@WORDS@@' }} />
        <Stack.Group screenOptions={{ presentation: 'modal', gestureEnabled: true }}>
          <Stack.Screen name="ComposeModal" component={ComposeModalScreen} options={{ title: 'Compose' }} />
        </Stack.Group>
      </Stack.Navigator>
    </NavigationContainer>
  );
}

const styles = StyleSheet.create({
  root: {
    flex: 1,
    padding: spacing.lg,
    gap: spacing.md,
  },
  openButton: { paddingVertical: spacing.sm },
});
""", {"@@WORDS@@": ctx.screen_words})
    files = {
        "src/navigation/RootNavigator.tsx": code,
        "src/navigation/navigationTheme.ts": _TS_NAV_THEME,
        "src/theme/theme.ts": _TS_THEME,
    }
    warnings = [
        _NAV_DEPS_WARNING.replace("Add stack/tab/drawer packages as listed per file.",
                                  "Also: npm install @react-navigation/native-stack"),
    ]
    return files, warnings


def _expo_nav_auth_flow(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    snake = ctx.screen_snake
    code = _fill(
        _TS_HEADER + r"""import React, { createContext, useContext, useMemo, useState } from 'react';
import { NavigationContainer } from '@react-navigation/native';
import { createNativeStackNavigator } from '@react-navigation/native-stack';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { toNavigationTheme } from './navigationTheme';
import { useTheme, themeForScheme, radius, spacing, typography } from '../theme/theme';
import { useColorScheme } from 'react-native';

// ---------------------------------------------------------------------------
// Auth context. Deliberately minimal and mock-free: `authenticate` is a
// function YOU provide (API call, SDK, etc). No fake success path exists.
// ---------------------------------------------------------------------------

export interface AuthSession {
  token: string;
  /** Epoch seconds; optional. */
  expiresAt?: number;
}

export type AuthenticateFn = (email: string, password: string) => Promise<AuthSession>;

export interface AuthContextValue {
  session: AuthSession | null;
  signIn: (email: string, password: string) => Promise<void>;
  signOut: () => void;
  busy: boolean;
  error: string | null;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export interface AuthProviderProps {
  children: React.ReactNode;
  /** Your real authentication function (e.g. POST /auth/login). */
  authenticate: AuthenticateFn;
  /** Called after signOut; clear stored tokens here. */
  onSessionCleared?: () => void;
}

export function AuthProvider({ children, authenticate, onSessionCleared }: AuthProviderProps): React.ReactElement {
  const [session, setSession] = useState<AuthSession | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const value = useMemo<AuthContextValue>(
    () => ({
      session,
      busy,
      error,
      signIn: async (email, password) => {
        setBusy(true);
        setError(null);
        try {
          const nextSession = await authenticate(email, password);
          setSession(nextSession);
        } catch (err) {
          setError(err instanceof Error ? err.message : 'Sign-in failed');
        } finally {
          setBusy(false);
        }
      },
      signOut: () => {
        setSession(null);
        setError(null);
        onSessionCleared?.();
      },
    }),
    [session, busy, error, authenticate, onSessionCleared],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside <AuthProvider>');
  return value;
}

// ---------------------------------------------------------------------------
// Screens
// ---------------------------------------------------------------------------

function SignInScreen() {
  const { signIn, busy, error } = useAuth();
  const { colors, radius: rad, spacing: sp, typography: type } = useTheme();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');

  const canSubmit = email.trim().length > 3 && password.length >= 8 && !busy;

  return (
    <KeyboardAvoidingView
      style={[styles.flex, { backgroundColor: colors.background }]}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <View style={[styles.form, { padding: sp.lg }]}>
        <Text style={[type.title, { color: colors.text }]} accessibilityRole="header">
          Sign in
        </Text>
        {error ? (
          <Text accessibilityLiveRegion="polite" style={[styles.error, { color: colors.danger }]}>
            {error}
          </Text>
        ) : null}
        <TextInput
          accessibilityLabel="Email"
          autoCapitalize="none"
          autoCorrect={false}
          keyboardType="email-address"
          placeholder="Email"
          placeholderTextColor={colors.textMuted}
          value={email}
          onChangeText={setEmail}
          style={[styles.input, { borderColor: colors.border, color: colors.text, backgroundColor: colors.surface, borderRadius: rad.sm }]}
        />
        <TextInput
          accessibilityLabel="Password"
          secureTextEntry
          placeholder="Password"
          placeholderTextColor={colors.textMuted}
          value={password}
          onChangeText={setPassword}
          style={[styles.input, { borderColor: colors.border, color: colors.text, backgroundColor: colors.surface, borderRadius: rad.sm }]}
        />
        <Pressable
          accessibilityRole="button"
          accessibilityState={{ busy, disabled: !canSubmit }}
          disabled={!canSubmit}
          onPress={() => signIn(email.trim(), password)}
          style={({ pressed }) => [
            styles.submit,
            { backgroundColor: colors.primary, borderRadius: rad.md, opacity: !canSubmit || pressed ? 0.55 : 1 },
          ]}
        >
          {busy ? (
            <ActivityIndicator color={colors.onPrimary} />
          ) : (
            <Text style={[styles.submitLabel, { color: colors.onPrimary }]}>Sign in</Text>
          )}
        </Pressable>
      </View>
    </KeyboardAvoidingView>
  );
}

function HomeScreen() {
  const { signOut, session } = useAuth();
  const { colors, typography: type } = useTheme();
  return (
    <View style={[styles.root, { backgroundColor: colors.background }]}>
      <Text style={[type.title, { color: colors.text }]} accessibilityRole="header">
        Welcome
      </Text>
      <Text style={[type.body, { color: colors.textMuted }]}>
        Signed in with a session token ({session ? session.token.length : 0} chars). Replace
        this screen with your main app.
      </Text>
      <Pressable accessibilityRole="button" onPress={signOut}>
        <Text style={[type.body, { color: colors.primary }]}>Sign out</Text>
      </Pressable>
    </View>
  );
}

// ---------------------------------------------------------------------------
// Navigator: switches between auth and main stacks based on session state.
// ---------------------------------------------------------------------------

export type AuthFlowParamList = {
  SignIn: undefined;
  Main: undefined;
};

const Stack = createNativeStackNavigator<AuthFlowParamList>();

export function AuthFlowNavigator(): React.ReactElement {
  const { session } = useAuth();
  const scheme = useColorScheme();
  const theme = themeForScheme(scheme);
  const { colors } = useTheme();
  return (
    <NavigationContainer theme={toNavigationTheme(theme)}>
      <Stack.Navigator screenOptions={{ headerStyle: { backgroundColor: colors.surface }, headerTintColor: colors.text }}>
        {session ? (
          <Stack.Screen name="Main" component={HomeScreen} options={{ title: '@@WORDS@@' }} />
        ) : (
          <Stack.Screen name="SignIn" component={SignInScreen} options={{ headerShown: false }} />
        )}
      </Stack.Navigator>
    </NavigationContainer>
  );
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  root: {
    flex: 1,
    padding: spacing.lg,
    gap: spacing.md,
    justifyContent: 'center',
  },
  form: { flex: 1, justifyContent: 'center', gap: spacing.md },
  input: {
    borderWidth: 1,
    minHeight: 48,
    paddingHorizontal: 12,
    fontSize: typography.body.fontSize,
  },
  submit: {
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 48,
    marginTop: spacing.xs,
  },
  submitLabel: { fontWeight: '600', fontSize: typography.body.fontSize },
  error: { marginBottom: spacing.xs },
});
""", {"@@WORDS@@": ctx.screen_words})
    files = {
        "src/navigation/AuthFlowNavigator.tsx": code,
        "src/navigation/navigationTheme.ts": _TS_NAV_THEME,
        "src/theme/theme.ts": _TS_THEME,
    }
    warnings = [
        _NAV_DEPS_WARNING.replace("Add stack/tab/drawer packages as listed per file.",
                                  "Also: npm install @react-navigation/native-stack"),
        "No mock auth: pass a real `authenticate` function to <AuthProvider> "
        "(e.g. POST to your backend). The sign-in button stays disabled until then.",
        "Persist the session token with local.secure_store output (expo-secure-store); "
        "do not store tokens in plain AsyncStorage.",
    ]
    return files, warnings


def _expo_nav_guard(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _TS_HEADER + r"""import React, { useCallback, useEffect, useRef, useState } from 'react';
import type { NavigationProp } from '@react-navigation/native';
import { useNavigation, StackActions, CommonActions } from '@react-navigation/native';

export interface RequireAuthProps {
  /** True when the current user may see the children. */
  isAuthenticated: boolean;
  /** Route name to redirect to when not authenticated. */
  signInRoute: string;
  /** Optional route params for the redirect target. */
  signInParams?: Record<string, unknown>;
  children: React.ReactNode;
}

/**
 * Navigation guard for protected screens. Renders children only while
 * authenticated; otherwise performs a one-shot `replace` to the sign-in
 * route so the back button cannot return to protected content.
 */
export function RequireAuth({
  isAuthenticated,
  signInRoute,
  signInParams,
  children,
}: RequireAuthProps): React.ReactElement | null {
  const navigation = useNavigation<NavigationProp<Record<string, Record<string, unknown> | undefined>>>();
  const redirecting = useRef(false);

  useEffect(() => {
    if (isAuthenticated) { redirecting.current = false; return; }
    if (!isAuthenticated && !redirecting.current) {
      redirecting.current = true;
      navigation.dispatch(StackActions.replace(signInRoute, signInParams));
    }
  }, [isAuthenticated, signInRoute, signInParams, navigation]);

  if (!isAuthenticated) return null;
  return <>{children}</>;
}

/**
 * Hook variant for screens that need conditional UI (e.g. locked overlay)
 * instead of an immediate redirect.
 */
export function useAuthGuard(
  isAuthenticated: boolean,
  signInRoute: string,
): { locked: boolean; lock: () => void } {
  const navigation = useNavigation<NavigationProp<Record<string, Record<string, unknown> | undefined>>>();
  const [locked, setLocked] = useState(!isAuthenticated);

  useEffect(() => {
    setLocked(!isAuthenticated);
  }, [isAuthenticated]);

  const lock = useCallback(() => {
    setLocked(true);
    navigation.dispatch(StackActions.replace(signInRoute));
  }, [navigation, signInRoute]);

  return { locked, lock };
}
""", {})
    files = {
        "src/navigation/RequireAuth.tsx": code,
        "src/theme/theme.ts": _TS_THEME,
    }
    warnings = [
        "Requires a React Navigation stack navigator; render <RequireAuth> inside the "
        "screen component it protects.",
        "Pair with nav.auth_flow output; auth state source is your AuthContext/store.",
    ]
    return files, warnings


def _expo_nav_deep_link(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _TS_HEADER + r"""import { LinkingOptions } from '@react-navigation/native';

/**
 * Linking configuration for @@SCHEME@@:// deep links.
 *
 * Route map below must match your navigator's Screen names and params.
 * Pass to <NavigationContainer linking={linking}> in your root navigator.
 */
export const linking: LinkingOptions<Record<string, Record<string, unknown> | undefined>> = {
  prefixes: ['@@SCHEME@@://'],
  config: {
    screens: {
      Home: 'home',
      Detail: {
        path: 'detail/:id',
        parse: {
          id: (value) => decodeURIComponent(value),
        },
      },
    },
  },
};

/**
 * Parse a raw URL at runtime (e.g. from push notification data or QR scan).
 * Returns null when the URL is not a supported app link.
 */
export function parseAppUrl(url: string | null | undefined): { path: string; params: Record<string, string> } | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    if (!parsed.protocol.startsWith('@@SCHEME@@')) return null;
    const path = parsed.host
      ? parsed.host + parsed.pathname
      : parsed.pathname.replace(/^\//, '');
    const params: Record<string, string> = {};
    parsed.searchParams.forEach((value, key) => {
      params[key] = value;
    });
    return { path, params };
  } catch {
    return null;
  }
}
""", {"@@SCHEME@@": ctx.scheme})
    files = {
        "src/navigation/linking.ts": code,
        "src/theme/theme.ts": _TS_THEME,
    }
    warnings = [
        "Android: to open @@SCHEME@@:// links add an intent-filter with "
        "<data android:scheme=\"@@SCHEME@@\" /> to MainActivity in AndroidManifest.xml "
        "(re-generate native folders via 'npx expo prebuild' first).".replace("@@SCHEME@@", ctx.scheme),
        "iOS: custom scheme needs CFBundleURLTypes in Info.plist (expo scheme field "
        "handles this on managed workflow); HTTPS universal links additionally need "
        "the Associated Domains entitlement + apple-app-site-association file on your domain.",
        "parseAppUrl uses the WHATWG URL API, available in Hermes-based React Native "
        "0.73+; on older runtimes replace it with a manual parse.",
    ]
    return files, warnings


def _expo_state_zustand(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _TS_HEADER + r"""import { create } from 'zustand';
import { createJSONStorage, persist } from 'zustand/middleware';
import AsyncStorage from '@react-native-async-storage/async-storage';

export interface AppState {
  /** Bookmarked item ids (example domain state - rename to fit your app). */
  bookmarks: string[];
  toggleBookmark: (id: string) => void;
  isBookmarked: (id: string) => boolean;
  clearBookmarks: () => void;
}

/**
 * Zustand store with JSON persistence in AsyncStorage.
 * Only serializable state is persisted via `partialize`.
 */
export const useAppStore = create<AppState>()(
  persist(
    (set, get) => ({
      bookmarks: [],
      toggleBookmark: (id) =>
        set((state) => ({
          bookmarks: state.bookmarks.includes(id)
            ? state.bookmarks.filter((existing) => existing !== id)
            : [...state.bookmarks, id],
        })),
      isBookmarked: (id) => get().bookmarks.includes(id),
      clearBookmarks: () => set({ bookmarks: [] }),
    }),
    {
      name: '@@SLUG@@.appState',
      storage: createJSONStorage(() => AsyncStorage),
      partialize: (state) => ({ bookmarks: state.bookmarks }),
    },
  ),
);
""", {"@@SLUG@@": ctx.slug})
    files = {"src/store/useAppStore.ts": code}
    warnings = [
        "Dependencies (unpinned; resolve inside the project): "
        "npm install zustand @react-native-async-storage/async-storage",
        "Persistence key is '%s.appState'; version it with the `version` + "
        "`migrate` persist options when the shape changes." % ctx.slug,
    ]
    return files, warnings


def _expo_net_axios(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _TS_HEADER + r"""import axios, {
  AxiosError,
  AxiosInstance,
  AxiosRequestConfig,
  InternalAxiosRequestConfig,
} from 'axios';

/** Error shape surfaced to UI code. */
export class ApiError extends Error {
  readonly status?: number;
  readonly body?: unknown;

  constructor(message: string, status?: number, body?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

export interface CreateApiClientOptions {
  baseURL: string;
  /** Called per request to attach e.g. a bearer token. */
  getAccessToken?: () => string | null | undefined;
  /** Called once on 401 so the app can sign the user out / refresh. */
  onUnauthorized?: (error: ApiError) => void;
  timeoutMs?: number;
}

export function createApiClient(options: CreateApiClientOptions): AxiosInstance {
  const { baseURL, getAccessToken, onUnauthorized, timeoutMs = 15000 } = options;

  const client = axios.create({
    baseURL,
    timeout: timeoutMs,
    headers: { Accept: 'application/json' },
  });

  client.interceptors.request.use((config: InternalAxiosRequestConfig) => {
    const relative = config.url ?? '';
    if (!relative.startsWith('/') || relative.startsWith('//') || relative.includes('\\') || (config.baseURL && config.baseURL !== baseURL)) throw new Error('Unsafe API request target');
    const token = getAccessToken?.();
    if (token) {
        config.headers.Authorization = 'Bearer ' + token;
    }
    return config;
  });

  client.interceptors.response.use(
    (response) => response,
    (error: AxiosError) => {
      const status = error.response?.status;
      const apiError = new ApiError(
        error.message || 'Request failed',
        status,
        error.response?.data,
      );
      if (status === 401) {
        onUnauthorized?.(apiError);
      }
      return Promise.reject(apiError);
    },
  );

  return client;
}

/** Typed request helpers bound to one client. */
export interface ApiClient {
  get: <T>(url: string, config?: AxiosRequestConfig) => Promise<T>;
  post: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) => Promise<T>;
  put: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) => Promise<T>;
  delete: <T>(url: string, config?: AxiosRequestConfig) => Promise<T>;
}

export function toApiClient(client: AxiosInstance): ApiClient {
  return {
    get: <T>(url: string, config?: AxiosRequestConfig) => client.get<T>(url, config).then((r) => r.data),
    post: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) => client.post<T>(url, data, config).then((r) => r.data),
    put: <T>(url: string, data?: unknown, config?: AxiosRequestConfig) => client.put<T>(url, data, config).then((r) => r.data),
    delete: <T>(url: string, config?: AxiosRequestConfig) => client.delete<T>(url, config).then((r) => r.data),
  };
}

/**
 * Default client bound to the configured API base URL. The URL lives in
 * source for local development only; move it to env config per environment.
 */
export const api: ApiClient = toApiClient(
  createApiClient({
    baseURL: '@@API@@',
    // getAccessToken / onUnauthorized: wire to your auth + secure storage.
  }),
);
""", {"@@API@@": ctx.api_base_url})
    files = {"src/api/client.ts": code}
    warnings = [
        "Dependency (unpinned; resolve inside the project): npm install axios",
        "api_base_url is embedded for development; inject per-environment values "
        "instead of committing production endpoints.",
        "Tokens are read through the getAccessToken callback - never hardcode them; "
        "store with expo-secure-store.",
    ]
    return files, warnings


def _expo_net_fetch(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _TS_HEADER + r"""/**
 * Typed fetch wrapper: JSON encoding/decoding, timeouts, error normalization
 * and optional bearer-token injection. No external dependency.
 */

export class ApiError extends Error {
  readonly status?: number;
  readonly body?: unknown;

  constructor(message: string, status?: number, body?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.body = body;
  }
}

export interface HttpOptions {
  query?: Record<string, string | number | boolean | undefined>;
  headers?: Record<string, string>;
  timeoutMs?: number;
  body?: unknown;
  /** Called per request to attach e.g. a bearer token. */
  getAccessToken?: () => string | null | undefined;
}

export interface HttpClient {
  get: <T>(path: string, options?: HttpOptions) => Promise<T>;
  post: <T>(path: string, body?: unknown, options?: HttpOptions) => Promise<T>;
  put: <T>(path: string, body?: unknown, options?: HttpOptions) => Promise<T>;
  delete: <T>(path: string, options?: HttpOptions) => Promise<T>;
}

function buildUrl(baseURL: string, path: string, query?: HttpOptions['query']): string {
  let url = baseURL + path;
  if (query) {
    const search = new URLSearchParams();
    for (const [key, value] of Object.entries(query)) {
      if (value !== undefined) search.append(key, String(value));
    }
    const encoded = search.toString();
    if (encoded) url += (url.includes('?') ? '&' : '?') + encoded;
  }
  return url;
}

async function request<T>(method: string, baseURL: string, path: string, options: HttpOptions = {}): Promise<T> {
  const { query, headers, timeoutMs = 15000, body, getAccessToken } = options;
  if (!path.startsWith('/') || path.startsWith('//') || path.includes('\\')) throw new ApiError('Unsafe API request target');
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);

  const requestHeaders: Record<string, string> = {
    Accept: 'application/json',
    ...headers,
  };
  if (body !== undefined) requestHeaders['Content-Type'] = 'application/json';
  const token = getAccessToken?.();
  if (token) requestHeaders.Authorization = 'Bearer ' + token;

  try {
    const response = await fetch(buildUrl(baseURL, path, query), {
      method,
      headers: requestHeaders,
      signal: controller.signal,
      redirect: 'error',
      body: body === undefined ? undefined : JSON.stringify(body),
    });

    const text = await response.text();
    let parsed: unknown = undefined;
    if (text) {
      try {
        parsed = JSON.parse(text);
      } catch {
        parsed = text;
      }
    }
    if (!response.ok) {
      throw new ApiError(
        'HTTP ' + response.status + ' for ' + method + ' ' + path,
        response.status,
        parsed,
      );
    }
    return parsed as T;
  } catch (error) {
    if (error instanceof ApiError) throw error;
    if (error instanceof Error && error.name === 'AbortError') {
      throw new ApiError('Request timed out');
    }
    throw new ApiError(error instanceof Error ? error.message : 'Network request failed');
  } finally {
    clearTimeout(timer);
  }
}

export function createHttpClient(baseURL: string): HttpClient {
  return {
    get: (path, options) => request('GET', baseURL, path, options),
    post: (path, body, options) => request('POST', baseURL, path, { ...options, body }),
    put: (path, body, options) => request('PUT', baseURL, path, { ...options, body }),
    delete: (path, options) => request('DELETE', baseURL, path, options),
  };
}

/** Default client bound to the configured API base URL. */
export const http: HttpClient = createHttpClient('@@API@@');
""", {"@@API@@": ctx.api_base_url})
    files = {"src/api/http.ts": code}
    warnings = [
        "No external dependencies: uses the React Native fetch + AbortController.",
        "api_base_url is embedded for development; inject per-environment values "
        "instead of committing production endpoints.",
        "URLSearchParams is available in React Native 0.71+; on older RN use a query helper.",
    ]
    return files, warnings


def _expo_local_async_storage(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _TS_HEADER + r"""import AsyncStorage from '@react-native-async-storage/async-storage';

/**
 * Namespaced, typed key-value storage over AsyncStorage.
 * Keys are prefixed with the app slug to avoid collisions.
 */

export const STORAGE_PREFIX = '@@SLUG@@.';

/** Central key registry; add your keys here so they are greppable. */
export const STORAGE_KEYS = {
  onboardingCompleted: STORAGE_PREFIX + 'onboardingCompleted',
  lastSyncAt: STORAGE_PREFIX + 'lastSyncAt',
  userPreferences: STORAGE_PREFIX + 'userPreferences',
} as const;

export type StorageKey = (typeof STORAGE_KEYS)[keyof typeof STORAGE_KEYS];

export async function getString(key: StorageKey | string): Promise<string | null> {
  try {
    return await AsyncStorage.getItem(key);
  } catch (error) {
    // Storage may be unavailable (e.g. encrypted device locked); callers decide.
    return null;
  }
}

export async function setString(key: StorageKey | string, value: string): Promise<boolean> {
  try {
    await AsyncStorage.setItem(key, value);
    return true;
  } catch (error) {
    return false;
  }
}

export async function getJson<T>(key: StorageKey | string): Promise<T | null> {
  const raw = await getString(key);
  if (raw === null) return null;
  try {
    return JSON.parse(raw) as T;
  } catch (error) {
    return null;
  }
}

export async function setJson(key: StorageKey | string, value: unknown): Promise<boolean> {
  try {
    return await setString(key, JSON.stringify(value));
  } catch (error) {
    return false;
  }
}

export async function remove(key: StorageKey | string): Promise<boolean> {
  try {
    await AsyncStorage.removeItem(key);
    return true;
  } catch (error) {
    return false;
  }
}

/** Removes every app-namespaced key (e.g. on sign-out). Use with care. */
export async function clearNamespaced(): Promise<number> {
  try {
    const keys = await AsyncStorage.getAllKeys();
    const ours = keys.filter((key) => key.startsWith(STORAGE_PREFIX));
    await Promise.all(ours.map((key) => AsyncStorage.removeItem(key)));
    return ours.length;
  } catch (error) {
    return 0;
  }
}
""", {"@@SLUG@@": ctx.slug})
    files = {"src/storage/keyValue.ts": code}
    warnings = [
        "Dependency (unpinned; resolve inside the project): "
        "npx expo install @react-native-async-storage/async-storage",
        "AsyncStorage is NOT encrypted; store tokens/secrets with local.secure_store "
        "(expo-secure-store) instead.",
    ]
    return files, warnings


def _expo_local_secure_store(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _TS_HEADER + r"""import * as SecureStore from 'expo-secure-store';

/**
 * Token/secret storage backed by iOS Keychain and Android Keystore via
 * expo-secure-store. Never use AsyncStorage for these values.
 */

const KEY_PREFIX = '@@SLUG@@.secure.';

export const SECURE_KEYS = {
  authToken: KEY_PREFIX + 'authToken',
  refreshToken: KEY_PREFIX + 'refreshToken',
} as const;

export type SecureKey = (typeof SECURE_KEYS)[keyof typeof SECURE_KEYS];

/** Options applied on iOS; Android Keystore always encrypts at rest. */
const WRITE_OPTIONS: SecureStore.SecureStoreOptions = {
  keychainAccessible: SecureStore.WHEN_UNLOCKED_THIS_DEVICE_ONLY,
};

export async function saveSecret(key: SecureKey | string, value: string): Promise<void> {
  await SecureStore.setItemAsync(key, value, WRITE_OPTIONS);
}

export async function readSecret(key: SecureKey | string): Promise<string | null> {
  try {
    return await SecureStore.getItemAsync(key, WRITE_OPTIONS);
  } catch (error) {
    return null;
  }
}

export async function deleteSecret(key: SecureKey | string): Promise<void> {
  try {
    await SecureStore.deleteItemAsync(key, WRITE_OPTIONS);
  } catch (error) {
    // Deleting a missing key is fine.
  }
}

/** Convenience: sign-out style cleanup for every registered secret. */
export async function clearSecrets(): Promise<void> {
  await Promise.all(Object.values(SECURE_KEYS).map((key) => deleteSecret(key)));
}
""", {"@@SLUG@@": ctx.slug})
    files = {"src/storage/secureStore.ts": code}
    warnings = [
        "Dependency (unpinned; resolve inside the project): npx expo install expo-secure-store",
        "Keychain/Keystore are native: run 'npx expo prebuild' or a development build "
        "so the native module is compiled in.",
        "Never log secret values; the readSecret return is for in-memory use only.",
    ]
    return files, warnings


def _expo_auth_login(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _TS_HEADER + r"""/**
 * Auth service interface + a default HTTP implementation.
 * Adjust `LOGIN_PATH` and payload/response shapes to your backend.
 * No request is made by this module unless you call the functions.
 */

import { ApiError, http } from '../../api/http';

export const LOGIN_PATH = '/auth/login';

export interface AuthSession {
  token: string;
  /** Epoch seconds; optional. */
  expiresAt?: number;
  user?: {
    id: string;
    email: string;
    displayName?: string;
  };
}

export interface LoginCredentials {
  email: string;
  password: string;
}

export interface AuthService {
  signIn: (credentials: LoginCredentials) => Promise<AuthSession>;
}

export const authService: AuthService = {
  async signIn(credentials: LoginCredentials): Promise<AuthSession> {
    try {
      return await http.post<AuthSession>(LOGIN_PATH, credentials);
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        throw new Error('Email or password is incorrect');
      }
      throw error;
    }
  },
};

export function isValidEmail(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim());
}
""", {})
    screen = _fill(
        _TS_HEADER + r"""import React, { useState } from 'react';
import {
  ActivityIndicator,
  KeyboardAvoidingView,
  Platform,
  Pressable,
  ScrollView,
  StyleSheet,
  Text,
  TextInput,
  View,
} from 'react-native';
import { useSafeAreaInsets } from 'react-native-safe-area-context';
import { authService, LoginCredentials } from './authApi';
import { useTheme, radius, spacing, typography } from '../../theme/theme';

export interface LoginScreenProps {
  /** Called with the session after a successful sign-in. */
  onAuthenticated: (token: string) => void;
  /** Inject another AuthService (tests, alternate backends). */
  service?: typeof authService;
}

/**
 * Email + password sign-in screen. Keyboard-safe, validated, accessible.
 * The result token is handed to the app through `onAuthenticated` - persist
 * it with expo-secure-store (see src/storage/secureStore.ts).
 */
export function LoginScreen({ onAuthenticated, service = authService }: LoginScreenProps): React.ReactElement {
  const { colors, radius: rad, spacing: sp, typography: type } = useTheme();
  const insets = useSafeAreaInsets();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [fieldErrors, setFieldErrors] = useState<{ email?: string; password?: string }>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async () => {
    const errors: { email?: string; password?: string } = {};
    if (!isValidEmailLocal(email)) errors.email = 'Enter a valid email address';
    if (password.length < 8) errors.password = 'Use at least 8 characters';
    setFieldErrors(errors);
    if (Object.keys(errors).length > 0 || busy) return;

    setBusy(true);
    setFormError(null);
    try {
      const credentials: LoginCredentials = { email: email.trim(), password };
      const session = await service.signIn(credentials);
      onAuthenticated(session.token);
    } catch (error) {
      setFormError(error instanceof Error ? error.message : 'Sign-in failed');
    } finally {
      setBusy(false);
    }
  };

  return (
    <KeyboardAvoidingView
      style={styles.flex}
      behavior={Platform.OS === 'ios' ? 'padding' : undefined}
    >
      <ScrollView
        style={styles.flex}
        contentContainerStyle={[
          styles.content,
          { paddingTop: insets.top + sp.xl, paddingBottom: insets.bottom + sp.xl },
        ]}
        keyboardShouldPersistTaps="handled"
      >
        <Text style={[type.title, { color: colors.text }]} accessibilityRole="header">
          Sign in
        </Text>

        {formError ? (
          <Text accessibilityLiveRegion="polite" style={[styles.error, { color: colors.danger }]}>
            {formError}
          </Text>
        ) : null}

        <View style={styles.field}>
          <Text style={[type.caption, { color: colors.text }]}>Email *</Text>
          <TextInput
            accessibilityLabel="Email"
            autoCapitalize="none"
            autoCorrect={false}
            keyboardType="email-address"
            placeholder="you@example.com"
            placeholderTextColor={colors.textMuted}
            value={email}
            onChangeText={(next) => {
              setEmail(next);
              setFieldErrors((prev) => ({ ...prev, email: undefined }));
            }}
            style={[
              styles.input,
              {
                borderColor: fieldErrors.email ? colors.danger : colors.border,
                color: colors.text,
                backgroundColor: colors.surface,
                borderRadius: rad.sm,
              },
            ]}
          />
          {fieldErrors.email ? <Text style={[styles.error, { color: colors.danger }]}>{fieldErrors.email}</Text> : null}
        </View>

        <View style={styles.field}>
          <Text style={[type.caption, { color: colors.text }]}>Password *</Text>
          <TextInput
            accessibilityLabel="Password"
            secureTextEntry
            placeholder="At least 8 characters"
            placeholderTextColor={colors.textMuted}
            value={password}
            onChangeText={(next) => {
              setPassword(next);
              setFieldErrors((prev) => ({ ...prev, password: undefined }));
            }}
            style={[
              styles.input,
              {
                borderColor: fieldErrors.password ? colors.danger : colors.border,
                color: colors.text,
                backgroundColor: colors.surface,
                borderRadius: rad.sm,
              },
            ]}
          />
          {fieldErrors.password ? <Text style={[styles.error, { color: colors.danger }]}>{fieldErrors.password}</Text> : null}
        </View>

        <Pressable
          accessibilityRole="button"
          accessibilityState={{ busy, disabled: busy }}
          disabled={busy}
          onPress={submit}
          style={({ pressed }) => [
            styles.submit,
            { backgroundColor: colors.primary, borderRadius: rad.md, opacity: pressed || busy ? 0.7 : 1 },
          ]}
        >
          {busy ? (
            <ActivityIndicator color={colors.onPrimary} />
          ) : (
            <Text style={[styles.submitLabel, { color: colors.onPrimary }]}>Sign in</Text>
          )}
        </Pressable>
      </ScrollView>
    </KeyboardAvoidingView>
  );
}

function isValidEmailLocal(value: string): boolean {
  return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(value.trim());
}

const styles = StyleSheet.create({
  flex: { flex: 1 },
  content: {
    flexGrow: 1,
    paddingHorizontal: spacing.lg,
    gap: spacing.md,
  },
  field: { gap: 6 },
  input: {
    borderWidth: 1,
    minHeight: 48,
    paddingHorizontal: 12,
    fontSize: typography.body.fontSize,
  },
  error: { marginTop: 4, fontSize: 13 },
  submit: {
    alignItems: 'center',
    justifyContent: 'center',
    minHeight: 48,
    marginTop: spacing.sm,
  },
  submitLabel: { fontWeight: '600', fontSize: typography.body.fontSize },
});
""", {})
    files = {
        "src/features/auth/authApi.ts": code,
        "src/features/auth/LoginScreen.tsx": screen,
        "src/theme/theme.ts": _TS_THEME,
    }
    files.update(_expo_net_fetch(ctx)[0])
    warnings = [
        "Dependencies: none beyond src/api/http.ts (net.setup.fetch output) and "
        "react-native-safe-area-context (npx expo install react-native-safe-area-context).",
        "Adjust LOGIN_PATH and payload/response types to your real backend; no "
        "endpoint is called unless the screen is used.",
        "Persist the returned token with local.secure_store output (expo-secure-store), "
        "never in plain AsyncStorage or source code.",
    ]
    return files, warnings


def _expo_auth_logout(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _TS_HEADER + r"""import * as SecureStore from 'expo-secure-store';
import AsyncStorage from '@react-native-async-storage/async-storage';

/**
 * Logout flow: best-effort server-side token revocation, then local
 * cleanup (secure storage + namespaced AsyncStorage keys).
 * Navigation reset is caller-owned (see comment at the bottom).
 */

export interface LogoutOptions {
  /** Token to revoke; omit to skip the revocation call. */
  token?: string | null;
  /** Server revoke endpoint; set to null to skip the network call. */
  revokePath?: string | null;
  apiBaseUrl?: string;
  /** Namespaced AsyncStorage keys to clear (defaults to app keys). */
  asyncStorageKeys?: string[];
  secureStoreKeys?: string[];
}

export async function logout(options: LogoutOptions = {}): Promise<void> {
  const {
    token,
    revokePath = '/auth/revoke',
    apiBaseUrl = '@@API@@',
    asyncStorageKeys = ['@@SLUG@@.appState'],
    secureStoreKeys = ['@@SLUG@@.secure.authToken', '@@SLUG@@.secure.refreshToken'],
  } = options;

  // 1) Best-effort revocation. Failures must not block local sign-out.
  if (token && revokePath) {
    try {
      await fetch(apiBaseUrl + revokePath, {
        method: 'POST',
        headers: {
          Authorization: 'Bearer ' + token,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ token }),
      });
    } catch (error) {
      // Server unreachable - continue with local cleanup.
    }
  }

  // 2) Secure storage cleanup (tokens must not survive logout).
  await Promise.all(
    secureStoreKeys.map(async (key) => {
      try {
        await SecureStore.deleteItemAsync(key);
      } catch (error) {
        // Missing key is fine.
      }
    }),
  );

  // 3) Namespaced app data cleanup.
  if (asyncStorageKeys.length > 0) {
    try {
      await Promise.all(asyncStorageKeys.map((key) => AsyncStorage.removeItem(key)));
    } catch (error) {
      // Storage may be unavailable; continue.
    }
  }
}

/*
 * Navigation reset example (run after `await logout()`):
 *
 *   navigation.reset({
 *     index: 0,
 *     routes: [{ name: 'SignIn' }],
 *   });
 */
""", {"@@API@@": ctx.api_base_url, "@@SLUG@@": ctx.slug})
    files = {"src/features/auth/logout.ts": code}
    warnings = [
        "Dependencies (unpinned; resolve inside the project): "
        "npx expo install expo-secure-store @react-native-async-storage/async-storage",
        "Revoke path defaults to /auth/revoke - align it with your backend; the "
        "revocation call is best-effort and never blocks local cleanup.",
        "Reset navigation state after logout (snippet in the file) so back navigation "
        "cannot re-enter protected screens.",
    ]
    return files, warnings


def _expo_device_gallery(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _TS_HEADER + r"""import * as ImagePicker from 'expo-image-picker';

/**
 * Gallery (photo library) picker built on expo-image-picker, with the
 * permission flow handled explicitly and a structured result.
 */

export interface GalleryPickOptions {
  /** Cap for multi-select; pass >1 to allow albums selection. */
  selectionLimit?: number;
  /** 0..1 JPEG quality. */
  quality?: number;
  /** Show the crop UI (single selection only). */
  allowsEditing?: boolean;
}

export interface GalleryPickResult {
  cancelled: boolean;
  permissionDenied: boolean;
  /** Local file:// URIs of picked assets. */
  uris: string[];
}

export async function pickFromGallery(
  options: GalleryPickOptions = {},
): Promise<GalleryPickResult> {
  const { selectionLimit = 1, quality = 0.85, allowsEditing = false } = options;

  const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (!permission.granted) {
    return { cancelled: true, permissionDenied: !permission.canAskAgain, uris: [] };
  }

  const result = await ImagePicker.launchImageLibraryAsync({
    mediaTypes: ImagePicker.MediaTypeOptions.Images,
    quality,
    allowsEditing,
    allowsMultipleSelection: selectionLimit > 1,
    selectionLimit,
  });

  if (result.canceled) {
    return { cancelled: true, permissionDenied: false, uris: [] };
  }
  return {
    cancelled: false,
    permissionDenied: false,
    uris: result.assets.map((asset) => asset.uri),
  };
}
"""
    files = {"src/features/media/gallery.ts": code}
    warnings = [
        "Dependency (unpinned; resolve inside the project): npx expo install expo-image-picker",
        "iOS: NSPhotoLibraryUsageDescription must be set (app.json "
        "expo.ios.infoPlist or config plugin expo-image-picker photosPermission). "
        "Missing strings cause App Store rejection / runtime crashes.",
        "Android 13+ photo permissions are handled by the library manifest; no manual entry needed.",
    ]
    return files, warnings


def _expo_device_camera(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _TS_HEADER + r"""import * as ImagePicker from 'expo-image-picker';

/**
 * Camera capture built on expo-image-picker with an explicit permission
 * flow. For live in-app camera UI use expo-camera instead (not installed).
 */

export interface CameraCaptureOptions {
  quality?: number;
  /** Show the crop UI after capture. */
  allowsEditing?: boolean;
  /** Copy the captured photo into the device photo library. */
}

export interface CameraCaptureResult {
  cancelled: boolean;
  permissionDenied: boolean;
  /** Local file:// URI of the captured photo. */
  uri?: string;
}

export async function captureFromCamera(
  options: CameraCaptureOptions = {},
): Promise<CameraCaptureResult> {
  const { quality = 0.85, allowsEditing = false } = options;

  const permission = await ImagePicker.requestCameraPermissionsAsync();
  if (!permission.granted) {
    return { cancelled: true, permissionDenied: !permission.canAskAgain };
  }

  const result = await ImagePicker.launchCameraAsync({
    mediaTypes: ImagePicker.MediaTypeOptions.Images,
    quality,
    allowsEditing,
  });

  if (result.canceled || result.assets.length === 0) {
    return { cancelled: true, permissionDenied: false };
  }
  return { cancelled: false, permissionDenied: false, uri: result.assets[0].uri };
}
"""
    files = {"src/features/media/camera.ts": code}
    warnings = [
        "Dependency (unpinned; resolve inside the project): npx expo install expo-image-picker",
        "iOS: NSCameraUsageDescription is REQUIRED in Info.plist (app.json "
        "expo.ios.infoPlist or the expo-image-picker config plugin cameraPermission). "
        "Without it the app crashes on launch of the camera.",
        "Android: CAMERA permission is merged by the library; request at use-time "
        "as done here.",
    ]
    return files, warnings


def _expo_device_gps(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _TS_HEADER + r"""import { useEffect, useRef, useState } from 'react';
import * as Location from 'expo-location';

/**
 * Location helpers on expo-location: one-shot fix plus a lifecycle-safe
 * watch hook. Background location is intentionally NOT configured here.
 */

export interface LocationPoint {
  latitude: number;
  longitude: number;
  /** Meters. */
  accuracy?: number;
  /** Epoch milliseconds. */
  timestamp: number;
}

export interface LocationResult {
  point?: LocationPoint;
  permissionDenied: boolean;
  error?: string;
}

export async function getCurrentPosition(
  accuracy: Location.LocationAccuracy = Location.Accuracy.Balanced,
): Promise<LocationResult> {
  const permission = await Location.requestForegroundPermissionsAsync();
  if (!permission.granted) {
    return { permissionDenied: !permission.canAskAgain };
  }
  try {
    const position = await Location.getCurrentPositionAsync({ accuracy });
    return {
      permissionDenied: false,
      point: {
        latitude: position.coords.latitude,
        longitude: position.coords.longitude,
        accuracy: position.coords.accuracy ?? undefined,
        timestamp: position.timestamp,
      },
    };
  } catch (error) {
    return {
      permissionDenied: false,
      error: error instanceof Error ? error.message : 'Location unavailable',
    };
  }
}

export interface UseLocationWatchOptions {
  accuracy?: Location.LocationAccuracy;
  /** Minimum change in meters before an update fires. */
  distanceInterval?: number;
  enabled?: boolean;
}

/** Subscribes to location updates; cleans up on unmount/disable. */
export function useLocationWatch(options: UseLocationWatchOptions = {}): {
  point: LocationPoint | null;
  permissionDenied: boolean;
  error?: string;
} {
  const { accuracy = Location.Accuracy.Balanced, distanceInterval = 25, enabled = true } = options;
  const [point, setPoint] = useState<LocationPoint | null>(null);
  const [permissionDenied, setPermissionDenied] = useState(false);
  const [error, setError] = useState<string | undefined>(undefined);
  const subscription = useRef<Location.LocationSubscription | null>(null);

  useEffect(() => {
    let cancelled = false;
    if (!enabled) return;

    (async () => {
      const permission = await Location.requestForegroundPermissionsAsync();
      if (cancelled) return;
      if (!permission.granted) {
        setPermissionDenied(!permission.canAskAgain);
        return;
      }
      subscription.current = await Location.watchPositionAsync(
        { accuracy, distanceInterval },
        (position) => {
          setPoint({
            latitude: position.coords.latitude,
            longitude: position.coords.longitude,
            accuracy: position.coords.accuracy ?? undefined,
            timestamp: position.timestamp,
          });
        },
      );
    })();

    return () => {
      cancelled = true;
      subscription.current?.remove();
      subscription.current = null;
    };
  }, [accuracy, distanceInterval, enabled]);

  return { point, permissionDenied, error };
}
"""
    files = {"src/features/location/location.ts": code}
    warnings = [
        "Dependency (unpinned; resolve inside the project): npx expo install expo-location",
        "iOS: NSLocationWhenInUseUsageDescription is REQUIRED; add it via app.json "
        "expo.ios.infoPlist or the expo-location config plugin locationWhenInUsePermission.",
        "Background location (always permission + UIBackgroundModes location) is NOT "
        "configured here; add it only if the spec requires it.",
    ]
    return files, warnings


def _expo_device_sharing(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _TS_HEADER + r"""import { Share } from 'react-native';
import * as Sharing from 'expo-sharing';

/**
 * Share helpers: text via the built-in Share sheet, files via expo-sharing.
 */

export interface ShareTextResult {
  success: boolean;
  dismissed: boolean;
}

export async function shareText(message: string, title?: string): Promise<ShareTextResult> {
  try {
    const result = await Share.share({ message, title });
    if (result.action === Share.sharedAction) {
      return { success: true, dismissed: false };
    }
    return { success: false, dismissed: true };
  } catch (error) {
    return { success: false, dismissed: true };
  }
}

export interface ShareFileResult {
  success: boolean;
  unavailable?: boolean;
}

/** Shares a local file (file:// URI) with any capable app. */
export async function shareFile(uri: string, mimeType?: string, dialogTitle?: string): Promise<ShareFileResult> {
  const available = await Sharing.isAvailableAsync();
  if (!available) {
    return { success: false, unavailable: true };
  }
  try {
    await Sharing.shareAsync(uri, { mimeType, dialogTitle });
    return { success: true };
  } catch (error) {
    return { success: false };
  }
}
"""
    files = {"src/features/share/share.ts": code}
    warnings = [
        "Dependency (unpinned; resolve inside the project): npx expo install expo-sharing",
        "Files must be local file:// URIs that your app can read (e.g. from "
        "expo-file-system cacheDirectory).",
    ]
    return files, warnings


def _expo_device_permissions(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    keys = ctx.extras["permissions"]
    code = _fill(
        _TS_HEADER + r"""import { Linking, Platform } from 'react-native';
import * as ImagePicker from 'expo-image-picker';
import * as Location from 'expo-location';
import * as Notifications from 'expo-notifications';

/**
 * Unified permission manager with per-permission user-facing rationales.
 * Policy: request at the moment of use, explain why, degrade gracefully.
 */

export type PermissionKey =
  | 'camera'
  | 'media_library'
  | 'location'
  | 'notifications';

/** Shown to the user BEFORE the OS dialog (app UI responsibility). */
export const PERMISSION_RATIONALES: Record<PermissionKey, string> = {
  camera: 'We need camera access so you can take photos for this feature.',
  media_library: 'We need photo library access so you can attach pictures.',
  location: 'We need your location to find results near you.',
  notifications: 'Allow notifications to get updates about your activity.',
};

export interface PermissionState {
  key: PermissionKey;
  granted: boolean;
  /** false means the OS dialog will not appear again; offer Settings. */
  canAskAgain: boolean;
}

async function checkAndRequest(key: PermissionKey): Promise<PermissionState> {
  switch (key) {
    case 'camera': {
      const result = await ImagePicker.requestCameraPermissionsAsync();
      return { key, granted: result.granted, canAskAgain: result.canAskAgain };
    }
    case 'media_library': {
      const result = await ImagePicker.requestMediaLibraryPermissionsAsync();
      return { key, granted: result.granted, canAskAgain: result.canAskAgain };
    }
    case 'location': {
      const result = await Location.requestForegroundPermissionsAsync();
      return { key, granted: result.granted, canAskAgain: result.canAskAgain };
    }
    case 'notifications': {
      const result = await Notifications.requestPermissionsAsync();
      const granted = result.granted || result.ios?.status === Notifications.IosAuthorizationStatus.PROVISIONAL;
      return { key, granted, canAskAgain: result.canAskAgain };
    }
    default: {
      const exhaustive: never = key;
      return { key: exhaustive, granted: false, canAskAgain: true };
    }
  }
}

/**
 * Requests the given permissions in order. Stops at the first denial
 * unless `continueOnDenied` is set.
 */
export async function ensurePermissions(
  keys: readonly PermissionKey[],
  continueOnDenied = false,
): Promise<Partial<Record<PermissionKey, PermissionState>>> {
  const results: Partial<Record<PermissionKey, PermissionState>> = {};
  for (const key of keys) {
    const state = await checkAndRequest(key);
    results[key] = state;
    if (!state.granted && !continueOnDenied) break;
  }
  return results;
}

/** Opens the app settings page where the user can re-enable permissions. */
export async function openSettings(): Promise<void> {
  if (Platform.OS === 'ios' || Platform.OS === 'android') {
    await Linking.openSettings();
  }
}
""", {})
    files = {"src/features/permissions/permissions.ts": code}
    warnings = [
        "Dependencies (unpinned; resolve inside the project): "
        "npx expo install expo-image-picker expo-location expo-notifications",
        "Requested in this build: %s." % ", ".join(keys),
        "iOS Info.plist strings for each requested permission are REQUIRED "
        "(camera, photo library, location, notifications alerts); see the matching "
        "device.* module warnings for the exact keys.",
        "Android 13+ notification runtime permission is requested via expo-notifications.",
    ]
    return files, warnings


def _expo_push_permission(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _TS_HEADER + r"""import { Platform } from 'react-native';
import * as Notifications from 'expo-notifications';

/**
 * Push permission + channel setup (expo-notifications).
 * Request at a natural moment, not on first launch.
 */

export interface PushPermissionResult {
  granted: boolean;
  canAskAgain: boolean;
  /** Device push token (native FCM/APNs format). Null when unavailable. */
  deviceToken?: string;
}

const ANDROID_CHANNEL_ID = '@@SLUG@@-default';

export async function ensureAndroidChannel(): Promise<void> {
  if (Platform.OS !== 'android') return;
  await Notifications.setNotificationChannelAsync(ANDROID_CHANNEL_ID, {
    name: 'General',
    importance: Notifications.AndroidImportance.DEFAULT,
    lightColor: '#2563EB',
  });
}

export async function requestPushPermission(): Promise<PushPermissionResult> {
  await ensureAndroidChannel();

  const settings = await Notifications.requestPermissionsAsync();
  const granted =
    settings.granted ||
    settings.ios?.status === Notifications.IosAuthorizationStatus.PROVISIONAL;

  if (!granted) {
    return { granted: false, canAskAgain: settings.canAskAgain };
  }

  // A device token requires a native build (see warnings); it is null in
  // environments without push infrastructure (e.g. web / Expo Go limits).
  try {
    const token = await Notifications.getDevicePushTokenAsync();
    return { granted: true, canAskAgain: settings.canAskAgain, deviceToken: token.data };
  } catch (error) {
    return { granted: true, canAskAgain: settings.canAskAgain };
  }
}

/** Registers to receive notifications while the app is foregrounded. */
export function setForegroundPresentation(): void {
  Notifications.setNotificationHandler({
    handleNotification: async () => ({
      shouldShowBanner: true,
      shouldShowList: true,
      shouldPlaySound: false,
      shouldSetBadge: false,
    }),
  });
}
""", {"@@SLUG@@": ctx.slug})
    files = {"src/features/notifications/requestPermission.ts": code}
    warnings = [
        "Dependency (unpinned; resolve inside the project): npx expo install expo-notifications",
        "Remote push needs a development build: 'npx expo run:android' / 'npx expo run:ios' "
        "or EAS Build. Expo Go cannot receive remote pushes on Android (SDK 53+).",
        "Android: FCM requires google-services.json placed by YOU (never commit it). "
        "iOS: enable Push Notifications capability + APNs key in your Apple account.",
        "Never embed server keys or sender IDs in source; configure them in native "
        "config files excluded from VCS.",
    ]
    return files, warnings


def _expo_push_handle_tap(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _TS_HEADER + r"""import { useEffect, useRef } from 'react';
import * as Notifications from 'expo-notifications';
import type { NavigationProp } from '@react-navigation/native';
import { useNavigation, StackActions, CommonActions } from '@react-navigation/native';

/**
 * Notification tap handling: maps notification data to a route and
 * navigates on tap (cold start via the initial response, warm start via
 * the response listener).
 *
 * Render this hook INSIDE the NavigationContainer (e.g. a small component
 * under it) so navigation is ready.
 */

export interface PushRoute {
  name: string;
  params?: Record<string, unknown>;
}

/**
 * Default mapper: expects `data.route` (a registered screen name) and
 * optional `data.id`. Extend the switch to whitelist your real routes -
 * never navigate to a route name taken verbatim from server data.
 */
export function routeFromNotificationData(data: unknown): PushRoute | null {
  if (typeof data !== 'object' || data === null) return null;
  const payload = data as Record<string, unknown>;
  const route = typeof payload.route === 'string' ? payload.route : null;
  if (!route) return null;
  switch (route) {
    case 'Detail':
      return {
        name: 'Detail',
        params: { id: typeof payload.id === 'string' ? payload.id : '' },
      };
    case 'Home':
      return { name: 'Home' };
    default:
      return null;
  }
}

export function useNotificationNavigation(): void {
  const navigation = useNavigation<NavigationProp<Record<string, Record<string, unknown> | undefined>>>();
  const responseListener = useRef<ReturnType<typeof Notifications.addNotificationResponseReceivedListener> | null>(null);

  useEffect(() => {
    // Cold start: app opened by tapping a notification.
    Notifications.getLastNotificationResponseAsync().then((response) => {
      const route = routeFromNotificationData(
        response?.notification.request.content.data,
      );
      if (route) {
        navigation.dispatch(CommonActions.navigate({ name: route.name, params: route.params }));
      }
    });

    // Warm start: notification tapped while app is open/backgrounded.
    responseListener.current = Notifications.addNotificationResponseReceivedListener((response) => {
      const route = routeFromNotificationData(
        response.notification.request.content.data,
      );
      if (route) {
        navigation.dispatch(CommonActions.navigate({ name: route.name, params: route.params }));
      }
    });

    return () => {
      responseListener.current?.remove();
      responseListener.current = null;
    };
  }, [navigation]);
}
""", {})
    files = {
        "src/features/notifications/notificationNavigation.tsx": code,
    }
    warnings = [
        "Dependencies: expo-notifications (npx expo install expo-notifications) plus "
        "@react-navigation/native (see nav.setup.stack).",
        "Mount a component calling useNotificationNavigation() inside "
        "<NavigationContainer>; pair with nav.deep_link for URL-based routing.",
        "routeFromNotificationData whitelists routes; never navigate using raw "
        "server-provided route names.",
    ]
    return files, warnings


# ---------------------------------------------------------------------------
# Flutter module builders
# ---------------------------------------------------------------------------

_DART_HEADER = "// Generated by Harmonika mobile source generators (offline).\n// Source only: not compiled or executed. Review before use.\n"


def _flutter_screen(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _DART_HEADER + r"""import 'package:flutter/material.dart';

/// @@WORDS@@ screen: inset- and keyboard-safe single child scroll view.
class @@PSFX@@ extends StatelessWidget {
  const @@PSFX@@({
    super.key,
    this.title = '@@WORDS@@',
    this.subtitle,
    required this.child,
  });

  final String title;
  final String? subtitle;
  final Widget child;

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    final TextTheme text = Theme.of(context).textTheme;
    return Scaffold(
      appBar: AppBar(title: Text(title)),
      body: SafeArea(
        child: SingleChildScrollView(
          keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
          padding: EdgeInsets.only(
            left: 16,
            right: 16,
            top: 16,
            // Keeps content above the keyboard when a field is focused.
            bottom: 16 + MediaQuery.viewInsetsOf(context).bottom,
          ),
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 640),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.stretch,
                children: <Widget>[
                  if (subtitle != null)
                    Text(
                      subtitle!,
                      style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
                    ),
                  const SizedBox(height: 16),
                  child,
                ],
              ),
            ),
          ),
        ),
      ),
    );
  }
}
""", {"@@PASCAL@@": ctx.screen_name, "@@WORDS@@": ctx.screen_words,
          "@@PSFX@@": _pascal_sfx(ctx.screen_name, "Screen")})
    files = {"lib/screens/%s.dart" % _snake_sfx(ctx.screen_snake, "screen"): code}
    warnings = ["No external dependencies: Flutter SDK widgets only."]
    return files, warnings


def _flutter_form(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    fields = ctx.extras["fields"]
    controller_lines = []
    field_blocks = []
    dispose_lines = []
    clear_lines = []
    values_lines = []
    for f in fields:
        name = f["name"]
        ctrl = "_%sController" % name
        controller_lines.append(
            "  final TextEditingController %s = TextEditingController();" % ctrl
        )
        dispose_lines.append("    %s.dispose();" % ctrl)
        clear_lines.append("    %s.clear();" % ctrl)
        values_lines.append("        '%s': %s.text.trim()," % (name, ctrl))

        required_line = (
            "              if (trimmed.isEmpty) return 'This field is required';\n"
            if f["required"]
            else ""
        )
        if f["type"] == "email":
            extra_check = (
                "              if (trimmed.isNotEmpty &&\n"
                "                  !RegExp(r'^[^\\s@]+@[^\\s@]+\\.[^\\s@]+$').hasMatch(trimmed)) {\n"
                "                return 'Enter a valid email address';\n"
                "              }\n"
            )
        elif f["type"] == "password":
            extra_check = (
                "              if (trimmed.isNotEmpty && trimmed.length < 8) {\n"
                "                return 'Use at least 8 characters';\n"
                "              }\n"
            )
        elif f["type"] == "number":
            extra_check = (
                "              if (trimmed.isNotEmpty &&\n"
                "                  !RegExp(r'^-?\\d+(?:\\.\\d+)?$').hasMatch(trimmed)) {\n"
                "                return 'Enter a valid number';\n"
                "              }\n"
            )
        else:
            extra_check = ""

        extra_props = []
        if f["type"] == "email":
            extra_props.append("            keyboardType: TextInputType.emailAddress,")
        elif f["type"] == "number":
            extra_props.append("            keyboardType: TextInputType.number,")
        if f["type"] == "password":
            extra_props.append("            obscureText: true,")
        if f["type"] == "multiline":
            extra_props.append("            maxLines: 4,")
        extra_block = ("\n".join(extra_props) + "\n") if extra_props else ""

        validator = (
            "            validator: (String? value) {\n"
            "              final String trimmed = (value ?? '').trim();\n"
            "%s%s"
            "              return null;\n"
            "            },"
        ) % (required_line, extra_check)

        field_blocks.append(
            "          TextFormField(\n"
            "            controller: %s,\n"
            "            decoration: InputDecoration(labelText: %s),\n"
            "%s"
            "            maxLength: 2000,\n"
            "            autovalidateMode: AutovalidateMode.onUserInteraction,\n"
            "%s\n"
            "          ),"
            % (
                ctrl,
                json.dumps(f["label"]),
                extra_block,
                validator,
            )
        )
    code = _fill(
        _DART_HEADER
            + r"""import 'package:flutter/material.dart';

/// Validated form: @@WORDS@@.
/// Keyboard-safe (viewInsets padding) and returns values as a Map.
class @@PASCAL@@Form extends StatefulWidget {
  const @@PASCAL@@Form({super.key, this.submitLabel = 'Submit', required this.onSubmitted});

  final String submitLabel;
  final Future<void> Function(Map<String, String> values) onSubmitted;

  @override
  State<@@PASCAL@@Form> createState() => _@@PASCAL@@FormState();
}

class _@@PASCAL@@FormState extends State<@@PASCAL@@Form> {
  final GlobalKey<FormState> _formKey = GlobalKey<FormState>();
  bool _submitting = false;
@@CONTROLLERS@@

  @override
  void dispose() {
@@DISPOSE@@
    super.dispose();
  }

  Future<void> _submit() async {
    final FormState? form = _formKey.currentState;
    if (form == null || !form.validate() || _submitting) return;
    setState(() => _submitting = true);
    try {
      await widget.onSubmitted(<String, String>{
@@VALUES@@
      });
      if (mounted) {
        ScaffoldMessenger.of(context).showSnackBar(
          const SnackBar(content: Text('Submitted')),
        );
      }
    } finally {
      if (mounted) setState(() => _submitting = false);
    }
  }

  void _reset() {
    _formKey.currentState?.reset();
@@CLEAR@@
    setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    return Form(
      key: _formKey,
      child: SingleChildScrollView(
        keyboardDismissBehavior: ScrollViewKeyboardDismissBehavior.onDrag,
        padding: EdgeInsets.only(
          left: 16,
          right: 16,
          top: 16,
          bottom: 16 + MediaQuery.viewInsetsOf(context).bottom,
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
@@FIELDS@@
            const SizedBox(height: 16),
            FilledButton(
              onPressed: _submitting ? null : _submit,
              child: Text(_submitting ? 'Submitting…' : widget.submitLabel),
            ),
            TextButton(onPressed: _submitting ? null : _reset, child: const Text('Reset')),
          ],
        ),
      ),
    );
  }
}
""", {
            "@@PASCAL@@": ctx.screen_name,
            "@@WORDS@@": ctx.screen_words,
            "@@CONTROLLERS@@": "\n".join(controller_lines),
            "@@DISPOSE@@": "\n".join(dispose_lines),
            "@@CLEAR@@": "\n".join(clear_lines),
            "@@VALUES@@": "\n".join(values_lines),
            "@@FIELDS@@": "\n".join(field_blocks),
        })
    files = {"lib/forms/%s.dart" % _snake_sfx(ctx.screen_snake, "form"): code}
    warnings = ["No external dependencies: Flutter SDK widgets only.",
                "onSubmitted receives validated string values; wire it to your API layer."]
    return files, warnings


def _flutter_list(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _DART_HEADER + r"""import 'package:flutter/material.dart';

/// Typed list item rendered by @@PSFX@@.
class @@PASCAL@@Item {
  const @@PASCAL@@Item({required this.id, required this.title, this.subtitle, this.meta});

  final String id;
  final String title;
  final String? subtitle;
  final String? meta;
}

/// Refreshable list with separators and an inline empty state.
class @@PSFX@@ extends StatelessWidget {
  const @@PSFX@@({
    super.key,
    required this.items,
    this.refreshing = false,
    this.onRefresh,
    this.onPressItem,
  });

  final List<@@PASCAL@@Item> items;
  final bool refreshing;
  final Future<void> Function()? onRefresh;
  final ValueChanged<@@PASCAL@@Item>? onPressItem;

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    final TextTheme text = Theme.of(context).textTheme;

    if (items.isEmpty) {
      return Center(
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            Icon(Icons.inbox_outlined, size: 48, color: scheme.outline),
            const SizedBox(height: 12),
            Text('Nothing here yet', style: text.titleMedium),
            const SizedBox(height: 4),
            Text(
              'Items will appear in this list.',
              style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
            ),
          ],
        ),
      );
    }

    return RefreshIndicator(
      onRefresh: () async => onRefresh?.call(),
      child: ListView.separated(
        itemCount: items.length,
        padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 12),
        separatorBuilder: (BuildContext context, int index) => const SizedBox(height: 8),
        itemBuilder: (BuildContext context, int index) {
          final @@PASCAL@@Item item = items[index];
          return Card(
            elevation: 0,
            color: scheme.surfaceContainerHighest.withOpacity(0.4),
            child: ListTile(
              title: Text(item.title, style: text.titleMedium),
              subtitle: (item.subtitle != null)
                  ? Text(item.subtitle!, maxLines: 2, overflow: TextOverflow.ellipsis)
                  : null,
              trailing: (item.meta != null)
                  ? Text(item.meta!, style: text.labelSmall?.copyWith(color: scheme.outline))
                  : null,
              onTap: onPressItem == null ? null : () => onPressItem!(item),
            ),
          );
        },
      ),
    );
  }
}
""", {"@@PASCAL@@": ctx.screen_name,
          "@@PSFX@@": _pascal_sfx(ctx.screen_name, "ListScreen")})
    files = {"lib/screens/%s.dart" % _snake_sfx(ctx.screen_snake, "list_screen"): code}
    warnings = ["No external dependencies: Flutter SDK widgets only."]
    return files, warnings


def _flutter_card(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _DART_HEADER + r"""import 'package:flutter/material.dart';

/// Themed, tappable card with an optional image slot.
class @@PSFX@@ extends StatelessWidget {
  const @@PSFX@@({
    super.key,
    required this.title,
    this.subtitle,
    this.imageUrl,
    this.onTap,
  });

  final String title;
  final String? subtitle;
  final String? imageUrl;
  final VoidCallback? onTap;

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    final TextTheme text = Theme.of(context).textTheme;
    final Widget content = Column(
      crossAxisAlignment: CrossAxisAlignment.stretch,
      children: <Widget>[
        ClipRRect(
          borderRadius: const BorderRadius.vertical(top: Radius.circular(12)),
          child: SizedBox(
            height: 140,
            child: imageUrl != null
                ? Image.network(imageUrl!, fit: BoxFit.cover,
                    errorBuilder: (BuildContext context, Object error, StackTrace? stack) {
                    return ColoredBox(
                      color: scheme.surfaceContainerHighest,
                      child: Icon(Icons.image_outlined, color: scheme.outline),
                    );
                  })
                : ColoredBox(
                    color: scheme.surfaceContainerHighest,
                    child: Icon(Icons.image_outlined, color: scheme.outline),
                  ),
          ),
        ),
        Padding(
          padding: const EdgeInsets.all(16),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(title, style: text.titleMedium, maxLines: 2, overflow: TextOverflow.ellipsis),
              if (subtitle != null) ...<Widget>[
                const SizedBox(height: 4),
                Text(
                  subtitle!,
                  style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
                  maxLines: 2,
                  overflow: TextOverflow.ellipsis,
                ),
              ],
            ],
          ),
        ),
      ],
    );

    return Card(
      elevation: 1,
      clipBehavior: Clip.antiAlias,
      shape: RoundedRectangleBorder(borderRadius: BorderRadius.circular(12)),
      child: onTap == null
          ? content
          : InkWell(onTap: onTap, child: content),
    );
  }
}
""", {"@@PASCAL@@": ctx.screen_name,
          "@@PSFX@@": _pascal_sfx(ctx.screen_name, "Card")})
    files = {"lib/widgets/%s.dart" % _snake_sfx(ctx.screen_snake, "card"): code}
    warnings = ["No external dependencies: Flutter SDK widgets only."]
    return files, warnings


def _flutter_modal(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _DART_HEADER + r"""import 'package:flutter/material.dart';

/// Bottom sheet helper: rounded, scrollable, keyboard- and inset-safe.
Future<T?> show@@PSFX@@<T extends Object?>(
  BuildContext context, {
  required String title,
  required Widget Function(BuildContext context) builder,
}) {
  return showModalBottomSheet<T>(
    context: context,
    isScrollControlled: true,
    showDragHandle: true,
    useSafeArea: true,
    shape: const RoundedRectangleBorder(
      borderRadius: BorderRadius.vertical(top: Radius.circular(20)),
    ),
    builder: (BuildContext sheetContext) {
      return Padding(
        padding: EdgeInsets.only(
          left: 16,
          right: 16,
          bottom: MediaQuery.viewInsetsOf(sheetContext).bottom,
        ),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          crossAxisAlignment: CrossAxisAlignment.stretch,
          children: <Widget>[
            Text(
              title,
              style: Theme.of(sheetContext).textTheme.titleMedium,
            ),
            const SizedBox(height: 12),
            Flexible(child: SingleChildScrollView(child: builder(sheetContext))),
            const SizedBox(height: 12),
          ],
        ),
      );
    },
  );
}
""", {"@@PASCAL@@": ctx.screen_name,
          "@@PSFX@@": _pascal_sfx(ctx.screen_name, "Sheet")})
    files = {"lib/widgets/%s.dart" % _snake_sfx(ctx.screen_snake, "sheet"): code}
    warnings = ["No external dependencies: Flutter SDK widgets only."]
    return files, warnings


def _flutter_skeleton(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _DART_HEADER + r"""import 'package:flutter/material.dart';

/// Pulsing skeleton placeholder rows. Disposes its controller cleanly.
class @@PSFX@@ extends StatefulWidget {
  const @@PSFX@@({super.key, this.rows = 3, this.rowHeight = 64, this.withAvatar = false});

  final int rows;
  final double rowHeight;
  final bool withAvatar;

  @override
  State<@@PSFX@@> createState() => _@@PSFX@@State();
}

class _@@PSFX@@State extends State<@@PSFX@@> with SingleTickerProviderStateMixin {
  late final AnimationController _controller = AnimationController(
    vsync: this,
    duration: const Duration(milliseconds: 1400),
  )..repeat(reverse: true);

  late final Animation<double> _opacity = Tween<double>(begin: 0.35, end: 1.0)
      .animate(CurvedAnimation(parent: _controller, curve: Curves.easeInOut));

  @override
  void dispose() {
    _controller.dispose();
    super.dispose();
  }

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    return Column(
      children: List<Widget>.generate(widget.rows, (int index) {
        return FadeTransition(
          opacity: _opacity,
          child: Padding(
            padding: const EdgeInsets.only(bottom: 12),
            child: Row(
              children: <Widget>[
                if (widget.withAvatar) ...<Widget>[
                  CircleAvatar(
                    radius: 22,
                    backgroundColor: scheme.surfaceContainerHighest,
                  ),
                  const SizedBox(width: 12),
                ],
                Expanded(
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: <Widget>[
                      _bar(scheme, 0.72, 12),
                      const SizedBox(height: 8),
                      _bar(scheme, 0.48, widget.rowHeight / 3),
                    ],
                  ),
                ),
              ],
            ),
          ),
        );
      }),
    );
  }

  Widget _bar(ColorScheme scheme, double widthFactor, double height) {
    return FractionallySizedBox(
      widthFactor: widthFactor,
      alignment: Alignment.centerLeft,
      child: Container(
        height: height,
        decoration: BoxDecoration(
          color: scheme.surfaceContainerHighest,
          borderRadius: BorderRadius.circular(height / 2),
        ),
      ),
    );
  }
}
""", {"@@PASCAL@@": ctx.screen_name,
          "@@PSFX@@": _pascal_sfx(ctx.screen_name, "Skeleton")})
    files = {"lib/widgets/%s.dart" % _snake_sfx(ctx.screen_snake, "skeleton"): code}
    warnings = ["No external dependencies: Flutter SDK widgets only."]
    return files, warnings


def _flutter_empty_state(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _DART_HEADER + r"""import 'package:flutter/material.dart';

/// Themed empty state with optional icon and call-to-action.
class @@PASCAL@@ extends StatelessWidget {
  const @@PASCAL@@({
    super.key,
    required this.title,
    this.message,
    this.icon,
    this.actionLabel,
    this.onAction,
  });

  final String title;
  final String? message;
  final IconData? icon;
  final String? actionLabel;
  final VoidCallback? onAction;

  @override
  Widget build(BuildContext context) {
    final ColorScheme scheme = Theme.of(context).colorScheme;
    final TextTheme text = Theme.of(context).textTheme;
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(32),
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: <Widget>[
            if (icon != null) ...<Widget>[
              Icon(icon, size: 48, color: scheme.outline),
              const SizedBox(height: 16),
            ],
            Text(title, style: text.titleMedium, textAlign: TextAlign.center),
            if (message != null) ...<Widget>[
              const SizedBox(height: 8),
              Text(
                message!,
                style: text.bodyMedium?.copyWith(color: scheme.onSurfaceVariant),
                textAlign: TextAlign.center,
              ),
            ],
            if (actionLabel != null && onAction != null) ...<Widget>[
              const SizedBox(height: 16),
              FilledButton(onPressed: onAction, child: Text(actionLabel!)),
            ],
          ],
        ),
      ),
    );
  }
}
""", {"@@PASCAL@@": ctx.screen_name})
    files = {"lib/widgets/%s_empty_state.dart" % ctx.screen_snake: code}
    warnings = ["No external dependencies: Flutter SDK widgets only."]
    return files, warnings


def _flutter_material(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _DART_HEADER + r"""import 'package:flutter/material.dart';

/// Material 3 component-level theme tuning on top of ColorScheme.fromSeed.
/// Wire into MaterialApp:
///   theme: AppMaterialTheme.light(context.platformBrightness == Brightness.dark)
class AppMaterialTheme {
  AppMaterialTheme._();

  static const Color seed = Color(0xFF2563EB);

  static ThemeData light() => _build(Brightness.light);

  static ThemeData dark() => _build(Brightness.dark);

  static ThemeData _build(Brightness brightness) {
    final ColorScheme scheme = ColorScheme.fromSeed(
      seedColor: seed,
      brightness: brightness,
    );
    return ThemeData(
      useMaterial3: true,
      colorScheme: scheme,
      // MD3 shape scale.
      cardTheme: const CardTheme(elevation: 1, shape: RoundedRectangleBorder(
        borderRadius: BorderRadius.all(Radius.circular(16)),
      )),
      filledButtonTheme: FilledButtonThemeData(
        style: FilledButton.styleFrom(
          shape: const StadiumBorder(),
          minimumSize: const Size(0, 48),
        ),
      ),
      inputDecorationTheme: InputDecorationTheme(
        filled: true,
        fillColor: scheme.surfaceContainerHighest.withOpacity(0.4),
        border: OutlineInputBorder(
          borderRadius: BorderRadius.circular(12),
        ),
      ),
      navigationBarTheme: NavigationBarThemeData(
        backgroundColor: scheme.surface,
        indicatorColor: scheme.primaryContainer,
      ),
    );
  }
}
""", {})
    files = {"lib/theme/app_material_theme.dart": code}
    warnings = ["No external dependencies: Flutter SDK widgets only.",
                "Compose with mobile.theme.dark_mode output for light/dark switching."]
    return files, warnings


def _flutter_cupertino(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    code = _fill(
        _DART_HEADER + r"""import 'package:flutter/cupertino.dart';

/// Cupertino (iOS-flavored) theme for apps that prefer the iOS look.
/// Use with CupertinoApp instead of MaterialApp, or borrow pieces per-screen.
const CupertinoThemeData AppCupertinoTheme = CupertinoThemeData(
  brightness: Brightness.light,
  primaryColor: Color(0xFF2563EB),
  scaffoldBackgroundColor: CupertinoColors.systemBackground,
  barBackgroundColor: CupertinoColors.secondarySystemBackground,
  textTheme: CupertinoTextThemeData(
    textStyle: TextStyle(
      fontSize: 17,
      color: CupertinoColors.label,
    ),
    navTitleTextStyle: TextStyle(
      fontSize: 17,
      fontWeight: FontWeight.w600,
      color: CupertinoColors.label,
    ),
  ),
);

/// Dark variant for dark Cupertino appearance.
const CupertinoThemeData AppCupertinoThemeDark = CupertinoThemeData(
  brightness: Brightness.dark,
  primaryColor: Color(0xFF60A5FA),
  scaffoldBackgroundColor: CupertinoColors.systemBackground,
  barBackgroundColor: CupertinoColors.secondarySystemBackground,
  textTheme: CupertinoTextThemeData(
    textStyle: TextStyle(fontSize: 17, color: CupertinoColors.label),
  ),
);

/// Example Cupertino scaffold using the theme above.
class CupertinoStarterPage extends StatelessWidget {
  const CupertinoStarterPage({super.key});

  @override
  Widget build(BuildContext context) {
    return CupertinoPageScaffold(
      navigationBar: const CupertinoNavigationBar(
        middle: Text('@@NAME@@'),
      ),
      child: const Center(
        child: Text('Cupertino starter', style: TextStyle(fontSize: 17)),
      ),
    );
  }
}
""", {"@@NAME@@": ctx.name})
    files = {"lib/theme/app_cupertino_theme.dart": code}
    warnings = ["No external dependencies: Flutter SDK (Cupertino) widgets only."]
    return files, warnings


def _flutter_dark_mode(ctx: _Ctx) -> Tuple[Dict[str, str], List[str]]:
    themes = _build_flutter_scaffold(ctx)[0]["lib/theme/app_themes.dart"]
    switch_code = _fill(
        _DART_HEADER + r"""import 'package:flutter/material.dart';

/// Segmented control for ThemeMode. Wire the selected mode into
/// MaterialApp(themeMode: ...) and persist it with shared_preferences
/// (add the dependency yourself if you want persistence).
class ThemeModeSwitch extends StatelessWidget {
  const ThemeModeSwitch({super.key, required this.mode, required this.onChanged});

  final ThemeMode mode;
  final ValueChanged<ThemeMode> onChanged;

  static const Map<ThemeMode, String> _labels = <ThemeMode, String>{
    ThemeMode.system: 'System',
    ThemeMode.light: 'Light',
    ThemeMode.dark: 'Dark',
  };

  @override
  Widget build(BuildContext context) {
    return SegmentedButton<ThemeMode>(
      segments: _labels.entries
          .map(
            (MapEntry<ThemeMode, String> entry) => ButtonSegment<ThemeMode>(
              value: entry.key,
              label: Text(entry.value),
              icon: switch (entry.key) {
                ThemeMode.system => const Icon(Icons.brightness_auto_outlined),
                ThemeMode.light => const Icon(Icons.light_mode_outlined),
                ThemeMode.dark => const Icon(Icons.dark_mode_outlined),
              },
            ),
          )
          .toList(),
      selected: <ThemeMode>{mode},
      onSelectionChanged: (Set<ThemeMode> selection) {
        onChanged(selection.first);
      },
    );
  }
}
""", {})
    files = {
        "lib/theme/app_themes.dart": themes,
        "lib/widgets/theme_mode_switch.dart": switch_code,
    }
    warnings = [
        "No external dependencies: Flutter SDK widgets only.",
        "Wire into MaterialApp: theme: AppThemes.light(), darkTheme: AppThemes.dark(), "
        "themeMode: <selected>. For persistence add shared_preferences yourself.",
    ]
    return files, warnings


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
#
# Skill ids follow backend/mobile_engine/specification.txt (214 canonical
# skills). This engine implements a bounded, honest subset: source generators
# only. Everything that would require real toolchains (bare RN native
# folders, Xcode projects, Gradle builds, store deploys, OTA, CI runners)
# is intentionally NOT in this registry or is explicitly unsupported.


# ---------------------------------------------------------------------------
# Framework dispatch wrappers (multi-framework skills). Defined BEFORE the
# registry so the dict literal below can reference the function objects.
# ---------------------------------------------------------------------------


def _dispatch_screen(ctx: "_Ctx") -> Tuple[Dict[str, str], List[str]]:
    if ctx.framework == "flutter":
        return _flutter_screen(ctx)
    return _expo_screen(ctx)


def _dispatch_form(ctx: "_Ctx") -> Tuple[Dict[str, str], List[str]]:
    if ctx.framework == "flutter":
        return _flutter_form(ctx)
    return _expo_form(ctx)


def _dispatch_list(ctx: "_Ctx") -> Tuple[Dict[str, str], List[str]]:
    if ctx.framework == "flutter":
        return _flutter_list(ctx)
    return _expo_list(ctx)


def _dispatch_card(ctx: "_Ctx") -> Tuple[Dict[str, str], List[str]]:
    if ctx.framework == "flutter":
        return _flutter_card(ctx)
    return _expo_card(ctx)


def _dispatch_modal(ctx: "_Ctx") -> Tuple[Dict[str, str], List[str]]:
    if ctx.framework == "flutter":
        return _flutter_modal(ctx)
    return _expo_modal(ctx)


def _dispatch_skeleton(ctx: "_Ctx") -> Tuple[Dict[str, str], List[str]]:
    if ctx.framework == "flutter":
        return _flutter_skeleton(ctx)
    return _expo_skeleton(ctx)


def _dispatch_material(ctx: "_Ctx") -> Tuple[Dict[str, str], List[str]]:
    if ctx.framework == "flutter":
        return _flutter_material(ctx)
    return _expo_material(ctx)


def _dispatch_cupertino(ctx: "_Ctx") -> Tuple[Dict[str, str], List[str]]:
    if ctx.framework == "flutter":
        return _flutter_cupertino(ctx)
    return _expo_cupertino(ctx)


def _dispatch_dark_mode(ctx: "_Ctx") -> Tuple[Dict[str, str], List[str]]:
    if ctx.framework == "flutter":
        return _flutter_dark_mode(ctx)
    return _expo_dark_mode(ctx)


def _dispatch_empty_state(ctx: "_Ctx") -> Tuple[Dict[str, str], List[str]]:
    if ctx.framework == "flutter":
        return _flutter_empty_state(ctx)
    return _expo_empty_state(ctx)


_SKILLS: Dict[str, Dict[str, Any]] = {
    # ---- Layer 4: Project Scaffolder -----------------------------------
    "scaffold.expo": {
        "description": (
            "Generate a coherent Expo (managed workflow) TypeScript starter: "
            "App entry, theme kit, app.json, package.json, tsconfig and an honest "
            "README. Not installed or built; versions resolved via 'npx expo install --fix'."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Expo starter input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework")},
        ),
        "build": _build_expo_scaffold,
    },
    "scaffold.react_native_cli": {
        "description": (
            "UNSUPPORTED (honest limit): a bare React Native CLI project requires the "
            "official template for its native ios/ and android/ folders, which cannot "
            "be generated offline. Run 'npx @react-native-community/cli init <ProjectName>' "
            "locally, then apply the Expo-compatible module outputs of this engine on top."
        ),
        "frameworks": (),
        "unsupported_reason": (
            "A bare React Native project cannot be faked: native ios/ + android/ "
            "templates require the official CLI. Run 'npx @react-native-community/cli init "
            "<ProjectName>' locally; then use the TypeScript module generators of this "
            "engine inside that project."
        ),
        "schema": _obj_schema(
            "Registered but unsupported; dispatch always fails with guidance.",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework")},
        ),
    },
    "scaffold.flutter": {
        "description": (
            "Generate a Flutter starter: pubspec, Material 3 light/dark themes, main.dart, "
            "a widget test and README. Platform folders are NOT generated; materialize "
            "them with 'flutter create .'."
        ),
        "frameworks": ("flutter",),
        "schema": _obj_schema(
            "Flutter starter input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework")},
        ),
        "build": _build_flutter_scaffold,
    },
    "scaffold.kotlin_android": {
        "description": (
            "Generate a native Android starter (Kotlin + Jetpack Compose Material 3): "
            "Gradle KTS project, edge-to-edge MainActivity with ViewModel + StateFlow, "
            "IME-safe responsive layout, version catalog. Not built."
        ),
        "frameworks": ("kotlin",),
        "schema": _obj_schema(
            "Kotlin/Android starter input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework")},
        ),
        "build": _build_kotlin_scaffold,
    },
    "scaffold.swift_ios": {
        "description": (
            "Generate a native iOS source kit (Swift + SwiftUI): app entry, TabView + "
            "NavigationStack shell, typed detail navigation, Settings form, plus an "
            "xcodegen project.yml. No .xcodeproj is emitted; create it with xcodegen or Xcode."
        ),
        "frameworks": ("swift",),
        "schema": _obj_schema(
            "Swift/iOS starter input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework")},
        ),
        "build": _build_swift_scaffold,
    },
    "scaffold.ionic": {
        "description": (
            "Generate an Ionic-style web shell + Capacitor config: Vite + TypeScript "
            "sources, capacitor.config.ts. Native shells are added later with "
            "'npx cap add android|ios' (not generated here)."
        ),
        "frameworks": ("ionic",),
        "schema": _obj_schema(
            "Ionic/Capacitor starter input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework")},
        ),
        "build": _build_ionic_scaffold,
    },
    # ---- Layer 5: UI/UX Builder ----------------------------------------
    "mobile.gen.screen": {
        "description": (
            "Generate a themed screen: keyboard-safe (KeyboardAvoidingView), inset-aware, "
            "with title/subtitle/body slot. Plain React Native + TypeScript."
        ),
        "frameworks": ("expo", "flutter"),
        "schema": _obj_schema(
            "Screen generator input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "screen_name": dict(_prop("screen_name"), default="Home")},
        ),
        "build": _dispatch_screen,
    },
    "mobile.gen.component": {
        "description": (
            "Generate a reusable themed button component: variants, busy/disabled states, "
            "ripple + opacity feedback, accessibility wiring. React Native primitives only."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Component generator input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "screen_name": dict(_prop("screen_name"), default="PrimaryButton")},
        ),
        "build": _expo_component,
    },
    "mobile.gen.form": {
        "description": (
            "Generate a validated form module: per-field client validation "
            "(required/email/password/number/multiline), keyboard-safe layout, "
            "submit + reset states. Values go to your onSubmit callback."
        ),
        "frameworks": ("expo", "flutter"),
        "schema": _obj_schema(
            "Form generator input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "screen_name": dict(_prop("screen_name"), default="Profile"),
             "fields": {
                 "type": "array",
                 "description": "1-12 field specs: name (snake_case), label, type, required.",
                 "items": {
                     "type": "object",
                     "properties": {
                         "name": {"type": "string"},
                         "label": {"type": "string"},
                         "type": {"type": "string", "enum": list(_FIELD_TYPES)},
                         "required": {"type": "boolean"},
                     },
                     "required": ["name"],
                     "additionalProperties": False,
                 },
                 "default": [
                     {"name": "email", "label": "Email", "type": "email", "required": True},
                     {"name": "password", "label": "Password", "type": "password", "required": True},
                 ],
             }},
        ),
        "build": _dispatch_form,
    },
    "mobile.gen.list": {
        "description": (
            "Generate a typed FlatList screen: pull-to-refresh, separators, empty state, "
            "item press handling. Data ownership stays with the caller."
        ),
        "frameworks": ("expo", "flutter"),
        "schema": _obj_schema(
            "List screen generator input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "screen_name": dict(_prop("screen_name"), default="Catalog")},
        ),
        "build": _dispatch_list,
    },
    "mobile.gen.card": {
        "description": (
            "Generate a media card component: image slot with placeholder, platform "
            "shadow/elevation, optional press handling and trailing content."
        ),
        "frameworks": ("expo", "flutter"),
        "schema": _obj_schema(
            "Card generator input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "screen_name": dict(_prop("screen_name"), default="Media")},
        ),
        "build": _dispatch_card,
    },
    "mobile.gen.modal": {
        "description": (
            "Generate a bottom-sheet modal component: backdrop tap + back-button close, "
            "drag handle, inset-aware padding. Built on the React Native Modal primitive."
        ),
        "frameworks": ("expo", "flutter"),
        "schema": _obj_schema(
            "Modal generator input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "screen_name": dict(_prop("screen_name"), default="BottomSheet")},
        ),
        "build": _dispatch_modal,
    },
    "mobile.gen.tab_bar": {
        "description": (
            "Generate a custom bottom tab bar + tab navigator: AppTabBar component with "
            "icon slot, typed TabParamList, one placeholder screen per tab (2-5 tabs)."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Tab bar generator input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "screen_name": dict(_prop("screen_name"), default="Main"),
             "tabs": {
                 "type": "array",
                 "description": "2-5 PascalCase tab names.",
                 "items": {"type": "string", "pattern": "^[A-Z][A-Za-z0-9]{0,63}$"},
                 "default": ["Home", "Search", "Profile"],
             }},
        ),
        "build": _expo_tab_bar,
    },
    "mobile.gen.drawer": {
        "description": (
            "Generate a drawer navigator with themed drawer content and one placeholder "
            "screen per item (2-6 items). Includes gesture-handler/reanimated setup warnings."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Drawer generator input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "screen_name": dict(_prop("screen_name"), default="Main"),
             "items": {
                 "type": "array",
                 "description": "2-6 PascalCase drawer item names.",
                 "items": {"type": "string", "pattern": "^[A-Z][A-Za-z0-9]{0,63}$"},
                 "default": ["Home", "Library", "Settings"],
             }},
        ),
        "build": _expo_drawer,
    },
    "mobile.gen.skeleton": {
        "description": (
            "Generate a pulsing skeleton loader component (Animated API): rows, avatar "
            "option, clean loop teardown."
        ),
        "frameworks": ("expo", "flutter"),
        "schema": _obj_schema(
            "Skeleton loader generator input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "screen_name": dict(_prop("screen_name"), default="ListSkeleton")},
        ),
        "build": _dispatch_skeleton,
    },
    "mobile.gen.empty_state": {
        "description": (
            "Generate an empty state component: icon slot, title, message and optional "
            "call-to-action button. Theme aware."
        ),
        "frameworks": ("expo", "flutter"),
        "schema": _obj_schema(
            "Empty state generator input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "screen_name": dict(_prop("screen_name"), default="EmptyState"),
             "message": {
                 "type": "string",
                 "description": "Default helper text shown under the title.",
                 "default": "Nothing here yet.",
             }},
        ),
        "build": _dispatch_empty_state,
    },
    "mobile.style.material": {
        "description": (
            "Generate Material Design 3 styling layer: React Native token module "
            "(elevation scale, shape, state layers, ripple, window classes) or a Flutter "
            "Material 3 ThemeData file."
        ),
        "frameworks": ("expo", "flutter"),
        "schema": _obj_schema(
            "Material styling input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework")},
        ),
        "build": _dispatch_material,
    },
    "mobile.style.cupertino": {
        "description": (
            "Generate Cupertino styling layer: React Native tokens with real iOS "
            "PlatformColors + iOS typography, or a Flutter CupertinoThemeData file."
        ),
        "frameworks": ("expo", "flutter"),
        "schema": _obj_schema(
            "Cupertino styling input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework")},
        ),
        "build": _dispatch_cupertino,
    },
    "mobile.style.adaptive": {
        "description": (
            "Generate adaptive platform styling helpers: per-platform press behavior "
            "(ripple vs opacity), radius and token flavor selection (Material/Cupertino)."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Adaptive styling input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework")},
        ),
        "build": _expo_adaptive,
    },
    "mobile.responsive.tablet": {
        "description": (
            "Generate responsive layout utilities: reactive breakpoints (compact/medium/"
            "expanded) and a TwoPaneLayout for tablets and unfolded foldables."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Responsive layout input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework")},
        ),
        "build": _expo_tablet,
    },
    "mobile.theme.dark_mode": {
        "description": (
            "Generate dark mode support: React Native preference provider with persisted "
            "system/light/dark switch (AsyncStorage), or Flutter light/dark themes with a "
            "ThemeMode switch widget."
        ),
        "frameworks": ("expo", "flutter"),
        "schema": _obj_schema(
            "Dark mode setup input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework")},
        ),
        "build": _dispatch_dark_mode,
    },
    # ---- Layer 6: Navigation --------------------------------------------
    "nav.setup.stack": {
        "description": (
            "Generate a typed native-stack navigator (Home + Detail with params), themed "
            "headers and a shared navigation-theme adapter."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Stack navigation input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "screen_name": dict(_prop("screen_name"), default="Home")},
        ),
        "build": _expo_nav_stack,
    },
    "nav.setup.tab": {
        "description": (
            "Generate a typed bottom-tab root navigator with themed tab bar options and "
            "one placeholder screen per tab (2-5 tabs)."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Tab navigation input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "screen_name": dict(_prop("screen_name"), default="Main"),
             "tabs": {
                 "type": "array",
                 "description": "2-5 PascalCase tab names.",
                 "items": {"type": "string", "pattern": "^[A-Z][A-Za-z0-9]{0,63}$"},
                 "default": ["Home", "Search", "Profile"],
             }},
        ),
        "build": _expo_nav_tab,
    },
    "nav.setup.modal": {
        "description": (
            "Generate a stack navigator where modal screens are grouped with "
            "presentation: 'modal' and gesture-enabled dismissal."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Modal navigation input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "screen_name": dict(_prop("screen_name"), default="Home")},
        ),
        "build": _expo_nav_modal,
    },
    "nav.auth_flow": {
        "description": (
            "Generate an auth-gated navigation shell: AuthProvider with an injected real "
            "authenticate function (no mock success), SignIn screen, and a navigator that "
            "switches between auth and main screens by session."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Auth flow navigation input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "screen_name": dict(_prop("screen_name"), default="Welcome")},
        ),
        "build": _expo_nav_auth_flow,
    },
    "nav.guard": {
        "description": (
            "Generate navigation guards: RequireAuth wrapper (one-shot replace to the "
            "sign-in route) and a useAuthGuard hook for locked overlays."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Navigation guard input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "screen_name": dict(_prop("screen_name"), default="Protected")},
        ),
        "build": _expo_nav_guard,
    },
    "nav.deep_link": {
        "description": (
            "Generate React Navigation linking config for a scheme-based deep link "
            "(scheme derived from app name), a runtime URL parser, plus Android "
            "intent-filter / iOS universal-link setup warnings."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Deep link setup input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "screen_name": dict(_prop("screen_name"), default="Home")},
        ),
        "build": _expo_nav_deep_link,
    },
    # ---- Layer 7: State management ---------------------------------------
    "state.setup.zustand": {
        "description": (
            "Generate a Zustand store with JSON persistence in AsyncStorage: typed state, "
            "actions, partialize. No guessed versions."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Zustand setup input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework")},
        ),
        "build": _expo_state_zustand,
    },
    # ---- Layer 8: Network -------------------------------------------------
    "net.setup.axios": {
        "description": (
            "Generate an Axios client module: base URL, auth token injection via callback, "
            "401 hook, timeouts, ApiError normalization and typed request helpers."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Axios setup input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "api_base_url": dict(_prop("api_base_url"), default=DEFAULT_API_BASE_URL)},
        ),
        "build": _expo_net_axios,
    },
    "net.setup.fetch": {
        "description": (
            "Generate a dependency-free typed fetch client: JSON handling, AbortController "
            "timeouts, query params, ApiError class, token injection callback."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Fetch client input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "api_base_url": dict(_prop("api_base_url"), default=DEFAULT_API_BASE_URL)},
        ),
        "build": _expo_net_fetch,
    },
    # ---- Layer 9: Local storage -------------------------------------------
    "local.async_storage": {
        "description": (
            "Generate a namespaced AsyncStorage key-value module: central key registry, "
            "JSON get/set, bulk namespace clear. Plain values only - not for secrets."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "AsyncStorage setup input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework")},
        ),
        "build": _expo_local_async_storage,
    },
    "local.secure_store": {
        "description": (
            "Generate a secure token module on expo-secure-store (iOS Keychain / Android "
            "Keystore): save/read/delete/clear with this-device-only keychain policy."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Secure store setup input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework")},
        ),
        "build": _expo_local_secure_store,
    },
    # ---- Layer 10: Auth -----------------------------------------------------
    "auth.mobile.login": {
        "description": (
            "Generate a login feature: AuthService interface + HTTP implementation, "
            "validated keyboard-safe LoginScreen, token handed to onAuthenticated. "
            "Endpoint paths are placeholders for your backend."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Login feature input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "screen_name": dict(_prop("screen_name"), default="Login"),
             "api_base_url": dict(_prop("api_base_url"), default=DEFAULT_API_BASE_URL)},
        ),
        "build": _expo_auth_login,
    },
    "auth.mobile.logout": {
        "description": (
            "Generate a logout module: best-effort server token revocation, secure store "
            "cleanup, namespaced AsyncStorage cleanup, navigation-reset guidance."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Logout module input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "api_base_url": dict(_prop("api_base_url"), default=DEFAULT_API_BASE_URL)},
        ),
        "build": _expo_auth_logout,
    },
    # ---- Layer 12: Device features -------------------------------------------
    "device.gallery": {
        "description": (
            "Generate a gallery picker module on expo-image-picker: explicit permission "
            "flow, multi-select, structured result. Native config warnings included."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Gallery picker input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework")},
        ),
        "build": _expo_device_gallery,
    },
    "device.camera": {
        "description": (
            "Generate a camera capture module on expo-image-picker: permission flow, "
            "crop option, save-to-photos option, structured result."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Camera capture input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework")},
        ),
        "build": _expo_device_camera,
    },
    "device.gps": {
        "description": (
            "Generate a location module on expo-location: foreground permission flow, "
            "one-shot position, lifecycle-safe watch hook. Background location NOT configured."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Location input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework")},
        ),
        "build": _expo_device_gps,
    },
    "device.sharing": {
        "description": (
            "Generate share helpers: text via the system Share sheet and files via "
            "expo-sharing with availability checks."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Sharing input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework")},
        ),
        "build": _expo_device_sharing,
    },
    "device.permissions": {
        "description": (
            "Generate a unified permissions manager (camera, media library, location, "
            "notifications): rationales, ensurePermissions flow, denied -> openSettings."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Permissions manager input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "permissions": {
                 "type": "array",
                 "description": "Subset of: camera, media_library, location, notifications.",
                 "items": {"type": "string", "enum": list(_PERMISSION_KEYS)},
                 "default": list(_PERMISSION_KEYS),
             }},
        ),
        "build": _expo_device_permissions,
    },
    # ---- Layer 11: Push notifications ------------------------------------------
    "push.permission.request": {
        "description": (
            "Generate push permission setup on expo-notifications: Android channel, "
            "permission request, device token fetch, foreground presentation handler."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Push permission input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework")},
        ),
        "build": _expo_push_permission,
    },
    "push.handle.tap": {
        "description": (
            "Generate notification tap handling: cold + warm start listeners, whitelisted "
            "data-to-route mapper, navigation via React Navigation hook."
        ),
        "frameworks": ("expo",),
        "schema": _obj_schema(
            "Push tap handling input",
            {"name": _prop("name"), "identifier": _prop("identifier"), "framework": _prop("framework"),
             "screen_name": dict(_prop("screen_name"), default="Home")},
        ),
        "build": _expo_push_handle_tap,
    },
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

_BASE_INPUT_KEYS = set(_BASE_PROPERTIES)


def _make_ctx(skill_id: str, skill: Dict[str, Any], input_data: Any) -> _Ctx:
    if not isinstance(input_data, dict):
        raise ValidationError("input must be an object (dict), got %s" % type(input_data).__name__)
    for key in input_data:
        if not isinstance(key, str):
            raise ValidationError("input keys must be strings")
    schema = skill["schema"]
    properties: Dict[str, Dict[str, Any]] = schema["properties"]
    allowed = set(properties)
    unknown = sorted(set(input_data) - allowed)
    if unknown:
        raise ValidationError(
            "unknown input field(s) for %s: %s (allowed: %s)"
            % (skill_id, ", ".join(unknown), ", ".join(sorted(allowed)))
        )

    name = _validate_name(input_data.get("name", properties["name"]["default"]))
    identifier = _validate_identifier(input_data.get("identifier", properties["identifier"]["default"]))
    default_framework = skill["frameworks"][0] if skill["frameworks"] else "expo"
    framework = _validate_framework(
        input_data.get("framework", default_framework), skill["frameworks"]
    )

    screen_name: Optional[str] = None
    if "screen_name" in properties:
        raw_screen = input_data.get("screen_name", properties["screen_name"].get("default", "Home"))
        screen_name = _validate_pascal(raw_screen, "screen_name")

    api_base_url: Optional[str] = None
    if "api_base_url" in properties:
        raw_url = input_data.get("api_base_url")
        if raw_url is not None:
            api_base_url = _validate_https_url(raw_url)

    extras: Dict[str, Any] = {}
    for key, spec in properties.items():
        if key in _BASE_INPUT_KEYS:
            continue
        validator = _EXTRA_VALIDATORS.get(key)
        if validator is None:
            raise GeneratorError("internal: no validator for extra field %r" % key)
        value = input_data.get(key, spec.get("default"))
        extras[key] = validator(value)

    return _Ctx(
        skill_id=skill_id,
        name=name,
        identifier=identifier,
        framework=framework,
        screen_name=screen_name,
        api_base_url=api_base_url,
        extras=extras,
    )


def _published_schema(entry):
    schema = copy.deepcopy(entry['schema'])
    schema['properties']['framework']['enum'] = list(entry['frameworks'])
    if entry['frameworks']:schema['properties']['framework']['default'] = entry['frameworks'][0]
    return schema


def descriptors() -> Dict[str, Dict[str, Any]]:
    """Return ``{skill_id: {description, input_schema}}`` for every skill.

    The schema is a JSON-Schema-style object with ``additionalProperties:
    false`` - unknown input fields are rejected by :func:`dispatch`.
    """

    return {
        skill_id: {
            "description": entry["description"],
            "input_schema": _published_schema(entry),
        }
        for skill_id, entry in sorted(_SKILLS.items())
    }


def support(skill_id: str) -> bool:
    """True when :func:`dispatch` can complete this skill.

    ``scaffold.react_native_cli`` is registered (so it shows up in
    descriptors with an honest explanation) but returns False here because
    a bare React Native project cannot be generated completely offline.
    """

    if not isinstance(skill_id, str):
        return False
    entry = _SKILLS.get(skill_id)
    if entry is None:
        return False
    return bool(entry["frameworks"])


def dispatch(skill_id: str, input_data: Any) -> Dict[str, Any]:
    """Generate source files for ``skill_id``.

    Returns ``{status, mode, files, warnings, runtime_verified}`` where
    ``files`` is a list of ``{path, content}`` (relative paths, sorted).
    No filesystem, network or subprocess access is performed; the output is
    deterministic for identical inputs.

    Raises (all subclasses of GeneratorError/ValueError):
        UnknownSkillError - skill id not in the registry
        UnsupportedSkillError - registered skill that cannot be completed
        ValidationError - bad input shape, unknown fields, or unsafe values
    """

    if not isinstance(skill_id, str) or not skill_id.strip():
        raise UnknownSkillError("skill_id must be a non-empty string")
    entry = _SKILLS.get(skill_id)
    if entry is None:
        raise UnknownSkillError("unknown skill id: %r" % skill_id)
    if not entry["frameworks"]:
        raise UnsupportedSkillError(
            "%s is unsupported: %s" % (skill_id, entry["unsupported_reason"])
        )

    ctx = _make_ctx(skill_id, entry, input_data if input_data is not None else {})
    files, warnings = entry["build"](ctx)
    if not files:
        raise GeneratorError("internal: generator for %s produced no files" % skill_id)

    for path, content in files.items():
        if path.startswith("/") or ".." in path.split("/"):
            raise GeneratorError("internal: unsafe generated path %r" % path)
        if not isinstance(content, str) or not content:
            raise GeneratorError("internal: empty generated content for %r" % path)

    return {
        "status": "completed",
        "mode": "source_generation",
        "files": [{"path": path, "content": files[path]} for path in sorted(files)],
        "warnings": warnings,
        "runtime_verified": False,
    }
