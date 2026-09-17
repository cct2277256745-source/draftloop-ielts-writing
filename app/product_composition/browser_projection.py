"""Allowlisted browser projection of persisted C3 reports, never retrieval inputs."""
from collections.abc import Mapping
import re

from app.product_platform.contracts import ErrorCode, PlatformError, ProcessingOutcome, SubmissionState, digest
from app.application.learning_context import LEARNING_CONTEXT_VERSION
from .contracts import PresentationState
from app.application.detailed_report import VERSION as DETAIL_VERSION
from app.core.calibration import requires_rescore, valid_band

VERSION = "draftloop-browser-workspace-v1"
_PRIVATE = re.compile(
    "/" + r"(?:Users|home)/" + r"|file://|EMPIRICAL_RAG_PACKAGE_PATH|(?:[A-Z]:\\)",
    re.I,
)


def _safe_text(value):
    if not isinstance(value, str) or len(value) > 16000 or _PRIVATE.search(value):
        raise ValueError("Unsafe browser text.")
    return value


def rag_projection(source):
    source = source if isinstance(source, Mapping) else {}
    enabled = (source.get("state") == "RAG_ENABLED"
               and source.get("mode") == "LOCAL_PRIVATE_RESEARCH"
               and source.get("package") == "ielts-frozen-retrieval-v1"
               and source.get("directScoreAuthority") is False)
    return {
        "status": "ENABLED" if enabled else "DISABLED",
        "mode": "LOCAL_PRIVATE_RESEARCH" if enabled else None,
        "packageIdentity": "ielts-frozen-retrieval-v1" if enabled else None,
        "scoreAuthority": False,
        "reason": None if enabled else _safe_text(source.get("reason") or "PACKAGE_NOT_CONFIGURED"),
        "evidenceState": _safe_text(source.get("evidenceState") or "NOT_REACHED"),
    }


def detailed_projection(value, locked):
    if (not isinstance(value, dict) or value.get('version') != DETAIL_VERSION
            or value.get('lockedScoreSha256') != locked
            or value.get('contentSha256') != digest({k:v for k,v in value.items() if k!='contentSha256'})):
        raise ValueError('Detailed report lineage mismatch.')
    allowed = {'version','lockedScoreSha256','essayVersionId','taskType','targetBand','criterionAnalyses',
        'criterion','analysis','strengths','limitations','strongestCriteria','priorities','id','category','title',
        'explanation','action','replacement','location','paragraphIndex','missing','quote','start','end',
        'sentenceQuote','sentenceStart','sentenceEnd','paragraphs','index','role','original','assessment',
        'impact','corrections','kind','reason','optimized','changes','targetBandEstimate','estimateReason',
        'mindMap','thesis','branches','point','support','gap','topicLearning','theme','expressions',
        'expression','meaning','usage','source','examples','scenario','structure','adaptation','relatedTopics',
        'reuseOf','hypothetical','encouragement','nextActions','minutes','steps','successCheck','contentSha256'}
    def project(item, depth=0):
        if depth>12: raise ValueError('Detailed report is too deeply nested.')
        if isinstance(item,str): return _safe_text(item)
        if item is None or isinstance(item,(bool,int,float)): return item
        if isinstance(item,list) and len(item)<=100: return [project(v,depth+1) for v in item]
        if isinstance(item,dict) and not set(item)-allowed:
            return {k:project(v,depth+1) for k,v in item.items()}
        raise ValueError('Unknown detailed report field.')
    return project(value)


def calibration_projection(value, final_band):
    if not isinstance(value,dict) or value.get('status') not in {'NOT_AVAILABLE','ACCEPTED_INITIAL','RESCORED'}:
        raise ValueError('Incomplete calibration cannot publish a report.')
    initial=value.get('initialBand'); audit=value.get('auditBand')
    if not valid_band(initial) or value.get('finalBand')!=final_band or value.get('directScoreAuthority') is not False:
        raise ValueError('Calibration score lineage mismatch.')
    status=value['status']; triggered=value.get('rescoreTriggered')
    if status=='NOT_AVAILABLE':
        if audit is not None or triggered or initial!=final_band:
            raise ValueError('Unavailable calibration cannot change scores.')
    elif (not valid_band(audit) or requires_rescore(initial,audit)!=triggered
            or value.get('absoluteDifference')!=abs(initial-audit)
            or (status=='RESCORED')!=triggered
            or (not triggered and initial!=final_band)):
        raise ValueError('Calibration threshold mismatch.')
    return {'status':status,'initialBand':initial,'auditBand':audit,
        'absoluteDifference':value.get('absoluteDifference'),'rescoreTriggered':bool(triggered),
        'retrievalPasses':value.get('retrievalPasses',0),'finalBand':final_band,
        'criteria':[{'criterion':_safe_text(c['criterion']),'initialBand':c['initialBand'],
            'auditBand':c['auditBand'],'difference':c['difference'],'rationale':_safe_text(c['rationale'])}
            for c in value.get('criteria',[])], 'scoreAuthority':False}


