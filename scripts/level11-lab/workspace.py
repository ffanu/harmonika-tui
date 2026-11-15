"""Atlas starter intentionally lacks scope, durability and fairness implementation."""
class WorkspaceStore:
 def __init__(self,path):self.path=str(path)
 def create_ticket(self,owner,project,key,data):raise NotImplementedError()
 def list_tickets(self,owner,project):return 200,{'tickets':[]}
 def update_ticket(self,owner,project,id,revision,data):raise NotImplementedError()
 def submit(self,owner,project,key,data):raise NotImplementedError()
 def get(self,owner,project,id):return 404,{'error':'not_found'}
 def lookup(self,owner,project,key):return 404,{'error':'not_found'}
 def cancel(self,owner,project,id):raise NotImplementedError()
 def tick(self):return False
 def artifact(self,owner,project,id):return 409,{'error':'not_complete'}
 def events(self,owner,project,after=0,limit=50):return 200,{'events':[],'cursor':after}
 def mechanics(self):return {'jobs':0,'units':0,'events':0}
