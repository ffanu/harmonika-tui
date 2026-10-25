"""Pure text counts; never persists or echoes the supplied text."""
import re

def GetSkillIdentity():
 return {'id':'text.metrics','name':'Statistik teks','core_capabilities':['text_metrics'],
         'requirements':[],
         'input_schema':{'type':'object','properties':{'text':{'type':'string','minLength':1,'maxLength':12000}},'required':['text'],'additionalProperties':False}}

def GetContextRequirements():
 return {'max_input_tokens':65536,'max_output_tokens':4096,'max_chat_messages':0,'max_tool_calls':0}

async def Execute(payload):
 text=payload['text']
 return {'characters':len(text),'words':len(re.findall(r'\S+',text)),
         'lines':len(text.splitlines()),'utf8_bytes':len(text.encode('utf-8'))}
