import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { App } from "./App";
import { DraftLoopApplicationService } from "./application/DraftLoopApplicationService";
import { DemoC3ProductBoundary } from "./data/demoWorkspace";
import { makeDemoWorkspace } from "./data/demoWorkspace";
import type { ExportArtifactContract, SemanticState } from "./domain/contracts";

const serviceFor = (state: SemanticState) =>
  new DraftLoopApplicationService(new DemoC3ProductBoundary(state));

describe("DraftLoop composition", () => {
  it("keeps the complete first viewport Chinese-first and focused on one action", async () => {
    render(<App service={serviceFor("COMPLETE")} />);

    expect(await screen.findByRole("heading", { name: "写作报告", level: 1 })).toBeVisible();
    expect(screen.getByRole("button", { name: "开始修改当前重点" })).toBeVisible();
    expect(screen.getByRole("button", { name: "导出 PDF" })).toBeVisible();
    expect(screen.getByText("合成演示，仅供学习评估；并非官方考试成绩。")).toBeVisible();
    expect(screen.getByRole("navigation", { name: "账户与设置" })).toHaveTextContent(
      "隐私与数据设置",
    );
    expect(document.querySelector(".status-label-compact")).not.toHaveAttribute("aria-hidden");
    expect(screen.queryByText(/AI 助手|智能改写|一键润色/u)).not.toBeInTheDocument();
  });

  it("opens the contextual evidence sheet and returns focus on Escape", async () => {
    const user = userEvent.setup();
    render(<App service={serviceFor("COMPLETE")} />);
    const evidence = await screen.findByRole("button", { name: /查看对应证据/u });
    const sheet = document.getElementById("evidence-sheet");

    expect(sheet).toHaveAttribute("aria-hidden", "true");
    await user.click(evidence);
    expect(sheet).toHaveAttribute("aria-hidden", "false");
    expect(screen.getByRole("button", { name: "收起" })).toHaveFocus();

    await user.keyboard("{Escape}");
    await waitFor(() => expect(sheet).toHaveAttribute("aria-hidden", "true"));
    expect(evidence).toHaveFocus();
  });

  it("makes compact evidence modal and restores focus after the inert report is released", async () => {
    const compactMatch = {
      matches: true,
      media: "(max-width: 900px)",
      onchange: null,
      addListener: vi.fn(),
      removeListener: vi.fn(),
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      dispatchEvent: vi.fn(),
    };
    vi.mocked(window.matchMedia)
      .mockImplementationOnce(() => compactMatch)
      .mockImplementationOnce(() => compactMatch);
    const user = userEvent.setup();
    render(<App service={serviceFor("COMPLETE")} />);
    const evidence = await screen.findByRole("button", { name: /查看对应证据/u });

    await user.click(evidence);
    const sheet = screen.getByRole("dialog", { name: "证据" });
    const report = document.querySelector(".report-column");
    expect(sheet).toHaveAttribute("aria-modal", "true");
    expect(report).toHaveAttribute("inert");

    await user.keyboard("{Escape}");
    await waitFor(() => expect(report).not.toHaveAttribute("inert"));
    expect(evidence).toHaveFocus();
  });

  it("moves from the report into a learner-owned revision flow", async () => {
    const user = userEvent.setup();
    render(<App service={serviceFor("COMPLETE")} />);

    await user.click(await screen.findByRole("button", { name: "开始修改当前重点" }));
    expect(screen.getByRole("heading", { name: "补足这一句的解释" })).toBeVisible();
    expect(screen.getByLabelText("你的补充解释")).toBeVisible();
    expect(screen.getByText(/不会替你改写整段/u)).toBeVisible();
  });

  it("delivers the independently authorized export artifact without printing the page", async () => {
    class RecordingBoundary extends DemoC3ProductBoundary {
      public delivered = 0;

      public override async deliverReportExport(
        _artifact: ExportArtifactContract,
      ): Promise<void> {
        this.delivered += 1;
      }
    }

    const boundary = new RecordingBoundary("COMPLETE");
    const user = userEvent.setup();
    render(<App service={new DraftLoopApplicationService(boundary)} />);

    await user.click(await screen.findByRole("button", { name: "导出 PDF" }));
    expect(await screen.findByRole("status")).toHaveTextContent("PDF 已就绪");
    expect(boundary.delivered).toBe(1);
    expect(window.print).not.toHaveBeenCalled();
  });

  it("fails closed when complete semantics do not have valid presentation lineage", async () => {
    class InvalidLineageBoundary extends DemoC3ProductBoundary {
      public override async readWorkspace() {
        const valid = makeDemoWorkspace("COMPLETE");
        return {
          ...valid,
          presentationProjection: {
            ...valid.presentationProjection,
            sourceSemanticSha256: "9".repeat(64),
          },
        };
      }
    }

    const user = userEvent.setup();
    render(<App service={new DraftLoopApplicationService(new InvalidLineageBoundary())} />);
    await screen.findByRole("heading", { name: "报告暂不可用" });
    await user.click(screen.getByRole("button", { name: "开始修改" }));

    expect(screen.queryByLabelText("你的补充解释")).not.toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "报告暂不可用" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "导出 PDF" })).not.toBeInTheDocument();
  });

  it("shows a recoverable load failure instead of hanging on loading", async () => {
    class FailingBoundary extends DemoC3ProductBoundary {
      public override async readWorkspace(): Promise<never> {
        throw new Error("offline");
      }
    }

    render(<App service={new DraftLoopApplicationService(new FailingBoundary())} />);
    expect(await screen.findByRole("heading", { name: "暂时无法打开报告" })).toBeVisible();
    expect(screen.getByRole("button", { name: "重新载入" })).toBeVisible();
  });

  it.each([
    ["REVIEW_REQUIRED", "需要人工复核"],
    ["PARTIAL", "分析尚未完整"],
    ["FAILED", "本次分析未完成"],
  ] as const)("never presents %s as a normal report", async (state, heading) => {
    render(<App service={serviceFor(state)} />);

    expect(await screen.findByRole("heading", { name: heading, level: 1 })).toBeVisible();
    expect(screen.queryByRole("button", { name: "导出 PDF" })).not.toBeInTheDocument();
    expect(screen.queryByText("预计区间 6.0–7.0")).not.toBeInTheDocument();
    expect(screen.getByText(/不能生成普通写作报告或 PDF/u)).toBeVisible();
    expect(screen.queryByText(state)).not.toBeInTheDocument();
  });
});
