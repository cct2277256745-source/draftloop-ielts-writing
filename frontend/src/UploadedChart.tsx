import { useEffect, useState } from "react";
import { BrowserApi } from "./application/BrowserApi";
import { t } from "./locale";
const api = new BrowserApi();

export function UploadedChart({ id }: { id: string }) {
  const [url, setUrl] = useState(""), [error, setError] = useState("");
  useEffect(() => {
    let active = true, objectUrl = ""; setUrl(""); setError("");
    void api.image(id).then((blob) => { if (active) { objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); } })
      .catch(() => { if (active) setError(t("图表暂时无法读取，请重新连接或选择图片。")); });
    return () => { active = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [id]);
  return error ? <p role="alert">{t(error)}</p> : url ? <img className="live-chart-preview" src={url} alt={t("此稿的 Task 1 题目图表")} /> : <p role="status">{t("正在读取图表…")}</p>;
}

