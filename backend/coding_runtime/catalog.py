"""Versioned workspace skill declarations; no dynamic executable loading."""
BASE_FILE_SKILLS = frozenset(('fs.read','fs.write','fs.list','fs.tree','fs.glob','code.read','code.write','code.edit','code.search','code.grep'))
BASE_EXEC_SKILLS = frozenset(('exec.shell','exec.python','exec.node','exec.background','test.run','git.status','git.diff','git.commit'))
FOUNDATION_SKILLS = frozenset(('scaffold.project','scaffold.component','scaffold.api','code.diff','code.patch'))
TOOL_SKILLS = frozenset(('code.format','format.run','lint.run','type.check','build.run','pkg.list','pkg.install','test.single','test.coverage','code.rename','refactor.rename','refactor.extract','refactor.dead_code'))
DEBUG_SKILLS = frozenset(('debug.attach','debug.breakpoint'))
PHASE2_SKILLS = FOUNDATION_SKILLS | TOOL_SKILLS | DEBUG_SKILLS
BASE_SKILLS = BASE_FILE_SKILLS | BASE_EXEC_SKILLS
FILE_SKILLS = BASE_FILE_SKILLS | FOUNDATION_SKILLS
EXEC_SKILLS = BASE_EXEC_SKILLS | TOOL_SKILLS | DEBUG_SKILLS
SKILLS = FILE_SKILLS | EXEC_SKILLS
WRITES = frozenset(('fs.write','code.write','code.edit','git.commit','scaffold.project','scaffold.component','scaffold.api','code.patch','code.format','format.run','build.run','pkg.install','code.rename','refactor.rename','refactor.extract','refactor.dead_code'))
APPROVAL = frozenset(('exec.shell','exec.python','exec.node','exec.background','test.run','git.commit','code.format','format.run','build.run','pkg.install','test.single','test.coverage','code.rename','refactor.rename','refactor.extract','refactor.dead_code','debug.attach'))

