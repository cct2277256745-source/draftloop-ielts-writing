import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type FormEvent,
  type KeyboardEvent as ReactKeyboardEvent,
  type RefObject,
} from "react";
import { DraftLoopApplicationService } from "./application/DraftLoopApplicationService";
import { DemoC3ProductBoundary } from "./data/demoWorkspace";
import {
  accessFor,
  isNormalReport,
  semanticStates,
  type NormalReportContent,
  type SemanticState,
  type WorkspaceSnapshot,
} from "./domain/contracts";

type Surface =
  | "report"
  | "revision"
  | "new"
  | "history"
  | "profile"
  | "progress"
  | "privacy"
  | "settings";

interface AppProps {
  readonly service?: DraftLoopApplicationService;
}

const navigation: ReadonlyArray<{ readonly id: Surface; readonly label: string }> = [
  { id: "new", label: "新建写作" },
  { id: "history", label: "历史记录" },
  { id: "profile", label: "学习档案" },
];

const workflowNavigation: ReadonlyArray<{ readonly id: Surface; readonly label: string }> = [
  { id: "report", label: "写作报告" },
  { id: "revision", label: "开始修改" },
  { id: "progress", label: "学习进度" },
];

const utilityNavigation: ReadonlyArray<{ readonly id: Surface; readonly label: string }> = [
  { id: "privacy", label: "隐私与数据" },
  { id: "settings", label: "设置" },
];

function requestedState(): SemanticState {
  const requested = new URLSearchParams(window.location.search).get("state")?.toUpperCase();
  return semanticStates.find((state) => state === requested) ?? "COMPLETE";
}

function navigationLabel(surface: Surface): string {
  return [...navigation, ...workflowNavigation, ...utilityNavigation].find(
    (item) => item.id === surface,
  )?.label ?? "写作报告";
}

const semanticStateLabels: Record<SemanticState, string> = {
  QUEUED: "等待分析",
  PROCESSING: "正在分析",
  COMPLETE: "分析完成",
  PARTIAL: "分析不完整",
  REVIEW_REQUIRED: "等待人工复核",
  FAILED: "分析失败",
  CANCELLED: "已取消",
};

const compactStateLabels: Record<SemanticState, string> = {
  QUEUED: "等待",
  PROCESSING: "分析中",
  COMPLETE: "完成",
  PARTIAL: "不完整",
  REVIEW_REQUIRED: "复核",
  FAILED: "失败",
  CANCELLED: "已取消",
};

function newRequestKey(prefix: string): string {
  const suffix =
    typeof crypto !== "undefined" && "randomUUID" in crypto
      ? crypto.randomUUID()
      : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
  return `${prefix}:${suffix}`;
}

function useMediaQuery(query: string): boolean {
  const [matches, setMatches] = useState(() => window.matchMedia(query).matches);

  useEffect(() => {
    const media = window.matchMedia(query);
    const update = () => setMatches(media.matches);
    update();
    media.addEventListener("change", update);
    return () => media.removeEventListener("change", update);
  }, [query]);

  return matches;
}

