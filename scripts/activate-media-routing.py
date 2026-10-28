import pathlib,hashlib,subprocess,json,time,shutil
r=pathlib.Path('/opt/harmonika-ai/media-suite');p=pathlib.Path('/opt/harmonika-ai/crush-bridge/worker.py');old=p.read_text();assert 'harmonika_media_suite_rpc' not in old
needle='    op = data.get("operation")\n';assert old.count(needle)==1
hook='''    if op in ("media_capabilities", "transcribe_audio", "upscale_image", "browse_page", "synthesize_speech", "audio.synthesize"):
        sys.path.insert(0,"/opt/harmonika-ai/media-suite")
        import rpc_facade as harmonika_media_suite_rpc
        return harmonika_media_suite_rpc.safe_dispatch(data)
'''
new=old.replace(needle,needle+hook)
compile(new,str(p),'exec');stage=p.with_name('worker.media-next.py');stage.write_text(new)
smoke={'operation':'media_capabilities'}
x=subprocess.run(['/usr/bin/python3',str(stage)],input=json.dumps(smoke).encode(),capture_output=True,timeout=30,check=True);v=json.loads(x.stdout);assert v['features']['audio_transcription'] and v['features']['image_upscale'] and v['features']['browser_inspection'] and v['features']['speech_synthesis'] is False
backup=pathlib.Path('/var/backups/harmonika-media-routing')/str(time.time_ns());backup.mkdir(parents=True,mode=0o700);shutil.copy2(p,backup/'worker.py');stage.chmod(p.stat().st_mode & 0o777);stage.replace(p)
cfg=json.loads((r/'config.json').read_text());cfg['browser_allowed_hosts']=['example.com','harmonika.id','github.com','docs.python.org','developer.mozilla.org'];tmp=r/'config.next';tmp.write_text(json.dumps(cfg,indent=2));shutil.chown(tmp,user='harmonika-media',group='harmonika-media');tmp.chmod(0o600);tmp.replace(r/'config.json')
print(json.dumps({'rpc_hook_active':True,'backup':str(backup),'gateway_restarted':False,'research_tts_public_enabled':False}))
