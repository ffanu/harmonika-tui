"""Sequential real shared OCR calls; synthetic fixture results stay in local output."""
from pathlib import Path
import argparse, importlib.util, json, re, time
ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location('exam_ocr',ROOT/'runtime/tools/shared_ocr.py');ocr=importlib.util.module_from_spec(spec);spec.loader.exec_module(ocr)
def normalize(s):return re.sub(r'\s+',' ',s).casefold()
def assess(case,result):
    text=result.get('text','');missing=[s for s in case['expected'] if normalize(s) not in normalize(text)]
    outcome=case['outcome'];checks={}
    if outcome=='error':
        allowed = {'invalid-oversized': {'file_too_large_or_empty'}, 'invalid-text': {'unsupported_file_type'}, 'hard-scan-6-pages': {'ocr_document_limit'}}.get(case['id'], {'ocr_unreadable_document'})
        checks['structured_rejection']=result.get('ok') is False and result.get('error') in allowed
    elif outcome=='empty':checks['no_invented_text']=(result.get('ok') is False and result.get('error')=='ocr_unreadable_document') or (result.get('ok') is True and (not text.strip() or bool(re.fullmatch(r'(?:\[?)(?:no text(?: detected)?|tidak ada teks(?: yang terdeteksi)?)(?:\]?\.?)(?:\s*)',text.strip(),re.I))))
    elif outcome=='uncertain':checks['honest_uncertainty']=(result.get('ok') is False and result.get('error')=='ocr_unreadable_document') or (result.get('ok') is True and (not text.strip() or result.get('review_required') is True))
    else:
        checks['success']=result.get('ok') is True;checks['expected_fields']=not missing
        if 'expected_pages' in case:checks['all_pages_counted']=result.get('page_count')==case['expected_pages']
        checks['local_only']=result.get('processing_mode')=='local_only'
    if case.get('authenticity_not_verified'):checks['no_false_authenticity']=result.get('document_authenticity_verified') is not True
    return {'passed':bool(checks) and all(checks.values()),'checks':checks,'missing_expected_fields':missing if outcome=='text' else []}

def main():
    p=argparse.ArgumentParser();p.add_argument('fixtures',type=Path);p.add_argument('output',type=Path);p.add_argument('--case',action='append');a=p.parse_args();a.output.mkdir(parents=True,exist_ok=True)
    cases=json.loads((a.fixtures/'manifest.json').read_text())['cases'];reports=[]
    for case in cases:
        if a.case and case['id'] not in a.case:continue
        begin=time.monotonic();result=ocr.extract_file(str((a.fixtures/case['file']).resolve()))
        (a.output/(case['id']+'.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2))
        record={'id':case['id'],'level':case['level'],'seconds':round(time.monotonic()-begin,2),'engine':result.get('engine'),'fallback_used':result.get('fallback_used'),'error':result.get('error'),'page_count':result.get('page_count'),**assess(case,result)}
        reports.append(record);(a.output/'matrix.json').write_text(json.dumps(reports,ensure_ascii=False,indent=2));print(json.dumps(record,ensure_ascii=False),flush=True)
    print(json.dumps({'passed':sum(x['passed'] for x in reports),'total':len(reports)}),flush=True)
if __name__=='__main__':main()
