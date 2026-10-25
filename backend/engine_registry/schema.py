"""Deliberately bounded JSON Schema subset for operator-installed pure skills.

Unknown keywords fail closed. No refs, regex execution, remote schemas or code.
"""
import math

class SchemaError(ValueError):
    pass

KEYS = {'type', 'description', 'properties', 'required', 'additionalProperties',
        'items', 'minItems', 'maxItems', 'minLength', 'maxLength', 'minimum', 'maximum', 'enum'}
TYPES = {'object', 'array', 'string', 'integer', 'number', 'boolean', 'null'}

def audit(schema, depth=0):
    if depth > 8 or type(schema) is not dict or set(schema) - KEYS or type(schema.get('type')) is not str or schema.get('type') not in TYPES:
        raise SchemaError('unsupported_skill_schema')
    kind = schema['type']
    if 'description' in schema and (type(schema['description']) is not str or len(schema['description']) > 500):
        raise SchemaError('invalid_schema_description')
    for key in ('minItems', 'maxItems', 'minLength', 'maxLength'):
        if key in schema and (type(schema[key]) is not int or not 0 <= schema[key] <= 65536):
            raise SchemaError('invalid_schema_bound')
    for key in ('minimum','maximum'):
        if key in schema and (type(schema[key]) not in (int,float) or abs(schema[key]) > 1e100 or not math.isfinite(schema[key])):
            raise SchemaError('invalid_schema_bound')
    for low, high in (('minItems', 'maxItems'), ('minLength', 'maxLength'), ('minimum', 'maximum')):
        if low in schema and high in schema and schema[low] > schema[high]:
            raise SchemaError('invalid_schema_bound')
    if 'enum' in schema:
        values=schema['enum']
        if type(values) is not list or not 1 <= len(values) <= 64 or any(type(v) not in (str,int,bool,type(None)) for v in values):
            raise SchemaError('invalid_schema_enum')
    if kind == 'object':
        props=schema.get('properties', {})
        if type(props) is not dict or len(props) > 64 or any(type(k) is not str or not 1 <= len(k) <= 100 for k in props):
            raise SchemaError('invalid_schema_properties')
        required=schema.get('required', [])
        if type(required) is not list or any(type(k) is not str or k not in props for k in required) or len(set(required)) != len(required):
            raise SchemaError('invalid_schema_required')
        if type(schema.get('additionalProperties',False)) is not bool:
            raise SchemaError('invalid_schema_properties')
        for value in props.values():audit(value,depth+1)
    elif any(k in schema for k in ('properties','required','additionalProperties')):
        raise SchemaError('invalid_schema_keyword')
    if kind == 'array':
        if 'items' not in schema:raise SchemaError('missing_item_schema')
        audit(schema['items'],depth+1)
    elif any(k in schema for k in ('items','minItems','maxItems')):
        raise SchemaError('invalid_schema_keyword')
    if kind != 'string' and any(k in schema for k in ('minLength','maxLength')):
        raise SchemaError('invalid_schema_keyword')
    if kind not in ('integer','number') and any(k in schema for k in ('minimum','maximum')):
        raise SchemaError('invalid_schema_keyword')
    return schema

def validate(value, schema, depth=0):
    if depth > 12:raise SchemaError('skill_input_depth')
    kind=schema['type'];t=type(value)
    valid={'object':t is dict,'array':t is list,'string':t is str,'integer':t is int,
           'number':t in (int,float),'boolean':t is bool,'null':value is None}[kind]
    if not valid:raise SchemaError('skill_input_type')
    if t is float and not math.isfinite(value):raise SchemaError('skill_input_number')
    if 'enum' in schema and not any(type(value) is type(v) and value == v for v in schema['enum']):raise SchemaError('skill_input_enum')
    if kind == 'object':
        props=schema.get('properties',{})
        if any(k not in value for k in schema.get('required',[])) or (not schema.get('additionalProperties',False) and set(value)-set(props)):
            raise SchemaError('skill_input_fields')
        for k,v in value.items():
            if k in props:validate(v,props[k],depth+1)
    if kind == 'array':
        if not schema.get('minItems',0) <= len(value) <= schema.get('maxItems',1000):raise SchemaError('skill_input_length')
        for v in value:validate(v,schema['items'],depth+1)
    if kind == 'string' and not schema.get('minLength',0) <= len(value) <= schema.get('maxLength',65536):raise SchemaError('skill_input_length')
    if kind in ('integer','number') and (('minimum' in schema and value < schema['minimum']) or ('maximum' in schema and value > schema['maximum'])):
        raise SchemaError('skill_input_number')
    return value
