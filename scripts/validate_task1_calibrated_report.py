#!/usr/bin/env python3
"""Opt-in synthetic chart with real vision, Rubric and Task 1 calibration calls."""
import base64
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.validate_calibrated_report import ValidationTransport
from app.application.local_task1 import LocalTask1Foundation
from app.product_composition.local_browser import BrowserRuntime


def main():
    root=Path('.scratch/ui-report-redesign/task1-live-validation')
    root.mkdir(parents=True,exist_ok=True)
    if not (root/'chart.png').exists():
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        fig,ax=plt.subplots(figsize=(8,5),dpi=140)
        years=[('2000',[60,30,10],-.18,'#224763'),('2020',[40,35,25],.18,'#679587')]
        for year,values,offset,color in years:
            ax.bar([v+offset for v in range(3)],values,.36,label=year,color=color)
            for v,amount in enumerate(values):ax.text(v+offset,amount+1,str(amount)+'%',ha='center')
        ax.set_xticks([0,1,2],['Coal','Natural gas','Renewables']);ax.set_ylim(0,75)
        ax.set_ylabel('Share of electricity generation (%)')
        ax.set_title('Electricity generation in Country A, 2000 and 2020')
        ax.legend(frameon=False);ax.spines[['top','right']].set_visible(False)
        fig.tight_layout();fig.savefig(root/'chart.png');plt.close(fig)
    transport=ValidationTransport(root)
    def foundation(request):
        return LocalTask1Foundation(runtime.models.snapshot(),transport=transport,
            on_checkpoint=runtime.checkpoint,rag_runtime=runtime.rag)(request)
    runtime=BrowserRuntime(root,foundation=foundation)
    original=runtime.checkpoint
    def checkpoint(event):
        print(json.dumps({k:v for k,v in event.items() if k not in {'lockedScoreSha256','errorFingerprint'}},ensure_ascii=False),flush=True)
        original(event)
    runtime.checkpoint=checkpoint
    start=time.monotonic()
    try:
        token=runtime.login()
        _,upload=runtime.dispatch('POST','/uploads',token,{'mediaType':'image/png',
            'dataBase64':base64.b64encode((root/'chart.png').read_bytes()).decode()},'')
        question='The chart compares the shares of electricity generated from coal, natural gas and renewables in Country A in 2000 and 2020. Summarise the information by selecting and reporting the main features, and make comparisons where relevant.'
        essay='''The bar chart compares three sources of electricity in Country A in 2000 and 2020. The figures are given as percentages of total generation.

Overall, coal remained the largest source in both years, although its share declined considerably. By contrast, the proportions generated from natural gas and renewables increased, with renewables recording the larger rise. The gap between coal and the other sources therefore became narrower.

In 2000, coal accounted for 60% of electricity production. This was twice the share of natural gas, at 30%, and six times the figure for renewables, which supplied only 10%. Together, the two smaller sources represented 40% of the total.

By 2020, the proportion for coal had fallen by 20 percentage points to 40%. Natural gas rose modestly to 35%, leaving it only five percentage points below coal. Renewables increased to 25%, a gain of 15 percentage points. Although they still contributed the smallest share, their combined total with natural gas reached 60%, reversing the balance seen in 2000.'''
        _,created=runtime.dispatch('POST','/submissions',token,{'taskType':'task1','question':question,
            'candidateScript':essay,'uploadId':upload['upload']['uploadId'],'targetBand':7},'task1-calibration-'+str(time.time_ns()))
        runtime.platform.service.run_one_job('task1-live-check',runtime.processor,lease_seconds=1800)
        sid=created['submission']['submissionId']
        _,workspace=runtime.dispatch('GET','/workspaces/'+sid,token,{},'')
        report=workspace['presentation']['report']
        summary={'scope':'SYNTHETIC_CHART_AND_TEXT_LIVE_PROVIDER_NOT_ACCURACY_EVALUATION',
            'state':workspace['semanticResult']['state'],'failureCode':workspace['presentation']['failureCode'],
            'semanticFailureCode':workspace['semanticResult'].get('failureCode'),
            'elapsedSeconds':round(time.monotonic()-start,1),'exactRequestReplayHits':transport.hits,
            'calibrationAudit':report.get('calibrationAudit') if report else None,
            'paragraphCount':len(report.get('detailedReport',{}).get('paragraphs',[])) if report else 0}
        (root/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
        (root/'workspace.json').write_text(json.dumps(workspace,ensure_ascii=False))
        print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
        return 0 if report else 2
    finally:
        runtime.rag.close();runtime.platform.close()

if __name__=='__main__':raise SystemExit(main())
