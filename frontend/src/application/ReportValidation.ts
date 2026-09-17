import type { DetailedReport, ReportLocation, ServerReport, WritingSource } from './BrowserApi';

const prose=(v:unknown):v is string=>typeof v==='string';
const strings=(v:unknown):v is string[]=>Array.isArray(v)&&v.every(prose);
const band=(v:unknown)=>typeof v==='number'&&Number.isFinite(v)&&v>=0&&v<=9&&Number.isInteger(v*2);
const hash=(v:unknown)=>typeof v==='string'&&/^[a-f0-9]{64}$/.test(v);
const categories=['WORD_CHOICE','GRAMMAR','TASK_RESPONSE','DEVELOPMENT','COHERENCE','MISSING_ELEMENT'];

/** Defensive wire validation. Scoring and content-hash authority remain on the server. */
export function validDetailedReport(report:ServerReport,source?:WritingSource):boolean {
  if(report.detailedReport===undefined)return true;
  try {
    const d:DetailedReport=report.detailedReport;
    if(!source||d.version!=='detailed-writing-report-v1'||d.lockedScoreSha256!==report.lockedScoreSha256||!hash(d.contentSha256)
      ||!/^essay:[a-f0-9]{64}$/.test(d.essayVersionId)||!['task1','task2'].includes(d.taskType)
      ||(source.taskType!==undefined&&source.taskType!==d.taskType)||(d.targetBand!==null&&!band(d.targetBand)))return false;
    const criteria=Object.keys(report.criteria);
    if(!Array.isArray(d.criterionAnalyses)||d.criterionAnalyses.length!==4
      ||new Set(d.criterionAnalyses.map(c=>c.criterion)).size!==4
      ||d.criterionAnalyses.some(c=>!criteria.includes(c.criterion)||!prose(c.analysis)||!strings(c.strengths)||!strings(c.limitations)))return false;
    const strongest=criteria.filter(c=>report.criteria[c]===Math.max(...Object.values(report.criteria)));
    if(!strings(d.strongestCriteria)||d.strongestCriteria.length!==strongest.length||strongest.some(c=>!d.strongestCriteria.includes(c as typeof d.strongestCriteria[number])))return false;
    if(!Array.isArray(d.paragraphs)||!d.paragraphs.length||!Array.isArray(d.priorities)||d.priorities.length>3)return false;
    const characters=Array.from(source.candidateScript);
    const located=(l:ReportLocation):boolean=>{
      if(!l||!Number.isInteger(l.paragraphIndex)||l.paragraphIndex<1||l.paragraphIndex>d.paragraphs.length||!prose(l.quote)||!prose(l.sentenceQuote))return false;
      if(l.missing===true)return l.quote===''&&l.sentenceQuote===''&&l.start===null&&l.end===null&&l.sentenceStart===null&&l.sentenceEnd===null;
      if(l.missing!==false||!Number.isInteger(l.start)||!Number.isInteger(l.end)||!Number.isInteger(l.sentenceStart)||!Number.isInteger(l.sentenceEnd))return false;
      return l.start!>=0&&l.end!>l.start!&&l.sentenceStart!<=l.start!&&l.sentenceEnd!>=l.end!&&l.sentenceEnd!<=characters.length
        &&characters.slice(l.start!,l.end!).join('')===l.quote&&characters.slice(l.sentenceStart!,l.sentenceEnd!).join('')===l.sentenceQuote
        &&d.paragraphs[l.paragraphIndex-1].original.includes(l.sentenceQuote);
    };
    if(d.paragraphs.some((p,i)=>p.index!==i+1||!prose(p.original)||!source.candidateScript.includes(p.original)||!prose(p.optimized)||!prose(p.role)||!prose(p.assessment)
      ||!['SUPPORTS','LIMITS','NEUTRAL'].includes(p.impact)||!strings(p.changes)||!prose(p.estimateReason)||(p.targetBandEstimate!==null&&!band(p.targetBandEstimate))
      ||!Array.isArray(p.corrections)||p.corrections.some(c=>!categories.includes(c.kind)||!located(c.location)||c.location.paragraphIndex!==p.index||!prose(c.replacement)||!prose(c.reason))))return false;
    if(d.priorities.some(p=>!prose(p.id)||!categories.includes(p.category)||!prose(p.title)||!prose(p.explanation)||!prose(p.action)||!prose(p.replacement)||!located(p.location)))return false;
    if(!prose(d.mindMap.thesis)||!Array.isArray(d.mindMap.branches)||d.mindMap.branches.length!==d.paragraphs.length
      ||d.mindMap.branches.some((b,i)=>b.paragraphIndex!==i+1||!prose(b.role)||!prose(b.point)||!strings(b.support)||!prose(b.gap)))return false;
    const topic=d.topicLearning;
    if(!prose(topic.theme)||!Array.isArray(topic.expressions)||topic.expressions.some(e=>!prose(e.expression)||!prose(e.meaning)||!prose(e.usage)||!['CANDIDATE','OPTIMIZED'].includes(e.source))
      ||!Array.isArray(topic.examples)||topic.examples.some(e=>!prose(e.title)||!prose(e.scenario)||!prose(e.structure)||!prose(e.adaptation)||!strings(e.relatedTopics)||e.hypothetical!==true||(e.reuseOf!==null&&!prose(e.reuseOf))))return false;
    return prose(d.encouragement)&&Array.isArray(d.nextActions)&&d.nextActions.length>0&&d.nextActions.length<=3
      &&d.nextActions.every(a=>prose(a.title)&&typeof a.minutes==='number'&&a.minutes>0&&strings(a.steps)&&prose(a.successCheck));
  } catch {return false;}
}
