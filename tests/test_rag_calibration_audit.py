"""Synthetic, network-free policy and sequencing evidence; not score-accuracy proof."""
from dataclasses import replace
import json
from types import SimpleNamespace
import unittest

from app.application.rag_audit import RagCalibrationService
from app.core.assessment_finalization import FinalizationBundle
from app.core.calibration import requires_rescore
from app.core.criterion_scoring import CriterionScoringService
from app.core.providers import ProviderCallResult
from app.core.rag_retrieval import RetrievalEvidenceItem, RetrievalEvidencePack
from app.core.submission import digest
from tests.criterion_scoring_support import CapturingCriterionTransport, assessed_output, scoring_inputs


class References:
    def __init__(self): self.queries = []

    def retrieve(self, query, criterion, top_k):
        self.queries.append(query)
        item = RetrievalEvidenceItem(object_id=criterion+'-private', object_type='SYNTHETIC_TEST',
            criterion=criterion, features=('test-performance',), text='Synthetic comparison: clear claims need specific support.',
            provenance='synthetic-test', source_artifact_id='private-test-artifact',
            rights='LICENSE_UNCLEAR', usage='PRIVATE_RESEARCH_ONLY', dense_rank=1, bm25_rank=1,
            dense_retrieval_score=.8, bm25_retrieval_score=2., rrf_score=.02, direct_score_authority=False)
        pack = RetrievalEvidencePack((item,), digest(query), criterion, digest('test-package'),
            'synthetic-test-v1', 1, 1, 1, '')
        return replace(pack, pack_sha256=digest(pack.content(include_hash=False)))


class Auditor:
    def __init__(self, band): self.band, self.payload = band, None

    def call(self, contract, request):
        self.payload = json.loads(request.messages[1]['content'])
        return ProviderCallResult(content=json.dumps({'status': 'COMPLETE', 'criteria': [
            {'criterion': c, 'referenceBand': self.band, 'rationale': '当前论证的展开程度与这些参考证据存在差异。',
             'referenceIds': [refs[0]['id']]} for c, refs in self.payload['calibrationEvidence'].items()]}))


def assessment(band, inputs):
    lower, upper = int(band), int(band + .5)
    transport = CapturingCriterionTransport(lambda payload, attempt: assessed_output(payload, lower=lower, upper=upper))
    execution = CriterionScoringService(transport).run(*inputs)
    return FinalizationBundle.from_task2_bundle(execution.bundle)


