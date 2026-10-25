import importlib.util
import json
from pathlib import Path
import unittest

path=Path(__file__).resolve().parent.parent/'skills/harmonika-ai-integration/scripts/stream_protocol.py'
spec=importlib.util.spec_from_file_location('stream_protocol',path);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
class Stream(unittest.TestCase):
    def test_byte_by_byte_utf8_and_multiple_events(self):self.assertTrue(m.self_test()['ok'])
    def test_multiline_crlf_id_retry_and_heartbeat(self):
        d=m.SSEDecoder();events=d.feed(b': heartbeat\r\nid: 12\r\nretry: 1000\r\nevent: update\r\ndata: {\r\ndata: "x":1}\r\n\r\n')
        self.assertEqual(events,[{'event':'update','id':'12','data':'{\n"x":1}','retry':1000}])
    def test_cr_only_last_separator_flushes_as_real_line_ending(self):
        d=m.SSEDecoder();events=d.feed(b'data: {}\r\r');events+=d.finish()['events'];self.assertEqual(len(events),1)
    def test_truncated_json_not_dispatched_and_utf8_eof_error(self):
        d=m.SSEDecoder();self.assertEqual(d.feed(b'data: {"partial":'),[]);self.assertTrue(d.finish()['incomplete_event'])
        d=m.SSEDecoder();d.feed(b'\xf0\x9f')
        with self.assertRaisesRegex(m.ProtocolError,'incomplete_utf8'):d.finish()
    def test_oversized_line_or_multiline_event_refused(self):
        for data in [b'x'*33,b'data: '+b'x'*20+b'\ndata: '+b'y'*20+b'\n']:
            with self.assertRaisesRegex(m.ProtocolError,'too_large'):m.SSEDecoder(32).feed(data)
    def test_final_metadata_does_not_duplicate_text(self):
        c=m.TextCollector();c.accept({'data':'{"type":"delta","text":"Halo"}'})
        c.accept({'data':'{"type":"final","text":"Halo"}'});c.accept({'data':'{"type":"delta","text":"stale"}'})
        self.assertEqual(c.text,'Halo');self.assertEqual(c.status,'completed')
    def test_error_and_stop_are_not_success(self):
        for kind in ['error','stopped']:
            c=m.TextCollector();c.accept({'data':'{"type":"'+kind+'"}'});self.assertEqual(c.status,kind);self.assertTrue(c.finished)
    def test_tool_fragments_not_rendered_as_answer_and_output_bounded(self):
        c=m.TextCollector(3);self.assertEqual(c.accept({'data':'{"choices":[{"delta":{"tool_calls":[{"id":"a"}]}}]}'}),'')
        with self.assertRaisesRegex(m.ProtocolError,'output_too_large'):c.accept({'data':'{"type":"delta","text":"long"}'})
    def event(self,payload):return {'data':json.dumps(payload,ensure_ascii=False)}
    def test_inline_tags_opt_in_preserves_literal_code_by_default(self):
        source='print("<think>literal</think>")'
        c=m.TextCollector()
        self.assertEqual(c.accept(self.event({'type':'delta','text':source})),source)
        c.accept({'data':'[DONE]'})
        self.assertEqual(c.text,source);self.assertEqual(c.reasoning,'')
    def test_every_tag_split_and_single_byte_sse_utf8(self):
        content='<think>Analisis 🌍</think>Jawaban ✅<think>lagi</think>!'
        for split in range(len(content)+1):
            with self.subTest(split=split):
                c=m.TextCollector(parse_think_tags=True);d=m.SSEDecoder();answer=[];reasoning=[]
                payloads=[{'choices':[{'delta':{'content':piece}}]} for piece in (content[:split],content[split:])]
                raw=(''.join('data: '+json.dumps(p,ensure_ascii=False)+'\r\n\r\n' for p in payloads)+'data: [DONE]\n\n').encode()
                for byte in raw:
                    for event in d.feed(bytes([byte])):
                        answer.append(c.accept(event));reasoning.append(c.last_reasoning_delta)
                self.assertFalse(d.finish()['incomplete_event'])
                self.assertEqual(''.join(answer),'Jawaban ✅!');self.assertEqual(c.text,'Jawaban ✅!')
                self.assertEqual(''.join(reasoning),'Analisis 🌍lagi');self.assertEqual(c.reasoning,'Analisis 🌍lagi')
                self.assertEqual(c.status,'completed')
    def test_each_content_character_is_its_own_event(self):
        c=m.TextCollector(parse_think_tags=True)
        for char in '<think>private</think>public':c.accept(self.event({'type':'delta','text':char}))
        c.accept({'data':'[DONE]'})
        self.assertEqual(c.text,'public');self.assertEqual(c.reasoning,'private')
    def test_structured_reasoning_channels_and_incremental_delta(self):
        cases=[{'choices':[{'delta':{'reasoning_content':'secret','content':'answer'}}]},
               {'choices':[{'delta':{'reasoning':'secret','content':'answer'}}]},
               {'type':'delta','reasoning_content':'secret','text':'answer'},
               {'type':'delta','reasoning':'secret','text':'answer'}]
        for payload in cases:
            c=m.TextCollector();self.assertEqual(c.accept(self.event(payload)),'answer')
            self.assertEqual(c.last_reasoning_delta,'secret');self.assertEqual(c.reasoning,'secret')
            c.accept(self.event({'choices':[{'delta':{'tool_calls':[]}}]}))
            self.assertEqual(c.last_reasoning_delta,'')
        for kind in ['reasoning','reasoning.delta','response.reasoning.delta','response.reasoning_text.delta','response.reasoning_summary_text.delta']:
            c=m.TextCollector();self.assertEqual(c.accept(self.event({'type':kind,'delta':'secret'})),'')
            self.assertEqual(c.text,'');self.assertEqual(c.reasoning,'secret')
    def test_unfinished_think_never_success_or_answer(self):
        terminals=[{'data':'[DONE]'},self.event({'type':'final'}),self.event({'type':'response.completed'})]
        for terminal in terminals:
            c=m.TextCollector(parse_think_tags=True)
            c.accept(self.event({'type':'delta','text':'<think>secret</thi'}));c.accept(terminal)
            self.assertTrue(c.finished);self.assertEqual(c.status,'incomplete_reasoning')
            self.assertEqual(c.text,'');self.assertEqual(c.reasoning,'secret</thi')
            c.accept(self.event({'type':'delta','text':'stale'}));self.assertEqual(c.last_reasoning_delta,'')
    def test_eof_missing_terminal_and_partial_tag(self):
        c=m.TextCollector(parse_think_tags=True);c.accept(self.event({'type':'delta','text':'answer<thi'}))
        self.assertEqual(c.finish(),'<thi');self.assertEqual(c.text,'answer<thi');self.assertEqual(c.status,'incomplete')
        c=m.TextCollector(parse_think_tags=True);c.accept(self.event({'type':'delta','text':'<think>secret'}));c.finish()
        self.assertEqual(c.status,'incomplete_reasoning');self.assertEqual(c.text,'')
    def test_terminal_partial_answer_and_error_stop_flush(self):
        for kind in ['error','stopped']:
            c=m.TextCollector(parse_think_tags=True);c.accept(self.event({'type':'delta','text':'<think>secret</th'}))
            c.accept(self.event({'type':kind}));self.assertEqual(c.status,kind)
            self.assertEqual(c.text,'');self.assertEqual(c.reasoning,'secret</th')
        c=m.TextCollector(parse_think_tags=True);c.accept(self.event({'type':'delta','text':'literal <'}))
        self.assertEqual(c.accept({'data':'[DONE]'}),'<');self.assertEqual(c.status,'completed')
        self.assertEqual(c.text,'literal <')
    def test_reasoning_cap_independent_and_atomic(self):
        c=m.TextCollector(3,max_reasoning_chars=4,parse_think_tags=True)
        c.accept(self.event({'type':'delta','text':'<think>four</think>yes'}))
        self.assertEqual(c.text,'yes');self.assertEqual(c.reasoning,'four')
        with self.assertRaisesRegex(m.ProtocolError,'reasoning_too_large'):
            c.accept(self.event({'choices':[{'delta':{'reasoning_content':'!'}}]}))
        self.assertEqual(c.status,'error');self.assertTrue(c.finished)
        self.assertEqual(c.reasoning,'four');self.assertEqual(c.text,'yes')
        c=m.TextCollector(1,max_reasoning_chars=2)
        with self.assertRaisesRegex(m.ProtocolError,'reasoning_too_large'):
            c.accept(self.event({'type':'delta','text':'a','reasoning':'big'}))
        self.assertEqual(c.text,'');self.assertEqual(c.reasoning,'')
    def test_pending_delimiter_still_respects_caps_at_terminal(self):
        for source,reasoning_limit,answer_limit in [('x<',20,1),('<think>x</thi',1,20)]:
            c=m.TextCollector(answer_limit,max_reasoning_chars=reasoning_limit,parse_think_tags=True)
            c.accept(self.event({'type':'delta','text':source}))
            with self.assertRaisesRegex(m.ProtocolError,'too_large'):c.accept({'data':'[DONE]'})
            self.assertEqual(c.status,'error');self.assertTrue(c.finished)
            self.assertNotIn('x</thi',c.text)
    def test_invalid_event_payloads_and_text(self):
        for value,message in [('broken','invalid_event_json'),('[]','invalid_event_payload'),
                              ('{"type":"delta","text":{}}','invalid_text_delta')]:
            with self.assertRaisesRegex(m.ProtocolError,message):m.TextCollector().accept({'data':value})
    def test_invalid_reasoning_and_zero_caps(self):
        c=m.TextCollector(0,max_reasoning_chars=0)
        c.accept(self.event({'choices':[{'delta':{'content':None,'reasoning_content':None}}]}))
        with self.assertRaisesRegex(m.ProtocolError,'invalid_reasoning_delta'):
            c.accept(self.event({'type':'delta','reasoning':{'text':'secret'}}))
        with self.assertRaisesRegex(m.ProtocolError,'invalid_output_limit'):m.TextCollector(max_reasoning_chars=-1)
    def test_prefix_only_reasoning_preserves_answer_tags_at_every_split(self):
        source='<think>private</think>```\n<think>literal</think>\n```<thi'
        expected='```\n<think>literal</think>\n```<thi'
        for split in range(len(source)+1):
            c=m.TextCollector(parse_think_tags=True,think_prefix_only=True)
            for chunk in [source[:split],source[split:]]:c.accept(self.event({'type':'delta','text':chunk}))
            c.accept({'data':'[DONE]'})
            self.assertEqual(c.reasoning,'private');self.assertEqual(c.text,expected);self.assertEqual(c.status,'completed')
    def test_history_keeps_latest_and_system_with_whole_tool_turn(self):
        system={'role':'system','content':'rules'};old={'role':'user','content':'old'};call={'role':'assistant','tool_calls':[{'id':'c'}]};result={'role':'tool','tool_call_id':'c','content':'ok'};new={'role':'user','content':'new'}
        self.assertEqual(m.trim_history([system,old,call,result,new],2,counter=lambda x:1),[system,new])
        self.assertEqual(m.trim_history([system,old,call,result,new],5,counter=lambda x:1),[system,old,call,result,new])
    def test_orphan_tools_and_incomplete_pairs_refused(self):
        for messages in [[{'role':'user','content':'x'},{'role':'tool','tool_call_id':'missing'}],[{'role':'user','content':'x'},{'role':'assistant','tool_calls':[{'id':'c'}]}]]:
            with self.assertRaises(m.ProtocolError):m.trim_history(messages,1000)
    def test_too_large_main_request_not_silently_removed(self):
        with self.assertRaisesRegex(m.ProtocolError,'latest_turn_too_large'):m.trim_history([{'role':'system','content':'x'},{'role':'user','content':'y'*1000}],100)

if __name__=='__main__':unittest.main()
