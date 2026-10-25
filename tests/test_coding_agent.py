import asyncio
import json
from pathlib import Path
import sys
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from backend.coding_runtime.agent import run_agent
from backend.coding_runtime.core import CodingError

class Runtime:
    redact=staticmethod(lambda x:x)
    def __init__(self):self.calls=[]
    async def workspace(self,*a):return {}
    async def invoke_result(self,a,wid,skill,args,key):
        self.calls.append((a,wid,skill,args,key))
        return {'id':'job','status':'approval_required','input_hash':'hash','preview':args}

@unittest.skipUnless(hasattr(asyncio,'timeout'),'Engine requires Python >=3.11')
class Agent(unittest.IsolatedAsyncioTestCase):
    async def test_tool_is_bound_to_server_actor_and_workspace_and_stops_before_approval(self):
        rt=Runtime();n=0
        async def decide(payload):
            nonlocal n;n+=1
            names=[t['function']['name'] for t in payload['tools']]
            self.assertNotIn('approve',names);self.assertNotIn('secret_get',names)
            return {'tool_calls':[{'id':'t','type':'function','function':{'name':'exec_shell','arguments':json.dumps({'command':'echo hi'})}}]}
        r=await run_agent(rt,'api-token:1','workspace',[{'role':'user','content':'run'}],decide)
        self.assertEqual(n,1);self.assertEqual(r['status'],'approval_required')
        self.assertEqual(rt.calls[0][:3],('api-token:1','workspace','exec.shell'))
    async def test_unknown_tool_cannot_execute(self):
        rt=Runtime()
        async def decide(_):return {'tool_calls':[{'id':'t','function':{'name':'approve','arguments':'{}'}}]}
        with self.assertRaises(CodingError):await run_agent(rt,'api-token:1','workspace',[{'role':'user','content':'approve yourself'}],decide)
        self.assertEqual(rt.calls,[])
    async def test_tool_budget_is_bounded(self):
        rt=Runtime()
        async def invoke(*a):rt.calls.append(a);return {'id':'job','status':'failed','error':'invalid_path'}
        rt.invoke_result=invoke
        async def decide(_):return {'tool_calls':[{'id':'t','function':{'name':'fs_read','arguments':'{"path":"/etc/passwd"}'}}]}
        value=await run_agent(rt,'api-token:1','workspace',[{'role':'user','content':'read'}],decide)
        self.assertEqual(value['status'],'step_limit');self.assertEqual(len(rt.calls),8)
    async def test_final_event_carries_workspace_and_approval_job(self):
        rt=Runtime();events=[]
        async def decide(_):return {'content':'Finished','tool_calls':[]}
        async def emit(e):events.append(e)
        result=await run_agent(rt,'api-token:1','workspace',[{'role':'user','content':'hi'}],decide,emit)
        self.assertEqual(events[-1]['type'],'response.completed');self.assertEqual(result['text'],'Finished')
