"""Reference incremental SSE parser and bounded context helper; no network."""
import codecs
import json
import re
import sys

class ProtocolError(ValueError):pass

class SSEDecoder:
    def __init__(self,max_event_bytes=131072):
        self.decoder=codecs.getincrementaldecoder('utf-8-sig')('strict')
        self.buffer='';self.data=[];self.kind='message';self.event_id='';self.retry=None
        self.max_event_bytes=max_event_bytes;self.event_bytes=0

    def _line(self,line):
        if not line:
            result={'event':self.kind,'id':self.event_id,'data':'\n'.join(self.data)} if self.data else None
            if result is not None and self.retry is not None:result['retry']=self.retry
            self.data=[];self.kind='message';self.event_bytes=0
            return result
        if line.startswith(':'):return None
        field,sep,value=line.partition(':')
        if sep and value.startswith(' '):value=value[1:]
        if field=='data':self.data.append(value)
        elif field=='event':self.kind=value
        elif field=='id' and '\0' not in value:self.event_id=value
        elif field=='retry' and len(value)<=9 and re.fullmatch(r'[0-9]+',value):self.retry=int(value)
        self.event_bytes+=len(line.encode('utf8'))+1
        if self.event_bytes>self.max_event_bytes:raise ProtocolError('event_too_large')

    def feed(self,chunk):
        try:self.buffer+=self.decoder.decode(chunk)
        except UnicodeDecodeError:raise ProtocolError('invalid_utf8') from None
        output=[]
        while True:
            newline=re.search(r'[\r\n]',self.buffer)
            if newline is None:break
            offset=newline.start()
            if self.buffer[offset]=='\r' and offset==len(self.buffer)-1:break
            advance=2 if self.buffer[offset:offset+2]=='\r\n' else 1
            line=self.buffer[:offset];self.buffer=self.buffer[offset+advance:]
            if len(line.encode('utf8'))>self.max_event_bytes:raise ProtocolError('event_too_large')
            event=self._line(line)
            if event is not None:output.append(event)
        if len(self.buffer.encode('utf8'))+self.event_bytes>self.max_event_bytes:raise ProtocolError('event_too_large')
        return output

    def finish(self):
        try:self.decoder.decode(b'',final=True)
        except UnicodeDecodeError:raise ProtocolError('incomplete_utf8') from None
        # A final CR is itself a line terminator, even without a following LF.
        events=self.feed(b'\n') if self.buffer.endswith('\r') else []
        # Do not dispatch an event that was not terminated by a blank line.
        return {'events':events,'incomplete_event':bool(self.buffer or self.data or self.event_bytes)}

class TextCollector:
    """Collect answer and reasoning separately, with independent character caps.

    Inline <think> syntax is opt-in: ordinary code containing those literal
    strings must retain its meaning. Enable it only for a provider/model whose
    content stream uses this framing. Structured reasoning never needs opt-in.
    think_prefix_only limits framing to one initial block, preserving answer code.
    accept() returns answer text; last_reasoning_delta is reset on every call.
    EOF must be reported through finish() when no terminal event was received.
    """
    def __init__(self,max_output_chars=200000,*,max_reasoning_chars=200000,parse_think_tags=False,think_prefix_only=False):
        if any(type(limit) is not int or limit<0 for limit in (max_output_chars,max_reasoning_chars)):
            raise ProtocolError('invalid_output_limit')
        self.parts=[];self.reasoning_parts=[];self.status='waiting';self.finished=False
        self.size=0;self.reasoning_size=0;self.max_output_chars=max_output_chars
        self.max_reasoning_chars=max_reasoning_chars;self.parse_think_tags=parse_think_tags
        self.last_reasoning_delta='';self._pending='';self._in_think=False
        self.think_prefix_only=think_prefix_only

    def _split(self,chunk):
        if not self.parse_think_tags:return chunk,''
        pending=self._pending+chunk;answer=[];reasoning=[]
        while pending:
            tag='</think>' if self._in_think else '<think>'
            target=reasoning if self._in_think else answer
            offset=pending.find(tag)
            if offset>=0:
                target.append(pending[:offset]);pending=pending[offset+len(tag):]
                self._in_think=not self._in_think
                if not self._in_think and self.think_prefix_only:
                    # A provider's initial thought ends here. Later code/text
                    # containing literal tags is answer content, not framing.
                    self.parse_think_tags=False;answer.append(pending);pending='';break
                continue
            # Retain only a possible delimiter prefix, even across SSE events.
            keep=next((size for size in range(min(len(tag)-1,len(pending)),0,-1)
                       if pending.endswith(tag[:size])),0)
            target.append(pending[:-keep] if keep else pending)
            pending=pending[-keep:] if keep else ''
            break
        self._pending=pending
        return ''.join(answer),''.join(reasoning)

    def _append(self,answer,reasoning):
        if self.size+len(answer)>self.max_output_chars or self.reasoning_size+len(reasoning)>self.max_reasoning_chars:
            self.finished=True;self.status='error'
            raise ProtocolError('output_too_large' if self.size+len(answer)>self.max_output_chars else 'reasoning_too_large')
        if answer:self.parts.append(answer);self.size+=len(answer)
        if reasoning:self.reasoning_parts.append(reasoning);self.reasoning_size+=len(reasoning)
        self.last_reasoning_delta=reasoning
        if answer or reasoning:self.status='streaming'
        return answer

    def _terminal(self,status):
        # A partial closing tag belongs to reasoning; never flush it to answer.
        answer='' if self._in_think else self._pending
        reasoning=self._pending if self._in_think else ''
        self._pending=''
        result=self._append(answer,reasoning)
        self.finished=True
        self.status='incomplete_reasoning' if status=='completed' and self._in_think else status
        return result

    def accept(self,event):
        self.last_reasoning_delta=''
        if self.finished:return ''
        data=event['data']
        if data=='[DONE]':return self._terminal('completed')
        try:payload=json.loads(data)
        except (ValueError,TypeError):raise ProtocolError('invalid_event_json') from None
        if not isinstance(payload,dict):raise ProtocolError('invalid_event_payload')
        kind=payload.get('type')
        if payload.get('error') or kind=='error':return self._terminal('error')
        if kind=='stopped':return self._terminal('stopped')
        if kind in {'final','response.completed'}:
            return self._terminal('completed' if payload.get('ok',True) else 'error')
        chunk='';reasoning=''
        if kind in {'reasoning','reasoning.delta','response.reasoning.delta','response.reasoning_text.delta','response.reasoning_summary_text.delta'}:
            reasoning=payload.get('text',payload.get('delta',''))
        elif kind in {'delta','response.output_text.delta'}:
            chunk=payload.get('text',payload.get('delta',''))
            reasoning=payload.get('reasoning_content',payload.get('reasoning',''))
        elif isinstance(payload.get('choices'),list) and payload['choices']:
            choice=payload['choices'][0]
            if isinstance(choice,dict) and isinstance(choice.get('delta'),dict):
                delta=choice['delta'];chunk=delta.get('content')
                reasoning=delta.get('reasoning_content',delta.get('reasoning',''))
        chunk='' if chunk is None else chunk
        reasoning='' if reasoning is None else reasoning
        if not isinstance(chunk,str):raise ProtocolError('invalid_text_delta')
        if not isinstance(reasoning,str):raise ProtocolError('invalid_reasoning_delta')
        answer,inline_reasoning=self._split(chunk)
        return self._append(answer,reasoning+inline_reasoning)

    def finish(self):
        """Close a transport EOF; missing terminal events are never successful."""
        self.last_reasoning_delta=''
        if self.finished:return ''
        return self._terminal('incomplete_reasoning' if self._in_think else 'incomplete')

    @property
    def text(self):return ''.join(self.parts)
    @property
    def reasoning(self):return ''.join(self.reasoning_parts)

