"""Capture only each handler's transitive source dependencies, with byte/hash pins."""
import ast,base64,hashlib,importlib.util,json,lzma,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from debug_scanner import registry,common,targets,observations,evidence,reports,static

def modules():
 result=[common,targets,observations,evidence,reports,static]
 for name in ('advanced_static','logic_checks','configuration_checks'):
  if hasattr(registry,name):result.append(getattr(registry,name))
 return result+[registry]

def named_handler(op):
 groups=[(registry.TARGET_OPS,'classify'),(registry.SCOPE_OPS,'scope_check'),(registry.PROBE_OPS,'http_probe'),(registry.STATIC_OPS,'static_scan'),(registry.LOG_OPS,'inspect_logs'),(registry.BROWSER_OPS,'inspect_browser'),(registry.PERF_OPS,'inspect_performance'),(registry.EVIDENCE_OPS,'evidence_operation'),(registry.RCA_OPS,'analyze_causes'),(registry.REPORT_OPS,'report')]
 for group,name in groups:
  if op in group:return name
 for group_name,fn in [('ADVANCED_STATIC_OPS','advanced_static_scan'),('LOGIC_OPS','logic_scan'),('CONFIGURATION_OPS','configuration_scan')]:
  if op in getattr(registry,group_name,set()):return fn
 raise ValueError('bundle_handler_missing:'+op)

def entry_source(op):
 prefix='def invoke_captured(v):\n bounded(v)\n'
 if op=='catalog':return prefix+' fields(v,[])\n return capabilities()\n'
 if op=='scan':return prefix+' return pipeline(v)\n'
 if op in registry.EXTRA_SOURCE_OPS:
  body=" r=static_scan('sast.code.review',v)\n r={**r,'owasp_assessment_complete':False,'dynamic_security_tests_performed':False,'coverage_kind':'partial_CWE_rule_candidates_not_complete_OWASP_assessment'}\n"
 else:body=' r='+named_handler(op)+'('+repr(op)+',v)\n'
 return prefix+body+' return wrap_result('+repr(op)+',r,'+repr(registry.mode(op))+')\n'

def captured_source(op,target_config_path=None):
 imports=[];nodes=[];definitions={}
 # Retain declaration order for tables referencing earlier function objects.
 for module in modules():
  source=Path(module.__file__).read_text()
  for node in ast.parse(source).body:
   if isinstance(node,ast.Import) or isinstance(node,ast.ImportFrom) and node.level==0:
    imports.append(ast.get_source_segment(source,node));continue
   if isinstance(node,(ast.FunctionDef,ast.AsyncFunctionDef,ast.ClassDef)):names=[node.name];segment=ast.get_source_segment(source,node)
   elif isinstance(node,ast.Assign) and all(isinstance(t,ast.Name) for t in node.targets):
    names=[t.id for t in node.targets];segment=ast.get_source_segment(source,node)
    if len(names)==1:
     name=names[0];value=getattr(module,name)
     if module is registry and name=='SOURCE':segment='SOURCE='+repr(registry.SOURCE)
     else:
      try:literal=repr(value);ast.literal_eval(literal)
      except (ValueError,SyntaxError,TypeError):pass
      else:segment=name+'='+literal
     if module is static and name=='_HANDLERS' and op in registry.STATIC_OPS|registry.EXTRA_SOURCE_OPS|{'scan'}:
      fixed=op if op in registry.STATIC_OPS else 'sast.code.review';segment='_HANDLERS={'+repr(fixed)+':'+static._HANDLERS[fixed].__name__+'}'
     if name=='TARGET_CONFIG' and target_config_path is not None:segment='TARGET_CONFIG='+repr(str(target_config_path))
   else:continue
   index=len(nodes);nodes.append(segment)
   for name in names:
    if name in definitions:raise ValueError('duplicate_bundle_symbol:'+name)
    definitions[name]=index
 entry=entry_source(op);pending=[n.id for n in ast.walk(ast.parse(entry)) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load)];selected=set()
 while pending:
  name=pending.pop();index=definitions.get(name)
  if index is None or index in selected:continue
  selected.add(index);pending.extend(n.id for n in ast.walk(ast.parse(nodes[index])) if isinstance(n,ast.Name) and isinstance(n.ctx,ast.Load))
 source='\n'.join(dict.fromkeys(imports))+'\n'+'\n\n'.join(nodes[i] for i in sorted(selected))+'\n'+entry
 compile(source,'<captured-scanner>','exec');return source

def bundle(destination,target_config_path=None):
 dest=Path(destination);dest.mkdir(parents=True,exist_ok=True);ids=sorted(registry.ACTIVE_OPS|{'catalog','scan'});sizes={}
 for op in ids:
  raw=captured_source(op,target_config_path).encode();packed=base64.b64encode(lzma.compress(raw,preset=6)).decode();sizes[op]=len(raw)
  identity={'id':'debug_scan.'+op,'name':'Harmonika diagnostic '+op,'core_capabilities':[registry.mode(op)],'requirements':[],'input_schema':registry.input_schema(op)}
  source='import base64,hashlib,lzma\n_SOURCE='+repr(packed)+'\n_RAW_BYTES='+repr(len(raw))+'\n_RAW_SHA='+repr(hashlib.sha256(raw).hexdigest())+'\ndef GetSkillIdentity():\n return '+repr(identity)+'\ndef GetContextRequirements():\n return {"max_input_tokens":65536,"max_output_tokens":262144,"max_chat_messages":0,"max_tool_calls":0}\nasync def Execute(payload):\n decoder=lzma.LZMADecompressor(memlimit=33554432)\n raw=decoder.decompress(base64.b64decode(_SOURCE,validate=True),max_length=_RAW_BYTES+1)\n if len(raw)!=_RAW_BYTES or not decoder.eof or decoder.unused_data or hashlib.sha256(raw).hexdigest()!=_RAW_SHA:raise ValueError("scanner_source_integrity_failed")\n ns={"__name__":"harmonika_captured_scanner"}\n exec(compile(raw,"<harmonika-captured-scanner>","exec"),ns)\n return ns["invoke_captured"](payload'+('' if op=='catalog' else '.get("data")')+')\n'
  p=dest/identity['id'];p.mkdir(exist_ok=True);(p/'skill.py').write_text(source);(p/'manifest.json').write_text(json.dumps({'module_sha256':hashlib.sha256(source.encode()).hexdigest()}))
 return {'namespace':'debug_scan','plugins':len(ids),'ids':['debug_scan.'+x for x in ids],'source_sha256':registry.SOURCE['source_sha256'],'per_handler_decoded_bytes':sizes,'total_plugin_source_bytes':sum((dest/('debug_scan.'+op)/'skill.py').stat().st_size for op in ids)}
if __name__=='__main__':print(json.dumps(bundle(sys.argv[1] if len(sys.argv)>1 else ROOT/'dist/debug-scanner-plugins')))
