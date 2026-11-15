"""Loopback-only ingestion and separate operator read boundary."""
import argparse
import asyncio
import hmac
import json
import logging
from pathlib import Path

from aiohttp import web
from .store import Store


def unique(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError('duplicate_json_key')
        value[key] = item
    return value


def app(store, ingest_token, admin_token):
    if min(len(ingest_token), len(admin_token)) < 32 or ingest_token == admin_token:
        raise ValueError('separate_service_tokens_required')
    counters = {'accepted':0, 'rejected':0, 'write_failed':0}
    admission = asyncio.Semaphore(8)

    @web.middleware
    async def boundary(request, handler):
        if request.path == '/healthz' and request.method == 'GET':
            return web.json_response({'ok':True, 'service':'harmonika-quality-audit', 'schema_version':1})
        expected = ingest_token if request.path == '/ingest' else admin_token
        if not hmac.compare_digest(request.headers.get('Authorization',''), 'Bearer '+expected):
            return web.json_response({'error':{'code':'forbidden'}}, status=403, headers={'Cache-Control':'no-store'})
        try:
            await asyncio.wait_for(admission.acquire(), .25)
        except asyncio.TimeoutError:
            return web.json_response({'error':{'code':'audit_busy'}},status=429)
        try:
            result = await handler(request)
        except web.HTTPException as error:
            result = web.json_response({'error':{'code':'input_too_large' if error.status==413 else 'not_found'}},status=error.status)
        except (ValueError, TypeError, KeyError, UnicodeError, RecursionError) as error:
            counters['rejected'] += 1
            full=isinstance(error,ValueError) and str(error)=='storage_capacity'
            result = web.json_response({'error':{'code':'audit_capacity' if full else 'invalid_audit_event'}},status=429 if full else 400)
        except Exception:
            counters['write_failed'] += 1
            result = web.json_response({'error':{'code':'audit_storage_unavailable'}},status=503)
        finally:
            admission.release()
        result.headers['Cache-Control']='no-store'
        result.headers['X-Content-Type-Options']='nosniff'
        return result

    application=web.Application(middlewares=[boundary],client_max_size=196608)

    async def body(request):
        if request.query:
            raise ValueError('unsupported_query')
        return json.loads(await request.read(),object_pairs_hook=unique,
                          parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite_json')))

    async def ingest(request):
        value = await body(request)
        result = await asyncio.to_thread(store.ingest,value)
        await asyncio.to_thread(store.count,'duplicate' if result.get('duplicate') else 'ingested')
        counters['accepted'] += 1
        return web.json_response(result)

    async def consent(request):
        value=await body(request)
        if type(value) is not dict or set(value)-{'token_id','granted','days','basis'}:
            raise ValueError('invalid_consent')
        result=await asyncio.to_thread(store.consent,value['token_id'],value['granted'],
                                       days=value.get('days'),basis=value.get('basis','operator_confirmed'))
        return web.json_response(result)

    async def traces(request):
        if set(request.query)-{'token_id','before','limit','include_content'}:
            raise ValueError('unsupported_query')
        content=request.query.get('include_content','false')
        if content not in ('true','false'):
            raise ValueError('invalid_content_flag')
        limit=int(request.query.get('limit','5' if content=='true' else '25'))
        if content=='true' and limit>5:
            raise ValueError('content_page_limit')
        return web.json_response(await asyncio.to_thread(store.list,int(request.query['token_id']),
            before=request.query.get('before'),limit=limit,include_content=content=='true'))

    async def summary(request):
        if set(request.query)-{'token_id'}:
            raise ValueError('unsupported_query')
        return web.json_response(await asyncio.to_thread(store.summary,int(request.query['token_id']) if 'token_id' in request.query else None))

    async def purge(request):
        value=await body(request)
        if type(value) is not dict or set(value)-{'token_id'}:
            raise ValueError('invalid_purge')
        return web.json_response(await asyncio.to_thread(store.purge,value.get('token_id')))

    async def metrics(request):
        if request.query:
            raise ValueError('unsupported_query')
        return web.json_response({'process_counters':counters.copy(),
            'retention_days':store.retention//86400,'max_events':store.max_events,'max_bytes':store.max_bytes,
            'content_policy':'per-key consent, encrypted redacted samples',
            'raw_api_keys_stored':False,'automatic_training':False})

    async def maintenance(application):
        async def run():
            while True:
                try:
                    await asyncio.to_thread(store.purge)
                except Exception:
                    counters['write_failed']+=1
                await asyncio.sleep(60)
        task=asyncio.create_task(run())
        yield
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

    application.router.add_post('/ingest',ingest)
    application.router.add_get('/traces',traces)
    application.router.add_get('/summary',summary)
    application.router.add_get('/metrics',metrics)
    application.router.add_post('/consent',consent)
    application.router.add_post('/purge',purge)
    application.cleanup_ctx.append(maintenance)
    return application


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,required=True)
    args=parser.parse_args()
    config=json.loads(args.config.read_text())
    def private_file(name):
        path=Path(config[name])
        if path.stat().st_mode & 0o077:
            raise ValueError('private_file_permissions_required')
        return path.read_bytes()
    store=Store(config['database'],private_file('encryption_key_file'),private_file('identity_key_file'),
                retention_days=config.get('retention_days',30),max_events=config.get('max_events',100000),
                max_bytes=config.get('max_bytes',536870912))
    application=app(store,private_file('ingest_token_file').decode().strip(),private_file('admin_token_file').decode().strip())
    logging.basicConfig(level=logging.WARNING,format='%(levelname)s quality_audit')
    web.run_app(application,host='127.0.0.1',port=int(config.get('port',18208)),access_log=None,print=None)


if __name__=='__main__':
    main()
