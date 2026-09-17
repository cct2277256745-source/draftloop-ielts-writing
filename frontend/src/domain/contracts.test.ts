import { describe, expect, it } from "vitest";
import { makeDemoWorkspace } from "../data/demoWorkspace";
import { accessFor } from "./contracts";

describe("workspace contract access", () => {
  it("authorizes a normal report and export only across matching complete lineage", () => {
    expect(accessFor(makeDemoWorkspace("COMPLETE"))).toEqual({
      normalReport: true,
      normalExport: true,
      validLineage: true,
      reason: "READY",
    });
  });

  it.each(["PARTIAL", "REVIEW_REQUIRED", "FAILED"] as const)(
    "keeps %s outside normal report and export",
    (state) => {
      const access = accessFor(makeDemoWorkspace(state));
      expect(access.normalReport).toBe(false);
      expect(access.normalExport).toBe(false);
      expect(access.reason).toBe("SEMANTIC_NOT_COMPLETE");
    },
  );

  it("fails closed when an export points at another projection hash", () => {
    const valid = makeDemoWorkspace("COMPLETE");
    const tampered = {
      ...valid,
      exportArtifact: valid.exportArtifact
        ? { ...valid.exportArtifact, sourceProjectionSha256: "9".repeat(64) }
        : undefined,
    };
    expect(accessFor(tampered)).toMatchObject({
      normalReport: true,
      normalExport: false,
      validLineage: false,
      reason: "EXPORT_LINEAGE_MISMATCH",
    });
  });
});
