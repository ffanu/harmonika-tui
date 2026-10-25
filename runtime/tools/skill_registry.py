"""Versioned, inert registry for local document tools.

Descriptors are data, not executable plugins. Capability/readiness and access checks
belong to the worker/executor; registration alone never implies availability.
"""
from copy import deepcopy
import json
import math
from pathlib import Path

SCHEMA_VERSION = 1
MAX_REQUEST_BYTES = 1024 * 1024
MAX_DEPTH = 32
MAX_NODES = 100000
MAX_INTEGER_BITS = 4096


class SkillError(ValueError):
    """Stable machine code and customer-safe explanation, without input values."""
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code
        self.message = message


def _invalid(message):
    raise SkillError('invalid_input', message)


def _validate_json(value):
    """Bound JSON work before serialization; reject cyclic/exotic Python objects."""
    pending = [(value, 0)]
    nodes, minimum_bytes = 0, 0
    while pending:
        item, depth = pending.pop()
        nodes += 1
        if depth > MAX_DEPTH or nodes > MAX_NODES:
            _invalid('Input exceeds the permitted JSON complexity')
        kind = type(item)
        if item is None or kind is bool:
            minimum_bytes += 4
        elif kind is str:
            if len(item) > MAX_REQUEST_BYTES:
                raise SkillError('input_too_large', 'Input exceeds the 1 MiB request limit')
            try:
                minimum_bytes += len(item.encode('utf-8'))
            except UnicodeEncodeError:
                _invalid('Input strings must contain valid Unicode')
        elif kind is int:
            if item.bit_length() > MAX_INTEGER_BITS:
                _invalid('Input number exceeds the permitted precision')
            minimum_bytes += 1
        elif kind is float:
            if not math.isfinite(item):
                _invalid('Input numbers must be finite')
            minimum_bytes += 1
        elif kind is list:
            minimum_bytes += len(item)
            if len(item) + nodes > MAX_NODES:
                _invalid('Input exceeds the permitted JSON complexity')
            pending.extend((child, depth + 1) for child in item)
        elif kind is dict:
            minimum_bytes += len(item)
            if len(item) + nodes > MAX_NODES:
                _invalid('Input exceeds the permitted JSON complexity')
            for key, child in item.items():
                if type(key) is not str:
                    _invalid('JSON object keys must be strings')
                if len(key) > MAX_REQUEST_BYTES:
                    raise SkillError('input_too_large', 'Input exceeds the 1 MiB request limit')
                try:
                    minimum_bytes += len(key.encode('utf-8'))
                except UnicodeEncodeError:
                    _invalid('Input strings must contain valid Unicode')
                pending.append((child, depth + 1))
        else:
            _invalid('Input must contain only JSON values')
        if minimum_bytes > MAX_REQUEST_BYTES:
            raise SkillError('input_too_large', 'Input exceeds the 1 MiB request limit')
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False,
                             separators=(',', ':')).encode('utf-8')
    except (ValueError, TypeError, OverflowError, RecursionError, UnicodeEncodeError):
        _invalid('Input must be valid bounded JSON')
    if len(encoded) > MAX_REQUEST_BYTES:
        raise SkillError('input_too_large', 'Input exceeds the 1 MiB request limit')


def _validate_schema(value, schema, location='$'):
    """Validate the deliberately small schema vocabulary used by this registry."""
    expected = schema['type']
    if expected == 'object':
        if type(value) is not dict:
            _invalid(location + ' must be an object')
        properties = schema.get('properties', {})
        if any(name not in value for name in schema.get('required', [])):
            _invalid(location + ' is missing required fields')
        if schema.get('additionalProperties') is False and set(value) - set(properties):
            _invalid(location + ' contains unsupported fields')
        for name, child in value.items():
            if name in properties:
                _validate_schema(child, properties[name], location + '.' + name)
    elif expected == 'array':
        if type(value) is not list or len(value)>schema.get('maxItems',1000):_invalid(location+' must be a bounded array')
        if len(value)<schema.get('minItems',0):_invalid(location+' contains too few items')
        for item in value:_validate_schema(item,schema.get('items',{'type':'string'}),location+'[]')
    elif expected == 'integer':
        if type(value) is not int or not schema.get('minimum',0)<=value<=schema.get('maximum',100000):_invalid(location+' must be a bounded integer')
    elif expected == 'string':
        if type(value) is not str:
            _invalid(location + ' must be a string')
        if not schema.get('minLength', 0) <= len(value) <= schema.get('maxLength', MAX_REQUEST_BYTES):
            _invalid(location + ' has an invalid length')
        if 'enum' in schema and value not in schema['enum']:_invalid(location+' has an unsupported value')
        if schema.get('format') == 'absolute-path':
            if '\x00' in value or not Path(value).is_absolute():
                _invalid(location + ' must be an absolute local path')
    else:
        raise SkillError('invalid_registry', 'Registry schema uses an unsupported type')


_PATH = {'type': 'string', 'minLength': 1, 'maxLength': 4096,
         'format': 'absolute-path'}
