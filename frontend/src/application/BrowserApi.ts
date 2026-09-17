import { t } from "../locale";
import { validDetailedReport } from "./ReportValidation";
export type JobState = "QUEUED" | "PROCESSING" | "COMPLETE" | "PARTIAL" | "REVIEW_REQUIRED" | "FAILED" | "CANCELLED";
export type Criterion = "TA" | "TR" | "CC" | "LR" | "GRA";
export type TaskType = "task1" | "task2";
export interface PrivacySettings { policyVersion: string; consents: Array<{ purpose: "TRAINING" | "PRODUCT_ANALYTICS"; granted: boolean }> }
export interface LearningWorkspace {
  available: boolean; reason?: string;
  issues: Array<{ issueId: string; criterion: string; version: number; session: { status: string; assistanceDepth: string; revealed: Array<{ level: number; textEn: string; textZh: string; containsReferenceAnswer: boolean }> } }>;
  revisions: Array<{ revisionId: string; state: string; candidateScript: string; assistance: string;
    classifications?: Array<{ classificationId: string; label: string; status: string; reason: string }>;
    changes?: Array<{ changeId: string; originalText: string; revisedText: string }>;
    summary?: { resolvedIssueIds: string[]; unresolvedIssueIds: string[]; unclassifiedChangeIds: string[]; reviewRequiredClassificationIds: string[] } }>;
}
export interface LearningMemory {
  mastery: Array<{ skillKey: string; state: string; stateReason: string; evidenceEventIds: string[]; independentEventIds: string[] }>;
  events: Array<{ eventId: string; kind: string; skillKey: string; correct: boolean | null; demonstrated: boolean; assistanceDepth: string; occurredAt: string; submissionId?: string; targetEventId?: string }>;
}
export interface NextPractice { submissionId: string | null; question: string | null; sections: ServerReport["sections"] }
export interface WritingSource { question: string; candidateScript: string; createdAt?: string; taskType?: string; uploadId?: string | null; targetBand?: number | null }
export interface HistoryEntry { submissionId: string; state: JobState; title?: string; createdAt?: string; taskType?: string }
export interface CoachingRecord { id: string; criterion: string; text: string; textEn?: string; authority: "CURRENT_STUDENT_EVIDENCE" | "RAG_EVIDENCE" | "TOPIC_KB"; evidence?: Array<{ start: number; end: number; scope: string }> }
export type IssueCategory = 'WORD_CHOICE' | 'GRAMMAR' | 'TASK_RESPONSE' | 'DEVELOPMENT' | 'COHERENCE' | 'MISSING_ELEMENT';
export interface ReportLocation { paragraphIndex: number; missing: boolean; quote: string; start: number | null; end: number | null; sentenceQuote: string; sentenceStart: number | null; sentenceEnd: number | null }
export interface ReportPriority { id: string; category: IssueCategory; title: string; explanation: string; action: string; replacement: string; location: ReportLocation }
export interface ReportParagraph { index: number; role: string; original: string; assessment: string; impact: 'SUPPORTS' | 'LIMITS' | 'NEUTRAL'; corrections: Array<{ kind: IssueCategory; location: ReportLocation; replacement: string; reason: string }>; optimized: string; changes: string[]; targetBandEstimate: number | null; estimateReason: string }
export interface DetailedReport {
  version: 'detailed-writing-report-v1'; lockedScoreSha256: string; essayVersionId: string; taskType: TaskType; targetBand: number | null;
  criterionAnalyses: Array<{ criterion: Criterion; analysis: string; strengths: string[]; limitations: string[] }>;
  strongestCriteria: Criterion[]; priorities: ReportPriority[];
  mindMap: { thesis: string; branches: Array<{ paragraphIndex: number; role: string; point: string; support: string[]; gap: string }> };
  paragraphs: ReportParagraph[]; topicLearning: { theme: string; expressions: Array<{ expression: string; meaning: string; usage: string; source: 'CANDIDATE' | 'OPTIMIZED' }>;
    examples: Array<{ title: string; scenario: string; structure: string; adaptation: string; relatedTopics: string[]; reuseOf: string | null; hypothetical: true }> };
  encouragement: string; nextActions: Array<{ title: string; minutes: number; steps: string[]; successCheck: string }>; contentSha256: string;
}
export interface CalibrationAudit { status: 'NOT_AVAILABLE' | 'ACCEPTED_INITIAL' | 'RESCORED'; initialBand: number; auditBand: number | null; absoluteDifference: number | null; rescoreTriggered: boolean; retrievalPasses: number; finalBand: number; scoreAuthority: false; criteria: Array<{ criterion: Criterion; initialBand: number; auditBand: number; difference: number; rationale: string }> }
export interface ModelProfile { id: string; name: string; provider: string; model: string; baseUrl: string; apiKey?: string; hasApiKey?: boolean; vision: boolean }
export type ModelRole = 'task1' | 'task2' | 'audit' | 'rescore' | 'coaching';
export interface ModelSettings { revision: number; providers: Array<{ id: string; name: string; baseUrl: string; kind: string }>; profiles: ModelProfile[]; assignments: Record<ModelRole, string | null> }
export interface ServerReport {
  overallBand: number;
  criteria: Record<string, number>;
  likelyRange?: number[];
  confidence?: string;
  lockedScoreSha256: string;
  disclaimer: string;
  rag: { status: "ENABLED" | "DISABLED"; mode: "LOCAL_PRIVATE_RESEARCH" | null; packageIdentity: string | null; scoreAuthority: false; reason: string | null; evidenceState: string };
  sections: Array<{ key: string; label: string; records: CoachingRecord[] }>;
  detailedReport?: DetailedReport;
  calibrationAudit?: CalibrationAudit;
}
export interface ServerWorkspace {
  version: "draftloop-browser-workspace-v1";
  submissionId: string;
  rag: ServerReport["rag"];
  semanticResult: { submissionId: string; state: JobState; semanticResultSha256: string; failureCode: string | null };
  presentation: { state: string; sourceSemanticSha256: string; sourceReportSha256: string | null; failureCode: string | null; report: ServerReport | null };
  exportArtifact: { state: "BLOCKED"; reason: string };
  workspaceSha256: string;
  source?: WritingSource;
  assessmentIssues?: Array<{ criterion: Criterion; status: string; failureCode: string | null }>;
  progress?: { stage: string; startedAt: number; rescoreTriggered?: boolean; criteria: Partial<Record<Criterion, string>> } | null;
  capabilities?: { downloadPdf: boolean };
}
const BASE = "/api/draftloop/v1";
const hash = (value: unknown): value is string => typeof value === "string" && /^[a-f0-9]{64}$/.test(value);
const states: string[] = ["QUEUED", "PROCESSING", "COMPLETE", "PARTIAL", "REVIEW_REQUIRED", "FAILED", "CANCELLED"];
const validBand = (value: unknown) => typeof value === "number" && Number.isFinite(value) && value >= 0 && value <= 9;
const validId = (value: unknown): value is string => typeof value === "string" && /^submission_[a-zA-Z0-9]+$/.test(value);

