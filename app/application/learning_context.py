"""Persist current-text teaching locators while typed scoring evidence is available."""
from app.core.revision import RevisionIssue
from app.core.submission import digest

LEARNING_CONTEXT_VERSION = 'current-learning-context-v2'


def build_learning_context(value, coaching):
    evidence = value.current_evidence
    if evidence is None:
        return None
    essay = value.submission.essay_version
    locations = {}
    for paragraph in essay.paragraphs:
        locations[paragraph.locator.locator_id] = (paragraph.locator.start, paragraph.locator.end)
        for sentence in paragraph.sentences:
            locations[sentence.locator_id] = (sentence.start, sentence.end)
    observations = {item['observationId']: item for item in getattr(evidence, 'payload', {}).get('observations', ())}
    claims = {item.claim_id: item for item in getattr(evidence, 'claims', ())}
    findings = {(assessment.criterion, item['findingId']): item for assessment in value.bundle.assessments for item in assessment.findings}
    issues = []
    for diagnosis in value.diagnosis.bottlenecks:
        refs, ranges, statements = set(), [], []
        for finding_id in diagnosis.finding_ids:
            finding = findings[(diagnosis.criterion, finding_id)]
            refs.add(finding_id)
            for ref in finding.get('evidenceRefs', ()):
                observation = observations.get(ref.get('observationId'))
                if observation:
                    refs.add(observation['observationId'])
                    statements.append(observation['statement'])
                    for span in observation.get('evidenceSpans', ()):
                        start, end = span['start'], span['end']
                        if essay.original_text[start:end] != span['quote']:
                            raise ValueError('Learning evidence does not round-trip.')
                        ranges.append((start, end)); refs.add(span['spanId'])
                    if not observation.get('evidenceSpans'):
                        ranges.extend(locations[key] for key in observation.get('paragraphIds', ()) if key in locations)
                for key in ref.get('claimIds', ()):
                    claim = claims[key]; ranges.append((claim.start, claim.end)); refs.add(key)
                for key in ref.get('locatorIds', ()):
                    ranges.append(locations[key]); refs.add(key)
        scope = 'EXACT' if ranges else 'GLOBAL'
        # A diagnosis can cite distant findings. Never fill the intervening essay
        # to manufacture one huge "exact" quote; use a representative real span.
        if ranges:
            start,end=min(set(ranges),key=lambda span:(span[1]-span[0],span[0]))
            contained=[s for p in essay.paragraphs for s in p.sentences if start<=s.start and s.end<=end]
            if len(contained)>1: start,end=contained[0].start,contained[0].end
        else:
            start,end=0,len(essay.original_text)
        issue = RevisionIssue.create(diagnosis.item_id, diagnosis.criterion, essay, start, end, refs)
        items = [item for item in coaching.items if item.source_authority.value == 'CURRENT_STUDENT_EVIDENCE'
                 and item.criterion == diagnosis.criterion and set(item.finding_ids).intersection(diagnosis.finding_ids)]
        by_section = {item.section.value: item for item in items}
        issue_value = issue.content()
        issue_value.update(scope=scope, quote=essay.original_text[start:end],
            skillKey=diagnosis.criterion + ':' + '|'.join(diagnosis.official_claim_ids),
            observations=statements,
            coaching={key: {'textEn': item.text_en, 'textZh': item.text_zh} for key, item in by_section.items()})
        issues.append(issue_value)
    result = {'version': LEARNING_CONTEXT_VERSION, 'lockedScoreSha256': value.locked_score.snapshot_sha256,
        'essayVersionId': essay.essay_version_id, 'issues': issues}
    result['contextSha256'] = digest(result)
    return result
