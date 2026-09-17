import { t, currentLocale } from "./locale";
import { useEffect, useState } from 'react';
import { BrowserApi, type LearningMemory, type NextPractice } from './application/BrowserApi';
const api = new BrowserApi();
const mastery: Record<string, string> = { EXPOSURE: t("已接触，待练习"), GUIDED_USE: t("在提示下运用"), INDEPENDENT_USE: t("独立运用"), CROSS_TOPIC_TRANSFER: t("跨话题迁移"), STABLE_MASTERY: t("稳定掌握") };
const help: Record<string, string> = { UNKNOWN: t("帮助来源尚未确认"), LOCATION: t("查看了原文位置"), CATEGORY: t("查看了问题类型"), GUIDANCE: t("使用了修改方向提示"), REFERENCE: t("看过 AI 参考"), AI_REWRITE: t("使用 AI 改写"), PASTED: t("粘贴外部内容"), NONE: t("无辅助") };
export function LearningHistory({ onOpen, onNew }: { onOpen: (id: string) => void; onNew: () => void }) {
  const [data, setData] = useState<LearningMemory | null>(null), [practice, setPractice] = useState<NextPractice | null>(null);
  const [error, setError] = useState(''), [attempt, refresh] = useState(0), [busy, setBusy] = useState(false);
  useEffect(() => { let active = true; void Promise.all([api.memory(), api.practice()]).then(([memory, next]) => { if (active) { setData(memory); setPractice(next); setError(''); } }).catch((reason: Error) => { if (active) setError(reason.message); }); return () => { active = false; }; }, [attempt]);
  const corrected = new Set(data?.events.map((item) => item.targetEventId).filter(Boolean));
  return <section className="state-view live-learning"><h1>{t("学习与练习")}</h1><p>{t("从当前稿的具体问题出发。学习记录会注明提示和参考的使用情况。")}</p>
    {error ? <div role="alert"><p>{t(error)}</p><button className="text-button" onClick={() => refresh((v) => v + 1)}>{t("重新读取")}</button></div> : null}
    {!data ? <p role="status">{t("正在读取学习记录…")}</p> : <>
      <h2>{t("下一步练习")}</h2>{practice?.submissionId ? <><p className="live-english-feedback" lang="en">{practice.question}</p>{practice.sections.filter((section) => section.key === 'next_actions').flatMap((section) => section.records).map((item) => <p key={item.id}>{currentLocale() === "en-US" ? item.textEn || item.text : item.text}</p>)}<button className="primary-button" onClick={() => onOpen(practice.submissionId!)}>{t("打开这一稿，继续修改")}</button></> : <><p>{t("完成一份写作分析后，这里会显示有当前证据支持的练习建议。")}</p><button className="primary-button" onClick={onNew}>{t("开始写作")}</button></>}
      <h2>{t("话题学习")}</h2>{practice?.sections.some((s) => s.key === 'topic_learning') ? practice.sections.filter((s) => s.key === 'topic_learning').flatMap((s) => s.records).map((item) => <p key={item.id}>{currentLocale() === "en-US" ? item.textEn || item.text : item.text}</p>) : <p>{t("当前没有适用的话题参考。你可以先按本稿的修改建议练习。")}</p>}
      <h2>{t("掌握情况")}</h2><p className="live-private-note">{t("AI 参考、粘贴内容和来源未确认的修改不会提高独立掌握等级。跨话题与稳定掌握需要更多独立证据。")}</p>
      {data.mastery.length ? <ul className="live-mastery">{data.mastery.map((item) => <li key={item.skillKey}><strong>{item.skillKey.split(':')[0]}</strong><span>{t(mastery[item.state] ?? item.state)}</span><details><summary>{t("这项记录的依据")}</summary><p>{t("关联")}{item.evidenceEventIds.length} {t("次学习记录，其中")}{item.independentEventIds.length} {t("次可作为独立运用证据。")}</p></details></li>)}</ul> : <p>{t("还没有修改核验记录。完成一次修改并核验后，就能开始积累。")}</p>}
      <h2>{t("学习记录")}</h2>{data.events.filter((e) => e.kind === 'DEMONSTRATION').length ? <ol className="live-learning-events">{[...data.events].reverse().filter((e) => e.kind === 'DEMONSTRATION').map((event) => <li key={event.eventId}><p><strong>{event.skillKey.split(':')[0]}</strong> · {corrected.has(event.eventId) ? t("已标记为待复核") : event.demonstrated ? t("此次修改有改进证据") : t("尚未形成改进证据")}</p><p className="live-private-note">{new Date(event.occurredAt).toLocaleString(currentLocale())} · {t(help[event.assistanceDepth] ?? event.assistanceDepth)}</p>
        {event.submissionId ? <button className="text-button" onClick={() => onOpen(event.submissionId!)}>{t("查看对应写作")}</button> : null}
        {!corrected.has(event.eventId) ? <button className="text-button" disabled={busy} onClick={async () => { setBusy(true); try { await api.correctMemory(event.eventId); refresh((v) => v + 1); } catch (reason) { setError(reason instanceof Error ? reason.message : t("更正未完成。")); } finally { setBusy(false); } }}>{t("这条记录不准确")}</button> : null}
      </li>)}</ol> : <p>{t("尚无记录。")}</p>}
    </>}
  </section>;
}