export function App({ service: suppliedService }: AppProps) {
  const service = useMemo(
    () =>
      suppliedService ??
      new DraftLoopApplicationService(new DemoC3ProductBoundary(requestedState())),
    [suppliedService],
  );
  const [snapshot, setSnapshot] = useState<WorkspaceSnapshot | null>(null);
  const [loadError, setLoadError] = useState(false);
  const [loadAttempt, setLoadAttempt] = useState(0);
  const [surface, setSurface] = useState<Surface>("report");
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);
  const viewportRef = useRef<HTMLDivElement>(null);
  const mobileMenuButtonRef = useRef<HTMLButtonElement>(null);
  const accountButtonRef = useRef<HTMLButtonElement>(null);
  const accountControlRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    let current = true;
    setLoadError(false);
    void service
      .readWorkspace("submission:synthetic-c4-demo")
      .then((result) => {
        if (current) setSnapshot(result);
      })
      .catch(() => {
        if (current) setLoadError(true);
      });
    return () => {
      current = false;
    };
  }, [loadAttempt, service]);

  useEffect(() => {
    if (!mobileNavigationOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileNavigationOpen(false);
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [mobileNavigationOpen]);

  useEffect(() => {
    if (!accountOpen) return;
    const closeAccount = (event: KeyboardEvent | PointerEvent) => {
      if (event.type === "keydown" && (event as KeyboardEvent).key === "Escape") {
        setAccountOpen(false);
        accountButtonRef.current?.focus();
        return;
      }
      if (
        event.type === "pointerdown" &&
        event.target instanceof Node &&
        !accountControlRef.current?.contains(event.target)
      ) {
        setAccountOpen(false);
      }
    };
    document.addEventListener("keydown", closeAccount);
    document.addEventListener("pointerdown", closeAccount);
    return () => {
      document.removeEventListener("keydown", closeAccount);
      document.removeEventListener("pointerdown", closeAccount);
    };
  }, [accountOpen]);

  const navigate = (next: Surface) => {
    setSurface(next);
    setMobileNavigationOpen(false);
    setAccountOpen(false);
    viewportRef.current?.scrollTo({ top: 0, behavior: "smooth" });
  };

  const handleStateAction = useCallback(
    async (action: string) => {
      if (!snapshot) return;
      if (action === "返回历史记录") {
        navigate("history");
        return;
      }
      if (action === "重新开始") {
        navigate("new");
        return;
      }
      if (action === "联系支持") {
        navigate("settings");
        return;
      }
      if (action === "查看复核原因") {
        document.getElementById("review-reasons")?.focus();
        return;
      }
      if (action === "安全重试") {
        await service.retryAnalysis(snapshot, newRequestKey("analysis-retry"));
        setSnapshot(await service.readWorkspace(snapshot.semanticResult.submissionId));
        return;
      }
      if (action === "取消本次分析") {
        await service.cancelAnalysis(snapshot, newRequestKey("analysis-cancel"));
        setSnapshot(await service.readWorkspace(snapshot.semanticResult.submissionId));
      }
    },
    [service, snapshot],
  );

  const state = snapshot?.semanticResult.state ?? "PROCESSING";
  const showExport = snapshot ? accessFor(snapshot).normalExport : false;
  const statusLabel =
    state === "COMPLETE"
      ? "分析完成"
      : state === "REVIEW_REQUIRED"
        ? "等待复核"
        : state === "PROCESSING" || state === "QUEUED"
          ? "分析中"
          : "结果不可用";

  return (
    <div className="app-shell" data-state={state.toLowerCase()}>
      <a className="skip-link" href="#main-content">
        跳到主要内容
      </a>

      <Sidebar
        active={surface}
        mobileOpen={mobileNavigationOpen}
        onNavigate={navigate}
        onClose={() => setMobileNavigationOpen(false)}
        returnFocusRef={mobileMenuButtonRef}
      />

      <div className="workspace-frame">
        <header className="topbar" data-scrolled={scrolled}>
          <button
            ref={mobileMenuButtonRef}
            type="button"
            className="mobile-menu-button"
            aria-expanded={mobileNavigationOpen}
            aria-controls="primary-navigation"
            onClick={() => setMobileNavigationOpen((open) => !open)}
          >
            菜单
          </button>

          <p className="breadcrumb" aria-label="当前位置">
            <span>历史记录</span>
            <span aria-hidden="true">/</span>
            <span>大作文</span>
            <span aria-hidden="true">/</span>
            <time dateTime="2026-09-01">2026-09-01</time>
          </p>

          <div className="topbar-actions">
            <span className="analysis-status">
              <span className="status-dot" aria-hidden="true" />
              <span className="status-label-full">{statusLabel}</span>
              <span className="status-label-compact">
                {compactStateLabels[state]}
              </span>
            </span>
            {showExport ? (
              <ExportButton service={service} snapshot={snapshot!} />
            ) : null}
            <div ref={accountControlRef} className="account-control">
              <button
                ref={accountButtonRef}
                type="button"
                className="account-button"
                aria-expanded={accountOpen}
                aria-controls="account-menu"
                aria-haspopup="menu"
                onClick={() => setAccountOpen((open) => !open)}
              >
                我的账户
              </button>
              {accountOpen ? (
                <div id="account-menu" className="account-menu" role="menu">
                  <button type="button" role="menuitem" onClick={() => navigate("privacy")}>
                    隐私与数据
                  </button>
                  <button type="button" role="menuitem">退出登录</button>
                </div>
              ) : null}
            </div>
          </div>
        </header>

        <div
          ref={viewportRef}
          className="workspace-viewport"
          onScroll={(event) => setScrolled(event.currentTarget.scrollTop > 8)}
        >
          <main id="main-content" tabIndex={-1}>
            {!snapshot && loadError ? (
              <LoadFailure onRetry={() => setLoadAttempt((attempt) => attempt + 1)} />
            ) : !snapshot ? (
              <LoadingView />
            ) : surface === "report" ? (
              <ReportSurface
                snapshot={snapshot}
                onBeginRevision={() => navigate("revision")}
                onStateAction={handleStateAction}
              />
            ) : surface === "revision" ? (
              <RevisionSurface
                snapshot={snapshot}
                service={service}
                onReturn={() => navigate("report")}
                onStateAction={handleStateAction}
              />
            ) : (
              <SecondarySurface surface={surface} onReturn={() => navigate("report")} />
            )}
          </main>
        </div>
      </div>
    </div>
  );
}

function Sidebar({
  active,
  mobileOpen,
  onNavigate,
  onClose,
  returnFocusRef,
}: {
  readonly active: Surface;
  readonly mobileOpen: boolean;
  readonly onNavigate: (surface: Surface) => void;
  readonly onClose: () => void;
  readonly returnFocusRef: RefObject<HTMLButtonElement | null>;
}) {
  const sidebarRef = useRef<HTMLElement>(null);
  const wasOpenRef = useRef(false);

  useEffect(() => {
    if (mobileOpen) {
      wasOpenRef.current = true;
      sidebarRef.current?.querySelector<HTMLButtonElement>("button")?.focus();
    } else if (wasOpenRef.current) {
      wasOpenRef.current = false;
      returnFocusRef.current?.focus();
    }
  }, [mobileOpen, returnFocusRef]);

  const trapFocus = (event: ReactKeyboardEvent<HTMLElement>) => {
    if (!mobileOpen || event.key !== "Tab") return;
    const controls = Array.from(
      event.currentTarget.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
      ),
    );
    if (controls.length === 0) return;
    const first = controls[0];
    const last = controls[controls.length - 1];
    if (event.shiftKey && document.activeElement === first) {
      event.preventDefault();
      last.focus();
    } else if (!event.shiftKey && document.activeElement === last) {
      event.preventDefault();
      first.focus();
    }
  };

  const group = (items: typeof navigation) =>
    items.map((item) => (
      <button
        key={item.id}
        type="button"
        className="nav-item"
        aria-current={active === item.id ? "page" : undefined}
        onClick={() => onNavigate(item.id)}
      >
        {item.label}
      </button>
    ));

  return (
    <>
      <button
        type="button"
        className="navigation-scrim"
        aria-label="关闭菜单"
        aria-hidden={!mobileOpen}
        tabIndex={mobileOpen ? 0 : -1}
        data-open={mobileOpen}
        onClick={onClose}
      />
      <aside
        ref={sidebarRef}
        id="primary-navigation"
        className="sidebar"
        data-mobile-open={mobileOpen}
        aria-label="主导航"
        role={mobileOpen ? "dialog" : undefined}
        aria-modal={mobileOpen ? "true" : undefined}
        onKeyDown={trapFocus}
      >
        <button type="button" className="wordmark" onClick={() => onNavigate("report")}>
          DraftLoop
        </button>
        <nav className="navigation-stack">
          <div className="nav-group">{group(navigation)}</div>
          <div className="nav-divider" />
          <div className="nav-group">{group(workflowNavigation)}</div>
        </nav>
        <nav className="utility-navigation" aria-label="账户与设置">
          {group(utilityNavigation)}
        </nav>
      </aside>
    </>
  );
}

