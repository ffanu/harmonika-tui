"""Drive the real native binary and workbench in an isolated PTY, no user sessions."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('native_smoke',ROOT/'scripts/smoke-native-interaction.py')
native=importlib.util.module_from_spec(spec);spec.loader.exec_module(native)


class FileReadFixture(native.GatewayFixture):
    def __init__(self,path):
        self.path=str(path);self.tool_issued=False;self.tool_available=False
        super().__init__()
    def _upstream(self,request):
        import io
        body=json.loads(request.data)
        tools=[x.get('function',{}) for x in body.get('tools',[]) if isinstance(x,dict)]
        selected=next((x for x in tools if x.get('name')=='view'),None)
        self.tool_available=self.tool_available or selected is not None
        if selected and not self.tool_issued:
            self.tool_issued=True;self.requests.append(True)
            properties=selected.get('parameters',{}).get('properties',{})
            key='file_path' if 'file_path' in properties else 'path'
            arguments=json.dumps({key:self.path})
            chunks=[{'id':'fixture-view','object':'chat.completion.chunk','model':'harmonika-ai',
                'choices':[{'index':0,'delta':{'role':'assistant','tool_calls':[{'index':0,'id':'fixture_view',
                    'type':'function','function':{'name':'view','arguments':arguments}}]},'finish_reason':None}]},
                {'id':'fixture-view','object':'chat.completion.chunk','model':'harmonika-ai','choices':[{'index':0,'delta':{},'finish_reason':'tool_calls'}]}]
            return io.BytesIO((''.join('data: '+json.dumps(x)+'\n\n' for x in chunks)+'data: [DONE]\n\n').encode())
        return super()._upstream(request)


def main():
    installed='--installed' in sys.argv
    base=Path(tempfile.mkdtemp(prefix='harmonika-workbench-qa-')).resolve();tools=base/'tools';tools.mkdir()
    for name in ['workbench.py','workbench_ui.py','workbench_model.py']:shutil.copy2(ROOT/'runtime/tools'/name,tools/name)
    artifacts=Path.home()/'.config/harmonika-terminal/workbench-build/artifacts'
    for name in ['workbench-tmux','workbench-runtime.json']:shutil.copy2(artifacts/name,tools/name)
    config=base/'config';config.mkdir(mode=0o700);data=base/'data';data.mkdir()
    project=base/'project';project.mkdir();(project/'hello.py').write_text('def greet():\n    # preserved code\n    return "Hello"\n\nif __name__ == "__main__":\n    print("WORKBENCH_RUN_OK")\n')
    (project/'hello.ts').write_text('export const greeting: string = "Hello, Harmonika";\n')
    (project/'AGENTS.md').write_text('Synthetic QA only. Do not run commands.')
    (project/'package.json').write_text('{"name":"harmonika-workbench-qa","private":true}')
    (project/'tsconfig.json').write_text('{"compilerOptions":{"strict":true}}')
    (project/'pyproject.toml').write_text('[project]\nname="harmonika-workbench-qa"\nversion="0.0.0"\n')
    cfg={'models':{'large':{'model':'harmonika-ai','provider':'harmonika'},'small':{'model':'harmonika-ai','provider':'harmonika'}},
        'providers':{'harmonika':{'name':'Harmonika Ai','api_key':'fixture-token','models':[{'id':'harmonika-ai','name':'Harmonika Ai','context_window':32768,'max_tokens':8192}]}},
        'mcp':{},'lsp':{},'options':{'disable_provider_auto_update':True,'disable_default_providers':True,'disable_metrics':True,
        'data_directory':'.harmonika','context_paths':[],'global_context_paths':[],
        'disabled_skills':['harmonika-config','harmonika-hooks'],'tui':{'mouse':True,'scrollbar':'always','exit_banner':'compact'}},'permissions':{}}
    with_lsp='--with-lsp' in sys.argv
    if with_lsp:
        cfg['lsp']=json.loads((Path.home()/'.config/harmonika/harmonika.json').read_text()).get('lsp',{})
        cfg['options']['auto_lsp']=True
        cfg['permissions']['allowed_tools']=['view']  # only this isolated fixture; global approvals unchanged
    gateway=FileReadFixture(project/'hello.ts') if with_lsp else native.GatewayFixture()
    gateway.start();cfg['providers']['harmonika']['base_url']='http://127.0.0.1:'+str(gateway.port)+'/v1'
    for name in ['harmonika.json','crush.json']:(config/name).write_text(json.dumps(cfg));(config/name).chmod(0o600)
    binary=(Path.home()/'.config/harmonika-terminal/current/harmonika').resolve();before=hashlib.sha256(binary.read_bytes()).hexdigest()
    entry=[sys.executable,'-I',str(Path.home()/'.config/harmonika-terminal/launcher.py')] if installed else [sys.executable,'-I',str(tools/'workbench.py'),'start','--offline','--binary',str(binary),'--config',str(config),'--data',str(data),'--']
    wrapper=base/'start';wrapper.write_text('#!/bin/sh\nexec '+__import__('shlex').join(entry)+' "$@"\n');wrapper.chmod(0o700)
    native.LAUNCHER=wrapper
    env={k:v for k,v in os.environ.items() if not k.startswith(('HARMONIKA_','CRUSH_'))}
    env.update(HARMONIKA_GLOBAL_CONFIG=str(config),HARMONIKA_GLOBAL_DATA=str(data),TERM='xterm-256color',COLORTERM='truecolor',LANG='en_US.UTF-8')
    if installed:
        home=base/'home';home.mkdir()
        env.update(HOME=str(home),HARMONIKA_TERMINAL_HOME=str(Path.home()/'.config/harmonika-terminal'))
    session=None;checks={};captures=ROOT/'docs/mockups/workbench-captures';captures.mkdir(parents=True,exist_ok=True)
    try:
        session=native.TuiSession(env,str(project),['--cwd',str(project)] if installed else [],42,150)
        session.pump(5)
        visible=session.term.visible();(captures/'home.txt').write_text(visible)
        checks['native_home_and_workbench_status']='Harmonika' in visible and ('F2' in visible or 'Metrik' in visible)
        session.send(b'\x10');session.pump(1)
        palette=session.term.visible();(captures/'commands.txt').write_text(palette)
        checks['native_command_palette']=any(x in palette.lower() for x in ['commands','new chat','command']) and palette!=visible
        session.send(b'\x1b');session.pump(.5)
        if with_lsp:
            context_file=next((config/'workbench-state').glob('*/context.json'))
            ctx=json.loads(context_file.read_text())
            root_pid=int(subprocess.check_output([ctx['tmux'],'-L',ctx['socket'],'list-panes','-F','#{pane_pid}'],text=True).splitlines()[0])
            listing=subprocess.check_output(['ps','-axo','pid=,ppid=,comm='],text=True)
            processes={}
            for line in listing.splitlines():
                values=line.split(None,2)
                if len(values)==3:processes[int(values[0])]=(int(values[1]),values[2])
            owned={root_pid}
            for _ in range(8):owned.update(p for p,(parent,_) in processes.items() if parent in owned)
            count=sum(1 for p in owned if p in processes and Path(processes[p][1]).name=='node')
            # LSPs may stay idle until a file is read. Do not mislabel this as a
            # startup failure or force five unrelated servers to consume CPU.
            lsp_at_start=count
        session.send(b'\x1bOQ');session.pump(2)
        metrics=session.term.visible();(captures/'metrics.txt').write_text(metrics)
        checks['physical_F2_metrics_popup']='Metrik' in metrics and ('CPU' in metrics or 'Metadata' in metrics)
        session.send('q');session.pump(.8)
        session.send(b'\x1bOR');session.pump(1)
        events=session.term.visible();(captures/'events.txt').write_text(events)
        checks['physical_F3_events_popup']='Kejadian' in events
        session.send('q');session.pump(.8)
        session.send(b'\x1bOS');session.pump(1.5)
        files=session.term.visible();(captures/'files.txt').write_text(files)
        checks['physical_F4_file_code_pane']='File / Kode' in files and 'hello.py' in files
        session.send('/hello.py');session.send(b'\r');session.pump(.5)
        session.send('r');session.pump(.4)
        confirm=session.term.visible();checks['manual_run_requires_confirmation']='Jalankan berkas proyek ini?' in confirm
        session.send('y');session.pump(1)
        run_output=session.term.visible()
        checks['manual_run_inline_output']='WORKBENCH_RUN_OK' in run_output and 'Tekan Enter untuk kembali' in run_output
        session.send(b'\r');session.pump(.6)
        session.send('q');session.pump(.8)
        if not installed:
            session.send('Tampilkan kode fixture untuk uji streaming.');session.send(b'\r')
            completed=session.wait_until(lambda:native.MARK+'-L' in session.term.visible(),20,'streamed code')
            session.pump(2);chat=session.term.visible();(captures/'chat.txt').write_text(chat)
            checks['real_native_streamed_code']=completed
        if with_lsp:
            listing=subprocess.check_output(['ps','-axo','pid=,ppid=,comm='],text=True);processes={}
            for line in listing.splitlines():
                values=line.split(None,2)
                if len(values)==3:processes[int(values[0])]=(int(values[1]),values[2])
            owned={root_pid}
            for _ in range(8):owned.update(p for p,(parent,_) in processes.items() if parent in owned)
            count=sum(1 for p in owned if p in processes and Path(processes[p][1]).name=='node')
            checks['native_lsp_auto_start_on_file_read']=count>=1 and gateway.tool_issued
            (ROOT/'docs/TUI-WORKBENCH-LSP.json').write_text(json.dumps({'native_auto_lsp':True,'startup_node_client_count':lsp_at_start,
                'after_file_read_node_client_count':count,'view_tool_available':gateway.tool_available,'view_tool_called':gateway.tool_issued,
                'configured_lsp_count':len(cfg['lsp'])},indent=2))
        session.resize(24,80);session.pump(1);(captures/'narrow.txt').write_text(session.term.visible())
        checks['resize_80x24']=not session.closed and 'Harmonika' in session.term.visible()
        session.resize(42,150);session.pump(.5)
        checks['native_binary_identical']=hashlib.sha256(binary.read_bytes()).hexdigest()==before
        checks['no_inference_on_installed_smoke' if installed else 'fixture_model_only']=not gateway.requests if installed else bool(gateway.requests)
    finally:
        try:
            if session:
                session.send(b'\x1b');session.send('q');session.pump(.2)
                session.send(b'\x03');session.pump(1.5);session.send(b'\x03');session.pump(1)
                session._reap(False)
                checks['clean_exit']=session.exit_code==0
                if session.exit_code is None:session.terminate()
        finally:
            gateway.stop()
    report={'ok':all(checks.values()),'checks':checks,'isolated':True,'production_model_calls':False,
            'binary_sha256':before,'fixture_dir':str(base),'captures':'docs/mockups/workbench-captures'}
    (ROOT/'docs'/('TUI-WORKBENCH-INSTALLED-SMOKE.json' if installed else 'TUI-WORKBENCH-SMOKE.json')).write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2));return 0 if report['ok'] else 1


if __name__=='__main__':raise SystemExit(main())
