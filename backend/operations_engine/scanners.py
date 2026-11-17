"""Fixed, offline Semgrep/Trivy adapters; reports never contain code or matches."""
import hashlib
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import tempfile
from ._adapter import artifact_root, checked, environment, execute, project, snapshot, tool

HERE=Path(__file__).resolve().parent
PINS={'semgrep':'1.185.0','trivy':'0.76.2'}
RULE_HASHES={'semgrep.json':'6739937c909ece6b82a08bdedf55767b2ba1330ae8a0dcc54366bfd0e803dae0','docker-root.rego':'f9c7fbfc383e2451df9adf8e199069f191e261ed43420e40442f178248422631'}
SEVERITY={'ERROR':'HIGH','WARNING':'MEDIUM','INFO':'LOW','CRITICAL':'CRITICAL','HIGH':'HIGH','MEDIUM':'MEDIUM','LOW':'LOW','UNKNOWN':'UNKNOWN'}
RULE=re.compile(r'^[A-Za-z0-9_.:-]{1,180}$')


def _rule(name):
    file=checked(HERE/'rules'/name)
    if hashlib.sha256(file.read_bytes()).hexdigest()!=RULE_HASHES[name]:raise ValueError('scanner_rules_integrity_failed')
    return file


def _version(config,name):
    binary=tool(config,name)
    if not binary:return None,'scanner_executable_missing'
    try:
        with tempfile.TemporaryDirectory(prefix='harmonika-scanner-probe-') as temporary:
            code,raw=execute([binary,'--version'],cwd=HERE,timeout=10,env=environment(Path(temporary)),maximum=16384)
        match=re.search(rb'(?<![0-9])([0-9]+\.[0-9]+\.[0-9]+)(?![0-9])',raw)
        version=match.group(1).decode('ascii') if code==0 and match else None
        expected=config.get('tools',{}).get('versions',{}).get(name,PINS[name])
        if not version or version!=expected:return None,'scanner_version_mismatch'
        return version,None
    except (OSError,ValueError):return None,'scanner_probe_failed'


def _database(config):
    raw=config.get('tools',{}).get('trivy_cache')
    if not isinstance(raw,str) or not Path(raw).is_absolute():return None
    try:
        root=checked(raw,directory=True);database=checked(root/'db/trivy.db');metadata=checked(root/'db/metadata.json')
        # Trivy DB v2 is BoltDB, not the application's SQLite journal. The
        # actual scanner verifies DB structure; this probe only checks readiness.
        if database.stat().st_size<4096:return None
        if metadata.stat().st_size>16384:return None
        value=json.loads(metadata.read_text(encoding='utf-8'))
        if value.get('Version')!=2:return None
        expiry=datetime.fromisoformat(value.get('NextUpdate','').replace('Z','+00:00'))
        if expiry.tzinfo is None or expiry<=datetime.now(timezone.utc):return None
        return root
    except (OSError,ValueError,TypeError):return None


def capabilities(config):
    result={}
    policy=config.get('scanner_policy',{})
    for name in ('semgrep','trivy'):
        version,reason=_version(config,name)
        result[name]={'status':'available' if version else 'needs_toolchain','version':version,'reason':reason,'verified_scan':False}
        if name=='trivy':result[name]['vulnerability_database']='ready' if _database(config) else 'needs_toolchain'
        result[name]['enabled']=policy.get(name) is True if name=='semgrep' else list(policy.get(name,[])) if isinstance(policy.get(name),list) else []
    return {'status':'available' if all(row['status']=='available' for row in result.values()) else 'needs_toolchain','scanners':result}


def _relative_target(value,stage,files):
    if not isinstance(value,str):raise ValueError('invalid_scanner_report')
    text=value.replace('\\','/')
    prefix=str(stage).replace('\\','/')+'/'
    if text.startswith(prefix):text=text[len(prefix):]
    if text not in files and ' (' in text:text=text.split(' (',1)[0]
    if text not in files:raise ValueError('scanner_report_outside_snapshot')
    return text


def _finding(rule,file,line,severity):
    if not isinstance(rule,str) or not RULE.fullmatch(rule):raise ValueError('invalid_scanner_report')
    if not isinstance(line,int) or isinstance(line,bool) or not 0<=line<=10000000:line=0
    return {'rule':rule,'file':file,'line':line,'severity':SEVERITY.get(str(severity).upper(),'UNKNOWN')}