function ExportButton({
  service,
  snapshot,
}: {
  readonly service: DraftLoopApplicationService;
  readonly snapshot: WorkspaceSnapshot;
}) {
  const [exporting, setExporting] = useState(false);
  const [exportError, setExportError] = useState(false);
  const [exportReady, setExportReady] = useState(false);

  const exportReport = async () => {
    setExporting(true);
    setExportError(false);
    setExportReady(false);
    try {
      await service.deliverReportExport(snapshot);
      setExportReady(true);
    } catch {
      setExportError(true);
    } finally {
      setExporting(false);
    }
  };

  return (
    <div className="export-control">
      <button type="button" className="export-button" onClick={exportReport} disabled={exporting}>
        {exporting ? "准备中" : "导出 PDF"}
      </button>
      {exportError ? <span role="alert">导出失败，请重试</span> : null}
      {exportReady ? <span role="status">PDF 已就绪</span> : null}
    </div>
  );
}

function LoadingView() {
  return (
    <section className="state-view" aria-live="polite" aria-busy="true">
      <p className="eyebrow">正在载入</p>
      <h1>打开写作报告</h1>
      <p>正在核对结果、报告与导出文件的版本关系。</p>
      <div className="loading-line" />
    </section>
  );
}

function LoadFailure({ onRetry }: { readonly onRetry: () => void }) {
  return (
    <section className="state-view" role="alert" aria-labelledby="load-failure-title">
      <p className="eyebrow">连接中断</p>
      <h1 id="load-failure-title">暂时无法打开报告</h1>
      <p className="state-summary">没有显示未校验的数据。请重新连接后再试。</p>
      <div className="primary-actions">
        <button type="button" className="primary-button" onClick={onRetry}>
          重新载入
        </button>
      </div>
    </section>
  );
}

