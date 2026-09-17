import { UploadedChart } from "./UploadedChart";
import { useEffect, useRef, useState, type FormEvent } from 'react';
import { IconArrowRight, IconDownload, IconArrowBackUp, IconArrowForwardUp, IconChevronDown, IconCheck, IconTargetArrow } from '@tabler/icons-react';
import { BrowserApi, type ServerReport, type ServerWorkspace, type DetailedReport, type ReportLocation, type ReportParagraph, type IssueCategory } from './application/BrowserApi';

const api=new BrowserApi();
const criterionNames:Record<string,string>={TR:'任务回应',TA:'任务完成',CC:'连贯与衔接',LR:'词汇资源',GRA:'语法多样性与准确性'};
const categories:Record<IssueCategory,string>={WORD_CHOICE:'词语与表达',GRAMMAR:'语法与句式',TASK_RESPONSE:'回应题目',DEVELOPMENT:'论证展开',COHERENCE:'组织与衔接',MISSING_ELEMENT:'补全内容'};

function LocatedSentence({location}:{location:ReportLocation}) {
  if(location.missing)return <span className="missing-evidence">第 {location.paragraphIndex} 段 · 尚未提供这部分内容</span>;
  const start=location.sentenceQuote.indexOf(location.quote);
  return <span lang="en">{start<0?location.sentenceQuote:<>{location.sentenceQuote.slice(0,start)}<mark>{location.quote}</mark>{location.sentenceQuote.slice(start+location.quote.length)}</>}</span>;
}

export function RedesignedReport({report,workspace,onSubmitted,onRevise,onGuided}:{report:ServerReport;workspace:ServerWorkspace;onSubmitted:(id:string)=>void;onRevise:()=>void;onGuided:()=>void}) {
  const [tab,setTab]=useState<'rewrite'|'deep'>(()=>new URLSearchParams(location.search).get('report')==='deep'?'deep':'rewrite');
  const [exporting,setExporting]=useState(false),[error,setError]=useState('');
  const detail=report.detailedReport;
  const selectTab=(next:'rewrite'|'deep')=>{setTab(next);const query=new URLSearchParams(location.search);query.set('report',next);window.history.replaceState(null,'','?'+query);};
  const download=async()=>{setExporting(true);setError('');try{const blob=await api.pdf(workspace.submissionId),url=URL.createObjectURL(blob);const a=document.createElement('a');a.href=url;a.download='DraftLoop-写作报告.pdf';a.click();setTimeout(()=>URL.revokeObjectURL(url),1000);}catch(e){setError((e as Error).message);}finally{setExporting(false);}};
  return <article className="studio-report" data-submission-id={workspace.submissionId} data-locked-score-sha256={report.lockedScoreSha256}>
    <header className="studio-page-heading report-masthead"><div className="report-masthead-copy"><div className="report-brandline"><strong>DraftLoop</strong><span>AI Writing Coach for IELTS</span></div><span className="report-kicker">深度总报告 · FINAL DETAILED REPORT</span><h1>写作报告</h1><p className="report-context">Academic Writing · 当前提交版本</p><span className="report-version">{workspace.source?.createdAt?new Intl.DateTimeFormat('zh-CN',{month:'short',day:'numeric',hour:'2-digit',minute:'2-digit'}).format(new Date(workspace.source.createdAt)):'当前提交版本'}</span></div>
      {tab==='deep'&&workspace.capabilities?.downloadPdf?<button className="secondary-button" disabled={exporting} onClick={()=>void download()}><IconDownload size={18}/>{exporting?'正在生成…':'导出 PDF'}</button>:<div className="inline-score">本稿 <strong aria-label="服务端评分">{report.overallBand.toFixed(1)}</strong>{detail?.targetBand!=null?<><span>/</span>目标 <strong className="target-score">{detail.targetBand.toFixed(1)}</strong></>:null}</div>}
    </header>
    <div className="studio-tabs" role="tablist" aria-label="写作报告内容" onKeyDown={e=>{if(["ArrowLeft","ArrowRight","Home","End"].includes(e.key)){e.preventDefault();const next=e.key==="Home"?"rewrite":e.key==="End"?"deep":tab==="rewrite"?"deep":"rewrite";selectTab(next);document.getElementById(next+"-tab")?.focus();}}}>
      <button id="rewrite-tab" role="tab" tabIndex={tab==='rewrite'?0:-1} aria-controls="rewrite-panel" aria-selected={tab==='rewrite'} onClick={()=>selectTab('rewrite')}>根据修改建议重写</button>
      <button id="deep-tab" role="tab" tabIndex={tab==='deep'?0:-1} aria-controls="deep-panel" aria-selected={tab==='deep'} onClick={()=>selectTab('deep')}>深度批改</button>
    </div>
    {error?<p className="studio-notice error" role="alert">{error}</p>:null}
    {detail?tab==='rewrite'?<div id="rewrite-panel" role="tabpanel" aria-labelledby="rewrite-tab" className="studio-tab-content"><RewriteSpread detail={detail} workspace={workspace} onSubmitted={onSubmitted} onGuided={onGuided}/></div>
      :<div id="deep-panel" role="tabpanel" aria-labelledby="deep-tab" className="studio-tab-content"><DeepReport report={report} detail={detail} workspace={workspace}/></div>
      :<section className="legacy-report"><h2>这一稿的反馈</h2><p>这份历史报告尚未包含新版逐段批改。用原文重新批改，即可得到具体修改建议与完整报告。</p><button className="primary-button" onClick={onRevise}>用原文重新批改<IconArrowRight size={18}/></button>
        <dl className="studio-legacy-scores">{(['TA' in report.criteria?'TA':'TR','CC','LR','GRA'] as const).map(key=>[key,report.criteria[key]] as const).map(([key,score])=><div key={key}><dt>{criterionNames[key]}</dt><dd>{score.toFixed(1)}</dd></div>)}</dl>
        {report.sections.map(section=><section key={section.key}><h3>{section.label}</h3>{section.records.map(item=><p key={item.id}>{item.text}</p>)}</section>)}
      </section>}
  </article>;
}

