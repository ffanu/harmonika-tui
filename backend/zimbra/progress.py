"""Bound retry-only agent loops; aborting a client never proves a remote action stopped."""


class ProgressBudget:
    def __init__(self, max_starts=6):
        self.max_starts=max_starts
        self.starts=0
        self.completed_tools=0

    def observe(self,event):
        kind=event.get('type')
        if kind=='step_start':self.starts+=1
        elif kind=='tool_use' and event.get('part',{}).get('state',{}).get('status') in ('completed','error'):
            self.completed_tools+=1;self.starts=0
        elif kind=='text' and event.get('part',{}).get('text','').strip():self.starts=0
        return self.starts>=self.max_starts

    def interruption(self):
        return {'ok':False,'error':'agent_no_progress_budget','execution_state':'needs_reconciliation',
                'safe_to_retry':False,'remote_operation_cancelled':False,'completed_tools':self.completed_tools}
