"""Reviewed local release swaps with durable intents and verified rollback.

No command executor, service manager, signals, proxy, redirect or remote target.
All paths come from operator-registered aliases; journals retain hashes only.
"""
from contextlib import contextmanager
import hashlib
import hmac
import http.client
import ipaddress
import json
import os
from pathlib import Path, PurePosixPath
import re
import stat
from urllib.parse import urlsplit
from uuid import uuid4
try:
    import fcntl
except ImportError:
    fcntl=None
from .jobs import JobStore, JobError, digest, key_bytes, linklike

ZERO='0'*64
MAX_BYTES=256*1024*1024
MAX_FILES=4096

class DeploymentError(ValueError):
    def __init__(self,code):self.code=code;super().__init__(code)

def require(value,code):
    if not value:raise DeploymentError(code)

def root_path(value):
    p=Path(value)
    require(p.is_absolute() and '..' not in p.parts and p.is_dir(),'deployment_root_invalid')
    require(not any(linklike(q) for q in (p,*p.parents)) and not p.stat().st_mode&0o022,'deployment_path_unsafe')
    return p.resolve()

def sync_directory(path):
    if os.name=='posix':
        fd=os.open(path,os.O_RDONLY)
        try:os.fsync(fd)
        finally:os.close(fd)

def component(value):
    require(isinstance(value,str) and re.fullmatch('[A-Za-z0-9_][A-Za-z0-9._-]{0,127}',value) and value not in ('.','..'),'deployment_path_unsafe')
    return value

def artifact_path(root,value):
    require(isinstance(value,str) and len(value)<=512 and '\\' not in value,'deployment_artifact_invalid')
    parts=PurePosixPath(value)
    require(not parts.is_absolute() and bool(parts.parts) and value=='/'.join(parts.parts),'deployment_artifact_invalid')
    for part in parts.parts:component(part)
    path=root.joinpath(*parts.parts)
    require(not any(linklike(p) for p in (path,*path.parents)) and path.is_dir(),'deployment_artifact_invalid')
    require(path.resolve().is_relative_to(root) and path.resolve()!=root,'deployment_path_unsafe')
    return path

