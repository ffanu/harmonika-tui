"""Harmonika inference contract v1; adapters own transports, credentials and model IDs."""
from abc import ABC,abstractmethod
from contextlib import closing
from dataclasses import dataclass,field
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import threading
import time
from typing import Iterator,Optional
import urllib.error
import urllib.request
from uuid import uuid4

ROOT=Path(__file__).resolve().parents[1]
PARSER=ROOT/'skills/harmonika-ai-integration/scripts/stream_protocol.py'
if not PARSER.exists():PARSER=ROOT.parent/'skills/harmonika-ai-integration/scripts/stream_protocol.py'
spec=importlib.util.spec_from_file_location('harmonika_stream',PARSER);protocol=importlib.util.module_from_spec(spec);spec.loader.exec_module(protocol)

class IntegrationError(ValueError):
    def __init__(self,code,retryable=False):
        # Public errors contain stable codes, never provider exception text.
        safe=isinstance(code,str) and re.fullmatch(r'[a-z][a-z0-9_]{0,63}',code)
        self.code=code if safe else 'adapter_error'
        self.retryable=retryable is True and bool(safe)
        super().__init__(self.code)

GatewayError=IntegrationError

def clone(value):
    try:return json.loads(json.dumps(value,ensure_ascii=False,allow_nan=False))
    except (ValueError,TypeError,RecursionError):raise IntegrationError('invalid_payload') from None

def validate_tools(calls,*,complete=False):
    """Fragments may omit metadata or contain null placeholders; full calls may not."""
    if not isinstance(calls,list):raise IntegrationError('invalid_adapter_tools')
    for call in calls:
        if not isinstance(call,dict) or not call or set(call)-{'index','id','type','function'}:raise IntegrationError('invalid_adapter_tools')
        if 'index' in call and (type(call['index']) is not int or call['index']<0):raise IntegrationError('invalid_adapter_tools')
        if 'id' in call and not (not complete and call['id'] is None) and not isinstance(call['id'],str):raise IntegrationError('invalid_adapter_tools')
        if 'type' in call and call['type']!='function' and (complete or call['type'] is not None):raise IntegrationError('invalid_adapter_tools')
        function=call.get('function',{})
        if function is None and not complete:function={}
        if not isinstance(function,dict) or set(function)-{'name','arguments'}:raise IntegrationError('invalid_adapter_tools')
        if any(not isinstance(v,str) and (complete or v is not None) for v in function.values()):raise IntegrationError('invalid_adapter_tools')
        if complete:
            if not call.get('id') or call.get('type')!='function' or not function.get('name') or 'arguments' not in function:raise IntegrationError('invalid_adapter_tools')
            try:arguments=json.loads(function['arguments'])
            except (ValueError,TypeError,RecursionError):raise IntegrationError('invalid_adapter_tools') from None
            if not isinstance(arguments,dict):raise IntegrationError('invalid_adapter_tools')

def validate_usage(usage):
    # Nested provider token-detail dictionaries are retained, but never text.
    if not isinstance(usage,dict):raise IntegrationError('invalid_adapter_usage')
    for value in usage.values():
        if isinstance(value,dict):validate_usage(value)
        elif type(value) is not int or value<0:raise IntegrationError('invalid_adapter_usage')

def close_iterator(iterator):
    if iterator is not None and hasattr(iterator,'close'):
        try:iterator.close()
        except Exception:raise IntegrationError('adapter_close_failed') from None

