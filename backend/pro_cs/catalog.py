import json
from pathlib import Path
CORE={
'intent.detect.fast':'detect','intent.classify':'detect','intent.multi.detect':'detect','intent.ambiguity.detect':'detect','emotion.detect.realtime':'detect','frustration.detect':'detect','urgency.assess':'detect','crisis.detect':'detect',
'context.build.smart':'plan','context.combine.multi':'plan','context.resolve.reference':'plan','context.relevance.filter':'plan','context.priority.rank':'plan','context.token.optimize':'plan','context.invalidate.stale':'validate',
'antihalluc.fact.check':'validate','antihalluc.verify.before.send':'validate','antihalluc.no.invention':'validate','antihalluc.never.guess':'validate','antihalluc.evidence.required':'validate','antihalluc.cross.reference':'validate','qa.response.check':'validate','qa.fact.check':'validate','qa.privacy.check':'redact','privacy.data.minimize':'redact',
'conv.opening':'plan','conv.closing':'plan','conv.clarify.ask':'plan','conv.handoff.human':'plan','response.generate.natural':'plan','escalate.summary.generate':'plan','escalate.context.transfer':'plan','extract.date.from.file':'date','extract.amount.from.file':'amount','extract.contact.from.file':'contact','extract.location.from.file':'location'}
READERS={'extract.text.from.file','file.read.pdf','file.read.word','file.read.excel','file.read.powerpoint','file.read.csv','file.read.json','file.read.xml','file.read.yaml','file.read.txt','file.read.html','file.read.markdown','file.read.image.jpg','file.read.image.tiff','file.read.image.svg','file.read.zip','file.read.email.eml','file.read.calendar.ics','file.read.vcard','file.read.ebook.epub'}
def entries():
 rows=json.loads((Path(__file__).parent/'catalog.json').read_text())['skills']
 for row in rows:
  sid=row['id'];row['implemented']=sid in CORE or sid in READERS;row['status']='available' if row['implemented'] else 'context_required'
  row['reason']=None if row['implemented'] else 'authorized_action_connector_required' if sid.startswith('action.') else 'consent_and_learning_policy_required' if sid.startswith(('learn.','memory.','privacy.')) else 'model_or_channel_adapter_required'
  row['scope']='bounded_analysis_preview_only';row['auto_send']=False;row['limitations']=['Bounded preview operation; see engine policy']
 return rows
