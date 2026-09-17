import { render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { LiveWorkspace } from "./LiveWorkspace";

afterEach(() => { vi.unstubAllGlobals(); window.history.replaceState(null, "", "/"); });

it("renders the server-owned score and gated coaching without a demo-data import", async () => {
  const sid = "submission_controlled";
  const locked = "a".repeat(64);
  window.history.replaceState(null, "", "?submission=" + sid);
  const workspace = {
    version: "draftloop-browser-workspace-v1", submissionId: sid,
    semanticResult: { submissionId: sid, state: "COMPLETE", semanticResultSha256: "b".repeat(64), failureCode: null },
    workspaceSha256: "c".repeat(64), exportArtifact: { state: "BLOCKED", reason: "ARTIFACT_NOT_MATERIALIZED" },
    presentation: { state: "NORMAL", sourceSemanticSha256: "b".repeat(64), sourceReportSha256: "d".repeat(64), failureCode: null,
      report: { overallBand: 7.5, criteria: { TR: 7.5, CC: 7.5, LR: 7, GRA: 8 }, lockedScoreSha256: locked, disclaimer: "不是官方成绩。",
        rag: { status: "ENABLED", mode: "LOCAL_PRIVATE_RESEARCH", packageIdentity: "ielts-frozen-retrieval-v1", scoreAuthority: false, reason: null, evidenceState: "SUFFICIENT" },
        sections: [
          { key: "bottlenecks", label: "主要瓶颈", records: [{ id: "e".repeat(64), criterion: "TR", text: "服务端当前证据支持的反馈。", authority: "CURRENT_STUDENT_EVIDENCE" }] },
          { key: "topic_learning", label: "话题学习", records: [{ id: "f".repeat(64), criterion: "GRA", text: "已通过门控的参考文字。", authority: "RAG_EVIDENCE" }] },
        ] },
    },
  };
  vi.stubGlobal("fetch", vi.fn().mockResolvedValueOnce(new Response("{}", { status: 200 }))
    .mockResolvedValueOnce(new Response(JSON.stringify(workspace), { status: 200 })));
  render(<LiveWorkspace />);
  expect(await screen.findByLabelText("服务端评分")).toHaveTextContent("7.5");
  expect(screen.getByRole("article")).toHaveAttribute("data-locked-score-sha256", locked);
  expect(screen.getByText("已通过门控的参考文字。")).toBeInTheDocument();
  expect(screen.getByText("服务端当前证据支持的反馈。")).toBeInTheDocument();
  expect(screen.getByRole("tab", { name: "深度批改" })).toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "导出 PDF" })).not.toBeInTheDocument();
});

it("keeps the local browser fail-closed when C3 is unavailable", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("offline")));
  render(<LiveWorkspace />);
  expect(await screen.findByRole("alert")).toHaveTextContent("连接");
  expect(screen.queryByLabelText("服务端评分")).not.toBeInTheDocument();
  expect(screen.queryByText("语料证据增强：已启用")).not.toBeInTheDocument();
});

it("preserves a draft and idempotency key after a failed submission and reload", async () => {
  const { fireEvent, waitFor } = await import("@testing-library/react");
  localStorage.clear();
  const fetcher = vi.fn().mockImplementation(async (url: string) => {
    if (url.endsWith("/session")) return new Response("{}", { status: 200 });
    throw new Error("offline");
  });
  vi.stubGlobal("fetch", fetcher);
  const first = render(<LiveWorkspace />);
  fireEvent.change(screen.getByLabelText("作文题目"), { target: { value: "Discuss both views." } });
  fireEvent.change(screen.getByLabelText("你的作文"), { target: { value: "A draft that must survive." } });
  await waitFor(() => expect(screen.getByRole("button", { name: "立即批改" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "立即批改" }));
  expect(await screen.findByRole("alert")).toHaveTextContent("连接中断");
  const stored = JSON.parse(localStorage.getItem("draftloop.writing-draft.v1")!);
  expect(stored.key).toBeTruthy();
  first.unmount();
  render(<LiveWorkspace />);
  expect(screen.getByLabelText("你的作文")).toHaveValue("A draft that must survive.");
  await waitFor(() => expect(screen.getByRole("button", { name: "立即批改" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "立即批改" }));
  await screen.findByRole("alert");
  const calls = fetcher.mock.calls.filter(([url]) => url.endsWith("/submissions"));
  expect(calls).toHaveLength(2);
  expect(calls[0][1].headers["Idempotency-Key"]).toBe(calls[1][1].headers["Idempotency-Key"]);
  localStorage.clear();
});

it("renders dated history without treating server timestamps as numbers", async () => {
  const { fireEvent } = await import("@testing-library/react");
  vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(url.endsWith("/session") ? {} : {
    history: [{ submissionId: "submission_history", state: "COMPLETE", title: "Public transport and cities", createdAt: "2026-09-09T16:00:00+00:00" }],
  }), { status: 200 })));
  render(<LiveWorkspace />);
  fireEvent.click(screen.getByRole("button", { name: "历史记录" }));
  expect(await screen.findByRole("button", { name: /Public transport and cities/ })).toHaveTextContent("分析完成");
});

it("offers the original writing after incomplete scoring without showing a score or PDF", async () => {
  const { fireEvent } = await import("@testing-library/react");
  localStorage.clear();
  const sid = "submission_failed";
  window.history.replaceState(null, "", "?submission=" + sid);
  const workspace = {
    version: "draftloop-browser-workspace-v1", submissionId: sid,
    semanticResult: { submissionId: sid, state: "REVIEW_REQUIRED", semanticResultSha256: "b".repeat(64), failureCode: "ASSESSMENT_INCOMPLETE" },
    workspaceSha256: "c".repeat(64), exportArtifact: { state: "BLOCKED", reason: "ARTIFACT_NOT_MATERIALIZED" },
    presentation: { state: "REVIEW_REQUIRED", sourceSemanticSha256: "b".repeat(64), sourceReportSha256: null, failureCode: "ASSESSMENT_INCOMPLETE", report: null },
    source: { question: "Discuss both views.", candidateScript: "My original paragraph." },
    assessmentIssues: [{ criterion: "TR", status: "FAILURE", failureCode: "NETWORK_ERROR" }],
    capabilities: { downloadPdf: false },
  };
  vi.stubGlobal("fetch", vi.fn(async (url: string) => new Response(JSON.stringify(url.endsWith("/session") ? {} : workspace), { status: 200 })));
  render(<LiveWorkspace />);
  expect(await screen.findByText("模型连接中断")).toBeInTheDocument();
  expect(screen.queryByLabelText("服务端评分")).not.toBeInTheDocument();
  expect(screen.queryByRole("button", { name: "导出 PDF" })).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "保留原文，重新分析" }));
  expect(screen.getByLabelText("你的作文")).toHaveValue("My original paragraph.");
  expect(screen.getByLabelText("作文题目")).toHaveValue("Discuss both views.");
  localStorage.clear();
});