export class BrowserApi {
  constructor(private readonly fetcher: typeof fetch = (...args) => fetch(...args)) {}

  private async response(path: string, method = "GET", body?: unknown, key?: string): Promise<Response> {
    let response: Response;
    try {
      response = await this.fetcher(BASE + path, { method, credentials: "same-origin",
        headers: { "X-DraftLoop-Client": "browser-v1", ...(body !== undefined ? { "Content-Type": "application/json" } : {}),
          ...(key ? { "Idempotency-Key": key } : {}) },
        body: body === undefined ? undefined : JSON.stringify(body), signal: AbortSignal.timeout(path.startsWith('/settings/models/') ? 60000 : 30000),
      });
    } catch {
      throw new Error(t("连接中断或响应超时。你的写作已保留，请重新连接后继续。"));
    }
    if (!response.ok) {
      if (path.startsWith('/settings/models') && [400,409].includes(response.status)) {
        const value = await response.json().catch(() => null);
        const message = value?.error?.message;
        if (typeof message === 'string' && message.length < 300 && !/\/Users\/|\/home\/|api.key/i.test(message)) throw new Error(message);
      }
      const messages: Record<number, string> = {
        400: t("提交内容不符合要求。请检查题目和正文的长度后重试。"),
        401: t("本地会话已过期，请重新连接。"), 403: t("当前会话没有访问权限，请重新连接。"),
        404: t("没有找到这份记录，请从历史记录重新打开。"),
        409: t("记录状态已变化，请刷新后重试。"), 429: t("请求较多，请稍候再试。"),
        422: t("图片无法读取。请使用不超过 10 MiB 的 PNG、JPG 或 WebP 图表。"),
      };
      throw new Error(messages[response.status] ?? t("服务暂时不可用，请重试。你的输入仍然保留。"));
    }
    return response;
  }

  private async request(path: string, method = "GET", body?: unknown, key?: string): Promise<unknown> {
    const response = await this.response(path, method, body, key);
    try { return await response.json(); }
    catch { throw new Error(t("服务返回了无法读取的数据，请重新连接。")); }
  }

  async connect(): Promise<void> { await this.request("/session", "POST", {}); }

