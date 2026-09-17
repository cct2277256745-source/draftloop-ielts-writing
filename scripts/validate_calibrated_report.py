#!/usr/bin/env python3
"""Opt-in synthetic live-provider check. Prints no credentials or private RAG text."""
import json
from pathlib import Path
import sys
import time
import os
import threading

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app.product_composition.local_browser import BrowserRuntime
from app.application.local_task2 import LocalTask2Foundation
from app.core.providers import OpenAICompatibleTransport,ProviderCallResult
from app.core.submission import digest


class ValidationTransport:
    """Exact-request replay for this synthetic diagnostic only, never production."""
    def __init__(self,root):
        self.transport=OpenAICompatibleTransport();self.path=root/'provider-cache.json'
        self.lock=threading.Lock();self.hits=0
        self.cache=json.loads(self.path.read_text()) if self.path.exists() else {}
    def call(self,contract,request):
        key=digest({'messages':request.messages,'name':request.display_name,
            'model':contract.snapshot.model_id,'maxTokens':request.max_tokens,
            **({'reasoningEffort':request.reasoning_effort} if request.reasoning_effort else {})})
        with self.lock:
            if key in self.cache:
                self.hits+=1;return ProviderCallResult(content=self.cache[key])
        result=self.transport.call(contract,request)
        if result.ok:
            with self.lock:
                self.cache[key]=result.content
                fd=os.open(self.path,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
                with os.fdopen(fd,'w') as out: json.dump(self.cache,out,ensure_ascii=False)
        return result


def main():
    root=Path('.scratch/ui-report-redesign/live-validation')
    root.mkdir(parents=True,exist_ok=True)
    transport=ValidationTransport(root)
    def foundation(request):
        return LocalTask2Foundation(runtime.models.snapshot(),transport=transport,
            on_checkpoint=runtime.checkpoint,rag_runtime=runtime.rag)(request)
    runtime=BrowserRuntime(root,foundation=foundation)
    seen=set()
    original=runtime.checkpoint
    def checkpoint(event):
        key=(event.get('stage'),event.get('criterion'),event.get('outcome'),event.get('attempt'))
        if key not in seen:
            seen.add(key)
            print(json.dumps({k:v for k,v in event.items() if k in {
                'stage','criterion','outcome','attempt','initialBand','auditBand','absoluteDifference','rescoreTriggered','reason','acceptedCount','failureLocation'}},ensure_ascii=False),flush=True)
        original(event)
    runtime.checkpoint=checkpoint
    started=time.monotonic()
    try:
        fixture=json.loads(Path('tests/fixtures/browser-task2-local.json').read_text())
        token=runtime.login()
        _,created=runtime.dispatch('POST','/submissions',token,{'taskType':'task2',
            'question':fixture['question'],'candidateScript':fixture['candidateScript'],'targetBand':7.5},
            'calibrated-report-'+str(time.time_ns()))
        job=runtime.platform.service.run_one_job('calibration-live-check',runtime.processor,lease_seconds=1800)
        sid=created['submission']['submissionId']
        _,workspace=runtime.dispatch('GET','/workspaces/'+sid,token,{},'')
        report=workspace['presentation']['report']
        summary={'scope':'SYNTHETIC_TEXT_LIVE_PROVIDER_AND_REAL_PRIVATE_RAG_NOT_ACCURACY_EVALUATION',
            'state':workspace['semanticResult']['state'],'failureCode':workspace['presentation']['failureCode'],
            'elapsedSeconds':round(time.monotonic()-started,1),'exactRequestReplayHits':transport.hits,'runtimeRag':workspace['rag']['status'],
            'calibrationAudit':report.get('calibrationAudit') if report else None,
            'paragraphCount':len(report.get('detailedReport',{}).get('paragraphs',[])) if report else 0,
            'priorityCount':len(report.get('detailedReport',{}).get('priorities',[])) if report else 0,
            'lockedScoreSha256':report['lockedScoreSha256'] if report else None}
        (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
        print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
        if report:
            # The fixture is authored for this check, not a learner's private submission.
            (root/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        return 0 if report and summary['paragraphCount']==5 else 2
    finally:
        runtime.rag.close();runtime.platform.close()

if __name__=='__main__': raise SystemExit(main())