function ReportSurface({
  snapshot,
  onBeginRevision,
  onStateAction,
}: {
  readonly snapshot: WorkspaceSnapshot;
  readonly onBeginRevision: () => void;
  readonly onStateAction: (action: string) => Promise<void>;
}) {
  const access = accessFor(snapshot);
  const projection = snapshot.presentationProjection;

  if (!access.normalReport || !isNormalReport(projection)) {
    return <NonReportState snapshot={snapshot} onAction={onStateAction} />;
  }

  return <CompleteReport report={projection.content} onBeginRevision={onBeginRevision} />;
}

function CompleteReport({
  report,
  onBeginRevision,
}: {
  readonly report: NormalReportContent;
  readonly onBeginRevision: () => void;
}) {
  const [evidenceOpen, setEvidenceOpen] = useState(
    () => new URLSearchParams(window.location.search).get("evidence") === "open",
  );
  const [evidenceMoving, setEvidenceMoving] = useState(false);
  const [fullReportOpen, setFullReportOpen] = useState(false);
  const evidenceTriggerRef = useRef<HTMLButtonElement>(null);
  const closeEvidenceRef = useRef<HTMLButtonElement>(null);
  const evidenceSheetRef = useRef<HTMLElement>(null);
  const evidenceFocusPendingRef = useRef(false);
  const evidenceReturnFocusPendingRef = useRef(false);
  const compactEvidence = useMediaQuery("(max-width: 900px)");
  const focus = report.focus;

  const changeEvidence = (open: boolean) => {
    if (open) evidenceFocusPendingRef.current = true;
    if (!open) evidenceReturnFocusPendingRef.current = true;
    setEvidenceMoving(true);
    setEvidenceOpen(open);
    window.setTimeout(() => setEvidenceMoving(false), 280);
  };

  useEffect(() => {
    if (evidenceOpen && evidenceFocusPendingRef.current) {
      evidenceFocusPendingRef.current = false;
      closeEvidenceRef.current?.focus();
    } else if (!evidenceOpen && evidenceReturnFocusPendingRef.current) {
      evidenceReturnFocusPendingRef.current = false;
      evidenceTriggerRef.current?.focus();
    }
  }, [evidenceOpen]);

  useEffect(() => {
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape" && evidenceOpen) {
        changeEvidence(false);
        return;
      }
      if (event.key === "Tab" && evidenceOpen && compactEvidence) {
        const sheet = evidenceSheetRef.current;
        if (!sheet) return;
        const controls = Array.from(
          sheet.querySelectorAll<HTMLElement>(
            'button:not([disabled]), [href], [tabindex]:not([tabindex="-1"])',
          ),
        );
        if (controls.length === 0) return;
        const first = controls[0];
        const last = controls[controls.length - 1];
        if (!sheet.contains(document.activeElement)) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
        } else if (event.shiftKey && document.activeElement === first) {
          event.preventDefault();
          last.focus();
        } else if (!event.shiftKey && document.activeElement === last) {
          event.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", closeOnEscape);
    return () => document.removeEventListener("keydown", closeOnEscape);
  }, [compactEvidence, evidenceOpen]);

  return (
    <div
      className="report-stage"
      data-evidence-open={evidenceOpen}
      data-evidence-moving={evidenceMoving}
    >
      <article
        className="report-column"
        aria-labelledby="report-title"
        inert={compactEvidence && evidenceOpen ? true : undefined}
      >
        <header className="report-heading">
          <p className="eyebrow">
            {report.taskLabel} <span aria-hidden="true">·</span> {report.wordCount} 字
          </p>
          <h1 id="report-title">{report.title}</h1>
          <p className="score-summary">
            预计区间 <span>{report.likelyRange}</span>
            <span aria-hidden="true">·</span>
            信心 <strong>{report.confidence}</strong>
          </p>
          <details className="score-details">
            <summary>查看评分细项</summary>
            <dl>
              {report.criteria.map((criterion) => (
                <div key={criterion.label}>
                  <dt>{criterion.label}</dt>
                  <dd>{criterion.likelyRange}</dd>
                </div>
              ))}
            </dl>
          </details>
          <p className="disclaimer">{report.disclaimer}</p>
        </header>

        <div className="report-rule" />

        <section className="focus-section" aria-labelledby="focus-title">
          <p className="eyebrow">
            当前重点 <span aria-hidden="true">·</span> {focus.index} / {focus.total}
          </p>
          <h2 id="focus-title">{focus.title}</h2>
          <div className="focus-explanation">
            <p>{focus.explanation[0]}</p>
            <p>{focus.explanation[1]}</p>
          </div>

          <button
            ref={evidenceTriggerRef}
            type="button"
            className="evidence-trigger"
            aria-expanded={evidenceOpen}
            aria-controls="evidence-sheet"
            onClick={() => changeEvidence(!evidenceOpen)}
          >
            <span>{focus.evidenceLead}</span>
            <span className="evidence-underline">{focus.evidenceUnderline}</span>
            <span>{focus.evidenceTail}</span>
            <span className="sr-only">，查看对应证据</span>
          </button>

          <ol className="cause-chain" aria-label="论证结构">
            {focus.causeChain.map((item, index) => (
              <li key={item}>
                <span>{item}</span>
                {index < focus.causeChain.length - 1 ? (
                  <span className="chain-arrow" aria-hidden="true">
                    →
                  </span>
                ) : null}
              </li>
            ))}
          </ol>

          <div className="primary-actions">
            <button
              type="button"
              className="primary-button"
              aria-label="开始修改当前重点"
              onClick={onBeginRevision}
            >
              开始修改
            </button>
            <button
              type="button"
              className="text-button"
              aria-expanded={fullReportOpen}
              aria-controls="full-report"
              onClick={() => setFullReportOpen((open) => !open)}
            >
              {fullReportOpen ? "收起完整报告" : "查看完整报告"}
            </button>
          </div>
        </section>

        {fullReportOpen ? <FullReport /> : null}
      </article>

      <div className="evidence-edge-blur" aria-hidden="true" />
      <aside
        ref={evidenceSheetRef}
        id="evidence-sheet"
        className="evidence-sheet"
        aria-labelledby="evidence-title"
        aria-describedby={compactEvidence ? "evidence-context evidence-locator" : "evidence-locator"}
        aria-hidden={!evidenceOpen}
        role={compactEvidence && evidenceOpen ? "dialog" : undefined}
        aria-modal={compactEvidence && evidenceOpen ? "true" : undefined}
      >
        <p id="evidence-context" className="evidence-context">
          写作报告 <span aria-hidden="true">·</span> 当前重点 {focus.index} / {focus.total}
        </p>
        <h2 id="evidence-title">证据</h2>
        <p id="evidence-locator" className="evidence-locator">{focus.locator}</p>
        <blockquote>
          {focus.evidenceLead}
          <span>{focus.evidenceUnderline}</span>
          {focus.evidenceTail}
        </blockquote>
        <p className="boundary-note">{focus.boundaryNote}</p>
        <button
          ref={closeEvidenceRef}
          type="button"
          className="sheet-close"
          tabIndex={evidenceOpen ? 0 : -1}
          onClick={() => changeEvidence(false)}
        >
          收起
        </button>
      </aside>
    </div>
  );
}