def trim_history(messages,budget,counter=None):
    if type(budget) is not int or budget<1:raise ProtocolError('invalid_context_budget')
    counter=counter or (lambda message:len(json.dumps(message,ensure_ascii=False).encode('utf8'))+32)
    system=[];turns=[];pending=set()
    for message in messages:
        if not isinstance(message,dict):raise ProtocolError('invalid_history')
        role=message.get('role')
        if role=='system' and not turns:system.append(message);continue
        if role=='user':
            if pending:raise ProtocolError('missing_tool_results')
            turns.append([])
        if not turns or role not in {'user','assistant','tool'}:raise ProtocolError('invalid_history_order')
        if role=='assistant':
            if pending:raise ProtocolError('missing_tool_results')
            for call in message.get('tool_calls',[]):
                call_id=call.get('id')
                if not call_id or call_id in pending:raise ProtocolError('invalid_tool_history')
                pending.add(call_id)
        if role=='tool':
            call_id=message.get('tool_call_id')
            if call_id not in pending:raise ProtocolError('invalid_tool_history')
            pending.remove(call_id)
        turns[-1].append(message)
    if pending:raise ProtocolError('missing_tool_results')
    if not turns:raise ProtocolError('missing_user_turn')
    used=sum(counter(m) for m in system);kept=[]
    for turn in reversed(turns):
        cost=sum(counter(m) for m in turn)
        if used+cost>budget:
            if not kept:raise ProtocolError('latest_turn_too_large')
            break
        kept.insert(0,turn);used+=cost
    return system+[m for turn in kept for m in turn]

def self_test():
    raw=('data: {"choices":[{"delta":{"content":"Halo 🌍"}}]}\r\n\r\n'+'data: [DONE]\n\n').encode()
    decoder=SSEDecoder();collector=TextCollector()
    for value in raw:
        for event in decoder.feed(bytes([value])):collector.accept(event)
    assert collector.text=='Halo 🌍' and collector.finished and not decoder.finish()['incomplete_event']
    reasoning_collector=TextCollector(parse_think_tags=True)
    for fragment in ['<thi','nk>Analisis 🌍</th','ink>Jawaban']:
        reasoning_collector.accept({'data':json.dumps({'type':'delta','text':fragment})})
    reasoning_collector.accept({'data':'[DONE]'})
    assert reasoning_collector.text=='Jawaban' and reasoning_collector.reasoning=='Analisis 🌍'
    assert reasoning_collector.status=='completed'
    history=[{'role':'system','content':'Rules'},{'role':'user','content':'old'},{'role':'assistant','content':'answer'},{'role':'user','content':'new'}]
    result=trim_history(history,2,counter=lambda m:1)
    assert result==[history[0],history[-1]]
    return {'ok':True,'provider':'Harmonika Ai','scope':'reference_protocol','checks':['split_utf8','split_sse','terminal_event','separate_reasoning','split_think_tags','atomic_history_turns']}

if __name__=='__main__':
    if sys.argv[1:]!=['self-test']:raise SystemExit('Usage: stream_protocol.py self-test')
    print(json.dumps(self_test(),ensure_ascii=False))
