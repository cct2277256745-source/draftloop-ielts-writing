"""Small, versioned Task 1 commentary sidecar; no frozen corpus mutation.

This is lexical retrieval over attributed original summaries. It contains no
copied scripts, inferred criterion bands, embeddings, or scoring authority.
"""
from collections import Counter
from dataclasses import replace
import json
import math
from pathlib import Path
import re

from .rag_retrieval import RetrievalEvidenceItem, RetrievalEvidencePack, RetrievalProviderError
from .submission import digest

SOURCE = Path(__file__).resolve().parents[1] / 'resources/calibration/task1/commentary-observations.json'
CONTENT_SHA256 = '77094d318e3a9268a8e214416ddb1636132092bd945dcbe107eb064786ec5d28'
RIGHTS = 'ORIGINAL_SUMMARY_WITH_ATTRIBUTION'


def tokens(value):
    return re.findall(r'[a-z]+', value.lower())


def retrieve_task1_references(query, criterion, top_k=5):
    if criterion not in {'TA','CC','LR','GRA'} or not isinstance(query,str) or not query.strip():
        raise RetrievalProviderError('Invalid Task 1 reference query.')
    if isinstance(top_k,bool) or not isinstance(top_k,int) or not 1<=top_k<=5:
        raise RetrievalProviderError('Invalid reference limit.')
    corpus=json.loads(SOURCE.read_text())
    raw={k:v for k,v in corpus.items() if k!='contentSha256'}
    if corpus.get('contentSha256')!=CONTENT_SHA256 or digest(raw)!=CONTENT_SHA256:
        raise RetrievalProviderError('Task 1 reference sidecar changed.')
    documents=[tokens(' '.join([item['criterion'],*item['features'],item['summary']])) for item in corpus['items']]
    terms=set(tokens(query));average=sum(map(len,documents))/len(documents)
    df=Counter(term for document in documents for term in set(document))
    ranked=[]
    for item,document in zip(corpus['items'],documents):
        counts=Counter(document)
        score=sum(math.log(1+(len(documents)-df[t]+.5)/(df[t]+.5))*counts[t]*2.5/
            (counts[t]+1.5*(.25+.75*len(document)/average)) for t in terms if counts[t])
        # Criterion is an advisory rank preference; the shared gate rejects mismatches.
        score+=4 if item['criterion']==criterion else 0
        ranked.append((score,item))
    ranked.sort(key=lambda pair:(-pair[0],pair[1]['id']))
    items=[]
    for rank,(score,item) in enumerate(ranked[:top_k],1):
        provenance=corpus['source']['url']+'#page='+str(item['page'])
        items.append(RetrievalEvidenceItem(
            object_id='task1-commentary-'+item['id'], object_type='TASK1_COMMENTARY_SUMMARY',
            criterion=item['criterion'], features=tuple(['task1',*item['features']]),
            text='Task 1 examiner-commentary observation (original summary; no criterion band): '+item['summary'],
            provenance=provenance,source_artifact_id='ielts-2023-task1-'+item['sample'],
            rights=RIGHTS,usage='PRIVATE_RESEARCH_ONLY',dense_rank=None,bm25_rank=rank,
            dense_retrieval_score=None,bm25_retrieval_score=score,rrf_score=1/(60+rank)))
    pack=RetrievalEvidencePack(tuple(items),digest(query),criterion,CONTENT_SHA256,
        corpus['version'],0,len(documents),0,'')
    return replace(pack,pack_sha256=digest(pack.content(include_hash=False)))