function RewriteSpread({detail,workspace,onSubmitted,onGuided}:{detail:DetailedReport;workspace:ServerWorkspace;onSubmitted:(id:string)=>void;onGuided:()=>void}) {
  const original=workspace.source?.candidateScript??'';const storageKey='draftloop.rewrite.'+workspace.submissionId;
  const [text,setText]=useState(()=>{try{return localStorage.getItem(storageKey)??original;}catch{return original;}});
  const [timeline,setTimeline]=useState({items:[text],cursor:0});
  const [pending,setPending]=useState(false),[error,setError]=useState('');
  const [showOriginal,setShowOriginal]=useState(false);
  const key=useRef<string|null>(null),inFlight=useRef(false);
  useEffect(()=>{try{localStorage.setItem(storageKey,text);}catch{/* The editor remains usable without browser storage. */}},[storageKey,text]);
  const edit=(next:string)=>{setText(next);key.current=null;setTimeline(old=>{const items=[...old.items.slice(0,old.cursor+1),next].slice(-40);return{items,cursor:items.length-1};});};
  const travel=(delta:number)=>{const cursor=timeline.cursor+delta;if(cursor>=0&&cursor<timeline.items.length){setText(timeline.items[cursor]);setTimeline({...timeline,cursor});key.current=null;}};
  const submit=async(e:FormEvent)=>{e.preventDefault();if(inFlight.current||!text.trim()||!workspace.source)return;inFlight.current=true;setPending(true);setError('');key.current??=crypto.randomUUID();
    try{const id=await api.submit(workspace.source.question,text,key.current,{taskType:detail.taskType,uploadId:workspace.source.uploadId??null,targetBand:detail.targetBand});onSubmitted(id);}
    catch(e){setError((e as Error).message);}finally{inFlight.current=false;setPending(false);}};
  return <div className="rewrite-spread">
    <section className="priority-page"><h2>{detail.priorities.length?`这次只改好 ${detail.priorities.length} 件事`:'这一稿，继续打磨'}</h2><p className="page-introduction">聚焦影响目标分的关键问题，把建议落实到自己的下一稿。</p>
      <ol className="priority-list">{detail.priorities.map((issue,index)=><li className="priority-item" data-category={issue.category} key={issue.id}>
        <span className="priority-number" aria-hidden="true">{String(index+1).padStart(2,'0')}</span><div><div className="priority-title"><h3>{issue.title}</h3><span>{categories[issue.category]}</span></div>
          <dl className="priority-evidence"><div><dt>原句<span>第 {issue.location.paragraphIndex} 段</span></dt><dd><LocatedSentence location={issue.location}/></dd></div>
            {issue.replacement?<div><dt>建议表达</dt><dd lang="en"><mark className="improvement-mark">{issue.replacement}</mark></dd></div>:null}
            <div><dt>怎么改</dt><dd>{issue.action}</dd></div></dl>
          <details className="priority-reason"><summary>为什么改这里</summary><p>{issue.explanation}</p></details>
        </div></li>)}</ol>
      {!detail.priorities.length?<p>{detail.encouragement}</p>:null}
      <button className="text-button guided-entry" onClick={onGuided}>需要逐步提示？进入引导修改<IconArrowRight size={17}/></button>
    </section>
    <section className="rewrite-page"><h2>你的下一稿</h2><p className="page-introduction">{workspace.source?.question}</p>{workspace.source?.uploadId?<UploadedChart id={workspace.source.uploadId}/>:null}
      <form onSubmit={submit}><div className="writing-page-editor"><div className="editor-tools"><label htmlFor="rewrite-essay">作文正文</label><div><button type="button" className="icon-button" disabled={pending||timeline.cursor===0} aria-label="撤销修改" title="撤销修改" onClick={()=>travel(-1)}><IconArrowBackUp size={19}/></button><button type="button" className="icon-button" disabled={pending||timeline.cursor>=timeline.items.length-1} aria-label="重做修改" title="重做修改" onClick={()=>travel(1)}><IconArrowForwardUp size={19}/></button></div></div>
        <textarea id="rewrite-essay" lang="en" value={text} required maxLength={60000} disabled={pending} onChange={e=>edit(e.target.value)} spellCheck aria-label="重写作文正文"/>
      </div>
      {error?<p className="studio-notice error" role="alert">{error}</p>:null}
      <div className="rewrite-footer"><span>{text.trim().match(/\S+/g)?.length??0} 词</span><button type="button" className="text-button" aria-expanded={showOriginal} onClick={()=>setShowOriginal(!showOriginal)}>查看原稿<IconChevronDown size={16}/></button><button className="primary-button" disabled={pending||!text.trim()}>{pending?'正在提交…':'提交重写，深度批改'}<IconArrowRight size={18}/></button></div>
      </form>{showOriginal?<div className="original-drawer"><h3>此报告对应的原稿</h3><p lang="en">{original}</p></div>:null}
    </section>
  </div>;
}

