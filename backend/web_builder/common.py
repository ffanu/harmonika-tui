"""Strict builder primitives; client payloads never grant host permissions."""
import datetime as dt
import math,re
class BuilderError(ValueError):
    def __init__(self,code,status=400,details=None):self.code=code;self.status=status;self.details=details;super().__init__(code)
def require(condition,code,status=400):
    if not condition:raise BuilderError(code,status)
def identifier(value):
    require(type(value) is str and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value)),'invalid_identifier');return value
def money(value):
    require(type(value) is int and 0<=value<=10**15,'invalid_amount_idr');return value
def now_iso():return dt.datetime.now(dt.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def parse_time(value):
    require(type(value) is str and len(value)<=40,'invalid_timestamp')
    try:r=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:raise BuilderError('invalid_timestamp') from None
    require(r.tzinfo is not None,'timezone_required');return r.astimezone(dt.timezone.utc)
def validate(value,schema,depth=0):
    require(depth<=20,'input_too_deep');kind=schema.get('type')
    if type(kind) is list:
        errors=[]
        for candidate in kind:
            try:validate(value,{**schema,'type':candidate},depth+1);return
            except BuilderError as e:errors.append(e)
        raise BuilderError('invalid_union_type')
    if kind=='null':require(value is None,'invalid_null')
    elif kind=='object':
        require(type(value) is dict,'invalid_object')
        props=schema.get('properties',{})
        if schema.get('additionalProperties') is False:require(not(set(value)-set(props)),'unknown_fields')
        require(not(set(schema.get('required',[]))-set(value)),'missing_fields')
        for k,v in value.items():
            require(type(k) is str and len(k)<=100,'invalid_field')
            if k in props:validate(v,props[k],depth+1)
    elif kind=='array':
        require(type(value) is list and schema.get('minItems',0)<=len(value)<=schema.get('maxItems',1000),'invalid_array')
        for v in value:validate(v,schema.get('items',{}),depth+1)
    elif kind=='string':require(type(value) is str and schema.get('minLength',0)<=len(value)<=schema.get('maxLength',4000),'invalid_string')
    elif kind=='integer':require(type(value) is int and schema.get('minimum',-10**15)<=value<=schema.get('maximum',10**15),'invalid_integer')
    elif kind=='number':require(type(value) in (int,float) and math.isfinite(value) and schema.get('minimum',-10**15)<=value<=schema.get('maximum',10**15),'invalid_number')
    elif kind=='boolean':require(type(value) is bool,'invalid_boolean')
    if 'enum' in schema:require(value in schema['enum'],'invalid_enum')
    if 'pattern' in schema:require(type(value) is str and re.search(schema['pattern'],value) is not None,'invalid_format')

import hashlib,json
from pathlib import PurePosixPath
from uuid import UUID
def principal(value):
    require(type(value) is str and bool(re.fullmatch(r'api-token:[1-9][0-9]{0,18}',value)),'invalid_principal',403)
    return value
def uuid_id(value):
    try:require(type(value) is str and str(UUID(value))==value,'not_found',404)
    except (ValueError,AttributeError):raise BuilderError('not_found',404) from None
    return value
def safe_path(value):
    require(type(value) is str and 0<len(value)<=240 and '\\' not in value and not any(ord(x)<32 for x in value),'invalid_path')
    p=PurePosixPath(value)
    require(not p.is_absolute() and '..' not in p.parts and str(p)==value and value!='.','invalid_path')
    require(not any(x in ('.git','.ssh','.codex','.harmonika') for x in p.parts),'protected_path',403)
    return value
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False)
def digest(value):return hashlib.sha256(canonical(value).encode()).hexdigest()