  async models(): Promise<ModelSettings> { return this.request('/settings/models') as Promise<ModelSettings>; }
  async saveModels(value: ModelSettings): Promise<ModelSettings> {
    return this.request('/settings/models','POST',{ revision:value.revision, profiles:value.profiles, assignments:value.assignments }) as Promise<ModelSettings>;
  }
  async discoverModels(value: ModelProfile): Promise<{ models: string[]; status: string; message?: string }> {
    return this.request('/settings/models/discover','POST',value) as Promise<{ models:string[];status:string;message?:string }>;
  }
  async testModel(value: ModelProfile): Promise<{ ok: boolean; message: string }> {
    return this.request('/settings/models/test','POST',value) as Promise<{ ok:boolean;message:string }>;
  }

  async privacy(): Promise<PrivacySettings> {
    const result = await this.request("/privacy") as PrivacySettings;
    if (typeof result?.policyVersion !== "string" || !Array.isArray(result.consents)
      || result.consents.some((item) => !["TRAINING", "PRODUCT_ANALYTICS"].includes(item.purpose) || typeof item.granted !== "boolean")) throw new Error(t("数据设置暂时无法读取。"));
    return result;
  }

  async learning(id: string): Promise<LearningWorkspace> {
    const value = await this.request(`/workspaces/${encodeURIComponent(id)}/learning`) as LearningWorkspace;
    if (typeof value?.available !== "boolean" || !Array.isArray(value.issues) || !Array.isArray(value.revisions)) throw new Error(t("修改记录暂时无法读取。"));
    return value;
  }

  async revisionSource(id: string): Promise<WritingSource> {
    const value = await this.request(`/workspaces/${encodeURIComponent(id)}/learning/source`) as { source: WritingSource };
    if (typeof value?.source?.candidateScript !== 'string' || typeof value.source.question !== 'string') throw new Error(t('修改记录暂时无法读取。'));
    return value.source;
  }

  async memory(): Promise<LearningMemory> {
    const value = await this.request('/learning/memory') as LearningMemory;
    if (!Array.isArray(value?.mastery) || !Array.isArray(value.events)) throw new Error(t("学习记录暂时无法读取。"));
    return value;
  }
  async correctMemory(eventId: string): Promise<void> { await this.request('/learning/memory/correct', 'POST', { eventId }); }
  async practice(): Promise<NextPractice> {
    const value = await this.request('/learning/practice') as NextPractice;
    if (!Array.isArray(value?.sections)) throw new Error(t("练习建议暂时无法读取。"));
    return value;
  }

  async hint(id: string, issueId: string, expectedVersion: number, action: string, key: string): Promise<void> {
    await this.request(`/workspaces/${encodeURIComponent(id)}/learning/hints`, "POST", { issueId, expectedVersion, action }, key);
  }

  async verify(id: string, candidateScript: string, assistance: string, key: string): Promise<void> {
    await this.request(`/workspaces/${encodeURIComponent(id)}/learning/revisions`, "POST", { candidateScript, assistance }, key);
  }

  async consent(purpose: string, granted: boolean, policyVersion: string): Promise<void> {
    await this.request("/privacy/consent", "POST", { purpose, granted, policyVersion });
  }

  async exportData(): Promise<Blob> {
    const result = await this.request("/privacy/export");
    return new Blob([JSON.stringify(result, null, 2)], { type: "application/json" });
  }

  async deleteData(): Promise<{ deletionId: string; state: string; completedAt: string }> {
    const result = await this.request("/privacy/delete", "POST", { confirmation: "DELETE_MY_DATA" }) as { deletion: { deletionId: string; state: string; completedAt: string } };
    if (result?.deletion?.state !== "COMPLETE" || !result.deletion.deletionId) throw new Error(t("删除尚未确认完成，请重新连接后查看数据状态。"));
    return result.deletion;
  }

  async submit(question: string, candidateScript: string, idempotencyKey: string,
    options: { taskType: TaskType; uploadId?: string | null; targetBand?: number | null } = { taskType: "task2" }): Promise<string> {
    const result = await this.request("/submissions", "POST", { ...options, question, candidateScript }, idempotencyKey) as { submission?: { submissionId?: string } };
    const id = result?.submission?.submissionId;
    if (!validId(id)) throw new Error(t("提交响应无效，请使用相同输入重试。"));
    return id;
  }