function DeepReport({report,detail,workspace}:{report:ServerReport;detail:DetailedReport;workspace:ServerWorkspace}) {
  const [active,setActive]=useState('score-analysis');
  const navigation=[['score-analysis','评分解读'],['essay-map','文章思路'],['paragraphs','逐段精修'],['topic-learning','主题积累'],['next-training','下一步训练']];
  useEffect(()=>{
    const root=document.querySelector<HTMLElement>('.workspace-viewport');
    if(!root)return;
    let frame=0;
    const update=()=>{
      frame=0;
      const contents=root.querySelector<HTMLElement>('.report-contents');
      const horizontal=contents&&getComputedStyle(contents).flexDirection==='row';
      const threshold=root.getBoundingClientRect().top+(horizontal?contents.getBoundingClientRect().height:0)+48;
      let current='score-analysis';
      for(const id of ['score-analysis','essay-map','paragraphs','topic-learning','next-training']){
        const section=document.getElementById(id);
        if(section&&section.getBoundingClientRect().top<=threshold)current=id;
      }
      setActive(current);
    };
    const schedule=()=>{if(!frame)frame=requestAnimationFrame(update);};
    root.addEventListener('scroll',schedule,{passive:true});
    window.addEventListener('resize',schedule);
    update();
    return()=>{root.removeEventListener('scroll',schedule);window.removeEventListener('resize',schedule);if(frame)cancelAnimationFrame(frame);};
  },[]);
  const audit=report.calibrationAudit;
  return <>
    <div className="academic-score-strip"><div className="academic-score-row"><div className="academic-score-cell academic-overall"><span>本稿得分</span><strong aria-label="服务端评分">{report.overallBand.toFixed(1)}</strong><small>LOCKED SCORE</small></div><div className="academic-criterion-area"><dl>{(['TA' in report.criteria?'TA':'TR','CC','LR','GRA'] as const).map(key=>[key,report.criteria[key]] as const).map(([key,score])=><div key={key}><dt>{criterionNames[key]}</dt><dd>{score.toFixed(1)}</dd></div>)}</dl></div></div><p className="academic-score-note"><strong>你的优势：</strong>{detail.strongestCriteria.map(c=>criterionNames[c]).join('、')}<span> · {detail.criterionAnalyses.find(c=>c.criterion===detail.strongestCriteria[0])?.strengths[0]}</span></p></div>
    <div className="academic-layout"><nav className="report-contents" aria-label="报告目录">{navigation.map(([id,label])=><a key={id} href={'#'+id} aria-current={active===id?'location':undefined} onClick={()=>setActive(id)}>{label}</a>)}</nav>
      <div className="academic-document">
        <section id="score-analysis" className="academic-section"><ReportSectionMarker index="01" label="评分解读" english="SCORE OVERVIEW"/><h2>评分解读</h2><p className="section-lead">看清每一项的得分依据，知道哪些优势值得保留。</p>
          <ReportSectionMarker index="02" label="四项评分" english="FOUR CRITERIA ANALYSIS"/><div className="criterion-analysis-list">{detail.criterionAnalyses.map(criterion=><section key={criterion.criterion}><div className="criterion-analysis-heading"><h3>{criterionNames[criterion.criterion]}</h3><strong>{report.criteria[criterion.criterion].toFixed(1)}</strong>{detail.strongestCriteria.includes(criterion.criterion)?<span className="strength-tag"><IconCheck size={15}/>本篇强项</span>:null}</div><p>{criterion.analysis}</p>
            <div className="strengths-and-limits"><div><h4>做得好的地方</h4>{criterion.strengths.length?<ul>{criterion.strengths.map((item,i)=><li key={i}>{item}</li>)}</ul>:<p>按当前证据判断，继续夯实这一项。</p>}</div><div><h4>限制这一项的因素</h4>{criterion.limitations.length?<ul>{criterion.limitations.map((item,i)=><li key={i}>{item}</li>)}</ul>:<p>当前证据没有显示需要单独指出的限制因素。</p>}</div></div>
          </section>)}</div>
          {audit?<details className="calibration-details"><summary>{audit.status==='RESCORED'?'本次评分已完成重新检索与 Rubric 重评':audit.status==='ACCEPTED_INITIAL'?'独立审核完成，分差未触发重评':'本次未进行 RAG 审核'}<IconChevronDown size={16}/></summary><p>Rubric 初评 {audit.initialBand.toFixed(1)}{audit.auditBand!=null?` · RAG 审核 ${audit.auditBand.toFixed(1)} · 绝对差值 ${audit.absoluteDifference?.toFixed(1)}`:''}</p>{audit.criteria.map(c=><p key={c.criterion}><strong>{criterionNames[c.criterion]}：</strong>{c.rationale}</p>)}{audit.status==='NOT_AVAILABLE'?<p>当前评分依据为 Rubric 与作文证据；RAG 参考资料未就绪。</p>:<p>初评达到 7.0 时，分差达到 0.5 即重评；低于 7.0 时，分差超过 0.5 才重评。最终分数由 Rubric 判断锁定。</p>}</details>:null}
        </section>
        <section id="essay-map" className="academic-section"><ReportSectionMarker index="04" label="文章思路" english="ESSAY MIND MAP"/><h2>文章思路</h2><p className="section-lead">先还原你的表达顺序，再看论点与论据是否接得上。</p><div className="essay-map"><div className="map-thesis"><span>中心立场</span><p>{detail.mindMap.thesis}</p></div><ol>{detail.mindMap.branches.map(branch=><li key={branch.paragraphIndex}><span className="map-paragraph">第 {branch.paragraphIndex} 段</span><div><h3>{branch.role}</h3><p>{branch.point}</p>{branch.support.length?<ul>{branch.support.map((point,i)=><li key={i}>{point}</li>)}</ul>:null}{branch.gap?<p className="map-gap">待补全：{branch.gap}</p>:null}</div></li>)}</ol></div></section>
        <section id="paragraphs" className="academic-section"><ReportSectionMarker index="05" label="逐段精修" english="PARAGRAPH-BY-PARAGRAPH REVIEW"/><h2>逐段精修</h2>{detail.paragraphs.map(paragraph=><ParagraphReview key={paragraph.index} paragraph={paragraph}/>)}</section>
        <section id="topic-learning" className="academic-section"><ReportSectionMarker index="08" label="主题积累" english="TOPIC KNOWLEDGE PACK"/><h2>主题积累 · {detail.topicLearning.theme}</h2><p className="section-lead">把这一篇学会的内容，带到下一道题。</p>
          <ol className="expression-bank">{detail.topicLearning.expressions.map((item,i)=><li key={i}><strong lang="en">{item.expression}</strong><p>{item.meaning}</p><span>{item.usage}</span><small>{item.source==='CANDIDATE'?'来自你的原文':'来自本次优化'}</small></li>)}</ol>
          {detail.topicLearning.examples.map((example,i)=><section className="reusable-example" key={i}><div><h3>{example.title}</h3><span>{example.reuseOf?'复用你的已有例子':'可迁移的假设性例子'}</span></div><p lang="en">{example.scenario}</p><dl><div><dt>论证连接</dt><dd>{example.structure}</dd></div><div><dt>迁移方法</dt><dd>{example.adaptation}</dd></div></dl><div className="topic-tags">{example.relatedTopics.map(topic=><span key={topic}>{topic}</span>)}</div></section>)}
          {!detail.topicLearning.expressions.length&&!detail.topicLearning.examples.length?<p>本篇没有需要单独积累的新素材，先把逐段修改练熟。</p>:null}
        </section>
        <section id="next-training" className="academic-section"><ReportSectionMarker index="10" label="下一步训练" english="WHAT TO KEEP & WHAT TO TRAIN"/><h2>下一步训练</h2><p className="encouragement">{detail.encouragement}</p><ol className="next-actions">{detail.nextActions.map((action,i)=><li key={i}><div><IconTargetArrow size={22} stroke={1.6}/><h3>{action.title}</h3><span>{action.minutes} 分钟</span></div><ol>{action.steps.map((step,j)=><li key={j}>{step}</li>)}</ol><p><strong>完成标准：</strong>{action.successCheck}</p></li>)}</ol></section>
        <details className="report-question"><summary>题目与本稿原文<IconChevronDown size={16}/></summary><p>{workspace.source?.question}</p>{workspace.source?.uploadId?<UploadedChart id={workspace.source.uploadId}/>:null}<p lang="en">{workspace.source?.candidateScript}</p></details>
      </div>
    </div>
  </>;
}