@dataclass(frozen=True,repr=False)
class AIRequest:
    messages:list=field(repr=False)
    tools:list=field(default_factory=list,repr=False)
    generation:dict=field(default_factory=dict,repr=False)
    context:dict=field(default_factory=dict,repr=False)
    schema_version:int=1
    def __post_init__(self):
        if type(self.schema_version) is not int or self.schema_version!=1:raise IntegrationError('unsupported_contract_version')
        if not isinstance(self.messages,list) or not self.messages:raise IntegrationError('invalid_messages')
        for message in self.messages:
            if not isinstance(message,dict) or not isinstance(message.get('role'),str) or message.get('role') not in {'system','developer','user','assistant','tool'}:raise IntegrationError('invalid_message_role')
            if message.get('content') is not None and not isinstance(message['content'],(str,list)):raise IntegrationError('invalid_message_content')
            if set(message)-{'role','content','name','tool_calls','tool_call_id','reasoning','reasoning_content'}:raise IntegrationError('unsupported_message_field')
            if any(message.get(k) is not None and not isinstance(message[k],str) for k in ['name','tool_call_id','reasoning','reasoning_content']):raise IntegrationError('invalid_message_field')
            if message.get('reasoning') is not None and message.get('reasoning_content') is not None and message['reasoning']!=message['reasoning_content']:raise IntegrationError('conflicting_reasoning_fields')
            if isinstance(message.get('content'),list) and any(not isinstance(block,dict) or not isinstance(block.get('type'),str) for block in message['content']):raise IntegrationError('invalid_message_content')
            if 'tool_calls' in message:
                if message['role']!='assistant':raise IntegrationError('invalid_message_tools')
                validate_tools(message['tool_calls'],complete=True)
            if message['role']=='tool' and not message.get('tool_call_id'):raise IntegrationError('invalid_message_tools')
        if not isinstance(self.tools,list) or any(not isinstance(t,dict) for t in self.tools):raise IntegrationError('invalid_tools')
        for tool in self.tools:
            function=tool.get('function')
            if tool.get('type')!='function' or not isinstance(function,dict) or not isinstance(function.get('name'),str) or not function['name']:raise IntegrationError('invalid_tools')
            if 'parameters' in function and not isinstance(function['parameters'],dict):raise IntegrationError('invalid_tools')
        if not isinstance(self.generation,dict) or not isinstance(self.context,dict):raise IntegrationError('invalid_request_options')
        if set(self.generation)&{'messages','tools','model','stream','api_key','base_url','headers','authorization'}:raise IntegrationError('reserved_generation_field')
        context=clone(self.context)
        for name,value in context.items():
            if name not in {'request_id','conversation_id','profile_id','task_id'} or not isinstance(value,str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,128}',value):raise IntegrationError('invalid_request_context')
        context.setdefault('request_id',uuid4().hex)
        object.__setattr__(self,'messages',clone(self.messages));object.__setattr__(self,'tools',clone(self.tools))
        object.__setattr__(self,'generation',clone(self.generation));object.__setattr__(self,'context',context)
        if len(json.dumps(self.to_payload()).encode())>2*1024*1024:raise IntegrationError('request_too_large')
        if 'n' in self.generation and (type(self.generation['n']) is not int or self.generation['n']!=1):raise IntegrationError('unsupported_count')
    @classmethod
    def from_payload(cls,payload):
        if not isinstance(payload,dict) or set(payload)-{'schema_version','messages','tools','generation','context'}:raise IntegrationError('invalid_contract_payload')
        return cls(messages=payload.get('messages'),tools=payload.get('tools',[]),generation=payload.get('generation',{}),context=payload.get('context',{}),schema_version=payload.get('schema_version',1))
    @classmethod
    def from_openai(cls,body):
        if not isinstance(body,dict):raise IntegrationError('invalid_request')
        if type(body.get('n',1)) is not int or body.get('n',1)!=1:raise IntegrationError('unsupported_count')
        if type(body.get('stream',False)) is not bool:raise IntegrationError('invalid_stream')
        ignored={'messages','tools','stream','model','n','metadata'}
        return cls(body.get('messages'),body.get('tools',[]),{k:v for k,v in body.items() if k not in ignored},{} if body.get('metadata') is None else body['metadata'])
    def to_payload(self):
        return clone({'schema_version':self.schema_version,'messages':self.messages,'tools':self.tools,'generation':self.generation,'context':self.context})
    def transport_payload(self,model,stream):
        body=clone(self.generation);body.update(messages=clone(self.messages),model=model,stream=stream)
        if self.tools:body['tools']=clone(self.tools)
        return body