def catalog():
    fields={
        'fs.read':{'path':'string','start_line':'integer?','end_line':'integer?'},
        'code.read':{'path':'string','start_line':'integer?','end_line':'integer?'},
        'fs.write':{'path':'string','content':'string','overwrite':'boolean?','expected_sha256':'string?'},
        'code.write':{'path':'string','content':'string','overwrite':'boolean?','expected_sha256':'string?'},
        'code.edit':{'path':'string','old':'string','new':'string','expected_sha256':'string?'},
        'fs.list':{'path':'string?'},'fs.tree':{'path':'string?','depth':'integer?'},
        'fs.glob':{'pattern':'string'},'code.search':{'query':'string','path':'string?'},
        'code.grep':{'pattern':'string','path':'string?','literal':'boolean?'},
        'exec.shell':{'command':'string','timeout_seconds':'integer?'},
        'exec.background':{'command':'string','timeout_seconds':'integer?'},
        'exec.python':{'code':'string?','path':'string?','args':'array?','timeout_seconds':'integer?'},
        'exec.node':{'code':'string?','path':'string?','args':'array?','timeout_seconds':'integer?'},
        'test.run':{'runner':'python_unittest|node_test|npm_test','path':'string?','filter':'string?','timeout_seconds':'integer?'},
        'git.status':{},'git.diff':{'staged':'boolean?'},
        'git.commit':{'message':'string','paths':'array?','author_name':'string?','author_email':'string?'},
        'scaffold.project':{'path':'string','template':'python_http|node_http','name':'string'},
        'scaffold.component':{'path':'string','name':'string'},
        'scaffold.api':{'path':'string','route':'string','method':'GET|POST'},
        'code.diff':{'path':'string','new_content':'string'},
        'code.patch':{'path':'string','patch':'string','expected_sha256':'string'},
        'code.format':{'path':'string','language':'python|javascript|typescript|json?','expected_sha256':'string?'},
        'format.run':{'path':'string','language':'python|javascript|typescript|json?','expected_sha256':'string?'},
        'lint.run':{'path':'string','language':'python|javascript|typescript?'},
        'type.check':{'path':'string','language':'python|javascript|typescript?'},
        'build.run':{'path':'string','language':'python|javascript|typescript?'},
        'pkg.list':{'manager':'python|node'},
        'pkg.install':{'manager':'python|node','name':'string','version':'string'},
        'test.single':{'runner':'python_unittest|node_test','path':'string','name':'string'},
        'test.coverage':{'runner':'python_unittest|node_test','path':'string?'},
        'code.rename':{'path':'string','line':'integer','column':'integer','new_name':'string','expected_sha256':'string?'},
        'refactor.rename':{'path':'string','line':'integer','column':'integer','new_name':'string','expected_sha256':'string?'},
        'refactor.extract':{'path':'string','start_line':'integer','end_line':'integer','name':'string','expected_sha256':'string?'},
        'refactor.dead_code':{'path':'string','apply':'boolean?','expected_sha256':'string?'},
        'debug.attach':{'action':'start|status|continue|next|inspect|stop?','path':'string?','session_id':'string?','timeout_seconds':'integer?'},
        'debug.breakpoint':{'session_id':'string','path':'string','lines':'line_array'},

    }
    result=[]
    for name in sorted(SKILLS):
        props={};required=[]
        for key,kind in fields[name].items():
            optional=kind.endswith('?');kind=kind.rstrip('?')
            if not optional:required.append(key)
            if '|' in kind:schema={'type':'string','enum':kind.split('|')}
            elif kind=='line_array':schema={'type':'array','minItems':1,'maxItems':32,'items':{'type':'integer','minimum':1,'maximum':10000}}
            elif kind=='array':schema={'type':'array','maxItems':32,'items':{'type':'string','maxLength':1024}}
            elif kind=='integer':schema={'type':'integer','minimum':0 if key=='column' else 10 if name=='debug.attach' and key=='timeout_seconds' else 1,'maximum':300 if name=='debug.attach' and key=='timeout_seconds' else 120 if key=='timeout_seconds' else 10000}
            elif kind=='boolean':schema={'type':'boolean'}
            else:schema={'type':'string','maxLength':524288 if key in ('content','old','new','new_content','patch') else 16384 if key=='command' else 65536 if key=='code' else 240 if key=='path' else 1024}
            if key in ('line','start_line','end_line'):schema['description']='1-based source line'
            if key=='column':schema['description']='0-based character column in the source line'
            if key=='expected_sha256':schema['description']='SHA256 returned by code.read for this exact source version'
            props[key]=schema
        result.append({'id':name,'input_schema':{'type':'object','properties':props,'required':required,'additionalProperties':False},'execution_target':'coding_workspace','requires_approval':name in APPROVAL,'write_permission':name in WRITES,'network':False,'phase':2 if name in PHASE2_SKILLS else 1,
            'supported_languages':['python'] if name in ('code.rename','refactor.rename','refactor.extract','refactor.dead_code','debug.attach','debug.breakpoint') else ['python','node'] if name in TOOL_SKILLS else []})
    for entry in result:
        name=entry['id']
        if name in ('code.format','format.run'):entry['supported_languages']=['python','javascript','typescript','json']
        elif name in ('lint.run','type.check'):entry['supported_languages']=['python','javascript','typescript']
        elif name=='build.run':entry['supported_languages']=['python','javascript','typescript','jsx','tsx']
        elif name=='scaffold.project':entry['supported_languages']=['python','javascript'];entry['templates']=['python_http','node_http']
        elif name=='scaffold.component':entry['supported_languages']=['jsx']
        elif name=='scaffold.api':entry['supported_languages']=['python']
        if name=='lint.run':entry['limitations']=['Python ruff; JavaScript syntax check; TypeScript compiler diagnostics, not eslint']
        elif name in ('code.rename','refactor.rename','refactor.extract'):entry['limitations']=['Python semantic refactoring through rope; unsupported or ambiguous targets fail explicitly']
        elif name=='refactor.dead_code':entry['limitations']=['Unused import diagnostics; apply removes only safe unused imports, not arbitrary dead functions']
        elif name=='build.run':entry['limitations']=['Single Python entry-point zipapp or esbuild JS/TS/JSX/TSX bundle; not an arbitrary project build command']
        elif name=='pkg.install':entry['limitations']=['Offline allowlist only; no package registry network or lifecycle scripts']
        elif name=='code.patch':entry['limitations']=['One existing UTF-8 file with exact unified diff context and SHA256; no binary or multi-file patch']
        elif name in DEBUG_SKILLS:entry['limitations']=['Owned sandbox Python launch only; no host PID, no expression evaluate, hard TTL 300s and idle TTL 60s']
    return result

