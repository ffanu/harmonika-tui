"""Real GraphQL parser/schema with bounded read-only queries and tenant-bound resolvers."""
from graphql import GraphQLSchema,GraphQLObjectType,GraphQLField,GraphQLArgument,GraphQLString,GraphQLInt,GraphQLList,GraphQLScalarType,parse,validate,execute
from graphql.language import OperationDefinitionNode,FragmentDefinitionNode,FragmentSpreadNode,FieldNode
from .state import Error,require
from .registry import capabilities,descriptors

JSON=GraphQLScalarType('JSON',serialize=lambda v:v,description='Bounded JSON output; never arbitrary code or a data-fetching URL.')
def make_schema(state,extensions):
 def job(_,info,id):return state.public(state.get(info.context['actor'],id))
 def session(_,info,id):return state.session(info.context['actor'],id)
 def knowledge(_,info,query,limit=5):return extensions.query(query,limit)
 def events(_,info,topic,after=0,limit=100):return extensions.replay(info.context['actor'],topic,after,limit)
 def metadata(_,info,name):
  require('secrets:read' in info.context['scopes'],'secret_scope_required',403);return extensions.secret_metadata(info.context['actor'],name)
 skill=GraphQLObjectType('Skill',{'id':GraphQLField(GraphQLString),'status':GraphQLField(GraphQLString),'mode':GraphQLField(GraphQLString),'description':GraphQLField(GraphQLString)})
 query=GraphQLObjectType('Query',{
  'capabilities':GraphQLField(JSON,resolve=lambda _,info:{**capabilities(),'token_permissions':{'secret_metadata':'secrets:read' in info.context['scopes'],'secret_write':'secrets:write' in info.context['scopes']}}),
  'skills':GraphQLField(GraphQLList(skill),resolve=lambda *_:descriptors()),
  'job':GraphQLField(JSON,args={'id':GraphQLArgument(GraphQLString)},resolve=job),
  'session':GraphQLField(JSON,args={'id':GraphQLArgument(GraphQLString)},resolve=session),
  'knowledge':GraphQLField(JSON,args={'query':GraphQLArgument(GraphQLString),'limit':GraphQLArgument(GraphQLInt,default_value=5)},resolve=knowledge),
  'events':GraphQLField(JSON,args={'topic':GraphQLArgument(GraphQLString),'after':GraphQLArgument(GraphQLInt,default_value=0),'limit':GraphQLArgument(GraphQLInt,default_value=100)},resolve=events),
  'secretMetadata':GraphQLField(JSON,args={'name':GraphQLArgument(GraphQLString)},resolve=metadata)})
 return GraphQLSchema(query=query)

def run(schema,body,actor,scopes):
 require(type(body) is dict and set(body)<= {'query','variables','operationName'} and type(body.get('query')) is str,'invalid_graphql_request')
 require(0<len(body['query'].encode())<=8192,'graphql_query_limit',413)
 require(body.get('variables') is None or type(body['variables']) is dict,'invalid_graphql_variables')
 require(body.get('operationName') is None or type(body['operationName']) is str,'invalid_operation_name')
 try:doc=parse(body['query'],max_tokens=512)
 except Exception:return {'errors':[{'message':'GraphQL request rejected.','extensions':{'code':'graphql_parse_error'}}]}
 operations=[d for d in doc.definitions if isinstance(d,OperationDefinitionNode)];fragments={d.name.value:d for d in doc.definitions if isinstance(d,FragmentDefinitionNode)}
 require(len(operations)==1 and operations[0].operation.value=='query','graphql_read_only')
 count=0
 def walk(selection,depth,stack):
  nonlocal count
  require(depth<=8,'graphql_depth_limit')
  for node in selection.selections:
   count+=1;require(count<=40,'graphql_cost_limit')
   if isinstance(node,FieldNode):require(not node.name.value.startswith('__') or node.name.value=='__typename','graphql_introspection_disabled')
   if isinstance(node,FragmentSpreadNode):
    name=node.name.value;require(name in fragments and name not in stack,'invalid_graphql_fragment');walk(fragments[name].selection_set,depth+1,stack|{name})
   elif node.selection_set:walk(node.selection_set,depth+1,stack)
 walk(operations[0].selection_set,0,set())
 if validate(schema,doc):return {'errors':[{'message':'GraphQL request rejected.','extensions':{'code':'graphql_validation_error'}}]}
 result=execute(schema,doc,variable_values=body.get('variables'),operation_name=body.get('operationName'),context_value={'actor':actor,'scopes':scopes})
 out={'data':result.data}
 if result.errors:
  out['errors']=[]
  for e in result.errors[:10]:
   original=e.original_error;code=original.code if isinstance(original,Error) else 'graphql_resolver_error'
   out['errors'].append({'message':'GraphQL operation could not be completed.','path':e.path,'extensions':{'code':code}})
 return out
