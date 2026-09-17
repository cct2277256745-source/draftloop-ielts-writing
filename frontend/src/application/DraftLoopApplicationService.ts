import {
  accessFor,
  type ExportArtifactContract,
  type WorkspaceSnapshot,
} from "../domain/contracts";

export interface RevisionCommand {
  readonly submissionId: string;
  readonly expectedSubmissionVersion: number;
  readonly idempotencyKey: string;
  readonly candidateScript: string;
  readonly assistance: "NONE" | "HINT_LEVEL_1";
}

export interface AnalysisControlCommand {
  readonly submissionId: string;
  readonly expectedSubmissionVersion: number;
  readonly idempotencyKey: string;
}

/**
 * The authenticated boundary owns the Tenant Principal. Browser calls never
 * accept tenantId or ownerId, preventing the client from choosing authority.
 */
export interface AuthenticatedC3ProductBoundary {
  readWorkspace(submissionId: string): Promise<WorkspaceSnapshot>;
  requestReportExport(input: {
    readonly submissionId: string;
    readonly semanticSha256: string;
    readonly projectionSha256: string;
    readonly expectedSubmissionVersion: number;
  }): Promise<ExportArtifactContract>;
  deliverReportExport(artifact: ExportArtifactContract): Promise<void>;
  submitRevision(command: RevisionCommand): Promise<{ readonly accepted: true }>;
  retryAnalysis(command: AnalysisControlCommand): Promise<{ readonly accepted: true }>;
  cancelAnalysis(command: AnalysisControlCommand): Promise<{ readonly accepted: true }>;
}

export class DraftLoopApplicationService {
  public constructor(private readonly c3: AuthenticatedC3ProductBoundary) {}

  public readWorkspace(submissionId: string): Promise<WorkspaceSnapshot> {
    return this.c3.readWorkspace(submissionId);
  }

  public async authorizeReportExport(
    snapshot: WorkspaceSnapshot,
  ): Promise<ExportArtifactContract> {
    const access = accessFor(snapshot);
    const semanticSha256 = snapshot.semanticResult.contentSha256;
    if (
      !access.normalExport ||
      semanticSha256 === null ||
      snapshot.presentationProjection.kind !== "NORMAL_REPORT"
    ) {
      throw new Error("NORMAL_EXPORT_NOT_AUTHORIZED");
    }
    return this.c3.requestReportExport({
      submissionId: snapshot.semanticResult.submissionId,
      semanticSha256,
      projectionSha256: snapshot.presentationProjection.contentSha256,
      expectedSubmissionVersion: snapshot.semanticResult.submissionVersion,
    });
  }

  public async deliverReportExport(snapshot: WorkspaceSnapshot): Promise<ExportArtifactContract> {
    const artifact = await this.authorizeReportExport(snapshot);
    await this.c3.deliverReportExport(artifact);
    return artifact;
  }

  public submitRevision(
    snapshot: WorkspaceSnapshot,
    command: Omit<RevisionCommand, "submissionId" | "expectedSubmissionVersion">,
  ) {
    if (!accessFor(snapshot).normalReport) {
      throw new Error("REVISION_NOT_AUTHORIZED");
    }
    return this.c3.submitRevision({
      ...command,
      submissionId: snapshot.semanticResult.submissionId,
      expectedSubmissionVersion: snapshot.semanticResult.submissionVersion,
    });
  }

  public retryAnalysis(snapshot: WorkspaceSnapshot, idempotencyKey: string) {
    if (!(["PARTIAL", "FAILED"] as const).includes(snapshot.semanticResult.state as "PARTIAL" | "FAILED")) {
      throw new Error("RETRY_NOT_AUTHORIZED");
    }
    return this.c3.retryAnalysis({
      submissionId: snapshot.semanticResult.submissionId,
      expectedSubmissionVersion: snapshot.semanticResult.submissionVersion,
      idempotencyKey,
    });
  }

  public cancelAnalysis(snapshot: WorkspaceSnapshot, idempotencyKey: string) {
    if (!(["QUEUED", "PROCESSING"] as const).includes(snapshot.semanticResult.state as "QUEUED" | "PROCESSING")) {
      throw new Error("CANCEL_NOT_AUTHORIZED");
    }
    return this.c3.cancelAnalysis({
      submissionId: snapshot.semanticResult.submissionId,
      expectedSubmissionVersion: snapshot.semanticResult.submissionVersion,
      idempotencyKey,
    });
  }
}
