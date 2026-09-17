import { describe, expect, it } from "vitest";
import { BrowserApi } from "./BrowserApi";

describe("versioned server workspace boundary", () => {
  it("rejects a COMPLETE response without a verified normal projection instead of synthesizing a score", async () => {
    const api = new BrowserApi(async () => new Response(JSON.stringify({
      version: "draftloop-browser-workspace-v1", submissionId: "submission_test",
      semanticResult: { state: "COMPLETE" }, presentation: { state: "NORMAL", report: null },
    }), { status: 200 }));
    await expect(api.workspace("submission_test")).rejects.toThrow("工作区");
  });
});