function ReportSectionMarker({index,label,english}:{index:string;label:string;english:string}) {
  return <div className="report-section-marker" aria-hidden="true"><span className="report-section-index">{index}</span><span className="report-section-squares">□□□□□□</span><span className="report-section-label">{label}</span><span className="report-section-english">{english}</span></div>;
}

function ParagraphReview({paragraph}:{paragraph:ReportParagraph}) {
  const p=paragraph;
  const contribution={SUPPORTS:'支撑整体表现',LIMITS:'限制整体表现',NEUTRAL:'表现稳定'};
  return <section className="paragraph-review" id={'paragraph-'+p.index}>
    <header><h3>第 {p.index} 段 · {p.role}</h3><span className="paragraph-impact" data-impact={p.impact}>{contribution[p.impact]}</span></header>
    <p className="paragraph-original" lang="en">{p.original}</p><p className="paragraph-assessment">{p.assessment}</p>
    {p.corrections.length?<div className="correction-table-wrap"><table className="correction-table"><caption className="sr-only">第 {p.index} 段修改对照</caption><thead><tr><th scope="col">原文</th><th scope="col">优化表达</th><th scope="col">修改原因</th></tr></thead><tbody>{p.corrections.map((change,i)=><tr key={i}><td data-label="原文"><span className={'original-correction '+(change.location.missing?'is-missing':'')} lang={change.location.missing?'zh':'en'}>{change.location.missing?'此段缺少相应内容':change.location.quote}</span></td><td data-label="优化表达" lang="en">{change.replacement||'删去重复或不必要的表达'}</td><td data-label="修改原因">{change.reason}</td></tr>)}</tbody></table></div>:<p className="no-corrections"><IconCheck size={17}/>这一段没有需要单独列出的词句修改。</p>}
    {p.corrections.length?<div className="optimized-paragraph"><div><h4>优化后的段落</h4><span>{p.changes.join(' · ')}</span></div><p lang="en">{p.optimized}</p>{p.targetBandEstimate!=null?<details><summary>优化段落教学估计 {p.targetBandEstimate.toFixed(1)}<IconChevronDown size={15}/></summary><p>{p.estimateReason}</p></details>:null}</div>:null}
  </section>;
}
