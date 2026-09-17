from types import SimpleNamespace
import unittest
from app.application.example_memory import ExampleMemory
from app.core.submission import digest


class ExampleMemoryTests(unittest.TestCase):
    def test_reuses_only_relevant_owner_examples_and_honours_deleted_history(self):
        example={'title':'学校实习','scenario':'A school could offer internships to develop practical skills.',
            'structure':'教育连接就业','adaptation':'保留学校场景并调整技能类型。','relatedTopics':['education','employment'],
            'reuseOf':None,'hypothetical':True}
        detail={'topicLearning':{'theme':'教育','examples':[example]}}
        detail['contentSha256']=digest(detail);payload={'detailedReport':detail}
        artifact={'payload':payload,'contentSha256':digest(payload)}
        history={'owner':[{'state':'COMPLETE','submission_id':'one'}],'other':[]}
        def report(owner,sid):
            self.assertEqual(owner,'owner');self.assertEqual(sid,'one');return artifact
        platform=SimpleNamespace(history=lambda owner:history[owner],report=report)
        memory=ExampleMemory(platform)
        first=memory.retrieve('owner','Should universities provide employment training?')
        self.assertEqual(len(first),1);self.assertEqual(first[0]['scenario'],example['scenario'])
        self.assertEqual(memory.retrieve('other','University education'),[])
        self.assertEqual(memory.retrieve('owner','Animal habitats and biodiversity'),[])
        history['owner']=[]
        self.assertEqual(memory.retrieve('owner','University education'),[])

    def test_tampered_report_cannot_become_personal_memory(self):
        platform=SimpleNamespace(history=lambda owner:[{'state':'COMPLETE','submission_id':'one'}],
            report=lambda *args:{'payload':{'detailedReport':{}},'contentSha256':'wrong'})
        self.assertEqual(ExampleMemory(platform).retrieve('owner','University education'),[])
