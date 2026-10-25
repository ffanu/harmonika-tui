import io,json,sys,unittest,urllib.error
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime/tools'))
import ai_integration as m
import ai_adapters as adapters
import tui_gateway as gateway

class CustomLLM(m.AIAdapter):
    """Deliberately no HTTP or provider-wire dependency."""
    def __init__(self):self.calls=[];self.closed=False
    def stream(self,request,cancel):
        self.calls.append(request.to_payload())
        try:
            yield m.AIEvent('delta',{'reasoning':'synthetic reasoning'})
            yield m.AIEvent('delta',{'text':'custom answer'})
            yield m.AIEvent('usage',{'tokens':{'total_tokens':3}})
            yield m.AIEvent('completed',{'finish_reason':'stop'})
        finally:self.closed=True
    def complete(self,request,cancel):self.calls.append(request.to_payload());return m.AIResponse(text='custom answer')

class Failing(CustomLLM):
    def stream(self,request,cancel):
        raise m.IntegrationError('network_unavailable',retryable=True)
        yield
    def complete(self,request,cancel):raise m.IntegrationError('network_unavailable',retryable=True)

class Integration(unittest.TestCase):
    def request(self,**values):return m.AIRequest.from_payload({'schema_version':1,'messages':[{'role':'user','content':'hello'}],**values})
    def test_request_detaches_original_preserves_tool_history(self):
        payload={'schema_version':1,'messages':[{'role':'system','content':'private'},{'role':'user','content':'hi'},{'role':'assistant','content':None,'tool_calls':[{'id':'a','type':'function','function':{'name':'read','arguments':'{}'}}]},{'role':'tool','tool_call_id':'a','content':'value'}],'tools':[{'type':'function','function':{'name':'read'}}],'generation':{'temperature':.2},'context':{'conversation_id':'session-one'}}
        request=m.AIRequest.from_payload(payload);payload['messages'][0]['content']='changed'
        self.assertEqual(request.messages[0]['content'],'private');self.assertEqual(request.to_payload()['messages'][-1]['tool_call_id'],'a')
        self.assertNotIn('private',repr(request));self.assertNotIn('api_key',request.to_payload())
    def test_reserved_transport_fields_and_invalid_contract(self):
        for kwargs in [{'schema_version':2},{'generation':{'api_key':'private'}},{'context':{'endpoint':'http://remote'}},{'messages':[{'role':'root','content':'hi'}]}]:
            with self.subTest(kwargs=list(kwargs)),self.assertRaises(m.IntegrationError):self.request(**kwargs)
    def test_swap_custom_llm_without_gateway_or_ui_change(self):
        custom=CustomLLM();engine=gateway.Engine('fixture',{'fallback_enabled':False},wrapper=m.AIIntegration(custom))
        events=list(engine.stream({'model':'harmonika-ai','messages':[{'role':'user','content':'hello'}],'stream':True}))
        answer=''.join(e['choices'][0]['delta'].get('content','') for e in events if isinstance(e,dict) and e.get('choices'))
        self.assertEqual(answer,'custom answer');self.assertEqual(events[-1],'[DONE]');self.assertTrue(custom.closed)
        self.assertEqual(custom.calls[0]['schema_version'],1);self.assertNotIn('model',custom.calls[0]);self.assertNotIn('api_key',custom.calls[0])
    def test_monotonic_ids_and_exactly_one_terminal(self):
        events=list(m.AIIntegration(CustomLLM()).stream(self.request(context={'request_id':'request-one'})))
        self.assertEqual([e.sequence for e in events],[1,2,3,4]);self.assertTrue(all(e.request_id=='request-one' for e in events));self.assertEqual(sum(e.type=='completed' for e in events),1)
    def test_pre_output_failure_can_switch(self):
        fallback=CustomLLM();wrapper=m.AIIntegration(Failing(),fallback)
        self.assertEqual(list(wrapper.stream(self.request()))[-1].type,'completed');self.assertEqual(wrapper.complete(self.request()).text,'custom answer')
    def test_partial_output_prevents_switch(self):
        class Partial(CustomLLM):
            def stream(self,request,cancel):yield m.AIEvent('delta',{'text':'partial'});raise m.IntegrationError('network_unavailable',True)
        fallback=CustomLLM()
        with self.assertRaises(m.IntegrationError):list(m.AIIntegration(Partial(),fallback).stream(self.request()))
        self.assertEqual(fallback.calls,[])
    def test_cancellation_closes_source_no_fallback(self):
        primary=CustomLLM();fallback=CustomLLM();cancel=m.Cancellation();stream=m.AIIntegration(primary,fallback).stream(self.request(),cancel)
        next(stream);cancel.cancel()
        with self.assertRaisesRegex(m.IntegrationError,'request_cancelled'):next(stream)
        self.assertTrue(primary.closed);self.assertEqual(fallback.calls,[])
    def test_wire_consumer_close_propagates(self):
        primary=CustomLLM();stream=m.openai_stream(m.AIIntegration(primary).stream(self.request()));next(stream);stream.close();self.assertTrue(primary.closed)
    def test_terminal_before_extra_event_rejected_without_public_success(self):
        class Bad(CustomLLM):
            def stream(self,request,cancel):yield m.AIEvent('completed');yield m.AIEvent('delta',{'text':'late'})
        events=[]
        with self.assertRaisesRegex(m.IntegrationError,'event_after_completion'):
            for e in m.AIIntegration(Bad()).stream(self.request()):events.append(e)
        self.assertEqual(events,[])
    def test_registry_custom_selection(self):
        registry=adapters.AdapterRegistry();custom=CustomLLM();registry.register('company',lambda **_:custom)
        wrapper=adapters.build_integration('private',{'primary_adapter':'company','fallback_enabled':False},registry=registry)
        self.assertEqual(wrapper.complete(self.request()).text,'custom answer')
        with self.assertRaisesRegex(m.IntegrationError,'unknown_adapter'):registry.create('absent')
    def test_teacher_and_local_use_same_request_different_transport(self):
        calls=[]
        def open_request(req):
            calls.append(req)
            return io.BytesIO(json.dumps({'choices':[{'message':{'role':'assistant','content':'answer'},'finish_reason':'stop'}]}).encode())
        ready=lambda:{'ready':True,'model':'installed-local','tools':True}
        request=self.request(generation={'max_tokens':32},context={'request_id':'private-meta'})
        for primary in ['teacher','ollama','custom']:
            config={'primary_adapter':primary,'fallback_enabled':False,'custom_model':'own-model'}
            wrapper=adapters.build_integration('private-key',config,open_request,ready);wrapper.complete(request)
        self.assertEqual([json.loads(r.data)['model'] for r in calls],['harmonika-ai','installed-local','own-model'])
        self.assertEqual(calls[0].get_header('Authorization'),'Bearer private-key')
        self.assertEqual([r.get_header('Authorization') for r in calls[1:]],['Bearer local','Bearer local'])
        self.assertTrue(all('private-meta' not in r.data.decode() for r in calls))
    def test_custom_remote_endpoint_or_unsupported_tools_refused(self):
        for url in ['https://remote.example/v1','http://127.0.0.1@remote.example/v1','http://127.0.0.1/v1?target=remote']:
            with self.subTest(url=url),self.assertRaises(m.IntegrationError):adapters.build_integration('private',{'primary_adapter':'custom','fallback_enabled':False,'custom_model':'model','custom_base_url':url})
        wrapper=adapters.build_integration('private',{'primary_adapter':'custom','fallback_enabled':False,'custom_model':'model'})
        with self.assertRaisesRegex(m.IntegrationError,'custom_tools_unavailable'):wrapper.complete(self.request(tools=[{'type':'function','function':{'name':'read'}}]))
    def test_invalid_custom_response_fields_refused(self):
        for fields in [{'text':{}},{'reasoning':[]},{'tool_calls':{}},{'usage':[]},{'finish_reason':'bad\ncode'},{'text':'x'*200001}]:
            with self.subTest(fields=list(fields)),self.assertRaises(m.IntegrationError):m.AIResponse(**fields)
    def test_extension_factory_does_not_receive_teacher_key(self):
        registry=adapters.AdapterRegistry();seen=[]
        def factory(**context):seen.append(context['key']);return CustomLLM()
        registry.register('company',factory)
        adapters.build_integration('private-teacher-key',{'primary_adapter':'company','fallback_enabled':False},registry=registry)
        self.assertEqual(seen,[''])
        adapters.build_integration('private-teacher-key',{'primary_adapter':'company','fallback_enabled':False},registry=registry,credential_resolver=lambda name:'separate-company-key')
        self.assertEqual(seen[-1],'separate-company-key')
    def test_close_propagates_with_upstream_reference_retained(self):
        primary=CustomLLM();upstream=m.AIIntegration(primary).stream(self.request());edge=m.openai_stream(upstream)
        next(edge);edge.close();self.assertTrue(primary.closed)
    def test_terminal_then_error_never_yields_done_or_fails_over(self):
        class Bad(CustomLLM):
            def stream(self,request,cancel):yield m.AIEvent('completed');raise m.IntegrationError('network_unavailable',True)
        fallback=CustomLLM();events=[]
        with self.assertRaises(m.IntegrationError):
            for event in m.openai_stream(m.AIIntegration(Bad(),fallback).stream(self.request())):events.append(event)
        self.assertNotIn('[DONE]',events);self.assertEqual(fallback.calls,[])
    def test_reasoning_and_tool_delta_already_commit_backend(self):
        for data in [{'reasoning':'thinking'},{'tool_calls':[{'id':'a'}]}]:
            class Partial(CustomLLM):
                def stream(self,request,cancel):yield m.AIEvent('delta',data);raise m.IntegrationError('network_unavailable',True)
            fallback=CustomLLM()
            with self.assertRaises(m.IntegrationError):list(m.AIIntegration(Partial(),fallback).stream(self.request()))
            self.assertEqual(fallback.calls,[])
    def test_package_import_without_tools_path(self):
        import subprocess
        code="from runtime.tools.ai_integration import AIRequest; from runtime.tools.ai_adapters import build_integration; from runtime.tools.tui_gateway import Engine; request=AIRequest.from_payload({'messages':[{'role':'user','content':'hi'}]}); assert request.schema_version==1; build_integration('fixture',{'fallback_enabled':False}); print('ok')"
        result=subprocess.run([sys.executable,'-c',code],cwd=Path(__file__).resolve().parents[1],capture_output=True,text=True,timeout=5)
        self.assertEqual(result.returncode,0,result.stderr);self.assertEqual(result.stdout.strip(),'ok')