function FullReport() {
  return (
    <section id="full-report" className="full-report" aria-labelledby="full-report-title">
      <p className="eyebrow">完整报告</p>
      <h2 id="full-report-title">把清楚的观点写完整</h2>
      <div className="report-list">
        <section>
          <h3>已经做得好</h3>
          <p>立场清楚，段落中心明确，读者能够顺着你的主要观点阅读。</p>
        </section>
        <section>
          <h3>优先改进</h3>
          <p>因果关系停在结论处。补足中间的解释，比替换复杂词汇更重要。</p>
        </section>
        <section>
          <h3>下一次练习</h3>
          <p>选一个主体段，用“原因—机制—结果”补写两句，再检查是否都服务于中心句。</p>
        </section>
      </div>
    </section>
  );
}

function RevisionSurface({
  snapshot,
  service,
  onReturn,
  onStateAction,
}: {
  readonly snapshot: WorkspaceSnapshot;
  readonly service: DraftLoopApplicationService;
  readonly onReturn: () => void;
  readonly onStateAction: (action: string) => Promise<void>;
}) {
  const canRevise = accessFor(snapshot).normalReport;
  const [text, setText] = useState(
    "This is because practical experience lets students apply abstract knowledge to decisions they will face at work.",
  );
  const [hintOpen, setHintOpen] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [submitError, setSubmitError] = useState(false);
  const revisionKeyRef = useRef(newRequestKey("revision"));

  if (!canRevise) return <NonReportState snapshot={snapshot} onAction={onStateAction} />;

  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSubmitting(true);
    setSubmitError(false);
    try {
      await service.submitRevision(snapshot, {
        idempotencyKey: revisionKeyRef.current,
        candidateScript: text,
        assistance: hintOpen ? "HINT_LEVEL_1" : "NONE",
      });
      setSubmitted(true);
    } catch {
      setSubmitError(true);
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <article className="revision-column" aria-labelledby="revision-title">
      <p className="eyebrow">第 2 段 · 当前重点 1 / 3</p>
      <h1 id="revision-title">补足这一句的解释</h1>
      <p className="revision-intro">
        保留你的观点，只补写“为什么”。DraftLoop 不会替你改写整段。
      </p>

      <blockquote className="revision-evidence">
        …universities should give students more practical skills because this will help them in the future.
      </blockquote>

      <form onSubmit={submit}>
        <label htmlFor="revision-text">你的补充解释</label>
        <textarea
          id="revision-text"
          value={text}
          rows={7}
          maxLength={700}
          onChange={(event) => {
            if (submitted || submitError) {
              revisionKeyRef.current = newRequestKey("revision");
            }
            setText(event.target.value);
            setSubmitted(false);
            setSubmitError(false);
          }}
        />
        <div className="editor-meta">
          <button type="button" className="text-button" onClick={() => setHintOpen((open) => !open)}>
            {hintOpen ? "收起提示" : "需要一点提示"}
          </button>
          <span>{text.length} / 700</span>
        </div>
        {hintOpen ? (
          <p className="hint" role="note">
            提示 1：具体说明“实践经验”怎样帮助学生把知识用于真实选择。此处使用了引导帮助。
          </p>
        ) : null}
        {submitted ? (
          <p className="submission-confirmation" role="status">
            修改已提交验证。原文与帮助使用情况会一起保留。
          </p>
        ) : null}
        {submitError ? (
          <p className="submission-error" role="alert">
            修改没有提交成功。原文仍保留，请检查连接后重试。
          </p>
        ) : null}
        <div className="primary-actions">
          <button type="submit" className="primary-button" disabled={!text.trim() || submitting}>
            {submitting ? "提交中" : "提交修改"}
          </button>
          <button type="button" className="text-button" onClick={onReturn}>
            返回写作报告
          </button>
        </div>
      </form>
    </article>
  );
}

