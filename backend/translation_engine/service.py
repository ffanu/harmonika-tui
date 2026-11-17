"""Authenticated loopback HTTP boundary; aiohttp access logging is disabled."""
import asyncio
import hmac
import os
from pathlib import Path

from aiohttp import web

from .engine import (Audit, Catalog, Inference, MAX_OUTPUT_BYTES, MAX_REQUEST_BYTES,
                     Runtime, SKILLS, TranslationError, decode_json, encoded, obj, require)


def create_app(runtime=None, secret=None, token_file=None):
    if secret is None:
        secret = Path(token_file or os.environ.get("HARMONIKA_TRANSLATION_TOKEN_FILE",
                      "/etc/harmonika-ai/translation-engine.token")).read_text().strip()
    require(type(secret) is str and len(secret) >= 16, "translation_not_configured", 503)
    if runtime is None:
        catalog = Catalog()
        audit = Audit(os.environ.get("HARMONIKA_TRANSLATION_AUDIT_DB",
                      "/var/lib/harmonika-translation/audit.sqlite3"), secret)
        runtime = Runtime(catalog, provider=Inference(), audit=audit)

    def output(value, status=200):
        raw = encoded(value)
        require(len(raw) <= MAX_OUTPUT_BYTES, "translation_output_limit", 502)
        return web.Response(body=raw, status=status, content_type="application/json",
                            headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

    @web.middleware
    async def boundary(request, handler):
        try:
            require(request.remote in ("127.0.0.1", "::1", "::ffff:127.0.0.1"), "translation_forbidden", 403)
            supplied = request.headers.get("X-Harmonika-Translation-Secret", "")
            require(hmac.compare_digest(secret.encode("utf-8"), supplied.encode("utf-8")),
                    "translation_forbidden", 403)
            require(not request.query, "unsupported_query")
            return await handler(request)
        except TranslationError as error:
            return output({"ok": False, "error": error.code}, error.status)
        except web.HTTPException as error:
            code = "input_too_large" if error.status == 413 else "method_not_allowed" if error.status == 405 else "not_found"
            return output({"ok": False, "error": code}, error.status)
        except asyncio.CancelledError:
            raise
        except Exception:
            return output({"ok": False, "error": "translation_unavailable"}, 503)

    app = web.Application(middlewares=[boundary], client_max_size=MAX_REQUEST_BYTES)

    async def health(request):
        last_success = getattr(runtime.provider, "last_successful_inference_at", None)
        return output({"ok": True, "service": "harmonika-translation", "schema_version": 1,
                       "mode": "harmonika_text_inference", "inference_verified": last_success is not None,
                       "active_requests": runtime.active_requests,
                       "last_successful_inference_at": last_success,
                       "inference_verification": "successful_response_since_startup",
                       "language_quality_qualified": False,
                       "registration_is_not_verified_quality": True,
                       "limits": {"request_bytes": MAX_REQUEST_BYTES, "text_characters": 40000,
                                  "chunk_characters": 4000, "batch_items": 16,
                                  "concurrency": 2, "timeout_seconds": 120}})

    async def languages(request):
        return output(await runtime.invoke("translation.languages", {}, request.headers.get("X-Harmonika-Translation-Actor")))

    async def execute(request):
        require(request.content_type == "application/json", "json_required", 415)
        value = decode_json(await request.read())
        skill = {"/translate": "translation.translate", "/detect": "translation.detect",
                 "/batch": "translation.batch"}.get(request.path)
        if request.path == "/invoke":
            obj(value, ("skill_id", "input"))
            skill, value = value["skill_id"], value["input"]
            require(type(skill) is str and skill in SKILLS, "unknown_skill", 404)
        actor = request.headers.get("X-Harmonika-Translation-Actor")
        require(actor is None or (len(actor) <= 128 and actor.isascii()), "invalid_actor")
        return output(await runtime.invoke(skill, value, actor))

    for route in ("/health", "/healthz"):
        app.router.add_get(route, health)
    app.router.add_get("/languages", languages)
    for route in ("/translate", "/detect", "/batch", "/invoke"):
        app.router.add_post(route, execute)

    async def close(app):
        await runtime.close()

    app.on_cleanup.append(close)
    return app


def main():
    # No public interface option, raw startup exceptions or request access logs.
    try:
        app = create_app()
    except Exception:
        raise SystemExit("translation_not_configured") from None
    web.run_app(app, host="127.0.0.1", port=17918, access_log=None, print=None)


if __name__ == "__main__":
    main()
