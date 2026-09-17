import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { GuidedRevision } from './GuidedRevision';
import { PrivacySettings } from './PrivacySettings';
import { LiveWorkspace } from './LiveWorkspace';

afterEach(() => { vi.unstubAllGlobals(); localStorage.clear(); window.history.replaceState(null, '', '/'); });
const respond = (value: unknown) => new Response(JSON.stringify(value), { status: 200 });

it('reveals only the requested hint and sends the current version', async () => {
  let revealed = false;
  const fetcher = vi.fn(async (_url: string, init: RequestInit) => {
    if (init.method === 'POST') { revealed = true; return respond({}); }
    return respond({ available: true, issues: [{ issueId: 'issue-one', criterion: 'GRA', version: revealed ? 1 : 0,
      session: { status: 'ACTIVE', assistanceDepth: revealed ? 'LOCATION' : 'NONE', revealed: revealed ? [{ level: 1, textZh: '精确原文位置', textEn: 'Exact source text', containsReferenceAnswer: false }] : [] } }], revisions: [] });
  });
  vi.stubGlobal('fetch', fetcher);
  render(<GuidedRevision id="submission_fixture" source={{ question: 'Discuss both views.', candidateScript: 'Original draft.' }} onReport={() => {}} onAssess={() => {}} />);
  fireEvent.click(await screen.findByRole('button', { name: '展开原文位置' }));
  expect(await screen.findByText('精确原文位置')).toBeInTheDocument();
  expect(screen.queryByRole('button', { name: '展开参考表达' })).not.toBeInTheDocument();
  const request = fetcher.mock.calls.find(([, init]) => init.method === 'POST')![1];
  expect(JSON.parse(request.body as string)).toEqual({ issueId: 'issue-one', expectedVersion: 0, action: 'REVEAL' });
});

it('preserves a revision and idempotency key when verification submission loses its response', async () => {
  const fetcher = vi.fn(async (url: string) => {
    if (url.endsWith('/revisions')) throw new Error('offline');
    return respond({ available: true, issues: [], revisions: [] });
  });
  vi.stubGlobal('fetch', fetcher);
  render(<GuidedRevision id="submission_fixture" source={{ question: 'Discuss both views.', candidateScript: 'Original draft.' }} onReport={() => {}} onAssess={() => {}} />);
  const edit = await screen.findByLabelText('修改稿 V2');
  fireEvent.change(edit, { target: { value: 'My revised draft.' } });
  fireEvent.click(screen.getByRole('button', { name: '核验这次修改' }));
  await screen.findByRole('alert');
  const stored = JSON.parse(localStorage.getItem('draftloop.revision.submission_fixture')!);
  expect(stored.text).toBe('My revised draft.'); expect(stored.key).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: '核验这次修改' }));
  await waitFor(() => expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/revisions'))).toHaveLength(2));
  expect(JSON.parse(localStorage.getItem('draftloop.revision.submission_fixture')!).key).toBe(stored.key);
});

it('retries a confirmed failed verification with a new key and the unchanged saved draft', async () => {
  const oldKey = 'confirmed-failure-key';
  localStorage.setItem('draftloop.revision.submission_fixture', JSON.stringify({ text: 'My saved revision.', key: oldKey, assistance: 'UNKNOWN' }));
  const fetcher = vi.fn(async (_url: string, init: RequestInit) => respond(init.method === 'POST' ? {} : {
    available: true, issues: [], revisions: [{ revisionId: 'revision_' + oldKey, state: 'FAILED', candidateScript: 'My saved revision.', assistance: 'UNKNOWN' }],
  }));
  vi.stubGlobal('fetch', fetcher);
  render(<GuidedRevision id="submission_fixture" source={{ question: 'Discuss both views.', candidateScript: 'Original draft.' }} onReport={() => {}} onAssess={() => {}} />);
  await screen.findByText('本次核验未完成');
  fireEvent.click(screen.getByRole('button', { name: '核验这次修改' }));
  await waitFor(() => expect(fetcher.mock.calls.some(([, init]) => init.method === 'POST')).toBe(true));
  const request = fetcher.mock.calls.find(([, init]) => init.method === 'POST')![1];
  expect(new Headers(request.headers).get('Idempotency-Key')).not.toBe(oldKey);
  expect(JSON.parse(request.body as string)).toEqual({ candidateScript: 'My saved revision.', assistance: 'UNKNOWN' });
  expect(JSON.parse(localStorage.getItem('draftloop.revision.submission_fixture')!).key).not.toBe(oldKey);
});

it('requires the exact typed confirmation before a personal-data deletion command', async () => {
  const fetcher = vi.fn(async (url: string) => respond(url.endsWith('/delete') ? { deletion: { deletionId: 'deletion_fixture', state: 'COMPLETE', completedAt: '2026-09-10' } } : { policyVersion: 'policy', consents: [{ purpose: 'TRAINING', granted: false }, { purpose: 'PRODUCT_ANALYTICS', granted: false }] }));
  vi.stubGlobal('fetch', fetcher);
  render(<PrivacySettings onDeleted={() => {}} />);
  expect(await screen.findByRole('button', { name: '永久删除个人数据' })).toBeDisabled();
  fireEvent.change(screen.getByLabelText('输入“删除我的数据”以确认'), { target: { value: '删除我的数据' } });
  fireEvent.click(screen.getByRole('button', { name: '永久删除个人数据' }));
  expect(await screen.findByRole('heading', { name: '个人数据已删除' })).toBeInTheDocument();
  expect(fetcher.mock.calls.filter(([url]) => url.endsWith('/delete'))).toHaveLength(1);
});

it('requires a Task 1 chart before enabling analysis', async () => {
  localStorage.clear(); vi.stubGlobal('fetch', vi.fn(async () => respond({})));
  render(<LiveWorkspace />);
  fireEvent.change(screen.getByLabelText('写作任务'), { target: { value: 'task1' } });
  fireEvent.change(screen.getByLabelText('作文题目'), { target: { value: 'Describe the chart.' } });
  fireEvent.change(screen.getByLabelText('你的作文'), { target: { value: 'A draft.' } });
  await screen.findByText('已连接');
  expect(screen.getByLabelText('题目图表')).toBeInTheDocument();
  expect(screen.getByRole('button', { name: '立即批改' })).toBeDisabled();
});

it('switches interface language without translating or losing the writing draft', async () => {
  localStorage.clear(); vi.stubGlobal('fetch', vi.fn(async () => respond({})));
  render(<LiveWorkspace />);
  fireEvent.change(screen.getByLabelText('你的作文'), { target: { value: 'Keep this exact English text.' } });
  fireEvent.click(screen.getByRole('button', { name: 'Switch interface to English' }));
  expect(await screen.findByLabelText('Essay text')).toHaveValue('Keep this exact English text.');
  expect(screen.getByRole('button', { name: 'Get feedback' })).toBeInTheDocument();
  expect(document.documentElement.lang).toBe('en-US');
  expect(localStorage.getItem('draftloop.locale.v1')).toBe('en-US');
  fireEvent.click(screen.getByRole('button', { name: '切换为中文界面' }));
  expect(await screen.findByLabelText('你的作文')).toHaveValue('Keep this exact English text.');
});