function NonReportState({
  snapshot,
  onAction,
}: {
  readonly snapshot: WorkspaceSnapshot;
  readonly onAction?: (action: string) => Promise<void>;
}) {
  const projection = snapshot.presentationProjection;
  const isReview = projection.kind === "REVIEW_PROJECTION";
  const [actionError, setActionError] = useState(false);
  const [pendingAction, setPendingAction] = useState<string | null>(null);
  const content =
    projection.kind === "NORMAL_REPORT"
      ? {
          title: "报告暂不可用",
          summary: "结果与报告的版本关系未通过校验，因此不会显示或导出普通报告。",
          safeActions: ["返回历史记录", "联系支持"] as const,
        }
      : projection.content;

  return (
    <section className="state-view" data-kind={projection.kind.toLowerCase()} aria-labelledby="state-title">
      <p className="eyebrow">
        {isReview ? "复核状态" : "分析状态"} <span aria-hidden="true">·</span>{" "}
        {semanticStateLabels[snapshot.semanticResult.state]}
      </p>
      <h1 id="state-title">{content.title}</h1>
      <p className="state-summary">{content.summary}</p>
      {projection.kind === "REVIEW_PROJECTION" ? (
        <div id="review-reasons" className="review-reasons" tabIndex={-1}>
          <h2>复核原因</h2>
          <ul>
            {projection.content.reasons.map((reason) => (
              <li key={reason}>{reason}</li>
            ))}
          </ul>
        </div>
      ) : null}
      <p className="state-boundary">
        当前状态不会显示预计区间，也不能生成普通写作报告或 PDF。
      </p>
      <div className="primary-actions">
        {content.safeActions.map((action, index) => (
          <button
            key={action}
            type="button"
            className={index === 0 ? "primary-button" : "text-button"}
            disabled={pendingAction !== null}
            onClick={() => {
              if (!onAction) return;
              setPendingAction(action);
              setActionError(false);
              void onAction(action)
                .catch(() => setActionError(true))
                .finally(() => setPendingAction(null));
            }}
          >
            {pendingAction === action ? "处理中" : action}
          </button>
        ))}
      </div>
      {actionError ? (
        <p className="submission-error" role="alert">
          操作未完成，请稍后重试；现有写作和结果状态没有改变。
        </p>
      ) : null}
    </section>
  );
}

function SecondarySurface({
  surface,
  onReturn,
}: {
  readonly surface: Exclude<Surface, "report" | "revision">;
  readonly onReturn: () => void;
}) {
  const descriptions: Record<typeof surface, string> = {
    new: "选择任务类型，粘贴题目与写作内容后再提交分析。",
    history: "按时间查看你的写作、修改记录与各自独立的结果状态。",
    profile: "查看长期学习证据。使用过帮助的内容不会被算作独立掌握。",
    progress: "这里汇总已完成的练习与下一次建议，不用平均分掩盖失败结果。",
    privacy: "查看同意记录、导出个人数据，或提交可审计的删除请求。",
    settings: "管理界面语言与通知。Provider 密钥不会进入浏览器。",
  };

  return (
    <section className="secondary-view" aria-labelledby="secondary-title">
      <p className="eyebrow">DraftLoop</p>
      <h1 id="secondary-title">{navigationLabel(surface)}</h1>
      <p>{descriptions[surface]}</p>
      <button type="button" className="text-button" onClick={onReturn}>
        返回写作报告
      </button>
    </section>
  );
}