_FORMATS = ('pdf', 'docx', 'xlsx', 'pptx')
_ROUTES = {}
_DESCRIPTORS = {}


def _register(skill_id, action, extension, properties, required, filesystem, description):
    _ROUTES[skill_id] = (action, extension)
    _DESCRIPTORS[skill_id] = {
        'schema_version': SCHEMA_VERSION,
        'id': skill_id,
        'version': '1.0.0',
        'description': description,
        'input_schema': {'type': 'object', 'properties': deepcopy(properties),
                         'required': required, 'additionalProperties': False},
        'output_schema': {'type': 'object', 'required': ['result'],
                          'properties': {'result': {'type': 'object'}}},
        'permissions': {'filesystem': filesystem, 'network': False, 'shell': False},
        'execution_target': 'local_worker',
        'timeout_seconds': 30,
        'limits': {'max_request_bytes': MAX_REQUEST_BYTES,
                   'max_input_bytes': MAX_REQUEST_BYTES, 'max_output_bytes': MAX_REQUEST_BYTES,
                   'max_json_depth': MAX_DEPTH, 'max_json_nodes': MAX_NODES,
                   'max_integer_bits': MAX_INTEGER_BITS},
        'reference_fields': ['changes.append_files[]'] if action == 'edit' else [],
        'cancellable': True,
        'idempotency': 'required_for_write',
        'access': ['tui', 'internal'],
    }


for _format in _FORMATS:
    _extension = '.' + _format
    _register('doc.read_' + _format, 'inspect', _extension,
              {'path': _PATH}, ['path'], ['read'],
              'Read bounded ' + _format.upper() + ' content using the local document worker')
    _register('doc.create_' + _format, 'create', _extension,
              {'output_path': _PATH, 'content': {'type': 'object'}},
              ['output_path', 'content'], ['write'],
              'Create a new basic ' + _format.upper() + ' document without overwriting files')
_register('doc.edit_pdf', 'edit', '.pdf',
          {'path': _PATH, 'output_path': _PATH, 'changes': {'type': 'object'}},
          ['path', 'output_path', 'changes'], ['read', 'write'],
          'Create a PDF with selected/merged/rotated pages or watermark; source stays unchanged')


_URL={'type':'string','minLength':8,'maxLength':2048}
_STRING={'type':'string','minLength':1,'maxLength':256}
for _id in ('page_audit','technical','content','schema','sitemap','local','geo','backlinks','performance','visual','drift','keyword_cluster','search_console','ga4'):
    props={'url':_URL};required=['url'];writes=False
    if _id=='performance':props['strategy']={'type':'string','enum':['mobile','desktop']}
    if _id=='visual':
        props['output_dir']=_PATH;required.append('output_dir');writes=True
    if _id=='drift':
        props.update({'action':{'type':'string','enum':['capture','compare']},'baseline_path':_PATH})
        required+=['action','baseline_path'];writes=True
    if _id=='backlinks':props['links']={'type':'array','maxItems':10,'items':_URL}
    if _id=='keyword_cluster':
        props={'keywords':{'type':'array','minItems':1,'maxItems':50,'items':dict(_STRING,maxLength=200)},'serp_results':{'type':'object'}};required=['keywords']
    if _id in ('search_console','ga4'):
        props={'property_id':_STRING,'start_date':{'type':'string','minLength':10,'maxLength':10},'end_date':{'type':'string','minLength':10,'maxLength':10},'limit':{'type':'integer','minimum':1,'maximum':500}}
        required=['property_id','start_date','end_date']
    _skill='seo.'+_id
    _register(_skill,'analyze','',props,required,['read','write'] if writes else ['read'],'Evidence-based '+_id.replace('_',' ')+'; availability depends on the local adapter and authorized data')
    _d=_DESCRIPTORS[_skill]
    _d['worker']='seo';_d['permissions']['network']=True
    _d['timeout_seconds']=75 if _id in ('performance','visual') else 30
    _d['path_fields']=['output_dir'] if _id=='visual' else (['baseline_path'] if _id=='drift' else [])
    _d['output_field']='output_dir' if _id=='visual' else ('baseline_path' if _id=='drift' else None)
    if _id=='drift':_d['write_condition']={'field':'action','equals':'capture'}


_ENGINEERING_FILES = {'type':'array','maxItems':8,'items':{'type':'object','properties':{
    'path':{'type':'string','minLength':1,'maxLength':240},
    'content':{'type':'string','maxLength':32000},
    'language':{'type':'string','maxLength':32}},'required':['path','content'],'additionalProperties':False}}
_ENGINEERING_INPUT = {'request':{'type':'string','minLength':1,'maxLength':8000},
    'context':{'type':'string','maxLength':12000},'files':_ENGINEERING_FILES,
    'diff':{'type':'string','maxLength':64000},'logs':{'type':'string','maxLength':32000}}
