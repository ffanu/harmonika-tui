"""Automatic bounded tool exposure: <=32 definitions, explicit runtime allowlist."""
import re
from .catalog import BASE_SKILLS,PHASE2_SKILLS,tools
MAX_TOOLS=32
META_NAME='harmonika_tools'
BASE_BUDGET=13  # 18 core + 13 optional + one catalog selector

def select_initial(messages,available):
    available=set(available);base=BASE_SKILLS&available
    if not available&PHASE2_SKILLS:return base
    text='\n'.join(m.get('content','') for m in messages[-6:] if type(m) is dict and type(m.get('content')) is str).lower()
    groups=[
      (r'scaffold|starter|proyek|project|komponen|component|endpoint|route|react',('scaffold.project','scaffold.component','scaffold.api','code.diff','code.patch')),
      (r'format|rapikan|lint|type.?check|tipe|mypy|typescript|build|compile|kompil',('code.format','lint.run','type.check','build.run','format.run')),
      (r'test|uji|coverage|cakupan',('test.single','test.coverage')),
      (r'refactor|rename|ubah.?nama|ganti.?nama|extract|ekstrak|dead.?code|unused|tidak.?dipakai',('code.rename','refactor.rename','refactor.extract','refactor.dead_code','code.diff')),
      (r'debug|breakpoint|dap|jeda.?baris',('debug.attach','debug.breakpoint')),
      (r'paket|package|dependen|install|pip|npm|offline',('pkg.list','pkg.install')),
    ]
    preferred=[]
    for pattern,ids in groups:
        if re.search(pattern,text):preferred.extend(ids)
    preferred+=['scaffold.project','scaffold.component','scaffold.api','code.diff','code.patch','code.format','lint.run','type.check','build.run','test.single','test.coverage','pkg.list','refactor.rename']
    selected=[]
    for name in preferred:
        if name in available and name not in base and name not in selected:selected.append(name)
        if len(selected)==BASE_BUDGET:break
    return base|set(selected)

def definitions(active,available):
    result=tools(set(active)&set(available))
    if set(available)&PHASE2_SKILLS:
        result.append({'type':'function','function':{'name':META_NAME,
          'description':'Request optional Harmonika engineering tools by IDs from the available catalog. Keeps existing executed tools declared; no execution or approval occurs.',
          'parameters':{'type':'object','properties':{'skills':{'type':'array','maxItems':BASE_BUDGET,'items':{'type':'string','enum':sorted(set(available)&PHASE2_SKILLS)}}},'required':['skills'],'additionalProperties':False}}})
    if len(result)>MAX_TOOLS:raise ValueError('tool_budget_exceeded')
    return result

def discover(payload,used,available):
    if type(payload) is not dict or set(payload)!={'skills'} or type(payload['skills']) is not list or len(payload['skills'])>BASE_BUDGET:raise ValueError('invalid_tool_selection')
    ids=payload['skills']
    if any(type(x) is not str or x not in set(available)&PHASE2_SKILLS for x in ids):raise ValueError('unavailable_tool')
    optional=(set(ids)|set(used))&PHASE2_SKILLS
    if len(optional)>BASE_BUDGET:raise ValueError('tool_budget_exceeded_keep_used_tools')
    return (BASE_SKILLS&set(available))|optional
