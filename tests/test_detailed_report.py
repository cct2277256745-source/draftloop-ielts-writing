import copy
import unittest
import json
from types import SimpleNamespace
from app.core.providers import ProviderCallResult, ProviderFailureCode

from app.application.detailed_report import DetailedReportService, locate
from app.core.submission import digest
from app.product_composition.browser_projection import detailed_projection
from tests.c2_support import task2_foundation_fixture
from tests.detailed_report_support import detailed_output


class DetailedReportTests(unittest.TestCase):
    def setUp(self):
        self.submission,self.bundle,self.locked,_=task2_foundation_fixture()
        essay=self.submission.essay_version
        self.raw=detailed_output({'acceptedFindings':[a.content() for a in self.bundle.assessments],
            'paragraphs':[{'index':i+1,'text':essay.original_text[p.locator.start:p.locator.end]}
                for i,p in enumerate(essay.paragraphs)]})

    def validate(self, raw=None):
        return DetailedReportService.validate(raw or self.raw,self.submission,self.locked,target_band=7)

    def test_only_listed_edits_change_the_optimized_paragraph_and_score_is_untouched(self):
        essay=self.submission.essay_version
        p=essay.paragraphs[0]; sentence=p.sentences[0]
        quote=essay.original_text[sentence.start:sentence.end]
        self.raw['paragraphs'][0]['corrections']=[{'kind':'DEVELOPMENT','original':quote,
            'replacement':'A clear example supports this argument.','reason':'补充论点与例子之间的关系。','missing':False}]
        result=self.validate(); revised=result['paragraphs'][0]
        self.assertEqual(revised['optimized'],revised['original'].replace(quote,'A clear example supports this argument.',1))
        self.assertEqual(revised['corrections'][0]['location']['sentenceQuote'],quote)
        self.assertEqual(result['lockedScoreSha256'],self.locked.snapshot_sha256)
        self.assertEqual(detailed_projection(result,self.locked.snapshot_sha256),result)
        self.assertEqual(self.locked.content()['snapshotSha256'],self.locked.snapshot_sha256)

    def test_rejects_entire_essay_fabricated_quotes_and_missing_paragraph(self):
        essay=self.submission.essay_version
        for quote in [essay.original_text,'A phrase the learner never wrote.']:
            with self.subTest(quote=quote),self.assertRaises(ValueError): locate(essay,1,quote)
        self.raw['paragraphs']=self.raw['paragraphs'][:-1]
        with self.assertRaises(ValueError): self.validate()

    def test_overlapping_edits_and_unknown_learning_expression_are_rejected(self):
        p=self.submission.essay_version.paragraphs[0]
        quote=self.submission.essay_version.original_text[p.sentences[0].start:p.sentences[0].end]
        correction={'kind':'GRAMMAR','original':quote,'replacement':'Changed sentence.',
            'reason':'具体修改原因。','missing':False}
        raw=copy.deepcopy(self.raw);raw['paragraphs'][0]['corrections']=[correction,correction]
        with self.assertRaises(ValueError): self.validate(raw)
        self.raw['topicLearning']['expressions']=[{'expression':'invented high-score language',
            'meaning':'含义','usage':'语境','source':'OPTIMIZED'}]
        with self.assertRaises(ValueError): self.validate()

    def test_missing_content_is_labelled_absent_instead_of_quoting_whole_essay(self):
        self.raw['priorities']=[{'category':'MISSING_ELEMENT','title':'主体段缺少与论点相连的例子',
            'explanation':'读者无法验证这个推论。','action':'补一个具体场景。','paragraphIndex':1,
            'original':'','replacement':'For example, a local school could offer work placements.','missing':True}]
        location=self.validate()['priorities'][0]['location']
        self.assertIsNone(location['start']);self.assertEqual(location['quote'],'')

    def test_forged_projection_lineage_and_private_extra_fields_are_rejected(self):
        value=self.validate()
        with self.assertRaises(ValueError): detailed_projection(value,'f'*64)
        value['sourcePaths']=['private'];value['contentSha256']=digest({k:v for k,v in value.items() if k!='contentSha256'})
        with self.assertRaises(ValueError): detailed_projection(value,self.locked.snapshot_sha256)

    def test_truncated_report_retries_once_with_more_budget_and_never_uses_partial_text(self):
        calls=[]
        def call(contract, request):
            calls.append(request)
            if len(calls)==1:
                return ProviderCallResult.failed(ProviderFailureCode.TRUNCATED_RESPONSE,'truncated')
            return ProviderCallResult(content=json.dumps(self.raw))
        service=DetailedReportService(SimpleNamespace(call=call),None)
        result=service.run(self.submission,SimpleNamespace(main_review_projection=lambda:{}),
            self.locked,self.bundle,target_band=7)
        self.assertEqual([r.max_tokens for r in calls],[16000,32000])
        self.assertEqual(calls[0].messages,calls[1].messages)
        self.assertEqual(result['lockedScoreSha256'],self.locked.snapshot_sha256)

    def test_repeated_truncation_fails_closed_after_two_attempts(self):
        calls=[]
        def call(contract, request):
            calls.append(request)
            return ProviderCallResult.failed(ProviderFailureCode.TRUNCATED_RESPONSE,'truncated')
        with self.assertRaisesRegex(ValueError,'DEEP_COACHING_PROVIDER_FAILED'):
            DetailedReportService(SimpleNamespace(call=call),None).run(self.submission,
                SimpleNamespace(main_review_projection=lambda:{}),self.locked,self.bundle)
        self.assertEqual(len(calls),2)

    def test_report_explanation_cannot_reintroduce_removed_scoring_metadata(self):
        for value in ['按 e0001 判断。','TR-8-3 仅部分展示。','置信度 MEDIUM，评分区间 7–8。']:
            with self.subTest(value=value):
                raw=copy.deepcopy(self.raw);raw['criterionAnalyses'][0]['analysis']=value
                with self.assertRaisesRegex(ValueError,'ordinary Chinese'):self.validate(raw)