def tree(path,copy_to=None):
    """Hash bounded regular files by relative name, size, content; no payload logs."""
    require(path.is_dir() and not linklike(path),'deployment_path_unsafe')
    total=0;count=0;manifest=[]
    if copy_to is not None:copy_to.mkdir(mode=0o700)
    for base,dirs,files in os.walk(path,followlinks=False):
        base=Path(base);dirs.sort();files.sort()
        require(not any(linklike(p) for p in (base,*base.parents)),'deployment_path_unsafe')
        for name in dirs:
            component(name);require(not linklike(base/name),'deployment_path_unsafe')
            if copy_to is not None:(copy_to/(base/name).relative_to(path)).mkdir(mode=0o700)
        for name in files:
            component(name);source=base/name
            require(not linklike(source),'deployment_path_unsafe')
            fd=os.open(source,os.O_RDONLY|getattr(os,'O_NOFOLLOW',0))
            output=None
            try:
                before=os.fstat(fd)
                require(stat.S_ISREG(before.st_mode) and before.st_nlink==1 and not before.st_mode&0o022,'deployment_path_unsafe')
                count+=1;total+=before.st_size
                require(count<=MAX_FILES and total<=MAX_BYTES,'deployment_artifact_too_large')
                sha=hashlib.sha256();read=0
                if copy_to is not None:
                    dest=copy_to/source.relative_to(path)
                    output=os.open(dest,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
                while True:
                    chunk=os.read(fd,256*1024)
                    if not chunk:break
                    read+=len(chunk);require(read<=before.st_size,'deployment_artifact_changed')
                    sha.update(chunk)
                    if output is not None:
                        view=memoryview(chunk)
                        while view:view=view[os.write(output,view):]
                after=os.fstat(fd)
                require(read==before.st_size and (before.st_ino,before.st_size,before.st_mtime_ns)==(after.st_ino,after.st_size,after.st_mtime_ns),'deployment_artifact_changed')
                if output is not None:os.fsync(output)
                manifest.append([source.relative_to(path).as_posix(),before.st_size,sha.hexdigest()])
            finally:
                os.close(fd)
                if output is not None:os.close(output)
    require(count>0,'deployment_artifact_empty')
    if copy_to is not None:
        for base,_,_ in os.walk(copy_to,topdown=False):sync_directory(base)
    return {'sha256':digest(manifest),'files':count,'bytes':total}

@contextmanager
def alias_lock(root,identity=None):
    # Kernel lock lifetime follows the physical worker, not its journal lease.
    # A crashed worker releases it automatically; a merely stale worker does not.
    require(fcntl is not None,'deployment_platform_unsupported')
    path=root/'.operations-deployment.lock'
    require(not linklike(path),'deployment_lock_unsafe')
    fd=os.open(path,os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
    try:
        info=os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink==1 and not info.st_mode&0o077,'deployment_lock_unsafe')
        try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:raise DeploymentError('deployment_busy') from None
        try:
            if identity is not None:
                os.ftruncate(fd,0);os.write(fd,identity.encode('ascii'));os.fsync(fd)
            yield
        finally:fcntl.flock(fd,fcntl.LOCK_UN)
    finally:os.close(fd)

class DeploymentEngine:
    def __init__(self,root,key,config,job_store=None):
        require(type(config) is dict,'deployment_config_invalid')
        self.key=key_bytes(key);self.jobs=job_store or JobStore(root,self.key);self.config=config

    def _alias(self,actor,project,alias):
        component(alias)
        item=self.config.get('deployments',{}).get(alias)
        require(type(item) is dict,'deployment_not_registered')
        require(type(item.get('actors')) is list and type(item.get('projects')) is list and all(isinstance(v,str) for v in item['actors']+item['projects']),'deployment_config_invalid')
        require(actor in item['actors'] and project in item['projects'],'deployment_scope_denied')
        release=root_path(item['release_root']);artifacts=root_path(item['artifacts_root'])
        require(release!=artifacts and not release.is_relative_to(artifacts) and not artifacts.is_relative_to(release),'deployment_roots_overlap')
        releases=release/'releases';require(releases.is_dir() and not linklike(releases),'deployment_releases_missing')
        parsed=urlsplit(item.get('health_url',''))
        try:loopback=ipaddress.ip_address(parsed.hostname or '').is_loopback
        except ValueError:loopback=False
        require(parsed.scheme=='http' and loopback and parsed.port is not None and not parsed.username and not parsed.password and not parsed.query and not parsed.fragment,'deployment_health_url_denied')
        expect=item.get('health_expect',{})
        require(type(expect) is dict and expect.get('status')==200 and isinstance(expect.get('body_sha256'),str) and re.fullmatch('[a-f0-9]{64}',expect['body_sha256']),'deployment_health_expectation_invalid')
        fingerprint=digest({'alias':self.jobs.ref('deploy-alias',alias),'release':self.jobs.ref('deploy-root',str(release)),'artifacts':self.jobs.ref('artifact-root',str(artifacts)),'health':self.jobs.ref('health-url',item['health_url']),'expect':expect})
        return {'release':release,'releases':releases,'artifacts':artifacts,'health':parsed,'expect':expect,'fingerprint':fingerprint,'resource':self.jobs.ref('deployment-resource',str(release))}

    def _current(self,cfg):
        pointer=cfg['release']/'current'
        require(pointer.is_symlink(),'deployment_current_missing')
        relative=os.readlink(pointer)
        require(not Path(relative).is_absolute() and '\\' not in relative,'deployment_pointer_unsafe')
        parts=PurePosixPath(relative)
        require(len(parts.parts)==2 and parts.parts[0]=='releases' and relative=='/'.join(parts.parts),'deployment_pointer_unsafe')
        component(parts.parts[1]);target=cfg['release'].joinpath(*parts.parts)
        require(target.parent==cfg['releases'] and target.is_dir() and not linklike(target),'deployment_pointer_unsafe')
        snapshot=tree(target)
        return {'target':target,'ref':self.jobs.ref('release-ref',relative),**snapshot}

    def _health(self,cfg):
        parsed=cfg['health'];connection=http.client.HTTPConnection(parsed.hostname,parsed.port,timeout=5)
        try:
            connection.request('GET',parsed.path or '/',headers={'Connection':'close'})
            response=connection.getresponse();body=response.read(65537)
            checksum=hashlib.sha256(body).hexdigest()
            healthy=len(body)<=65536 and response.status==cfg['expect']['status'] and hmac.compare_digest(checksum,cfg['expect']['body_sha256'])
            return {'health_verified':healthy,'health_status':response.status,'health_body_sha256':checksum}
        except (OSError,http.client.HTTPException):return {'health_verified':False,'health_status':0,'health_body_sha256':ZERO}
        finally:connection.close()

    def preview(self,actor,project,alias,artifact,expected_artifact_sha256=None):
        cfg=self._alias(actor,project,alias);source=artifact_path(cfg['artifacts'],artifact);desired=tree(source);current=self._current(cfg)
        if expected_artifact_sha256 is not None:
            require(isinstance(expected_artifact_sha256,str) and re.fullmatch('[a-f0-9]{64}',expected_artifact_sha256),'deployment_artifact_invalid')
            require(hmac.compare_digest(desired['sha256'],expected_artifact_sha256),'deployment_artifact_changed')
        plan={'operation':'deployment.apply','config_sha256':cfg['fingerprint'],'artifact_ref':self.jobs.ref('artifact-ref',artifact),'artifact_sha256':desired['sha256'],'previous_release_sha256':current['sha256'],'previous_ref_sha256':current['ref'],'files':desired['files'],'bytes':desired['bytes']}
        return {**plan,'plan_sha256':digest(plan),'requires_review':True,'mutation_authorized':False}

    def _result(self,job):
        return {'ok':job['state']=='succeeded','status':job['state'],'job':job,'rollback_verified':job['evidence'].get('rollback_verified',False),'retry_safe':False}

    def _switch(self,cfg,target,expected):
        current=self._current(cfg)
        require(current['ref']==expected['ref'] and current['sha256']==expected['sha256'],'deployment_pointer_changed')
        require(target.parent==cfg['releases'] and target.is_dir() and not linklike(target),'deployment_pointer_unsafe')
        temporary=cfg['release']/('.operations-current-'+uuid4().hex)
        try:
            temporary.symlink_to('releases/'+target.name,target_is_directory=True)
            os.replace(temporary,cfg['release']/'current')
            if os.name=='posix':
                fd=os.open(cfg['release'],os.O_RDONLY)
                try:os.fsync(fd)
                finally:os.close(fd)
        finally:
            if temporary.is_symlink():temporary.unlink()

    def _existing_release(self,cfg,ref,sha):
        matches=[]
        for target in cfg['releases'].iterdir():
            if not target.is_dir() or linklike(target) or not re.fullmatch('[A-Za-z0-9_][A-Za-z0-9._-]{0,127}',target.name):continue
            if hmac.compare_digest(self.jobs.ref('release-ref','releases/'+target.name),ref):
                require(tree(target)['sha256']==sha,'deployment_previous_changed');matches.append(target)
        require(len(matches)==1,'deployment_previous_missing');return matches[0]

    def _journal(self,actor,project,job,state,values,owner):
        return self.jobs.transition(actor,project,job['job_ref'],job['version'],state,values,lease_owner=owner,lease_seconds=300)

    def _finish_switch(self,actor,project,cfg,job,target,previous,owner):
        job=self._journal(actor,project,job,'running',{'phase':'switch_started'},owner)
        self._switch(cfg,target,previous)
        job=self._journal(actor,project,job,'running',{'phase':'switched','effect_observed':True},owner)
        health=self._health(cfg);current=self._current(cfg)
        expected=job['evidence']
        if health['health_verified'] and current['sha256']==expected['artifact_sha256'] and current['ref']==expected['release_ref_sha256']:
            return self._result(self._journal(actor,project,job,'succeeded',{'phase':'rolled_back' if job['kind']=='deployment.rollback' else 'verified','rollback_verified':job['kind']=='deployment.rollback',**health},owner))
        # Preserve the backup identity before the rollback effect. A later resume
        # can inspect exactly this operation; it never starts a new deployment.
        job=self._journal(actor,project,job,'running',{'phase':'rollback_started','rollback_attempted':True,'error_type':'health_check_failed',**health},owner)
        backup=self._existing_release(cfg,previous['ref'],previous['sha256'])
        require(current['sha256']==expected['artifact_sha256'] and current['ref']==expected['release_ref_sha256'],'deployment_pointer_changed')
        self._switch(cfg,backup,current)
        rollback_health=self._health(cfg);restored=self._current(cfg)
        if rollback_health['health_verified'] and restored['ref']==previous['ref'] and restored['sha256']==previous['sha256']:
            return self._result(self._journal(actor,project,job,'rolled_back',{'phase':'rolled_back','rollback_verified':True,**rollback_health},owner))
        return self._result(self._journal(actor,project,job,'unknown',{'phase':'unknown','error_type':'rollback_unverified',**rollback_health},owner))

    def apply(self,actor,project,alias,artifact,expected_plan_sha256,ack,idempotency_key):
        require(ack is True,'deployment_review_required')
        require(isinstance(expected_plan_sha256,str) and re.fullmatch('[a-f0-9]{64}',expected_plan_sha256),'deployment_plan_invalid')
        cfg=self._alias(actor,project,alias)
        request=digest(['deployment.apply',cfg['fingerprint'],self.jobs.ref('artifact-ref',artifact),expected_plan_sha256])
        job=self.jobs.prepare(actor,project,'deployment.apply',idempotency_key,request,evidence={'phase':'prepared','plan_sha256':expected_plan_sha256,'config_sha256':cfg['fingerprint'],'deployment_ref_sha256':cfg['resource']})
        if job['state']!='prepared':return self._result(job)
        owner=uuid4().hex
        try:
            with alias_lock(cfg['release'],job['job_ref']):
                require(not self.jobs._deployment_blocked(cfg['resource'],job['job_ref']),'deployment_reconciliation_required')
                current_job=self.jobs.resume(actor,project,job['job_ref'])
                if current_job['state']!='prepared':return self._result(current_job)
                job=self._journal(actor,project,current_job,'running',{'phase':'prepared'},owner)
                plan=self.preview(actor,project,alias,artifact)
                require(hmac.compare_digest(plan['plan_sha256'],expected_plan_sha256),'deployment_plan_changed')
                previous=self._current(cfg);source=artifact_path(cfg['artifacts'],artifact)
                target=cfg['releases']/('r_'+job['job_ref'][4:36]);stage=cfg['releases']/('.stage_'+job['job_ref'][4:36])
                require(not target.exists() and not linklike(target) and not stage.exists() and not linklike(stage),'deployment_release_exists')
                details={'artifact_sha256':plan['artifact_sha256'],'previous_release_sha256':previous['sha256'],'previous_ref_sha256':previous['ref'],'release_ref_sha256':self.jobs.ref('release-ref','releases/'+target.name),'files':plan['files'],'bytes':plan['bytes'],'phase':'copy_started'}
                job=self._journal(actor,project,job,'running',details,owner)
                copied=tree(source,copy_to=stage)
                require(copied['sha256']==plan['artifact_sha256'] and tree(stage)['sha256']==plan['artifact_sha256'],'deployment_artifact_changed')
                os.rename(stage,target);sync_directory(cfg['releases'])
                job=self._journal(actor,project,job,'running',{'phase':'staged'},owner)
                return self._finish_switch(actor,project,cfg,job,target,previous,owner)
        except Exception as error:
            latest=self.jobs.resume(actor,project,job['job_ref'])
            if latest['state']=='running':
                # No pointer intent exists before switch_started. Staging failures
                # are terminal for this request and still never automatically replay.
                uncertain=latest['evidence'].get('phase') not in ('prepared','copy_started','staged')
                latest=self._journal(actor,project,latest,'unknown' if uncertain else 'failed',{'phase':'unknown' if uncertain else 'failed','last_phase':latest['evidence'].get('phase','prepared'),'error_type':'deployment_interrupted'},owner)
            result=self._result(latest)
            if isinstance(error,(DeploymentError,JobError)):result['error']={'code':error.code,'retry_safe':False}
            return result

    def resume(self,actor,project,alias,job_ref):
        cfg=self._alias(actor,project,alias);job=self.jobs.resume(actor,project,job_ref)
        require(job['kind'] in ('deployment.apply','deployment.rollback') and job['evidence'].get('config_sha256')==cfg['fingerprint'],'deployment_job_mismatch')
        return self._result(job)

    def reconcile(self,actor,project,alias,job_ref,expected_version):
        cfg=self._alias(actor,project,alias);job=self.jobs.resume(actor,project,job_ref)
        require(job['kind'] in ('deployment.apply','deployment.rollback') and job['evidence'].get('config_sha256')==cfg['fingerprint'],'deployment_job_mismatch')
        require(job['state']=='unknown' and job['version']==expected_version,'deployment_reconciliation_conflict')
        try:
            with alias_lock(cfg['release']):
                health=self._health(cfg);current=self._current(cfg);saved=job['evidence']
                if saved.get('last_phase')=='prepared' and not saved.get('release_ref_sha256'):
                    state='failed';facts={'effect_observed':True,'error_type':'effect_unchanged',**health}
                elif current['ref']==saved.get('release_ref_sha256') and current['sha256']==saved.get('artifact_sha256') and health['health_verified']:
                    state='succeeded';facts={'effect_observed':True,**health}
                elif current['ref']==saved.get('previous_ref_sha256') and current['sha256']==saved.get('previous_release_sha256') and health['health_verified']:
                    state='rolled_back' if saved.get('rollback_attempted') is True else 'failed'
                    facts={'effect_observed':True,'rollback_verified':state=='rolled_back',**health}
                else:return self._result(job)
                return self._result(self.jobs._reconcile_deployment(actor,project,job_ref,expected_version,state,facts))
        except DeploymentError as error:
            if error.code=='deployment_busy':return {**self._result(job),'error':{'code':'deployment_worker_not_quiescent','retry_safe':False}}
            raise

    def rollback_preview(self,actor,project,alias,job_ref):
        cfg=self._alias(actor,project,alias);original=self.jobs.resume(actor,project,job_ref);saved=original['evidence']
        require(original['kind']=='deployment.apply' and original['state']=='succeeded' and saved.get('config_sha256')==cfg['fingerprint'],'deployment_rollback_unavailable')
        current=self._current(cfg)
        require(current['ref']==saved.get('release_ref_sha256') and current['sha256']==saved.get('artifact_sha256'),'deployment_pointer_changed')
        self._existing_release(cfg,saved['previous_ref_sha256'],saved['previous_release_sha256'])
        plan={'operation':'deployment.rollback','config_sha256':cfg['fingerprint'],'source_job_ref':self.jobs.ref('rollback-job',job_ref),'previous_ref_sha256':current['ref'],'previous_release_sha256':current['sha256'],'release_ref_sha256':saved['previous_ref_sha256'],'artifact_sha256':saved['previous_release_sha256']}
        return {**plan,'plan_sha256':digest(plan),'requires_review':True,'mutation_authorized':False}

    def rollback(self,actor,project,alias,job_ref,expected_plan_sha256,ack,idempotency_key):
        require(ack is True,'deployment_review_required');cfg=self._alias(actor,project,alias)
        require(isinstance(expected_plan_sha256,str) and re.fullmatch('[a-f0-9]{64}',expected_plan_sha256),'deployment_plan_invalid')
        request=digest(['deployment.rollback',cfg['fingerprint'],self.jobs.ref('rollback-job',job_ref),expected_plan_sha256])
        job=self.jobs.prepare(actor,project,'deployment.rollback',idempotency_key,request,evidence={'phase':'prepared','plan_sha256':expected_plan_sha256,'config_sha256':cfg['fingerprint'],'deployment_ref_sha256':cfg['resource']})
        if job['state']!='prepared':return self._result(job)
        owner=uuid4().hex
        try:
            with alias_lock(cfg['release'],job['job_ref']):
                require(not self.jobs._deployment_blocked(cfg['resource'],job['job_ref']),'deployment_reconciliation_required')
                current_job=self.jobs.resume(actor,project,job['job_ref'])
                if current_job['state']!='prepared':return self._result(current_job)
                job=self._journal(actor,project,current_job,'running',{'phase':'prepared'},owner)
                plan=self.rollback_preview(actor,project,alias,job_ref)
                require(hmac.compare_digest(plan['plan_sha256'],expected_plan_sha256),'deployment_plan_changed')
                previous=self._current(cfg);target=self._existing_release(cfg,plan['release_ref_sha256'],plan['artifact_sha256'])
                job=self._journal(actor,project,job,'running',{'phase':'staged','artifact_sha256':plan['artifact_sha256'],'previous_ref_sha256':previous['ref'],'previous_release_sha256':previous['sha256'],'release_ref_sha256':plan['release_ref_sha256']},owner)
                return self._finish_switch(actor,project,cfg,job,target,previous,owner)
        except Exception as error:
            latest=self.jobs.resume(actor,project,job['job_ref'])
            if latest['state']=='running':
                uncertain=latest['evidence'].get('phase') not in ('prepared','staged')
                latest=self._journal(actor,project,latest,'unknown' if uncertain else 'failed',{'phase':'unknown' if uncertain else 'failed','last_phase':latest['evidence'].get('phase','prepared'),'error_type':'deployment_interrupted'},owner)
            result=self._result(latest)
            if isinstance(error,(DeploymentError,JobError)):result['error']={'code':error.code,'retry_safe':False}
            return result
