"""Coverage and provenance checks, not scoring-accuracy evaluation."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from app.application.rag_audit import RagCalibrationService
from app.core.rag_evidence import EvidenceGateContext,gate_retrieval_evidence
from app.core.rag_local_runtime import LocalRagRuntime
from app.core.task1_calibration_references import retrieve_task1_references,RIGHTS,SOURCE


class Task1ReferenceTests(unittest.TestCase):
    def test_all_four_criteria_have_attributed_task1_observations_without_band_anchors(self):
        for criterion in ('TA','CC','LR','GRA'):
            with self.subTest(criterion=criterion):
                pack=retrieve_task1_references('Overview comparison accuracy word choice sentence structures',criterion)
                gate=gate_retrieval_evidence(pack,EvidenceGateContext(criterion,(),(RIGHTS,),('PRIVATE_RESEARCH_ONLY',)))
                self.assertEqual(gate.status.value,'SUFFICIENT')
                self.assertGreaterEqual(len(gate.accepted_items),2)
                for item in gate.accepted_items:
                    self.assertIn('task1',item.features)
                    self.assertTrue(item.provenance.startswith('https://ielts.org/'))
                    self.assertIn('#page=',item.provenance)
                    self.assertFalse(item.direct_score_authority)
                    self.assertNotRegex(item.text,r'Band [0-9]')

    def test_modified_source_is_rejected(self):
        raw=json.loads(SOURCE.read_text());raw['items'][0]['summary']='Changed source'
        with patch('pathlib.Path.read_text',return_value=json.dumps(raw)):
            with self.assertRaisesRegex(ValueError,'sidecar changed'):
                retrieve_task1_references('overview','TA')

    def test_runtime_routes_all_task1_criteria_and_checks_authorization(self):
        calls=[]
        runtime=LocalRagRuntime.__new__(LocalRagRuntime)
        runtime.provider=SimpleNamespace(retrieve=lambda *args: self.fail('Task 2 corpus was queried'))
        runtime.authorized_now=lambda:True
        runtime.validation=SimpleNamespace(runtime_handle=SimpleNamespace(assert_unchanged=lambda:calls.append('checked')))
        for c in ('TA','CC','LR','GRA'):
            refs,_=RagCalibrationService(runtime,None,None).retrieve(c,'Describe the chart.','Overview is missing.',task_type='task1')
            self.assertGreaterEqual(len(refs),2)
            self.assertTrue(all(item['retrievalScope']=='CRITERION_REFERENCE' for item in refs))
        self.assertEqual(len(calls),4)
        runtime.authorized_now=lambda:False
        with self.assertRaisesRegex(ValueError,'authorization'):
            runtime.retrieve_calibration('overview','TA',task_type='task1')
