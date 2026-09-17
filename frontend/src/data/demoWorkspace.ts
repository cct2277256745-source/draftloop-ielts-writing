import type {
  AnalysisControlCommand,
  AuthenticatedC3ProductBoundary,
  RevisionCommand,
} from "../application/DraftLoopApplicationService";
import type {
  ExportArtifactContract,
  SemanticState,
  WorkspaceSnapshot,
} from "../domain/contracts";

const semanticHashes: Record<SemanticState, string | null> = {
  QUEUED: null,
  PROCESSING: null,
  COMPLETE: "a".repeat(64),
  PARTIAL: "b".repeat(64),
  REVIEW_REQUIRED: "c".repeat(64),
  FAILED: "d".repeat(64),
  CANCELLED: "e".repeat(64),
};

const demoPdfSha256 = "ab4a53e4dd1cf7af45b6202f53be8f1b35a14716b27f4584b1437e816309c17c";
const demoPdfBase64 =
  "JVBERi0xLjQKJURMMDEKMSAwIG9iago8PCAvVHlwZSAvQ2F0YWxvZyAvUGFnZXMgMiAwIFIgPj4KZW5kb2JqCjIgMCBvYmoKPDwgL1R5cGUgL1BhZ2VzIC9LaWRzIFszIDAgUl0gL0NvdW50IDEgPj4KZW5kb2JqCjMgMCBvYmoKPDwgL1R5cGUgL1BhZ2UgL1BhcmVudCAyIDAgUiAvTWVkaWFCb3ggWzAgMCA2MTIgNzkyXSAvUmVzb3VyY2VzIDw8IC9Gb250IDw8IC9GMSA0IDAgUiA+PiA+PiAvQ29udGVudHMgNSAwIFIgPj4KZW5kb2JqCjQgMCBvYmoKPDwgL1R5cGUgL0ZvbnQgL1N1YnR5cGUgL1R5cGUxIC9CYXNlRm9udCAvSGVsdmV0aWNhID4+CmVuZG9iago1IDAgb2JqCjw8IC9MZW5ndGggMjY3ID4+CnN0cmVhbQpCVAovRjEgMjAgVGYKNzIgNzYwIFRkCihEcmFmdExvb3AgV3JpdGluZyBSZXBvcnQpIFRqCi9GMSAxMSBUZgowIC0zMCBUZAooU3ludGhldGljIGRlbW9uc3RyYXRpb24gLSBub3QgYW4gb2ZmaWNpYWwgSUVMVFMgcmVzdWx0LikgVGoKMCAtMzAgVGQKKExpa2VseSByYW5nZTogNi4wLTcuMCB8IENvbmZpZGVuY2U6IG1lZGl1bSkgVGoKMCAtMzAgVGQKKEZvY3VzOiBleHBsYWluIHdoeSBwcmFjdGljYWwgc2tpbGxzIGxlYWQgdG8gZnV0dXJlIGJlbmVmaXRzLikgVGoKRVQKZW5kc3RyZWFtCmVuZG9iagp4cmVmCjAgNgowMDAwMDAwMDAwIDY1NTM1IGYgCjAwMDAwMDAwMTUgMDAwMDAgbiAKMDAwMDAwMDA2NCAwMDAwMCBuIAowMDAwMDAwMTIxIDAwMDAwIG4gCjAwMDAwMDAyNDcgMDAwMDAgbiAKMDAwMDAwMDMxNyAwMDAwMCBuIAp0cmFpbGVyCjw8IC9TaXplIDYgL1Jvb3QgMSAwIFIgPj4Kc3RhcnR4cmVmCjYzNQolJUVPRgo=";

const stateCopy: Record<Exclude<SemanticState, "COMPLETE" | "REVIEW_REQUIRED">, {
  title: string;
  summary: string;
  safeActions: readonly string[];
}> = {
  QUEUED: {
    title: "已进入分析队列",
    summary: "写作已安全保存。开始分析后，这里会显示实时进度。",
    safeActions: ["返回历史记录", "取消本次分析"],
  },
  PROCESSING: {
    title: "正在分析写作",
    summary: "正在核对任务回应、证据与语言表现。你可以离开此页，完成后再回来。",
    safeActions: ["返回历史记录", "取消本次分析"],
  },
  PARTIAL: {
    title: "分析尚未完整",
    summary: "部分环节没有完成，因此不会显示或导出普通写作报告。",
    safeActions: ["安全重试", "联系支持"],
  },
  FAILED: {
    title: "本次分析未完成",
    summary: "系统没有生成可用结果。你的原文已保留，可以安全重试。",
    safeActions: ["安全重试", "联系支持"],
  },
  CANCELLED: {
    title: "本次分析已取消",
    summary: "没有生成报告或导出文件。你可以从原文重新开始。",
    safeActions: ["重新开始", "返回历史记录"],
  },
};