@dataclass(frozen=True,repr=False)
class AIEvent:
    type:str
    data:dict=field(default_factory=dict,repr=False)
    sequence:int=0
    request_id:str=''

@dataclass(frozen=True,repr=False)
class AIResponse:
    text:str=''
    reasoning:str=field(default='',repr=False)
    tool_calls:list=field(default_factory=list,repr=False)
    usage:dict=field(default_factory=dict,repr=False)
    finish_reason:str='stop'
    def __post_init__(self):
        if not isinstance(self.text,str) or not isinstance(self.reasoning,str):raise IntegrationError('invalid_adapter_response_text')
        if len(self.text)>200000 or len(self.reasoning)>200000:raise IntegrationError('adapter_output_too_large')
        if not isinstance(self.tool_calls,list) or any(not isinstance(t,dict) for t in self.tool_calls):raise IntegrationError('invalid_adapter_response_tools')
        validate_tools(self.tool_calls,complete=True);validate_usage(self.usage)
        if not isinstance(self.finish_reason,str) or not re.fullmatch('[a-z_]{1,64}',self.finish_reason):raise IntegrationError('invalid_finish_reason')
        if len(json.dumps(clone([self.tool_calls,self.usage])).encode())>2*1024*1024:raise IntegrationError('adapter_output_too_large')
        object.__setattr__(self,'tool_calls',clone(self.tool_calls));object.__setattr__(self,'usage',clone(self.usage))

class Cancellation:
    def __init__(self):self._event=threading.Event()
    def cancel(self):self._event.set()
    def check(self):
        if self._event.is_set():raise IntegrationError('request_cancelled')

class AIAdapter(ABC):
    @abstractmethod
    def stream(self,request:AIRequest,cancel:Cancellation)->Iterator[AIEvent]:pass
    @abstractmethod
    def complete(self,request:AIRequest,cancel:Cancellation)->AIResponse:pass

class Normalizer:
    def __init__(self):
        self.collector=protocol.TextCollector(think_prefix_only=True);self.prefix='';self.decided=False
    def accept(self,payload):
        value=clone(payload)
        if not isinstance(value,dict):raise IntegrationError('invalid_upstream_response')
        value['model']='harmonika-ai';choices=value.get('choices',[])
        if not isinstance(choices,list) or len(choices)>1:raise IntegrationError('invalid_upstream_choices')
        if not choices:
            if not isinstance(value.get('usage'),dict):raise IntegrationError('invalid_upstream_choices')
            return value,False
        choice=choices[0]
        if not isinstance(choice,dict) or not isinstance(choice.get('delta',{}),dict):raise IntegrationError('invalid_upstream_delta')
        delta=choice.get('delta',{})
        if set(delta)-{'role','content','reasoning','reasoning_content','tool_calls','function_call'}:raise IntegrationError('unsupported_upstream_delta')
        if 'function_call' in delta:raise IntegrationError('legacy_function_call_unsupported')
        if delta.get('reasoning') is not None and delta.get('reasoning_content') is not None and delta['reasoning']!=delta['reasoning_content']:raise IntegrationError('conflicting_reasoning_fields')
        if 'tool_calls' in delta and delta['tool_calls'] is not None:validate_tools(delta['tool_calls'])
        chunk=delta.get('content')
        if isinstance(chunk,str) and not self.decided:
            self.prefix+=chunk
            if '<think>'.startswith(self.prefix.lstrip()) and len(self.prefix.lstrip())<len('<think>') and len(self.prefix)<64:
                delta['content']=''
            else:
                self.decided=True;self.collector.parse_think_tags=self.prefix.lstrip().startswith('<think>');delta['content']=self.prefix;self.prefix=''
        terminal=choice.get('finish_reason') is not None
        answer=self.collector.accept({'data':json.dumps(value)})
        reason=self.collector.last_reasoning_delta
        for name in ['content','reasoning','reasoning_content']:delta.pop(name,None)
        if answer:delta['content']=answer
        if reason:delta['reasoning_content']=reason
        meaningful=bool(answer or reason or delta.get('tool_calls') or delta.get('function_call'))
        if terminal:
            tail=self.end();tail_delta=tail.get('content','')
            if tail_delta:delta['content']=delta.get('content','')+tail_delta
            if tail.get('reasoning_content'):delta['reasoning_content']=delta.get('reasoning_content','')+tail['reasoning_content']
        choice['delta']=delta;value['model']='harmonika-ai'
        return value,meaningful
    def end(self):
        output={}
        if self.prefix:
            self.decided=True
            answer=self.collector.accept({'data':json.dumps({'type':'delta','text':self.prefix})});self.prefix=''
            if answer:output['content']=answer
        answer=self.collector.accept({'data':'[DONE]'})
        if answer:output['content']=output.get('content','')+answer
        if self.collector.last_reasoning_delta:output['reasoning_content']=self.collector.last_reasoning_delta
        if self.collector.status!='completed':raise GatewayError('incomplete_reasoning')
        return output