def writes_requested(skill,args):
    return skill in WRITES and not (skill=='refactor.dead_code' and not args.get('apply',False))

def needs_approval(skill,args):
    if skill=='refactor.dead_code' and not args.get('apply',False):return False
    return skill in APPROVAL and not (skill=='debug.attach' and args.get('action','start')!='start')

def tools(selected=None):
    descriptions={
      'fs.read':'Read a bounded workspace file, no host paths.',
      'fs.write':'Create a workspace file; replacement requires original SHA256.',
      'code.edit':'Replace one exact unique substring; read source first.',
      'exec.python':'Request sandboxed Python execution; pauses for client approval.',
      'exec.node':'Request sandboxed Node execution; pauses for client approval.',
      'exec.shell':'Request a network-disabled container command; pauses for approval.',
      'test.run':'Request real test run in sandbox; pauses for approval.',
      'debug.attach':'Start an approved owned Python debugger (action=start) or control an existing session_id with status/continue/next/inspect/stop; no host PID.',
      'debug.breakpoint':'Set verified source line breakpoints in an owned debug session; source stays unchanged.',
      'code.patch':'Apply one existing-file unified patch with a/path and b/path headers, exact context, and expected_sha256 from code.read.',
      'git.commit':'Request a Git commit of workspace only; explicit client approval required.',
      'scaffold.project':'Create a new standard-library Python HTTP or Node HTTP starter, including program, test and README, in an unused relative directory. Use this for starter/project setup; never deploy or overwrite files.',
      'scaffold.component':'Create a new reusable React JSX component at an unused .jsx path; no package install or overwrite.',
      'scaffold.api':'Create a new standalone Python route helper for GET or POST; integration with existing routes is separate.',
      'code.diff':'Preview an exact one-file unified diff against proposed new_content without changing source.',
      'code.format':'Format one Python/JS/TS/JSON source file with pinned tools after client approval; read source/hash first.',
      'format.run':'Alias of code.format for one file; source changes require approval.',
      'lint.run':'Read-only quality gate: Ruff for Python, JS syntax or TypeScript compiler diagnostics; findings fail the gate.',
      'type.check':'Read-only Python mypy or TypeScript compiler check; failure diagnostics are real.',
      'build.run':'Build one Python entry-point zipapp or JS/TS/JSX/TSX esbuild bundle; not an arbitrary project build command. Requires approval.',
      'pkg.list':'List the workspace package manifest and actually available offline package versions for python or node.',
      'pkg.install':'Install only a pinned offline allowlisted package. Read capabilities/list first; no registry access or lifecycle scripts. Requires approval.',
      'test.single':'Run exactly one qualified Python unittest ID or one exact Node test name after approval; zero matches is failure.',
      'test.coverage':'Run actual Python unittest or Node tests and measured coverage after approval; no invented percentages.',
      'code.rename':'Semantic Python symbol rename through rope including imports/references, not regex. Read source then specify 1-based line, 0-based column and hash; requires approval.',
      'refactor.rename':'Alias of semantic Python code.rename; ambiguous or unsupported targets fail explicitly.',
      'refactor.extract':'Extract exact complete Python statements into a method through rope. Read source/hash, specify line range; requires approval and verify behavior.',
      'refactor.dead_code':'Inspect unused-import diagnostics in a Python file. apply=true removes only safe unused imports after approval; does not remove arbitrary dead functions.',

    }
    return [{'type':'function','function':{'name':x['id'].replace('.','_'),'description':descriptions.get(x['id'],x['id']+' scoped to the current isolated workspace only.'),'parameters':x['input_schema']}} for x in catalog() if selected is None or x['id'] in selected]