for _id,_description in {
    'code_review':'Review supplied code or diff with evidence-backed heuristic findings and explicit limitations; no repository access or execution',
    'debug':'Diagnose supplied errors/logs and source; distinguish observations from hypotheses, ask for missing reproduction evidence',
    'testing_strategy':'Build a risk-based test plan from supplied code or requirements; never claim tests ran or coverage measured',
    'documentation':'Prepare source-grounded documentation facts and outline from supplied code or requirements; never invent endpoints or test results',
}.items():
    _skill='engineering.'+_id
    _register(_skill,'analyze','',_ENGINEERING_INPUT,['request'],[],_description)
    _d=_DESCRIPTORS[_skill];_d['worker']='engineering';_d['path_fields']=[]
    _d['limits']['max_input_bytes']=131072


# Remote coding capabilities are deliberately not executable by the local
# document worker. Readiness requires an authenticated coding workspace.
from coding_catalog import catalog as coding_catalog
for _item in coding_catalog():
    _skill=_item['id'];_ROUTES[_skill]=('coding','')
    _DESCRIPTORS[_skill]={
        **_item,'schema_version':1,'version':'1.0.0','worker':'coding',
        'description':_skill+' in an authenticated isolated remote workspace',
        'permissions':{'filesystem':['read','write'] if _item['write_permission'] else ['read'],
                       'network':False,'shell':_skill.startswith('exec.')},
        'timeout_seconds':150,'access':['tui','api'],'cancellable':True,
        'output_schema':{'type':'object'},'limits':{'max_request_bytes':1048576}}


# Tenant-scoped telecom definitions. Runtime readiness is fetched from the gateway;
# inert descriptors never give the local worker company/device permissions.
for _item in json.loads(Path(__file__).with_name('telco_catalog.json').read_text())['skills']:
    _skill=_item['id'];_ROUTES[_skill]=('telco','')
    _DESCRIPTORS[_skill]={**_item,'schema_version':1,'version':'1.0.0','worker':'telco',
        'permissions':{'filesystem':[],'network':True,'shell':False},
        'timeout_seconds':100,'access':['tui','api'],'cancellable':True,
        'output_schema':{'type':'object'},'limits':{'max_request_bytes':262144}}


for _item in json.loads(Path(__file__).with_name('legal_catalog.json').read_text())['skills']:
    _skill=_item['id'];_ROUTES[_skill]=('legal','')
    _DESCRIPTORS[_skill]={**_item,'schema_version':1,'version':'1.0.0','worker':'legal',
        'permissions':{'filesystem':[],'network':True,'shell':False},
        'timeout_seconds':100,'access':['tui','api'],'cancellable':False,
        'output_schema':{'type':'object'},'limits':{'max_request_bytes':262144}}


for _item in json.loads(Path(__file__).with_name('builder_catalog.json').read_text())['skills']:
    _skill=_item['id']
    if _skill in _DESCRIPTORS:raise ValueError('duplicate_builder_skill')
    _ROUTES[_skill]=('builder','')
    _DESCRIPTORS[_skill]={**_item,'schema_version':1,'version':'1.0.0','worker':'builder',
        'permissions':{'filesystem':[],'network':True,'shell':False},
        'timeout_seconds':150,'access':['tui','api'],'cancellable':True,
        'output_schema':{'type':'object'},'limits':{'max_request_bytes':1048576}}


class Registry:
    """Fixed registered operations, isolated from caller mutation and filesystem."""
    def get(self, skill_id):
        if type(skill_id) is not str or skill_id not in _DESCRIPTORS:
            raise SkillError('unknown_skill', 'The requested skill is not registered')
        return deepcopy(_DESCRIPTORS[skill_id])

    def list(self):
        return [deepcopy(_DESCRIPTORS[key]) for key in sorted(_DESCRIPTORS)]

    def validate_input(self, skill_id, payload):
        descriptor = self.get(skill_id)
        _validate_json(payload)
        if descriptor.get('worker') in ('telco','legal','builder'):
            if type(payload) is not dict:_invalid('Telco input must be an object')
            return  # Authenticated server owns domain-schema validation.
        _validate_schema(payload, descriptor['input_schema'])
        if descriptor.get('worker')=='coding':return
        action, extension = self.route(skill_id)
        path_fields = ('path',) if action == 'inspect' else ('output_path',)
        if action == 'edit':
            path_fields = ('path', 'output_path')
        if skill_id.startswith('engineering.'):
            from engineering_evidence import prepare
            try:prepare(payload)
            except (ValueError,TypeError):_invalid('Invalid bounded engineering evidence input')
            return
        if skill_id.startswith('seo.'):
            for name in descriptor.get('path_fields',[]):_validate_schema(payload[name],_PATH)
            if skill_id=='seo.drift' and Path(payload['baseline_path']).suffix.lower()!='.json':_invalid('baseline_path must use .json')
            return
        for name in path_fields:
            if Path(payload[name]).suffix.lower() != extension:
                _invalid(name + ' must match the registered document format')

    def route(self, skill_id):
        self.get(skill_id)
        return _ROUTES[skill_id]