class OpenAICompatibleAdapter(AIAdapter):
    """Wire-specific adapter. prepare returns body + auth, never forwarded to UI."""
    def __init__(self,endpoint,prepare,open_request):
        self.endpoint=endpoint;self.prepare=prepare;self.open_request=open_request
    def response(self,request,stream):
        body,auth=self.prepare(request,stream)
        hdrs={'Content-Type':'application/json','Authorization':'Bearer '+auth,'Accept':'text/event-stream' if stream else 'application/json'}
        if self.endpoint.startswith('https://chat.harmonika.id/'):
            dev_id=os.environ.get('HARMONIKA_DEVICE_ID','')
            if not dev_id:
                try:
                    df=Path.home()/'.config/harmonika/device-id'
                    if df.is_file():dev_id=df.read_text().strip()
                except Exception:pass
            if dev_id:hdrs['X-Harmonika-Device-ID']=dev_id
        wire=urllib.request.Request(self.endpoint,data=json.dumps(body,ensure_ascii=False,allow_nan=False).encode(),headers=hdrs)
        return self.open_request(wire)
    @staticmethod
    def failure(error,terminal=False):
        if isinstance(error,IntegrationError):return error
        if isinstance(error,urllib.error.HTTPError):return IntegrationError('upstream_http_'+str(error.code),not terminal and error.code in {500,502,503,504})
        if isinstance(error,(urllib.error.URLError,OSError,TimeoutError,socket.timeout)):return IntegrationError('stream_interrupted' if terminal else 'network_unavailable',not terminal)
        return IntegrationError('invalid_upstream_response')
    @staticmethod
    def delta(value):
        choice=(value.get('choices') or [{}])[0];delta=choice.get('delta',{});data={}
        if delta.get('content'):data['text']=delta['content']
        if delta.get('reasoning_content'):data['reasoning']=delta['reasoning_content']
        if delta.get('tool_calls'):data['tool_calls']=clone(delta['tool_calls'])
        if delta.get('function_call'):raise IntegrationError('legacy_function_call_unsupported')
        return data
    def stream(self,request,cancel):
        decoder=protocol.SSEDecoder();normal=Normalizer();bytes_seen=0;terminal=False;finish_reason='stop';usage={};deadline=time.monotonic()+120
        try:
            cancel.check()
            with closing(self.response(request,True)) as response:
                while True:
                    cancel.check()
                    if time.monotonic()>deadline:raise IntegrationError('response_timeout')
                    raw=response.read1(16384);bytes_seen+=len(raw)
                    if bytes_seen>8*1024*1024:raise IntegrationError('stream_too_large')
                    if not raw:break
                    for event in decoder.feed(raw):
                        cancel.check()
                        if event['data']=='[DONE]':
                            tail=normal.end();data={}
                            if tail.get('content'):data['text']=tail['content']
                            if tail.get('reasoning_content'):data['reasoning']=tail['reasoning_content']
                            if data:yield AIEvent('delta',data)
                            if usage:yield AIEvent('usage',{'tokens':usage})
                            yield AIEvent('completed',{'finish_reason':finish_reason});return
                        payload=json.loads(event['data'])
                        if not isinstance(payload,dict) or payload.get('error'):raise IntegrationError('upstream_error')
                        value,_=normal.accept(payload);choice=(value.get('choices') or [{}])[0]
                        if choice.get('finish_reason') is not None:terminal=True;finish_reason=choice['finish_reason']
                        if isinstance(value.get('usage'),dict):usage=clone(value['usage'])
                        data=self.delta(value)
                        if data:yield AIEvent('delta',data)
                raise IntegrationError('stream_interrupted')
        except GeneratorExit:raise
        except Exception as error:raise self.failure(error,terminal) from None
    def complete(self,request,cancel):
        try:
            cancel.check()
            with closing(self.response(request,False)) as response:
                raw=response.read(2*1024*1024+1)
                if len(raw)>2*1024*1024:raise IntegrationError('response_too_large')
                payload=json.loads(raw)
            cancel.check()
            if not isinstance(payload,dict) or payload.get('error') or not payload.get('choices'):raise IntegrationError('invalid_upstream_response')
            choice=payload['choices'][0];message=choice.get('message',{});normal=Normalizer()
            value,_=normal.accept({'choices':[{'delta':clone(message),'finish_reason':'stop'}]})
            delta=value['choices'][0]['delta']
            return AIResponse(delta.get('content',''),delta.get('reasoning_content',''),clone(delta.get('tool_calls',[])),clone(payload.get('usage') or {}),choice.get('finish_reason') or 'stop')
        except Exception as error:raise self.failure(error) from None