def report_projection(payload, *, legacy_evidence=False):
    """Only consumer-approved text crosses this boundary; no sourceIds or raw traces."""
    if payload.get("status") != "COMPLETE":
        raise ValueError("Normal reports require COMPLETE.")
    score = payload["scoreProjection"]
    locked = score["lockedScoreSha256"]
    if locked != payload["sourceHashes"]["lockedScoreSha256"]:
        raise ValueError("Locked score lineage mismatch.")
    if not isinstance(locked, str) or not re.fullmatch(r"[0-9a-f]{64}", locked):
        raise ValueError("Invalid locked score identity.")
    band = score["overallBand"]
    if isinstance(band, bool) or not isinstance(band, (int, float)) or not 0 <= band <= 9:
        raise ValueError("Invalid server-owned band.")
    criteria = score["criteria"]
    expected_criteria = {"TA", "CC", "LR", "GRA"} if "TA" in criteria else {"TR", "CC", "LR", "GRA"}
    if set(criteria) != expected_criteria or any(
        isinstance(v, bool) or not isinstance(v, (int, float)) or not 0 <= v <= 9
        for v in criteria.values()
    ):
        raise ValueError("Invalid criterion estimates.")
    rag = rag_projection(payload.get("rag"))
    sections = []
    for section in payload["sections"]:
        records = []
        for item in section["payload"].get("items", ()):
            authority = item["sourceAuthority"]
            if authority == "RAG_EVIDENCE" and not (
                rag["status"] == "ENABLED" and rag["evidenceState"] == "SUFFICIENT"
                and payload["sourceHashes"].get("ragEvidenceGateSha256")
                and payload["rag"].get("consumer") == "build_full_coaching"
            ):
                raise ValueError("Ungated empirical evidence.")
            if authority not in {"RAG_EVIDENCE", "CURRENT_STUDENT_EVIDENCE", "TOPIC_KB"}:
                raise ValueError("Unknown coaching authority.")
            record = {
                "id": digest({"item": item["itemId"]}),
                "criterion": _safe_text(item["criterion"]),
                "text": _safe_text(item["textZh"]),
                "textEn": _safe_text(item["textEn"]),
                "authority": authority,
            }
            context = payload.get('learningContext')
            if context is not None and (legacy_evidence or context.get('version') == LEARNING_CONTEXT_VERSION):
                if context.get('contextSha256') != digest({k: v for k, v in context.items() if k != 'contextSha256'}):
                    raise ValueError('Learning evidence identity mismatch.')
                record['evidence'] = [{'start': issue['start'], 'end': issue['end'], 'scope': issue['scope']}
                    for issue in context['issues'] if (legacy_evidence or issue['criterion'] == item['criterion'])
                    and set(item.get('findingIds', ())).intersection(issue['evidenceIds'])]
            records.append(record)
        if records:
            sections.append({"key": _safe_text(section["key"]),
                             "label": _safe_text(section["labelZh"]), "records": records})
    result = {"overallBand": band, "criteria": dict(criteria),
            "likelyRange": list(score["likelyRange"]), "confidence": score["confidence"],
            "lockedScoreSha256": locked,
            "disclaimer": _safe_text(payload["disclaimerZh"]),
            "sections": sections, "rag": rag}
    if payload.get('detailedReport') is not None:
        result['detailedReport']=detailed_projection(payload['detailedReport'],locked)
    if payload.get('calibrationAudit') is not None:
        result['calibrationAudit']=calibration_projection(payload['calibrationAudit'],band)
    return result


class PersistBrowserProjection:
    """Wrap the accepted C3 processor; persist the sanitized projection in its outcome."""
    def __init__(self, processor):
        self.processor = processor

    def __call__(self, request):
        outcome = self.processor(request)
        if outcome.state is not SubmissionState.COMPLETE:
            return outcome
        payload = dict(outcome.payload)
        content = report_projection(payload)
        payload["browserProjection"] = {"version": VERSION, "content": content,
                                        "contentSha256": digest(content)}
        return ProcessingOutcome(outcome.state, payload, outcome.failure_code)


def read_workspace(facade, principal, submission_id):
    presentation = facade.presentation_projection(principal, submission_id)
    semantic = facade.semantic_result(principal, submission_id)
    if presentation.semantic_result_sha256 != semantic.semantic_result_sha256:
        raise PlatformError(ErrorCode.CONFLICT, "Workspace changed; reload required.")
    report = None
    if presentation.state is PresentationState.NORMAL:
        payload = presentation.content()["payload"]
        stored = payload.get("browserProjection", {})
        expected = report_projection(payload)
        # Older complete reports retain their original content hash. New display
        # fields are copied from their same verified, immutable score projection.
        legacy = {k: v for k, v in expected.items() if k not in {"criteria", "likelyRange", "confidence"}}
        legacy["sections"] = [{**s, "records": [{k: v for k, v in r.items() if k != "textEn"}
            for r in s["records"]]} for s in legacy["sections"]]
        accepted_projections = [expected, legacy]
        if (payload.get('learningContext') or {}).get('version') == 'current-learning-context-v1':
            # Validate the retired projection exactly, then omit its potentially
            # cross-criterion locators from the new presentation. Score is intact.
            accepted_projections.append(report_projection(payload, legacy_evidence=True))
        if (stored.get("version") != VERSION or stored.get("content") not in accepted_projections
                or stored.get("contentSha256") != digest(stored.get("content"))):
            raise PlatformError(ErrorCode.CONFLICT, "Browser projection lineage mismatch.")
        report = expected
    content = {
        "version": VERSION, "submissionId": submission_id,
        # Runtime capability is distinct from report.rag (evidence actually used
        # for that persisted assessment). Enabling a runtime never upgrades an
        # older rubric-only report or a failed assessment.
        "rag": rag_projection(facade.rag_status(principal)),
        "semanticResult": semantic.content(),
        "presentation": {"state": presentation.state.value,
                         "sourceSemanticSha256": presentation.semantic_result_sha256,
                         "sourceReportSha256": presentation.report_content_sha256,
                         "failureCode": presentation.failure_code, "report": report},
        # No export bytes have been materialized or verified by this browser adapter.
        "exportArtifact": {"state": "BLOCKED", "reason": "ARTIFACT_NOT_MATERIALIZED"},
    }
    content["workspaceSha256"] = digest(content)
    return content