def _parse(name,mode,data,stage,files):
    findings=[];supported=0;partial=False
    if name=='semgrep':
        if not isinstance(data.get('results'),list) or not isinstance(data.get('paths'),dict):raise ValueError('invalid_scanner_report')
        scanned=data['paths'].get('scanned')
        if not isinstance(scanned,list) or len(scanned)>512:raise ValueError('invalid_scanner_report')
        for file in scanned:_relative_target(file,stage,files)
        supported=len(scanned);partial=bool(data.get('errors'))
        for row in data['results']:
            file=_relative_target(row.get('path'),stage,files)
            findings.append(_finding(row.get('check_id'),file,row.get('start',{}).get('line',0),row.get('extra',{}).get('severity')))
    else:
        if data.get('SchemaVersion')!=2 or not isinstance(data.get('Results',[]),list):raise ValueError('invalid_scanner_report')
        for target in data.get('Results',[]):
            file=_relative_target(target.get('Target'),stage,files);supported+=1
            key={'vuln':'Vulnerabilities','misconfig':'Misconfigurations','secret':'Secrets'}[mode]
            for row in target.get(key) or []:
                rule=row.get({'vuln':'VulnerabilityID','misconfig':'ID','secret':'RuleID'}[mode])
                line=row.get('StartLine',row.get('CauseMetadata',{}).get('StartLine',0))
                findings.append(_finding(rule,file,line,row.get('Severity')))
    if len(findings)>1000:raise ValueError('scanner_findings_limit')
    unique={json.dumps(row,sort_keys=True):row for row in findings}
    return sorted(unique.values(),key=lambda row:(row['file'],row['line'],row['rule'])),supported,partial


def scan(config,payload):
    if not isinstance(payload,dict) or set(payload)-{'scanner','mode','path'}:raise ValueError('invalid_scanner_payload')
    name=payload.get('scanner');mode=payload.get('mode','vuln')
    if name not in PINS or mode not in ('vuln','misconfig','secret'):raise ValueError('invalid_scanner_payload')
    policy=config.get('scanner_policy',{})
    if (name=='semgrep' and policy.get(name) is not True) or (name=='trivy' and mode not in policy.get(name,[])):raise ValueError('scanner_not_allowed')
    root,selected=project(config,payload.get('path','.'))
    version,reason=_version(config,name)
    if not version:return {'ok':False,'status':'needs_toolchain','scanner':name,'reason':reason}
    database=_database(config) if name=='trivy' else None
    if name=='trivy' and mode=='vuln' and database is None:return {'ok':False,'status':'needs_toolchain','scanner':name,'reason':'trivy_vulnerability_database_missing'}
    private=artifact_root(config)
    with tempfile.TemporaryDirectory(prefix='scanner-',dir=private) as temporary:
        work=Path(temporary);stage=work/'source';stage.mkdir(mode=0o700);home=work/'home';home.mkdir(mode=0o700)
        copied=snapshot(root,selected,stage);binary=tool(config,name)
        if name=='semgrep':
            argv=[binary,'scan','--config',str(_rule('semgrep.json')),'--json','--metrics=off','--disable-version-check','--no-git-ignore','--strict','--jobs','1','--timeout','10','--max-target-bytes','4194304','--',str(stage)]
        else:
            settings=work/'trivy.yaml';settings.write_text('{}\n',encoding='utf-8');ignore=work/'ignore';ignore.write_text('',encoding='utf-8')
            cache=database or work/'cache';cache.mkdir(mode=0o700,exist_ok=True)
            argv=[binary,'fs','--config',str(settings),'--ignorefile',str(ignore),'--secret-config','','--scanners',mode,'--format','json','--exit-code','0','--timeout','60s','--disable-telemetry','--skip-version-check','--skip-db-update','--skip-java-db-update','--skip-check-update','--offline-scan','--cache-backend','memory','--cache-dir',str(cache),'--list-all-pkgs=false','--quiet']
            if mode=='misconfig':argv+=['--config-check',str(_rule('docker-root.rego')),'--namespaces','harmonika']
            argv+=['--',str(stage)]
        code,raw=execute(argv,cwd=work,timeout=75,env=environment(home),maximum=4*1024*1024)
        if code!=0:raise ValueError('scanner_execution_failed')
        try:data=json.loads(raw.decode('utf-8'))
        except (ValueError,UnicodeError):raise ValueError('invalid_scanner_report') from None
        if not isinstance(data,dict):raise ValueError('invalid_scanner_report')
        findings,supported,partial=_parse(name,mode,data,stage,set(copied['files']))
        status='partial' if partial else 'completed' if supported else 'inconclusive'
        return {'ok':status=='completed','status':status,'scanner':name,'mode':mode if name=='trivy' else 'sast','version':version,'execution_performed':True,'passed':not findings if status=='completed' else None,'findings':findings,'findings_count':len(findings),'targets_scanned':supported,'files_copied':copied['files_copied'],'files_skipped':copied['files_skipped'],'coverage':'bounded_source_snapshot','reason':'scan_incomplete' if partial else 'no_supported_targets' if not supported else None,'limitations':['Only selected bounded source files and pinned local rules/databases were inspected. No code, package scripts, remediation, remote registry, or deployment was executed.']}