class AIIntegration:
    """Transport-neutral policy wrapper. Credentials and wire format stay in adapters."""
    def __init__(self,primary:AIAdapter,fallback:Optional[AIAdapter]=None):
        self.primary=primary;self.fallback=fallback
    def stream(self,request:AIRequest,cancel=None):
        if not isinstance(request,AIRequest):raise IntegrationError('invalid_request')
        request=AIRequest.from_payload(request.to_payload())
        cancel=cancel or Cancellation();sequence=0;emitted=False;text_size=0;reasoning_size=0;wire_size=0;deadline=time.monotonic()+120
        for adapter in [self.primary,self.fallback]:
            if adapter is None:break
            iterator=None
            try:
                cancel.check();iterator=iter(adapter.stream(AIRequest.from_payload(request.to_payload()),cancel));pending=None
                for event in iterator:
                    cancel.check()
                    if time.monotonic()>deadline:raise IntegrationError('response_timeout')
                    if sequence>262144:raise IntegrationError('too_many_adapter_events')
                    if not isinstance(event,AIEvent) or event.type not in {'delta','usage','completed'} or not isinstance(event.data,dict):raise IntegrationError('invalid_adapter_event')
                    if pending is not None:raise IntegrationError('event_after_completion')
                    if event.type=='delta':
                        if set(event.data)-{'text','reasoning','tool_calls'}:raise IntegrationError('invalid_adapter_delta')
                        if any(not isinstance(event.data[k],str) for k in ['text','reasoning'] if k in event.data):raise IntegrationError('invalid_adapter_delta')
                        if 'tool_calls' in event.data:validate_tools(event.data['tool_calls'])
                        text_size+=len(event.data.get('text',''));reasoning_size+=len(event.data.get('reasoning',''))
                        if text_size>200000 or reasoning_size>200000:raise IntegrationError('adapter_output_too_large')
                    event_size=len(json.dumps(clone(event.data)).encode());wire_size+=event_size
                    if event_size>131072:raise IntegrationError('adapter_event_too_large')
                    if wire_size>8*1024*1024:raise IntegrationError('adapter_output_too_large')
                    if event.type=='usage':
                        if set(event.data)!={'tokens'}:raise IntegrationError('invalid_adapter_usage')
                        validate_usage(event.data['tokens'])
                    if event.type=='completed':
                        if set(event.data)-{'finish_reason'}:raise IntegrationError('invalid_adapter_completion')
                        reason=event.data.get('finish_reason','stop')
                        if not isinstance(reason,str) or not re.fullmatch('[a-z_]{1,64}',reason):raise IntegrationError('invalid_finish_reason')
                        pending=AIEvent(event.type,clone(event.data));emitted=True;continue
                    sequence+=1;emitted=True
                    yield AIEvent(event.type,clone(event.data),sequence,request.context['request_id'])
                if pending is None:raise IntegrationError('missing_adapter_completion')
                cancel.check();sequence+=1
                yield AIEvent(pending.type,clone(pending.data),sequence,request.context['request_id'])
                return
            except IntegrationError as error:
                cancel.check()
                if emitted or adapter is self.fallback or self.fallback is None or not error.retryable:raise
            except Exception:raise IntegrationError('adapter_unavailable') from None
            finally:
                close_iterator(iterator)
        raise IntegrationError('backend_unavailable')
    def complete(self,request:AIRequest,cancel=None):
        if not isinstance(request,AIRequest):raise IntegrationError('invalid_request')
        request=AIRequest.from_payload(request.to_payload())
        cancel=cancel or Cancellation()
        try:
            cancel.check();result=self.primary.complete(AIRequest.from_payload(request.to_payload()),cancel);cancel.check()
        except IntegrationError as error:
            cancel.check()
            if not self.fallback or not error.retryable:raise
            try:result=self.fallback.complete(AIRequest.from_payload(request.to_payload()),cancel);cancel.check()
            except IntegrationError:raise
            except Exception:raise IntegrationError('adapter_unavailable') from None
        except Exception:raise IntegrationError('adapter_unavailable') from None
        if not isinstance(result,AIResponse):raise IntegrationError('invalid_adapter_response')
        return AIResponse(result.text,result.reasoning,result.tool_calls,result.usage,result.finish_reason)