class MentorContract(unittest.TestCase):
    """Synthetic mentor-labeled scenarios, NOT captured/verified provider formats.

    All four labels deliberately use the same generic Harmonika contract. No
    direct Codex/OpenCode/GoogleMode/WhatsApp endpoint is registered or contacted.
    """
    def request(self):return m.AIRequest([{'role':'user','content':'fixture'}])
    def test_four_mentor_scenarios_share_generic_contract(self):
        cases={
            'Codex':{'text':'```python\nprint("<think>literal</think>")\n```'},
            'OpenCode':{'tool_calls':[{'index':0,'id':'call-fixture','type':'function','function':{'name':'read_file','arguments':'{"path":'}}]},
            'GoogleMode':{'reasoning':'synthetic reasoning','text':'synthetic answer'},
            'WhatsAppAI':{'text':'Pesan fixture 🌍; notice tool dan thinking tetap teks biasa.'},
        }
        for label,data in cases.items():
            with self.subTest(mentor_fixture=label):
                class Fixture(CustomLLM):
                    def stream(self,request,cancel):
                        yield m.AIEvent('delta',data)
                        if 'tool_calls' in data:yield m.AIEvent('delta',{'tool_calls':[{'index':0,'function':{'arguments':'"README.md"}'}}]})
                        yield m.AIEvent('completed',{'finish_reason':'tool_calls' if 'tool_calls' in data else 'stop'})
                engine=gateway.Engine('fixture',{'fallback_enabled':False},wrapper=m.AIIntegration(Fixture()))
                events=list(engine.stream({'messages':[{'role':'user','content':'fixture'}],'stream':True}))
                answer=''.join(x['choices'][0]['delta'].get('content','') for x in events if isinstance(x,dict) and x.get('choices'))
                self.assertEqual(answer,data.get('text',''));self.assertEqual(events[-1],'[DONE]')
                for x in events:
                    if isinstance(x,dict):self.assertEqual(x['model'],'harmonika-ai')
        self.assertEqual(set(adapters.REGISTRY._factories),{'teacher','ollama','custom'})
    def test_unknown_and_mixed_event_fields_are_rejected(self):
        cases=[m.AIEvent('delta',{'text':'answer','notice':'hidden'}),
               m.AIEvent('delta',{'thinking':'secret'}),
               m.AIEvent('delta',{'tool_calls':[{'notice':'secret'}]}),
               m.AIEvent('delta',{'tool_calls':[{'index':True}]}),
               m.AIEvent('delta',{'tool_calls':[{'function':{'arguments':{}}}]}),
               m.AIEvent('usage',{'tokens':{},'reasoning':'secret'}),
               m.AIEvent('usage',{'tokens':{'total_tokens':'secret'}}),
               m.AIEvent('completed',{'text':'answer'}),m.AIEvent('delta',{'text':[]})]
        for bad in cases:
            class Bad(CustomLLM):
                def stream(self,request,cancel):yield bad;yield m.AIEvent('completed')
            with self.subTest(event_type=bad.type),self.assertRaises(m.IntegrationError):list(m.AIIntegration(Bad()).stream(self.request()))
    def test_complete_tool_calls_require_full_metadata(self):
        for call in [{'id':'fragment'},{'index':0,'function':{'arguments':'{}'}},{'id':'a','type':'function','function':{'name':'read','arguments':{},'thinking':'secret'}}]:
            with self.assertRaises(m.IntegrationError):m.AIResponse(tool_calls=[call])
        call={'id':'a','type':'function','function':{'name':'read','arguments':'{}'}}
        self.assertEqual(m.AIResponse(tool_calls=[call]).tool_calls,[call])
    def test_wire_legacy_thinking_and_reasoning_conflicts_fail_closed(self):
        for delta in [{'content':'answer','thinking':'secret'},
                      {'content':'answer','notice':'tool notice'},
                      {'reasoning':'one','reasoning_content':'two'},
                      {'tool_calls':[{'notice':'secret'}]}]:
            with self.assertRaises(m.IntegrationError):m.Normalizer().accept({'choices':[{'delta':delta}]})
        value,_=m.Normalizer().accept({'choices':[{'delta':{'reasoning':'same','reasoning_content':'same','content':'answer'}}]})
        self.assertEqual(value['choices'][0]['delta'],{'content':'answer','reasoning_content':'same'})
    def test_request_rejects_abnormal_roles_content_and_mixed_notices(self):
        for message in [{'role':[],'content':'x'},{'role':'user','content':['wrong']},
                        {'role':'assistant','content':'x','thinking':'secret'},
                        {'role':'assistant','content':'x','notice':'secret'},
                        {'role':'assistant','content':None,'reasoning':'one','reasoning_content':'two'}]:
            with self.assertRaises(m.IntegrationError):m.AIRequest([message])
        for body in [{'n':True},{'stream':'true'},{'metadata':[]}]:
            with self.assertRaises(m.IntegrationError):m.AIRequest.from_openai({'messages':[{'role':'user','content':'x'}],**body})
    def test_raw_adapter_and_cleanup_errors_are_redacted(self):
        class Broken(CustomLLM):
            def stream(self,request,cancel):raise RuntimeError('fixture-secret-value');yield
            def complete(self,request,cancel):raise RuntimeError('fixture-secret-value')
        for action in [lambda:list(m.AIIntegration(Broken()).stream(self.request())),lambda:m.AIIntegration(Broken()).complete(self.request())]:
            with self.assertRaisesRegex(m.IntegrationError,'^adapter_unavailable$') as caught:action()
            self.assertNotIn('fixture-secret-value',str(caught.exception))
        error=m.IntegrationError('Authorization: Bearer fixture-secret',True)
        self.assertEqual(str(error),'adapter_error');self.assertFalse(error.retryable)
        self.assertEqual(str(m.IntegrationError('upstream_http_401')),'upstream_http_401')
        class BadClose(CustomLLM):
            def stream(self,request,cancel):
                try:yield m.AIEvent('delta',{'text':'answer'})
                finally:raise RuntimeError('fixture-secret-value')
        stream=m.AIIntegration(BadClose()).stream(self.request());next(stream)
        with self.assertRaisesRegex(m.IntegrationError,'^adapter_close_failed$'):stream.close()

    def test_initial_thinking_followed_by_literal_code_every_split(self):
        source='<think>private</think>```\n<think>literal</think>\n```<thi'
        for split in range(len(source)+1):
            normal=m.Normalizer();answer=[];reasoning=[]
            for piece in [source[:split],source[split:]]:
                value,_=normal.accept({'choices':[{'delta':{'content':piece}}]})
                answer.append(value['choices'][0]['delta'].get('content',''))
                reasoning.append(value['choices'][0]['delta'].get('reasoning_content',''))
            tail=normal.end();answer.append(tail.get('content',''));reasoning.append(tail.get('reasoning_content',''))
            self.assertEqual(''.join(answer),'```\n<think>literal</think>\n```<thi')
            self.assertEqual(''.join(reasoning),'private')

    def test_failing_adapter_cannot_mutate_fallback_request_or_caller(self):
        class Mutating(CustomLLM):
            def stream(self,request,cancel):
                request.messages[0]['content']='changed'
                request.context['request_id']='changed'
                raise m.IntegrationError('network_unavailable',True)
                yield
            def complete(self,request,cancel):
                request.messages[0]['content']='changed'
                raise m.IntegrationError('network_unavailable',True)
        request=self.request();original=request.to_payload()
        fallback=CustomLLM();events=list(m.AIIntegration(Mutating(),fallback).stream(request))
        self.assertEqual(fallback.calls[0],original);self.assertEqual(request.to_payload(),original)
        self.assertTrue(all(e.request_id==original['context']['request_id'] for e in events))
        fallback=CustomLLM();m.AIIntegration(Mutating(),fallback).complete(request)
        self.assertEqual(fallback.calls[0],original);self.assertEqual(request.to_payload(),original)
    def test_total_tool_output_is_bounded_even_without_text(self):
        class Large(CustomLLM):
            def stream(self,request,cancel):
                for _ in range(65):yield m.AIEvent('delta',{'tool_calls':[{'index':0,'function':{'arguments':'x'*130000}}]})
                yield m.AIEvent('completed')
        with self.assertRaisesRegex(m.IntegrationError,'adapter_output_too_large'):
            for _ in m.AIIntegration(Large()).stream(self.request()):pass
    def test_empty_or_legacy_only_wire_payload_cannot_claim_success(self):
        for payload in [{},{'thinking':'secret'},{'choices':[]}]:
            with self.assertRaises(m.IntegrationError):m.Normalizer().accept(payload)

    def test_request_rejects_invalid_tool_shapes(self):
        for kwargs in [{'tools':[{}]},{'tools':[{'type':'function','function':{'name':'read','parameters':[]}}]},
                       {'messages':[{'role':'tool','content':'result'}]},
                       {'messages':[{'role':'assistant','content':None,'tool_calls':[{'id':'partial'}]}]}]:
            with self.assertRaises(m.IntegrationError):m.AIRequest.from_payload({'messages':[{'role':'user','content':'fixture'}],**kwargs})

    def test_pending_completion_is_detached_from_adapter_mutation(self):
        class Mutating(CustomLLM):
            def stream(self,request,cancel):
                data={'finish_reason':'stop'}
                yield m.AIEvent('completed',data)
                data['notice']='private'
        events=list(m.AIIntegration(Mutating()).stream(self.request()))
        self.assertEqual(events[0].data,{'finish_reason':'stop'})
    def test_complete_revalidates_mutated_response_and_rejects_raw_dict(self):
        for response in [dict(text='answer'),m.AIResponse(text='answer')]:
            if isinstance(response,m.AIResponse):response.tool_calls.append({'notice':'private'})
            class Bad(CustomLLM):
                def complete(self,request,cancel):return response
            with self.assertRaises(m.IntegrationError):m.AIIntegration(Bad()).complete(self.request())

    def test_tool_fragments_allow_null_metadata_but_complete_json_is_required(self):
        fragment={'index':0,'id':None,'type':None,'function':{'name':None,'arguments':'{"path":'}}
        m.validate_tools([fragment])
        for arguments in ['{"path":','[]','null']:
            with self.assertRaises(m.IntegrationError):m.AIResponse(tool_calls=[{'id':'a','type':'function','function':{'name':'read','arguments':arguments}}])
