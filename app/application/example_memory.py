"""Owner-scoped retrieval of hypothetical examples already saved in completed reports."""
from collections import OrderedDict
import math
import re
import threading

from app.core.submission import digest

TOPICS={
    'education':{'education','school','schools','university','universities','students','learning','internships','教育','学校','大学'},
    'employment':{'employment','job','jobs','work','career','skills','training','workplace','就业','工作','技能'},
    'environment':{'environment','pollution','climate','energy','transport','traffic','cars','环境','交通','能源'},
    'technology':{'technology','internet','digital','online','computer','computers','科技','互联网','技术'},
    'health':{'health','exercise','sport','sports','food','diet','健康','运动'},
    'society':{'government','public','community','communities','society','social','政府','社会'},
}


def terms(value):
    tokens=set(re.findall(r'[a-z]{3,}|[\u4e00-\u9fff]{2}',value.lower()))
    tokens.update(key for key,words in TOPICS.items() if tokens.intersection(words))
    return tokens-{'the','and','that','this','with','from','should','would','could','some','people','their','they','have','more'}


class ExampleMemory:
    def __init__(self, platform, *, encoder=None):
        self.platform,self.encoder=platform,encoder
        self.cache=OrderedDict(); self.lock=threading.Lock()

    def vector(self, value):
        key=digest(value)
        with self.lock:
            if key not in self.cache:
                raw=self.encoder.encode(value[:1800])
                self.cache[key]=tuple(float(x) for x in raw[0])
                if len(self.cache)>256: self.cache.popitem(last=False)
            return self.cache[key]

    def retrieve(self, principal, question, limit=3):
        query=terms(question); candidates=[]; seen=set()
        for entry in self.platform.history(principal)[:80]:
            if entry['state']!='COMPLETE': continue
            artifact=self.platform.report(principal,entry['submission_id']); report=artifact['payload']
            if artifact['contentSha256']!=digest(report): continue
            detail=report.get('detailedReport') or {}
            if detail.get('contentSha256')!=digest({k:v for k,v in detail.items() if k!='contentSha256'}): continue
            topic=detail.get('topicLearning',{})
            for example in topic.get('examples',[]):
                if not example.get('hypothetical'): continue
                identity=digest({'scenario':example['scenario']})
                if identity in seen: continue
                seen.add(identity)
                match_text=' '.join([topic['theme'],example['title'],example['scenario'],*example['relatedTopics']])
                matched=query.intersection(terms(match_text))
                # Require topical support even when embeddings are available.
                if not matched: continue
                score=len(matched)/max(1,math.sqrt(len(query)*len(terms(match_text))))
                candidates.append((score,identity,example,match_text))
        candidates.sort(key=lambda c:(-c[0],c[1]))
        if self.encoder is not None and candidates:
            try:
                vector=self.vector(question)
                candidates=[(score+.3*sum(a*b for a,b in zip(vector,self.vector(raw))),identity,example,raw)
                            for score,identity,example,raw in candidates[:12]]
                candidates.sort(key=lambda c:(-c[0],c[1]))
            except (ValueError,RuntimeError,OSError):
                pass
        return [{'id':identity,**{k:v for k,v in example.items() if k!='reuseOf'}}
                for _,identity,example,_ in candidates[:limit]]
