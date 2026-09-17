export const semanticStates = [
  "QUEUED",
  "PROCESSING",
  "COMPLETE",
  "PARTIAL",
  "REVIEW_REQUIRED",
  "FAILED",
  "CANCELLED",
] as const;

export type SemanticState = (typeof semanticStates)[number];
export type TerminalSemanticState = Extract<
  SemanticState,
  "COMPLETE" | "PARTIAL" | "REVIEW_REQUIRED" | "FAILED" | "CANCELLED"
>;

export interface SemanticResultContract {
  readonly contract: "draftloop.semantic-result";
  readonly version: "1.0.0";
  readonly submissionId: string;
  readonly submissionVersion: number;
  readonly state: SemanticState;
  readonly contentSha256: string | null;
  readonly failureCode?: string;
}

export interface CriterionEstimate {
  readonly label: "任务回应" | "连贯与衔接" | "词汇资源" | "语法多样性与准确性";
  readonly likelyRange: string;
}

export interface NormalReportContent {
  readonly taskLabel: "任务一" | "任务二";
  readonly wordCount: number;
  readonly title: "写作报告";
  readonly likelyRange: string;
  readonly confidence: "较低" | "中等" | "较高";
  readonly disclaimer: string;
  readonly criteria: readonly CriterionEstimate[];
  readonly focus: {
    readonly index: number;
    readonly total: number;
    readonly title: string;
    readonly explanation: readonly [string, string];
    readonly locator: string;
    readonly evidenceLead: string;
    readonly evidenceUnderline: string;
    readonly evidenceTail: string;
    readonly boundaryNote: string;
    readonly causeChain: readonly [string, string, string, string];
  };
}

export interface ReviewProjectionContent {
  readonly title: "需要人工复核";
  readonly summary: string;
  readonly reasons: readonly string[];
  readonly safeActions: readonly string[];
}

export interface StateProjectionContent {
  readonly title: string;
  readonly summary: string;
  readonly safeActions: readonly string[];
}

export type PresentationProjectionContract =
  | {
      readonly contract: "draftloop.presentation-projection";
      readonly version: "1.0.0";
      readonly projectionId: string;
      readonly projectionVersion: number;
      readonly submissionId: string;
      readonly semanticState: "COMPLETE";
      readonly kind: "NORMAL_REPORT";
      readonly sourceSemanticSha256: string;
      readonly contentSha256: string;
      readonly content: NormalReportContent;
    }
  | {
      readonly contract: "draftloop.presentation-projection";
      readonly version: "1.0.0";
      readonly projectionId: string;
      readonly projectionVersion: number;
      readonly submissionId: string;
      readonly semanticState: "REVIEW_REQUIRED";
      readonly kind: "REVIEW_PROJECTION";
      readonly sourceSemanticSha256: string | null;
      readonly contentSha256: string;
      readonly content: ReviewProjectionContent;
    }
  | {
      readonly contract: "draftloop.presentation-projection";
      readonly version: "1.0.0";
      readonly projectionId: string;
      readonly projectionVersion: number;
      readonly submissionId: string;
      readonly semanticState: Exclude<SemanticState, "COMPLETE" | "REVIEW_REQUIRED">;
      readonly kind: "STATE_PROJECTION";
      readonly sourceSemanticSha256: string | null;
      readonly contentSha256: string;
      readonly content: StateProjectionContent;
    };

export interface ExportArtifactContract {
  readonly contract: "draftloop.export-artifact";
  readonly version: "1.0.0";
  readonly artifactId: string;
  readonly artifactVersion: number;
  readonly submissionId: string;
  readonly kind: "PDF";
  readonly state: "READY" | "BLOCKED";
  readonly sourceSemanticSha256: string;
  readonly sourceProjectionSha256: string;
  readonly contentSha256: string | null;
  readonly filename: string | null;
}

export interface WorkspaceSnapshot {
  readonly semanticResult: SemanticResultContract;
  readonly presentationProjection: PresentationProjectionContract;
  readonly exportArtifact?: ExportArtifactContract;
}

export interface WorkspaceAccess {
  readonly normalReport: boolean;
  readonly normalExport: boolean;
  readonly validLineage: boolean;
  readonly reason:
    | "READY"
    | "SEMANTIC_NOT_COMPLETE"
    | "PROJECTION_NOT_NORMAL"
    | "SEMANTIC_LINEAGE_MISMATCH"
    | "EXPORT_NOT_READY"
    | "EXPORT_LINEAGE_MISMATCH";
}

const isSha256 = (value: string | null): value is string =>
  typeof value === "string" && /^[a-f0-9]{64}$/u.test(value);

/**
 * Validate server-owned state and lineage. This function never constructs a
 * report, score, projection, or export artifact from another contract.
 */
export function accessFor(snapshot: WorkspaceSnapshot): WorkspaceAccess {
  const semantic = snapshot.semanticResult;
  const projection = snapshot.presentationProjection;

  if (semantic.state !== "COMPLETE") {
    return {
      normalReport: false,
      normalExport: false,
      validLineage: projection.semanticState === semantic.state,
      reason: "SEMANTIC_NOT_COMPLETE",
    };
  }

  if (projection.kind !== "NORMAL_REPORT" || projection.semanticState !== "COMPLETE") {
    return {
      normalReport: false,
      normalExport: false,
      validLineage: false,
      reason: "PROJECTION_NOT_NORMAL",
    };
  }

  if (
    !isSha256(semantic.contentSha256) ||
    projection.submissionId !== semantic.submissionId ||
    projection.sourceSemanticSha256 !== semantic.contentSha256
  ) {
    return {
      normalReport: false,
      normalExport: false,
      validLineage: false,
      reason: "SEMANTIC_LINEAGE_MISMATCH",
    };
  }

  const artifact = snapshot.exportArtifact;
  if (!artifact || artifact.state !== "READY" || !isSha256(artifact.contentSha256)) {
    return {
      normalReport: true,
      normalExport: false,
      validLineage: true,
      reason: "EXPORT_NOT_READY",
    };
  }

  if (
    artifact.submissionId !== semantic.submissionId ||
    artifact.sourceSemanticSha256 !== semantic.contentSha256 ||
    artifact.sourceProjectionSha256 !== projection.contentSha256
  ) {
    return {
      normalReport: true,
      normalExport: false,
      validLineage: false,
      reason: "EXPORT_LINEAGE_MISMATCH",
    };
  }

  return {
    normalReport: true,
    normalExport: true,
    validLineage: true,
    reason: "READY",
  };
}

export function isNormalReport(
  projection: PresentationProjectionContract,
): projection is Extract<PresentationProjectionContract, { kind: "NORMAL_REPORT" }> {
  return projection.kind === "NORMAL_REPORT";
}