  async workspace(id: string): Promise<ServerWorkspace> {
    const result = await this.request("/workspaces/" + encodeURIComponent(id)) as ServerWorkspace;
    const fail = () => { throw new Error(t("工作区校验未通过，没有显示未验证的分数或证据。")); };
    if (!result || result.version !== "draftloop-browser-workspace-v1" || result.submissionId !== id
      || result.semanticResult?.submissionId !== id || !states.includes(result.semanticResult?.state)
      || !hash(result.semanticResult?.semanticResultSha256)
      || result.presentation?.sourceSemanticSha256 !== result.semanticResult.semanticResultSha256
      || !hash(result.workspaceSha256) || result.exportArtifact?.state !== "BLOCKED") fail();
    const report = result.presentation.report;
    if (result.presentation.state === "NORMAL") {
      if (result.semanticResult.state !== "COMPLETE" || !hash(result.presentation.sourceReportSha256)
        || !report || !hash(report.lockedScoreSha256) || !validBand(report.overallBand)
        || !report.criteria || !["CC,GRA,LR,TR", "CC,GRA,LR,TA"].includes(Object.keys(report.criteria).sort().join())
        || !Object.values(report.criteria).every(validBand)
        || report.rag?.scoreAuthority !== false || !Array.isArray(report.sections)
        || typeof report.disclaimer !== "string") fail();
      if (!validDetailedReport(report!, result.source)) fail();
      if (report!.rag.status === "ENABLED" && (report!.rag.mode !== "LOCAL_PRIVATE_RESEARCH"
        || report!.rag.packageIdentity !== "ielts-frozen-retrieval-v1")) fail();
      if (!["ENABLED", "DISABLED"].includes(report!.rag.status)) fail();
      if (report!.likelyRange !== undefined && (!Array.isArray(report!.likelyRange) || report!.likelyRange.length !== 2
        || !report!.likelyRange.every(validBand) || report!.likelyRange[0] > report!.likelyRange[1])) fail();
      if (report!.confidence !== undefined && !["HIGH", "MEDIUM", "LOW"].includes(report!.confidence)) fail();
      for (const section of report!.sections) {
        if (typeof section.label !== "string" || typeof section.key !== "string" || !Array.isArray(section.records)) fail();
        for (const record of section.records) {
          if (!hash(record.id) || typeof record.text !== "string" || typeof record.criterion !== "string"
            || (record.textEn !== undefined && typeof record.textEn !== "string")
            || !["CURRENT_STUDENT_EVIDENCE", "RAG_EVIDENCE", "TOPIC_KB"].includes(record.authority)) fail();
          if (record.authority === "RAG_EVIDENCE" && (report!.rag.status !== "ENABLED" || report!.rag.evidenceState !== "SUFFICIENT")) fail();
          if (record.evidence !== undefined && (!Array.isArray(record.evidence) || record.evidence.some((span) => !Number.isInteger(span.start) || !Number.isInteger(span.end)
            || span.start < 0 || span.end <= span.start || !result.source || span.end > Array.from(result.source.candidateScript).length))) fail();
        }
      }
    } else if (report !== null || result.capabilities?.downloadPdf) fail();
    if (result.source && (typeof result.source.question !== "string" || typeof result.source.candidateScript !== "string")) fail();
    return result;
  }

  async history(): Promise<HistoryEntry[]> {
    const result = await this.request("/history") as { history?: HistoryEntry[] };
    if (!Array.isArray(result?.history) || result.history.some((item) => !validId(item.submissionId) || !states.includes(item.state))) throw new Error(t("历史记录暂时无法读取。"));
    return result.history;
  }

  async cancel(id: string, key: string): Promise<void> {
    await this.request("/submissions/" + encodeURIComponent(id) + "/cancel", "POST", {}, key);
  }

  async pdf(id: string): Promise<Blob> {
    const response = await this.response("/workspaces/" + encodeURIComponent(id) + "/pdf");
    if (!response.headers.get("Content-Type")?.startsWith("application/pdf")) throw new Error(t("报告文件生成失败，请稍后重试。"));
    return response.blob();
  }

  async upload(file: File): Promise<string> {
    if (!file.size || file.size > 10 * 1024 * 1024 || !["image/png", "image/jpeg", "image/webp"].includes(file.type)) {
      throw new Error(t("请选择不超过 10 MiB 的 PNG、JPG 或 WebP 图表。"));
    }
    const dataBase64 = await new Promise<string>((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result).split(",")[1]);
      reader.onerror = () => reject(new Error(t("图片无法读取，请重新选择。")));
      reader.readAsDataURL(file);
    });
    const result = await this.request("/uploads", "POST", { mediaType: file.type, dataBase64 }) as { upload?: { uploadId?: string } };
    if (!result.upload?.uploadId || !/^upload_[a-zA-Z0-9]+$/.test(result.upload.uploadId)) throw new Error(t("图片上传响应无效，请重试。"));
    return result.upload.uploadId;
  }

  async image(id: string): Promise<Blob> {
    const response = await this.response("/uploads/" + encodeURIComponent(id));
    if (!response.headers.get("Content-Type")?.startsWith("image/")) throw new Error(t("图表暂时无法读取。"));
    return response.blob();
  }
}
