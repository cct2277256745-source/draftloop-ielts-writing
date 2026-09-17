import { t, currentLocale } from "./locale";
import { useEffect, useState } from "react";
import { BrowserApi, type LearningWorkspace, type WritingSource } from "./application/BrowserApi";

const api = new BrowserApi();
const hintNames = [t("原文位置"), t("问题类型"), t("修改方向"), t("参考表达")];
const labels: Record<string, string> = { REAL_IMPROVEMENT: t("有实质改进"), COSMETIC_CHANGE: t("表达调整"), UNCHANGED: t("问题尚未修改"), NEW_ERROR: t("出现新问题"), OVEREDITED: t("修改过多"), REGRESSION: t("有所退步") };
export function GuidedRevision({ id, source, onReport, onAssess }: { id: string; source: WritingSource; onReport: () => void; onAssess: (text: string) => void }) {
  const storage = "draftloop.revision." + id;
  const [draft, setDraft] = useState(() => { try { const value = JSON.parse(localStorage.getItem(storage) ?? "null"); if (typeof value?.text === "string") return { text: value.text as string, key: typeof value.key === "string" ? value.key : null as string | null, assistance: ["UNKNOWN", "PASTED", "AI_REWRITE"].includes(value.assistance) ? value.assistance as string : "UNKNOWN" }; } catch {} return { text: source.candidateScript, key: null as string | null, assistance: "UNKNOWN" }; });
  const [data, setData] = useState<LearningWorkspace | null>(null), [issueId, setIssueId] = useState("");
  const [error, setError] = useState(""), [busy, setBusy] = useState(false), [revision, refresh] = useState(0);
  const [saved, setSaved] = useState(true);
  useEffect(() => { try { localStorage.setItem(storage, JSON.stringify(draft)); setSaved(true); } catch { setSaved(false); } }, [storage, draft]);
  useEffect(() => {
    let active = true; let timer: ReturnType<typeof setTimeout>;
    const load = async () => {
      try { const value = await api.learning(id); if (!active) return; setData(value); setError("");
        if (value.revisions.some((item) => ["QUEUED", "RUNNING"].includes(item.state))) timer = setTimeout(load, 2000);
      } catch (reason) { if (active) setError(reason instanceof Error ? reason.message : t("修改记录连接中断。")); }
    };
    void load(); return () => { active = false; clearTimeout(timer); };
  }, [id, revision]);
  const issue = data?.issues.find((item) => item.issueId === issueId) ?? data?.issues[0];
  const failedAttempt = data?.revisions.some((item) => item.revisionId === "revision_" + draft.key && item.state === "FAILED");
  const run = async (action: () => Promise<void>) => { setBusy(true); setError(""); try { await action(); refresh((v) => v + 1); } catch (reason) { setError(reason instanceof Error ? reason.message : t("操作未完成，请重试。")); } finally { setBusy(false); } };
  return <section className="state-view live-guided"><div className="live-field-heading"><h1>{t("一次改好一个问题")}</h1><button className="text-button" onClick={onReport}>{t("返回报告")}</button></div>
    <p>{t("先自己尝试，需要时再展开提示。核验比较原稿与修改稿，原评分保持不变。")}</p>
    {error ? <div role="alert" className="live-field-error"><p>{t(error)}</p><button className="text-button" onClick={() => refresh((v) => v + 1)}>{t("重新读取")}</button></div> : null}
    {!data ? <p role="status">{t("正在读取修改依据…")}</p> : !data.available ? <><p>{t("这份较早的报告没有保存引导修改所需的原文定位。原报告仍可查看；重新分析后可使用提示和修改核验。")}</p><button className="primary-button" onClick={() => onAssess(draft.text)}>{t("保留正文，重新分析")}</button></> : <>
      {issue ? <section className="live-hints"><label htmlFor="revision-issue">{t("本次关注")}</label><select id="revision-issue" value={issue.issueId} onChange={(event) => setIssueId(event.target.value)} disabled={busy}>{data.issues.map((item, index) => <option key={item.issueId} value={item.issueId}>{index + 1}. {item.criterion} {t("· 当前稿的问题")}</option>)}</select>
        <ol>{issue.session.revealed.map((hint) => <li key={hint.level}><h3>{t(hintNames[hint.level - 1])}{hint.containsReferenceAnswer ? t(" · AI 参考") : ""}</h3><p className="live-source-text">{currentLocale() === "en-US" ? hint.textEn : hint.textZh}</p>{currentLocale() === "zh-CN" && hint.textEn !== hint.textZh ? <p lang="en" className="live-english-feedback">{hint.textEn}</p> : null}</li>)}</ol>
        <div className="live-actions">{issue.session.status === "STOPPED" ? <button className="secondary-button" disabled={busy} onClick={() => void run(() => api.hint(id, issue.issueId, issue.version, "RESUME", crypto.randomUUID()))}>{t("继续提示")}</button> : <>
          {issue.session.revealed.length < 4 ? <button className="secondary-button" disabled={busy} onClick={() => void run(() => api.hint(id, issue.issueId, issue.version, "REVEAL", crypto.randomUUID()))}>{t("展开")}{t(hintNames[issue.session.revealed.length])}</button> : <p>{t("四级提示已全部展开。")}</p>}
          <button className="text-button" disabled={busy} onClick={() => void run(() => api.hint(id, issue.issueId, issue.version, "STOP", crypto.randomUUID()))}>{t("暂停提示，自己修改")}</button>
        </>}</div><p className="live-private-note">{t("提示使用会保存在此稿的学习记录中。参考表达不代表独立掌握。")}</p>
      </section> : <p>{t("当前证据没有形成可定位的瓶颈；你仍可修改全文并核验变化。")}</p>}
      <form onSubmit={(event) => { event.preventDefault(); const key = failedAttempt ? crypto.randomUUID() : draft.key ?? crypto.randomUUID(); setDraft({ ...draft, key }); void run(() => api.verify(id, draft.text, draft.assistance, key)); }}>
        <label htmlFor="revision-text">{t("修改稿 V2")}</label><textarea id="revision-text" rows={14} maxLength={60000} required disabled={busy} value={draft.text} onChange={(event) => setDraft({ ...draft, text: event.target.value, key: null })} />
        <p className="live-private-note">{saved ? t("修改草稿已保存在此浏览器。") : t("草稿暂时无法保存，请勿关闭页面。")}</p>
        <label htmlFor="revision-assistance">{t("这次修改使用了哪些帮助？")}</label><select id="revision-assistance" value={draft.assistance} disabled={busy} onChange={(event) => setDraft({ ...draft, assistance: event.target.value, key: null })}><option value="UNKNOWN">{t("依据当前反馈修改 / 尚未确认")}</option><option value="PASTED">{t("粘贴了外部参考内容")}</option><option value="AI_REWRITE">{t("使用了 AI 改写")}</option></select>
        <div className="live-actions"><button className="primary-button" disabled={busy || !draft.text.trim()}>{busy ? t("正在提交…") : t("核验这次修改")}</button><button type="button" className="text-button" onClick={() => onAssess(draft.text)}>{t("作为新稿重新评分")}</button></div>
      </form>
      {data.revisions.length ? <section className="live-verifications"><h2>{t("修改核验记录")}</h2>{[...data.revisions].reverse().map((item) => <article key={item.revisionId}>
        <h3>{item.state === "QUEUED" ? t("等待核验") : item.state === "RUNNING" ? t("正在核验原文变化") : item.state === "FAILED" ? t("本次核验未完成") : t("核验结果")}</h3>
        {item.state === "FAILED" ? <p>{t("修改稿已保留。可直接再次核验，也可以继续修改。")}</p> : null}
        {item.summary ? <><p>{t("已解决")}{item.summary.resolvedIssueIds.length} / {item.summary.resolvedIssueIds.length + item.summary.unresolvedIssueIds.length} {t("个原有问题。此结果不表示分数变化。")}</p>
          {item.summary.reviewRequiredClassificationIds.length || item.summary.unclassifiedChangeIds.length ? <p>{t("部分变化仍需复核，尚不能确认改进。")}</p> : null}</> : null}
        {item.classifications?.map((finding) => <div key={finding.classificationId}><h4>{t(labels[finding.label] ?? finding.label)}{finding.status === "REVIEW_REQUIRED" ? t(" · 待复核") : ""}</h4><p>{finding.reason}</p></div>)}
        {item.changes?.length ? <details><summary>{t("查看原文与修改的对应变化")}</summary>{item.changes.map((change) => <div key={change.changeId} className="live-diff"><p><span>{t("原文")}</span><del>{change.originalText || t("（此处为新增）")}</del></p><p><span>{t("修改")}</span><ins>{change.revisedText || t("（已删除）")}</ins></p></div>)}</details> : null}
        <details><summary>{t("查看这次提交的修改稿")}</summary><p className="live-source-text" lang="en">{item.candidateScript}</p></details>
      </article>)}</section> : null}
    </>}
  </section>;
}
