"""Strict domain primitives; policy decisions never come from model claims."""
import datetime as dt
import math,re
class DomainError(ValueError):
    def __init__(self,code,status=400,details=None):self.code=code;self.status=status;self.details=details;super().__init__(code)
def require(condition,code,status=400):
    if not condition:raise DomainError(code,status)
def identifier(value):
    require(type(value) is str and bool(re.fullmatch(r'[A-Za-z0-9_-]{1,100}',value)),'invalid_identifier');return value
def money(value):
    require(type(value) is int and 0<=value<=10**15,'invalid_amount_idr');return value
def now_iso():return dt.datetime.now(dt.timezone.utc).isoformat(timespec='milliseconds').replace('+00:00','Z')
def parse_time(value):
    require(type(value) is str and len(value)<=40,'invalid_timestamp')
    try:r=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:raise DomainError('invalid_timestamp') from None
    require(r.tzinfo is not None,'timezone_required');return r.astimezone(dt.timezone.utc)
def validate(value,schema,depth=0):
    require(depth<=20,'input_too_deep');kind=schema.get('type')
    if type(kind) is list:
        errors=[]
        for candidate in kind:
            try:validate(value,{**schema,'type':candidate},depth+1);return
            except DomainError as e:errors.append(e)
        raise DomainError('invalid_union_type')
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
    if 'pattern' in schema:require(type(value) is str and re.fullmatch(schema['pattern'],value) is not None,'invalid_format')
