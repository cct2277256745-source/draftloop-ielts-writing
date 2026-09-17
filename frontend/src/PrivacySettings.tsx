import { t } from "./locale";
import { useEffect, useState } from "react";
import { BrowserApi, type PrivacySettings as Settings } from "./application/BrowserApi";

const api = new BrowserApi();
export function PrivacySettings({ onDeleted, embedded=false }: { onDeleted: () => void; embedded?: boolean }) {
  const Heading = embedded ? "h2" : "h1";
  const [settings, setSettings] = useState<Settings | null>(null);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [confirm, setConfirm] = useState("");
  const [receipt, setReceipt] = useState<string | null>(null);
  useEffect(() => { let active = true; void api.privacy().then((value) => { if (active) setSettings(value); }).catch((reason: Error) => { if (active) setError(reason.message); }); return () => { active = false; }; }, []);
  const run = async (action: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await action(); } catch (reason) { setError(reason instanceof Error ? reason.message : t("操作未完成，请重试。")); }
    finally { setBusy(false); }
  };
  if (receipt) return <section className="state-view"><Heading>{t("个人数据已删除")}</Heading><p>{t("作文、图表、报告、学习记录和原会话已清除。")}</p><p>{t("删除回执：")}{receipt}</p><button className="primary-button" onClick={onDeleted}>{t("打开空白工作区")}</button></section>;
  return <section className="state-view live-privacy"><Heading>{t("个人数据")}</Heading><p>{t("写作记录保存在此电脑。分析时，题目、正文和图表会发送至你配置的模型服务。")}</p>
    {error ? <p role="alert" className="live-field-error">{t(error)}</p> : null}
    <h2>{t("可选数据用途")}</h2><p>{t("默认关闭。更改这些选择不影响作文批改，也不会开启额外的数据收集程序。")}</p>
    {!settings ? <p role="status">{t("正在读取设置…")}</p> : settings.consents.map((item) => <label className="live-consent" key={item.purpose}><input type="checkbox" checked={item.granted} disabled={busy} onChange={(event) => { const granted = event.target.checked; void run(async () => { await api.consent(item.purpose, granted, settings.policyVersion); setSettings(await api.privacy()); }); }} /><span>{item.purpose === "TRAINING" ? t("允许将数据用于模型训练") : t("允许将数据用于产品使用分析")}</span></label>)}
    <h2>{t("导出你的记录")}</h2><p>{t("下载本账户保存的作文、报告、修改、提示和学习记录，格式为 JSON。")}</p>
    <button className="secondary-button" disabled={busy} onClick={() => void run(async () => { const blob = await api.exportData(); const url = URL.createObjectURL(blob); const link = document.createElement("a"); link.href = url; link.download = "DraftLoop-personal-data.json"; link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000); })}>{t("导出个人数据")}</button>
    <h2>{t("删除全部个人数据")}</h2><p>{t("此操作会删除本账户的作文、图表、报告、修改和学习记录，以及已登记备份中的对应数据，无法撤销。已经下载到电脑的文件需要自行删除。")}</p>
    <p>{t("确认后会清除此浏览器的写作草稿，并使当前账户会话失效。")}</p>
    <label htmlFor="delete-confirmation">{t("输入“删除我的数据”以确认")}</label><input id="delete-confirmation" autoComplete="off" value={confirm} onChange={(event) => setConfirm(event.target.value)} disabled={busy} />
    <button className="secondary-button" disabled={busy || confirm !== t("删除我的数据")} onClick={() => void run(async () => { const result = await api.deleteData(); for (const key of Object.keys(localStorage)) if (key.startsWith("draftloop.")) localStorage.removeItem(key); setReceipt(result.deletionId); })}>{busy ? t("正在处理…") : t("永久删除个人数据")}</button>
  </section>;
}