def openai_stream(events):
    """Only the edge gateway knows this format. TUI contract remains unchanged."""
    identifier='chatcmpl-'+uuid4().hex;created=int(time.time())
    try:
        for event in events:
            base={'id':identifier,'object':'chat.completion.chunk','created':created,'model':'harmonika-ai'}
            if event.type=='delta':
                data=event.data;delta={}
                if data.get('text'):delta['content']=data['text']
                if data.get('reasoning'):delta['reasoning_content']=data['reasoning']
                if data.get('tool_calls'):delta['tool_calls']=clone(data['tool_calls'])
                base['choices']=[{'index':0,'delta':delta,'finish_reason':None}];yield base
            elif event.type=='usage':base.update(choices=[],usage=clone(event.data['tokens']));yield base
            elif event.type=='completed':
                base['choices']=[{'index':0,'delta':{},'finish_reason':event.data.get('finish_reason','stop')}];yield base;yield '[DONE]'
    finally:
        close_iterator(events)

def openai_response(result):
    message={'role':'assistant','content':result.text}
    if result.reasoning:message['reasoning_content']=result.reasoning
    if result.tool_calls:message['tool_calls']=clone(result.tool_calls)
    return {'id':'chatcmpl-'+uuid4().hex,'object':'chat.completion','created':int(time.time()),'model':'harmonika-ai','choices':[{'index':0,'message':message,'finish_reason':result.finish_reason}],'usage':clone(result.usage)}
