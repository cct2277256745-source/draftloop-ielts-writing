"""Export parity and version-boundary regressions; all prose is test authored."""
import copy
from io import BytesIO
import unittest

from app.application.detailed_report import DetailedReportService
from app.product_composition.browser_pdf import render_report_pdf
from tests.c2_support import task2_foundation_fixture
from tests.detailed_report_support import detailed_output


def pdf_fixture():
    submission, bundle, locked, _ = task2_foundation_fixture()
    essay = submission.essay_version
    raw = detailed_output({'acceptedFindings':[a.content() for a in bundle.assessments],
        'paragraphs':[{'index':i+1,'text':essay.original_text[p.locator.start:p.locator.end]}
            for i,p in enumerate(essay.paragraphs)]})
    sentence = essay.paragraphs[0].sentences[0]
    quote = essay.original_text[sentence.start:sentence.end]
    raw['priorities'] = [{'category':'DEVELOPMENT','title':'把观点与具体场景连接起来',
        'explanation':'本例用于验证批改内容在网页和 PDF 之间的一致性。',
        'action':'先说明论点，再解释这个场景如何支持它。','paragraphIndex':1,
        'original':quote,'replacement':'A clear example supports this argument.','missing':False}]
    raw['paragraphs'][0]['corrections'] = [{'kind':'DEVELOPMENT','original':quote,
        'replacement':'A clear example supports this argument.','reason':'连接观点与具体场景。','missing':False}]
    raw['paragraphs'][0]['changes'] = ['补充论证连接']
    raw['paragraphs'][0]['targetBandEstimate'] = 7.0
    raw['paragraphs'][0]['estimateReason'] = '教学估计仅描述此段的表达与展开。'
    detail = DetailedReportService.validate(raw, submission, locked, target_band=7.0)
    report = {'overallBand':locked.content()['overallBand'],
        'criteria':{a.criterion:a.estimated_band for a in bundle.assessments},
        'lockedScoreSha256':locked.snapshot_sha256,'detailedReport':detail,'sections':[]}
    source = {'question':submission.question,'candidateScript':essay.original_text}
    return report, source


class BrowserReportPdfTests(unittest.TestCase):
    def test_exports_all_paragraphs_necessary_edits_and_learning_without_removed_copy(self):
        from pypdf import PdfReader
        report, source = pdf_fixture()
        data = render_report_pdf(report, source)
        text = '\n'.join(p.extract_text() for p in PdfReader(BytesIO(data)).pages)
        for required in ['评分解读','根据修改建议重写','考生文章思路','逐段精修',
                         '主题积累','下一步训练','A clear example supports this argument.','连接观点与具体场景。']:
            self.assertIn(required,text)
        for removed in ['评分把握','可能区间','不是 IELTS 官方成绩']:
            self.assertNotIn(removed,text)
        self.assertIn(str(len(report['detailedReport']['paragraphs'])), text)

    def test_rejects_a_different_source_or_locked_score(self):
        report, source = pdf_fixture()
        changed = dict(source, candidateScript=source['candidateScript']+'\n\nA new paragraph.')
        with self.assertRaisesRegex(ValueError,'PDF_SOURCE_VERSION_MISMATCH'):
            render_report_pdf(report,changed)
        report['lockedScoreSha256'] = 'f'*64
        with self.assertRaises(ValueError): render_report_pdf(report,source)

    def test_long_table_cells_paginate_without_losing_content(self):
        from pypdf import PdfReader
        report, source = pdf_fixture()
        # A historical accepted projection can carry long narrative sections.
        del report['detailedReport']
        report['sections']=[{'label':'长报告检查','records':[{'text':'长段内容。'*2200}]}]
        pdf=PdfReader(BytesIO(render_report_pdf(report,source)))
        self.assertGreater(len(pdf.pages),2)
        self.assertIn('本稿原文', ''.join(p.extract_text() for p in pdf.pages))