class CalibrationTests(unittest.TestCase):
    def test_browser_progress_accepts_retrieval_events_without_scoring_status(self):
        import threading
        from app.product_composition.local_browser import BrowserRuntime
        runtime = BrowserRuntime.__new__(BrowserRuntime)
        runtime.active = threading.local()
        runtime.active.submission_id = 'synthetic-progress'
        runtime.progress_lock = threading.Lock()
        runtime.progress = {'synthetic-progress': {'stage':'CRITERION_SCORING','criteria':{'TR':'ASSESSED'}}}
        runtime.checkpoint({'stage':'RAG_RETRIEVAL','criterion':'TR','outcome':'SUFFICIENT','acceptedCount':2})
        runtime.checkpoint({'stage':'RAG_AUDIT','rescoreTriggered':True})
        self.assertEqual(runtime.progress['synthetic-progress']['criteria'], {'TR':'ASSESSED'})
        self.assertTrue(runtime.progress['synthetic-progress']['rescoreTriggered'])

    def test_exact_asymmetric_boundary_and_absolute_difference(self):
        for first, audit, expected in [(7,6.5,True),(7.5,7,True),(6.5,6,False),(6.5,5.5,True),
            (7,7.5,True),(6.5,7,False),(6.5,7.5,True),(7,7,False),(0,.5,False),(9,8.5,True)]:
            with self.subTest(first=first,audit=audit): self.assertIs(requires_rescore(first,audit),expected)

    def test_invalid_scores_cannot_trigger_or_bypass_policy(self):
        for value in [True, '7', float('nan'), float('inf'), -1, 10, 6.25, None]:
            with self.subTest(value=value), self.assertRaises(ValueError): requires_rescore(value,7)

    def test_no_rescore_below_threshold_and_blind_audit(self):
        inputs=scoring_inputs(); first=assessment(6.5,inputs); provider=References(); auditor=Auditor(6)
        service=RagCalibrationService(SimpleNamespace(provider=provider),auditor,None)
        result=service.run(inputs[0],inputs[3],inputs[2],first,lambda _: self.fail('Unexpected re-score'))
        self.assertIs(result.bundle,first)
        self.assertEqual(result.audit['status'],'ACCEPTED_INITIAL')
        self.assertEqual(len(provider.queries),4)
        serialized=json.dumps(auditor.payload)
        for forbidden in ['initialBand','estimatedBand','targetBand','initialAssessment','dense_retrieval_score','sourceArtifactId']:
            self.assertNotIn(forbidden,serialized)

    def test_trigger_retrieves_again_and_uses_new_rubric_result_not_audit_or_average(self):
        inputs=scoring_inputs(); first=assessment(7,inputs); provider=References(); auditor=Auditor(6.5)
        final=assessment(7.5,inputs); captured=[]
        def rescore(review):
            review.validate(inputs[0],inputs[3]); captured.append(review)
            for criterion, context in review.criteria.items():
                self.assertIn('initialAssessment',context)
                self.assertIn('disagreement',context)
                self.assertIn('freshCalibrationEvidence',context)
            return final
        result=RagCalibrationService(SimpleNamespace(provider=provider),auditor,None).run(
            inputs[0],inputs[3],inputs[2],first,rescore)
        self.assertEqual(len(captured),1); self.assertEqual(len(provider.queries),8)
        self.assertEqual(result.audit['status'],'RESCORED'); self.assertEqual(result.audit['finalBand'],7.5)
        self.assertIs(result.bundle,final)
        self.assertNotEqual(provider.queries[0],provider.queries[4])

    def test_failed_rescore_cannot_publish_first_score(self):
        inputs=scoring_inputs(); first=assessment(7,inputs)
        result=RagCalibrationService(SimpleNamespace(provider=References()),Auditor(6.5),None).run(
            inputs[0],inputs[3],inputs[2],first,lambda _: None)
        self.assertIsNone(result.bundle); self.assertIsNone(result.audit['finalBand'])
        self.assertEqual(result.audit['status'],'REVIEW_REQUIRED')

    def test_missing_rag_is_visible_and_not_an_audit_pass(self):
        inputs=scoring_inputs(); first=assessment(7,inputs)
        result=RagCalibrationService(None,Auditor(6.5),None).run(inputs[0],inputs[3],inputs[2],first,None)
        self.assertEqual(result.audit['status'],'NOT_AVAILABLE'); self.assertIsNone(result.audit['auditBand'])

    def test_topic_crowding_retries_query_without_relaxing_the_criterion_gate(self):
        class Crowded(References):
            def retrieve(self,query,criterion,top_k):
                pack=super().retrieve(query,criterion,top_k)
                if 'Task:' in query:
                    item=replace(pack.items[0],criterion='CC' if criterion!='CC' else 'LR')
                    pack=replace(pack,items=(item,))
                    pack=replace(pack,pack_sha256=digest(pack.content(include_hash=False)))
                return pack
        provider=Crowded()
        result,_=RagCalibrationService(SimpleNamespace(provider=provider),None,None).retrieve(
            'TR','Should governments invest in transport?','The position is clear.')
        self.assertEqual(len(provider.queries),2)
        self.assertEqual(result[0]['retrievalScope'],'CRITERION_REFERENCE')
        self.assertTrue(result[0]['id'].startswith('TR-'))
        self.assertNotEqual(provider.queries[0],provider.queries[1])

    def test_re_score_uses_normal_criterion_validation_and_distinct_identity(self):
        inputs=scoring_inputs(); first=assessment(7,inputs); transport=CapturingCriterionTransport()
        def rescore(review):
            execution=CriterionScoringService(transport).run(*inputs,calibration_review=review)
            return FinalizationBundle.from_task2_bundle(execution.bundle)
        result=RagCalibrationService(SimpleNamespace(provider=References()),Auditor(6.5),None).run(
            inputs[0],inputs[3],inputs[2],first,rescore)
        self.assertIsNotNone(result.bundle)
        self.assertTrue(all('calibrationReview' in p for p in transport.payloads))
        self.assertNotEqual(first.assessments[0].execution_identity['promptVersion'],
            result.bundle.assessments[0].execution_identity['promptVersion'])

    def test_audit_cannot_cite_invented_reference_or_repeat_criterion(self):
        refs={c:[{'id': c+'-ref-1'}] for c in ('TR','CC','LR','GRA')}
        data={'status':'COMPLETE','criteria':[{'criterion':c,'referenceBand':7,
            'rationale':'A valid-length explanation.', 'referenceIds':['invented']} for c in refs]}
        with self.assertRaises(ValueError): RagCalibrationService.validate(json.dumps(data),list(refs),refs)
