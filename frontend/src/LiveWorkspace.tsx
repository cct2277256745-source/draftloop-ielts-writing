import { t, useLocale, currentLocale } from "./locale";
import { useEffect, useRef, useState, type Dispatch, type SetStateAction, type FormEvent } from "react";
import { BrowserApi, type Criterion, type TaskType, type HistoryEntry, type JobState, type ServerWorkspace, type WritingSource } from "./application/BrowserApi";
import { WorkspaceSettings } from "./ModelSettings";
import { UploadedChart } from "./UploadedChart";
import { RedesignedReport } from "./RedesignedReport";
import { IconPencil, IconFileDescription, IconClock, IconBook, IconSettings, IconArrowRight } from '@tabler/icons-react';
import { GuidedRevision } from "./GuidedRevision";
import { LearningHistory } from "./LearningHistory";

const api = new BrowserApi();
const labels: Record<JobState, string> = {
  QUEUED: t("等待分析"), PROCESSING: t("正在分析"), COMPLETE: t("分析完成"), PARTIAL: t("反馈尚未完整"),
  REVIEW_REQUIRED: t("需要复核"), FAILED: t("分析未完成"), CANCELLED: t("已取消"),
};
const taskCriteria = (task?: string): Criterion[] => [task === "task1" ? "TA" : "TR", "CC", "LR", "GRA"];
const criterionNames: Record<Criterion, string> = { TA: t("任务完成情况"), TR: t("任务回应"), CC: t("连贯与衔接"), LR: t("词汇资源"), GRA: t("语法多样性及准确性") };
const stages = ["TASK_UNDERSTANDING", "STUDENT_EVIDENCE", "CRITERION_SCORING", "RAG_AUDIT", "RESCORING", "COACHING", "DEEP_COACHING", "POST_SCORE_READY"];
const stageNames = [t("理解题目"), t("核对原文证据"), t("四项独立评分"), '独立审核评分', '按需重新评分', t("整理修改建议"), '逐段生成报告', t("生成报告")];
const failureCopy: Record<string, string> = {
  NETWORK_ERROR: t("模型连接中断"), TIMEOUT: t("模型响应超时"), SCHEMA_ERROR: t("模型返回格式无效"),
  QUOTA_EXCEEDED: t("模型账户余额或 API 额度不足，请恢复额度后重新分析"),
  TRUNCATED_RESPONSE: t("模型输出被截断"), EMPTY_RESPONSE: t("模型没有返回内容"), CONTENT_FILTERED: t("模型服务中止了生成"),
  HTTP_ERROR: t("模型服务暂时不可用"), MISSING_CREDENTIALS: t("尚未配置模型密钥"),
  TASK_UNDERSTANDING_FAILED: t("题目要求尚未成功核对。可以保留原文重新分析。"),
  STUDENT_EVIDENCE_FAILED: t("模型引用的原文未能通过核对。请重新分析，正文已保留。"),
  ASSESSMENT_INCOMPLETE: t("部分评分尚未完成，因此本次没有生成总分。可以使用原文重新分析。"),
  SCORE_REVIEW_REQUIRED: t("四项结果需要进一步复核，本次暂不发布分数。"),
  COACHING_FAILED: t("评分后的修改建议生成失败。可以保留原文重新分析。"),
  COACHING_INCOMPLETE: t("修改建议尚未完整。可以查看原文并重新分析。"),
  CALIBRATION_REVIEW_REQUIRED: '本次评分尚未完成 RAG 审核，暂不发布分数。请核对参考资料与模型配置后重试。',
  TASK1_RAG_AUDIT_INCOMPLETE: '当前小作文参考证据不足，尚未完成四项审核。原文和图表已保留。',
  DEEP_COACHING_FAILED: '逐段报告未通过原句核对。原文已保留，可以重新批改。',
  PREFLIGHT_FAILED: t("模型或评分资源尚未就绪，请检查本地配置后重试。"),
  TASK1_PREFLIGHT_FAILED: t("图表或图表模型尚未就绪，请检查图片与本地模型配置。"),
  TASK1_CHART_FACTS_INCOMPLETE: t("两次独立图表识别尚未取得一致结果。请检查图片是否清晰完整，再重新分析。"),
  TASK1_CHART_CLAIMS_INCOMPLETE: t("作文中的图表描述尚未完成核对。原文与图表已保留。"),
  TASK1_CRITERION_SCORING_INCOMPLETE: t("部分小作文评分需要复核，本次暂不发布分数。"),
  TASK1_COACHING_FAILED: t("小作文修改建议生成失败。原文与图表已保留。"),
};
const draftKey = "draftloop.writing-draft.v1";
type Draft = { question: string; essay: string; key: string | null; taskType: TaskType; uploadId: string | null; imageName: string | null; targetBand: number | null };
type View = "new" | "history" | "report" | "revise" | "privacy" | "settings" | "guided" | "learning";
function initialView(): View {
  const query = new URLSearchParams(location.search), view = query.get('view');
  if (view && ['new', 'history', 'revise', 'privacy', 'settings', 'learning'].includes(view)) return view==='privacy'?'settings':view as View;
  return query.has('submission') ? view === 'guided' ? 'guided' : 'report' : 'new';
}
const emptyDraft = (): Draft => ({ question: "", essay: "", key: null, taskType: "task2", uploadId: null, imageName: null, targetBand: null });
function loadDraft(): Draft {
  try {
    const value = JSON.parse(localStorage.getItem(draftKey) ?? "null");
    if (typeof value?.question === "string" && typeof value?.essay === "string") return {
      ...emptyDraft(), question: value.question, essay: value.essay, key: typeof value.key === "string" ? value.key : null,
      taskType: value.taskType === "task1" ? "task1" : "task2",
      uploadId: typeof value.uploadId === "string" ? value.uploadId : null,
      imageName: typeof value.imageName === "string" ? value.imageName : null,
      targetBand: typeof value.targetBand === "number" && value.targetBand >= 0 && value.targetBand <= 9 ? value.targetBand : null,
    };
  } catch { /* Storage is optional; editing remains available. */ }
  return emptyDraft();
}
function dateLabel(value?: string) {
  const date = value ? new Date(value) : null;
  return date && Number.isFinite(date.getTime()) ? new Intl.DateTimeFormat(currentLocale(), { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date) : "";
}

export function LiveWorkspace() {
  const [locale, setLocale] = useLocale();
  useEffect(() => { document.documentElement.lang = locale; }, [locale]);
  const [connected, setConnected] = useState(false);
  const [error, setError] = useState("");
  const [attempt, setAttempt] = useState(0);
  const [submissionId, setSubmissionId] = useState<string | null>(() => new URLSearchParams(location.search).get("submission"));
  const [view, setView] = useState<View>(initialView);
  const [workspace, setWorkspace] = useState<ServerWorkspace | null>(null);
  const [guidedSource, setGuidedSource] = useState<WritingSource | null>(null);
  const [history, setHistory] = useState<HistoryEntry[]>([]);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [draft, setDraft] = useState<Draft>(loadDraft);
  const [saved, setSaved] = useState(true);
  const [menuOpen, setMenuOpen] = useState(false);
  const [compact, setCompact] = useState(() => window.matchMedia("(max-width: 900px)").matches);
  const menuTrigger = useRef<HTMLButtonElement>(null);
  const sidebar = useRef<HTMLElement>(null);
  const [cancelPending, setCancelPending] = useState(false);
  const activeRoute = useRef({view, submissionId});
  activeRoute.current = {view, submissionId};
  useEffect(() => {
    const restore = () => {
      const nextView = initialView(), nextId = new URLSearchParams(location.search).get('submission');
      if (activeRoute.current.view === nextView && activeRoute.current.submissionId === nextId) return;
      setView(nextView); setSubmissionId(nextId);
      setWorkspace(null); setError(''); setAttempt((value) => value + 1);
    };
    window.addEventListener('popstate', restore);
    return () => window.removeEventListener('popstate', restore);
  }, []);

  useEffect(() => {
    try { localStorage.setItem(draftKey, JSON.stringify(draft)); setSaved(true); }
    catch { setSaved(false); }
  }, [draft]);
  useEffect(() => {
    const cleared = (event: StorageEvent) => {
      if ((event.key === draftKey || event.key === null) && event.newValue === null) {
        setDraft(emptyDraft()); setWorkspace(null); setSubmissionId(null); setHistory([]);
        setView("new"); setConnected(false); setAttempt((value) => value + 1);
        window.history.replaceState(null, "", location.pathname);
      }
    };
    window.addEventListener("storage", cleared);
    return () => window.removeEventListener("storage", cleared);
  }, []);
  useEffect(() => {
    const query = window.matchMedia("(max-width: 900px)");
    const update = () => { setCompact(query.matches); if (!query.matches) setMenuOpen(false); };
    query.addEventListener("change", update);
    return () => query.removeEventListener("change", update);
  }, []);
  useEffect(() => {
    if (!menuOpen || !compact) return;
    sidebar.current?.querySelector<HTMLButtonElement>("button")?.focus();
    const keyboard = (event: KeyboardEvent) => {
      if (event.key === "Escape") { setMenuOpen(false); menuTrigger.current?.focus(); }
      if (event.key === "Tab") {
        const buttons = [...(sidebar.current?.querySelectorAll<HTMLButtonElement>("button:not(:disabled)") ?? [])];
        const first = buttons[0], last = buttons[buttons.length - 1];
        if (event.shiftKey && document.activeElement === first) { event.preventDefault(); last?.focus(); }
        else if (!event.shiftKey && document.activeElement === last) { event.preventDefault(); first?.focus(); }
      }
    };
    document.addEventListener("keydown", keyboard);
    return () => document.removeEventListener("keydown", keyboard);
  }, [menuOpen, compact]);
  useEffect(() => {
    let current = true;
    void api.connect().then(() => { if (current) { setConnected(true); setError(""); } }).catch(() => {
      if (current) { setConnected(false); setError(t("无法连接本地工作区，请重新连接。已经写下的内容仍然保留。")); }
    });
    return () => { current = false; };
  }, [attempt]);
  useEffect(() => {
    if (!connected || !submissionId || view !== 'report') return;
    let current = true, failures = 0;
    let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try {
        const next = await api.workspace(submissionId);
        if (!current) return;
        failures = 0; setWorkspace(next); setError("");
        if (["QUEUED", "PROCESSING"].includes(next.semanticResult.state)) timer = setTimeout(load, 2000);
      } catch (reason) {
        if (!current) return;
        setError(reason instanceof Error ? reason.message : t("报告连接中断，请重新连接。"));
        if (++failures < 3) timer = setTimeout(load, 3000 * failures);
      }
    };
    void load();
    return () => { current = false; clearTimeout(timer); };
  }, [connected, submissionId, attempt, view]);
  useEffect(() => {
    if (!connected || !submissionId || view !== 'guided') return;
    let active = true; setGuidedSource(null);
    void api.revisionSource(submissionId).then((source) => { if (active) setGuidedSource(source); })
      .catch((reason: Error) => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, [connected, submissionId, view, attempt]);
  useEffect(() => {
    if (!connected || view !== "history") return;
    let current = true; setHistoryLoading(true);
    void api.history().then((items) => { if (current) setHistory(items); }).catch((reason) => {
      if (current) setError(reason instanceof Error ? reason.message : t("历史记录读取失败。"));
    }).finally(() => { if (current) setHistoryLoading(false); });
    return () => { current = false; };
  }, [connected, view, attempt]);

  const focusMain = () => setTimeout(() => {
    document.getElementById("main-content")?.focus({preventScroll:true});
    document.querySelector('.workspace-viewport')?.scrollTo?.({top:0,left:0,behavior:'instant'});
  }, 0);
  const open = (id: string, mode: 'report' | 'guided' = 'report', tab?:'deep') => {
    setError(""); setWorkspace(null); setSubmissionId(id); setView(mode); setMenuOpen(false);
    window.history.pushState(null, "", "?submission=" + encodeURIComponent(id) + (mode === 'guided' ? '&view=guided' : '')+(tab==='deep'?'&report=deep':'')); focusMain();
  };
  const navigate = (next: View) => {
    setView(next); setMenuOpen(false); setError("");
    window.history.pushState(null, "", (next === "report" || next === "guided") && submissionId
      ? "?submission=" + encodeURIComponent(submissionId) + (next === "guided" ? '&view=guided' : '')
      : next === 'new' ? location.pathname : '?view=' + next);
    focusMain();
  };
  const revise = (source: WritingSource) => {
    setDraft({ question: source.question, essay: source.candidateScript, key: null,
      taskType: source.taskType === "task1" ? "task1" : "task2", uploadId: source.uploadId ?? null,
      imageName: source.uploadId ? t("上一稿的图表") : null, targetBand: source.targetBand ?? null }); navigate("revise");
  };
  const reconnect = () => { setError(""); setAttempt((value) => value + 1); };
  const state = workspace?.semanticResult.state;
  const report = workspace?.presentation.state === "NORMAL" && state === "COMPLETE" ? workspace.presentation.report : null;
  const title = { new: t("新建写作"), history: t("历史记录"), report: t("写作报告"), revise: t("修改这一稿"), privacy: '设置', settings:'设置', guided: t("引导修改"), learning: '学习积累' }[view];
  const taskLabel = (view === "new" || view === "revise" ? draft.taskType : view === "guided" ? guidedSource?.taskType : workspace?.source?.taskType) === "task1" ? t("小作文") : t("大作文");

  return <div className="app-shell live-shell">
    <a className="skip-link" href="#main-content">{t("跳到主要内容")}</a>
    {compact && menuOpen ? <button className="live-nav-scrim" tabIndex={-1} aria-label={t("关闭菜单")} onClick={() => { setMenuOpen(false); menuTrigger.current?.focus(); }} /> : null}
    <aside ref={sidebar} id="live-navigation" className="sidebar" data-mobile-open={menuOpen} role={compact && menuOpen ? "dialog" : undefined} aria-modal={compact && menuOpen ? true : undefined} aria-label={t("主导航")} inert={compact && !menuOpen}>
      <button className="wordmark" onClick={() => navigate("new")}>DraftLoop</button>
      {compact ? <button className="text-button live-close-menu" onClick={() => { setMenuOpen(false); menuTrigger.current?.focus(); }}>{t("收起菜单")}</button> : null}
      <nav className="navigation-stack"><div className="nav-group">
        <button className="nav-item" aria-current={view === "new" || view === "revise" ? "page" : undefined} onClick={() => navigate("new")}><IconPencil size={24} stroke={1.6}/>{t("新建写作")}</button>
        <button className="nav-item" disabled={!submissionId} aria-current={view === "report" ? "page" : undefined} onClick={() => navigate("report")}><IconFileDescription size={24} stroke={1.6}/>{t("写作报告")}</button>
        <button className="nav-item" aria-current={view === "history" ? "page" : undefined} onClick={() => navigate("history")}><IconClock size={24} stroke={1.6}/>{t("历史记录")}</button>
        <button className="nav-item" aria-current={view === "learning" ? "page" : undefined} onClick={() => navigate("learning")}><IconBook size={24} stroke={1.6}/>学习积累</button>
      </div></nav>
      <button className="nav-item settings-navigation" aria-current={view === "settings" || view === 'privacy' ? "page" : undefined} onClick={() => navigate("settings")}><IconSettings size={24} stroke={1.6}/>设置</button>
        <button className="text-button live-locale" aria-label={locale === 'zh-CN' ? 'Switch interface to English' : '切换为中文界面'} onClick={() => setLocale(locale === 'zh-CN' ? 'en-US' : 'zh-CN')}>{locale === 'zh-CN' ? 'English' : '中文'}</button>
    </aside>
    <div className="workspace-frame" inert={compact && menuOpen}>
      <header className="topbar">
        <button ref={menuTrigger} className="mobile-menu-button" aria-controls="live-navigation" aria-expanded={menuOpen} onClick={() => setMenuOpen(true)}>{t("菜单")}</button>
        <p className="breadcrumb">{title}<span aria-hidden="true">/</span>{taskLabel}</p>
        <span className="analysis-status" role="status">{connected ? state && view === "report" ? t(labels[state]) : t("已连接") : error ? t("连接中断") : t("正在连接")}</span>
      </header>
      <div className="workspace-viewport"><main id="main-content" tabIndex={-1}>
        {error ? <div className="live-error" role="alert"><p>{t(error)}</p><button className="text-button" onClick={reconnect}>{t("重新连接")}</button></div> : null}
        {view === "new" || view === "revise" ? <SubmissionForm draft={draft} update={setDraft} connected={connected} saved={saved} revision={view === "revise"} onSubmitted={open} />
          : !connected ? <section className="state-view" aria-busy={!error}><h1>{t("连接本地工作区")}</h1><p>{t("正在读取你的写作记录。")}</p></section>
          : view === "privacy" || view==='settings' ? <WorkspaceSettings onDeleted={() => { setDraft(emptyDraft()); setWorkspace(null); setSubmissionId(null); setHistory([]); setConnected(false); navigate("new"); reconnect(); }} />
          : view === "learning" ? <LearningHistory onOpen={open} onNew={() => navigate("new")} />
          : view === "history" ? <section className="state-view live-history-view"><h1>{t("历史记录")}</h1><p>{t("每一稿的分析结果都保存在这里。")}</p>
            {historyLoading ? <p role="status">{t("正在读取记录…")}</p> : history.length === 0 ? <div className="live-empty"><p>{t("还没有写作记录。完成第一稿，开始积累自己的修改经验。")}</p><button className="primary-button" onClick={() => navigate("new")}>{t("开始第一稿")}</button></div>
              : <ul className="live-history">{history.map((item) => <li key={item.submissionId}><button onClick={() => open(item.submissionId)}>
                <span className="live-history-title">{item.title || t("写作")}<small>{item.taskType === "task1" ? t("小作文 · ") : ""}{dateLabel(item.createdAt)}</small></span><span className="live-history-state" data-state={item.state}>{t(labels[item.state])}</span>
              </button>{item.state === 'COMPLETE' ? <button className="text-button live-history-hint" onClick={() => open(item.submissionId, 'guided')}>{t('逐步提示与修改核验')}</button> : null}</li>)}</ul>}</section>
          : view === 'guided' && guidedSource && submissionId ? <GuidedRevision key={submissionId} id={submissionId} source={guidedSource} onReport={() => navigate('report')} onAssess={(text) => revise({ ...guidedSource, candidateScript: text })} />
          : !workspace || view === 'guided' ? <section className="state-view" aria-busy="true"><h1>{t("读取报告")}</h1><p>{t("正在读取这一稿的分析结果。")}</p></section>
          : report ? <RedesignedReport key={workspace.submissionId} report={report} workspace={workspace} onSubmitted={(id)=>open(id,'report','deep')} onRevise={() => workspace.source && revise(workspace.source)} onGuided={() => navigate("guided")} />
          : <section className="state-view" data-state={state}>
            <h1>{state === "COMPLETE" ? t("报告暂不可用") : t(labels[state!])}</h1>
            {state === "QUEUED" || state === "PROCESSING" ? <>
              <Progress workspace={workspace} />
              <p>{t("分析完成后会自动显示报告。你可以离开此页，稍后从历史记录继续查看。")}</p>
              <button className="text-button" disabled={cancelPending} onClick={async () => {
                setCancelPending(true);
                try { await api.cancel(submissionId!, crypto.randomUUID()); setAttempt((value) => value + 1); }
                catch (reason) { setError(reason instanceof Error ? reason.message : t("取消请求未确认，请重新连接后查看状态。")); }
                finally { setCancelPending(false); }
              }}>{cancelPending ? t("正在请求取消") : t("取消本次分析")}</button>
            </> : <>
              <p>{state === "CANCELLED" ? t("本次分析已取消，题目和正文仍可继续修改。") : t(failureCopy[workspace.semanticResult.failureCode ?? ""] ?? "") || t("本次结果尚未通过核对，暂时无法提供完整报告。你的原文已保留。")}</p>
              {workspace.assessmentIssues?.length ? <ul className="live-issues">{workspace.assessmentIssues.map((item) => <li key={item.criterion}><strong>{item.criterion} · {t(criterionNames[item.criterion])}</strong><span>{item.status === "ASSESSED" ? t("已完成，等待整体复核") : t(failureCopy[item.failureCode ?? ""] ?? "") || t("评分依据尚未通过核对")}</span></li>)}</ul> : null}
              <button className="primary-button" onClick={() => workspace.source ? revise(workspace.source) : navigate("new")}>{workspace.source ? t("保留原文，重新分析") : t("返回新建写作")}</button>
              {workspace.source ? <SourceWriting source={workspace.source} /> : null}
            </>}
          </section>}
      </main></div>
    </div>
  </div>;
}

function SubmissionForm({ draft, update, connected, saved, revision, onSubmitted }: {
  draft: Draft; update: Dispatch<SetStateAction<Draft>>; connected: boolean; saved: boolean; revision: boolean; onSubmitted: (id: string) => void;
}) {
  const [pending, setPending] = useState(false), [error, setError] = useState("");
  const [uploading, setUploading] = useState(false);
  const submitting = useRef(false);
  const words = draft.essay.trim().match(/\S+/g)?.length ?? 0;
  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (submitting.current || uploading || !connected || !draft.question.trim() || !draft.essay.trim() || (draft.taskType === "task1" && !draft.uploadId)) return;
    submitting.current = true; setPending(true); setError("");
    const key = draft.key ?? crypto.randomUUID(); update({ ...draft, key });
    try { onSubmitted(await api.submit(draft.question, draft.essay, key, {
      taskType: draft.taskType, uploadId: draft.taskType === "task1" ? draft.uploadId : null, targetBand: draft.targetBand })); }
    catch (reason) { setError(reason instanceof Error ? reason.message : t("提交未确认，请重试。输入已保留。")); }
    finally { submitting.current = false; setPending(false); }
  };
  return <section className="state-view live-composer"><h1>{revision ? t("让这一稿更进一步") : t("新建写作")}</h1>
    <p>{revision ? t("根据反馈修改，再提交新一轮分析。上一稿的报告会保留在历史记录中。") : t("贴上题目和正文，找出最值得改进的地方。")}</p>
    <form onSubmit={submit}>
      <div className="live-form-options"><label>{t("写作任务")}<select value={draft.taskType} disabled={pending || uploading} onChange={(event) => update({ ...draft, taskType: event.target.value as TaskType, uploadId: null, imageName: null, key: null })}><option value="task2">{t("Task 2 · 大作文")}</option><option value="task1">{t("Task 1 · 学术类小作文")}</option></select></label>
        <label>{t("目标分数（可选）")}<select value={draft.targetBand ?? ""} disabled={pending || uploading} onChange={(event) => update({ ...draft, targetBand: event.target.value ? Number(event.target.value) : null, key: null })}><option value="">{t("暂不设置")}</option>{[4, 4.5, 5, 5.5, 6, 6.5, 7, 7.5, 8, 8.5, 9].map((value) => <option key={value} value={value}>{value.toFixed(1)}</option>)}</select></label></div>
      <div className="writing-entry-spread"><div className="prompt-page"><div className="live-field-heading"><label htmlFor="question">{t("作文题目")}</label><span>{draft.taskType === "task1" ? "Task 1" : "Task 2"}</span></div>
      <textarea id="question" required maxLength={10000} value={draft.question} disabled={pending || uploading} rows={3} placeholder={t("粘贴完整的英文题目，包括写作要求…")} onChange={(event) => update({ ...draft, question: event.target.value, key: null })} />
      {draft.taskType === "task1" ? <div className="live-chart-input"><label htmlFor="chart-image">{t("题目图表")}</label><p className="live-private-note">{t("PNG、JPG 或 WebP，最大 10 MiB。请保留标题、坐标、图例和数据。")}</p>
        <input id="chart-image" type="file" accept="image/png,image/jpeg,image/webp" disabled={!connected || pending || uploading} onChange={async (event) => {
          const file = event.target.files?.[0]; if (!file) return;
          setUploading(true); setError("");
          try { const uploadId = await api.upload(file); update((current) => current.taskType === "task1" && current.question === draft.question && current.essay === draft.essay ? { ...current, uploadId, imageName: file.name, key: null } : current); }
          catch (reason) { setError(reason instanceof Error ? reason.message : t("图表上传失败，请重试。")); }
          finally { setUploading(false); }
        }} />
        {uploading ? <p role="status">{t("正在核对并上传图表…")}</p> : draft.uploadId ? <><p>{draft.imageName || t("图表已上传")}</p><UploadedChart id={draft.uploadId} /><button type="button" className="text-button" disabled={pending} onClick={() => update({ ...draft, uploadId: null, imageName: null, key: null })}>{t("移除此稿图表")}</button></> : null}
      </div> : null}
      <div className="prompt-guidance"><h2>写作前，先抓住题目要求</h2><p>{draft.taskType==='task1'?'确认图表对象、时间范围和主要特征，再组织概述与比较。':'确认需要讨论的对象和问题，确定立场，再选择能够支持观点的论据。'}</p></div>
      </div><div className="essay-entry-page"><div className="live-field-heading"><label htmlFor="essay">{t("你的作文")}</label><span aria-live="polite">{words} {t("词")}</span></div>
      <textarea id="essay" lang="en" required maxLength={60000} value={draft.essay} disabled={pending || uploading} rows={15} placeholder={t("在这里写作，或粘贴你的英文作文…")} aria-describedby="essay-help" onChange={(event) => update({ ...draft, essay: event.target.value, key: null })} />
      <div className="live-writing-meta"><p id="essay-help">{t("建议不少于")}{draft.taskType === "task1" ? 150 : 250} {t("词。保留原始分段即可。")}</p>{!saved?<p role="status">{t("当前浏览器无法保存草稿，请勿关闭页面")}</p>:null}</div>
      {error ? <p className="live-field-error" role="alert">{t(error)}</p> : null}
      <div className="live-actions"><button className="primary-button" disabled={!connected || pending || uploading || (draft.taskType === "task1" && !draft.uploadId) || !draft.question.trim() || !draft.essay.trim()}>{pending ? t("正在提交") : revision ? t("提交新一稿") : t("立即批改")}<IconArrowRight size={18}/></button>
        {draft.essay || draft.question ? <button type="button" className="text-button" disabled={pending || uploading} onClick={() => { if (window.confirm(t("清空此浏览器中的当前草稿？历史记录会保留。"))) update(emptyDraft()); }}>{t("清空草稿")}</button> : null}
      </div>
      </div></div>
    </form>
  </section>;
}

function Progress({ workspace }: { workspace: ServerWorkspace }) {
  const [now, setNow] = useState(Date.now());
  useEffect(() => { const timer = setInterval(() => setNow(Date.now()), 1000); return () => clearInterval(timer); }, []);
  const progress = workspace.progress;
  const task1 = workspace.source?.taskType === "task1";
  const candidateStages = task1 ? ["CHART_FACTS", "CHART_CLAIMS", ...stages.slice(2)] : stages;
  const currentStages = candidateStages.filter(stage => stage !== "RESCORING" || progress?.rescoreTriggered);
  const candidateNames = task1 ? [t("独立核对图表"), t("核对作文数据"), ...stageNames.slice(2)] : stageNames;
  const names = candidateNames.filter((_, i) => candidateStages[i] !== "RESCORING" || progress?.rescoreTriggered);
  const stage = progress?.stage === "RAG_RETRIEVAL" ? (progress.rescoreTriggered ? "RESCORING" : "RAG_AUDIT") : progress?.stage === "LOCKED_SCORE" ? "COACHING" : progress?.stage;
  const seconds = progress?.startedAt ? Math.max(0, Math.floor(now / 1000 - progress.startedAt)) : 0;
  const index = workspace.semanticResult.state === "QUEUED" ? -1 : Math.max(0, currentStages.indexOf(stage ?? currentStages[0]));
  return <div className="live-progress" aria-label={t("分析进度")}>
    <p role="status">{index < 0 ? t("等待前面的分析完成") : t(names[index])}{seconds > 0 ? ` · ${Math.floor(seconds / 60)}m ${seconds % 60}s` : ""}</p>
    <ol>{names.map((name, n) => <li key={name} data-progress={n < index ? "done" : n === index ? "current" : "waiting"} aria-current={n === index ? "step" : undefined}><span>{t(name)}</span><small>{n < index ? t("已完成") : n === index ? t("进行中") : t("等待")}</small></li>)}</ol>
    {progress?.stage === "CRITERION_SCORING" ? <p className="live-private-note">{taskCriteria(workspace.source?.taskType).map((key) => `${key} ${progress.criteria[key] === "ASSESSED" ? t("已完成") : progress.criteria[key] === "FAILED" ? t("需要重试") : t("分析中")}`).join(" · ")}</p> : null}
    {seconds > 120 ? <p className="live-private-note">{t("模型正在核对评分依据，耗时可能较长。无需重复提交。")}</p> : null}
  </div>;
}

function SourceWriting({ source }: { source: WritingSource }) {
  return <details className="full-report live-source"><summary>{t("查看题目与原文")}</summary><h3>{t("题目")}</h3><p>{source.question}</p>{source.uploadId ? <UploadedChart id={source.uploadId} /> : null}<h3>{t("原文")}</h3><p lang="en">{source.candidateScript}</p></details>;
}