export function makeDemoWorkspace(state: SemanticState = "COMPLETE"): WorkspaceSnapshot {
  const submissionId = "submission:synthetic-c4-demo";
  const semanticResult = {
    contract: "draftloop.semantic-result" as const,
    version: "1.0.0" as const,
    submissionId,
    submissionVersion: 12,
    state,
    contentSha256: semanticHashes[state],
    ...(state === "FAILED" ? { failureCode: "PROCESSOR_FAILURE" } : {}),
  };

  if (state === "COMPLETE") {
    const presentationSha256 = "f".repeat(64);
    return {
      semanticResult,
      presentationProjection: {
        contract: "draftloop.presentation-projection",
        version: "1.0.0",
        projectionId: "projection:synthetic-c4-demo",
        projectionVersion: 4,
        submissionId,
        semanticState: "COMPLETE",
        kind: "NORMAL_REPORT",
        sourceSemanticSha256: semanticHashes.COMPLETE!,
        contentSha256: presentationSha256,
        content: {
          taskLabel: "任务二",
          wordCount: 286,
          title: "写作报告",
          likelyRange: "6.0–7.0",
          confidence: "中等",
          disclaimer: "合成演示，仅供学习评估；并非官方考试成绩。",
          criteria: [
            { label: "任务回应", likelyRange: "6.0–7.0" },
            { label: "连贯与衔接", likelyRange: "6.0–7.0" },
            { label: "词汇资源", likelyRange: "6.0–7.0" },
            { label: "语法多样性与准确性", likelyRange: "6.0–7.0" },
          ],
          focus: {
            index: 1,
            total: 3,
            title: "先补足因果解释",
            explanation: [
              "你的论点是清楚的，但这里只写出了结果。",
              "再解释一步“为什么会产生这个结果”，论证就会更完整。",
            ],
            locator: "第 2 段 · 第 3 句",
            evidenceLead: "…universities should give students more practical skills because ",
            evidenceUnderline: "this will help them in the future",
            evidenceTail: ".",
            boundaryNote: "当前证据接近 6–7 分边界",
            causeChain: ["观点", "原因", "缺少解释", "结果"],
          },
        },
      },
      exportArtifact: {
        contract: "draftloop.export-artifact",
        version: "1.0.0",
        artifactId: "export:synthetic-c4-demo",
        artifactVersion: 2,
        submissionId,
        kind: "PDF",
        state: "READY",
        sourceSemanticSha256: semanticHashes.COMPLETE!,
        sourceProjectionSha256: presentationSha256,
        contentSha256: demoPdfSha256,
        filename: "DraftLoop-写作报告-2026-09-01.pdf",
      },
    };
  }

  if (state === "REVIEW_REQUIRED") {
    return {
      semanticResult,
      presentationProjection: {
        contract: "draftloop.presentation-projection",
        version: "1.0.0",
        projectionId: "projection:synthetic-review",
        projectionVersion: 1,
        submissionId,
        semanticState: "REVIEW_REQUIRED",
        kind: "REVIEW_PROJECTION",
        sourceSemanticSha256: semanticHashes.REVIEW_REQUIRED,
        contentSha256: "2".repeat(64),
        content: {
          title: "需要人工复核",
          summary: "图表事实与写作中的关键陈述存在冲突，目前无法形成可靠的普通报告。",
          reasons: ["任务一数据事实存在冲突", "关键证据无法自动确认"],
          safeActions: ["查看复核原因", "联系支持"],
        },
      },
    };
  }

  return {
    semanticResult,
    presentationProjection: {
      contract: "draftloop.presentation-projection",
      version: "1.0.0",
      projectionId: `projection:synthetic-${state.toLowerCase()}`,
      projectionVersion: 1,
      submissionId,
      semanticState: state,
      kind: "STATE_PROJECTION",
      sourceSemanticSha256: semanticHashes[state],
      contentSha256: "3".repeat(64),
      content: stateCopy[state],
    },
  };
}

export class DemoC3ProductBoundary implements AuthenticatedC3ProductBoundary {
  public constructor(private readonly state: SemanticState = "COMPLETE") {}

  public async readWorkspace(): Promise<WorkspaceSnapshot> {
    await Promise.resolve();
    return makeDemoWorkspace(this.state);
  }

  public async requestReportExport(input: {
    readonly submissionId: string;
    readonly semanticSha256: string;
    readonly projectionSha256: string;
    readonly expectedSubmissionVersion: number;
  }): Promise<ExportArtifactContract> {
    const snapshot = makeDemoWorkspace("COMPLETE");
    const artifact = snapshot.exportArtifact;
    if (
      !artifact ||
      artifact.submissionId !== input.submissionId ||
      artifact.sourceSemanticSha256 !== input.semanticSha256 ||
      artifact.sourceProjectionSha256 !== input.projectionSha256 ||
      snapshot.semanticResult.submissionVersion !== input.expectedSubmissionVersion
    ) {
      throw new Error("EXPORT_LINEAGE_CONFLICT");
    }
    return artifact;
  }

  public async deliverReportExport(artifact: ExportArtifactContract): Promise<void> {
    if (
      artifact.state !== "READY" ||
      artifact.contentSha256 !== demoPdfSha256 ||
      artifact.filename === null
    ) {
      throw new Error("EXPORT_ARTIFACT_NOT_READY");
    }
    const anchor = document.createElement("a");
    anchor.href = `data:application/pdf;base64,${demoPdfBase64}`;
    anchor.download = artifact.filename;
    anchor.rel = "noopener";
    document.body.append(anchor);
    anchor.click();
    anchor.remove();
  }

  public async submitRevision(command: RevisionCommand): Promise<{ readonly accepted: true }> {
    if (!command.idempotencyKey || command.expectedSubmissionVersion !== 12) {
      throw new Error("STALE_OR_INVALID_REVISION");
    }
    return { accepted: true };
  }


  public async retryAnalysis(command: AnalysisControlCommand): Promise<{ readonly accepted: true }> {
    if (!command.idempotencyKey || command.expectedSubmissionVersion !== 12) {
      throw new Error("STALE_OR_INVALID_RETRY");
    }
    return { accepted: true };
  }

  public async cancelAnalysis(command: AnalysisControlCommand): Promise<{ readonly accepted: true }> {
    if (!command.idempotencyKey || command.expectedSubmissionVersion !== 12) {
      throw new Error("STALE_OR_INVALID_CANCEL");
    }
    return { accepted: true };
  }
}
