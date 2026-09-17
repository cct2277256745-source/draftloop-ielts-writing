import {render,screen,fireEvent,waitFor} from '@testing-library/react';
import {afterEach,expect,it,vi} from 'vitest';
import {RedesignedReport} from './RedesignedReport';
import type {ServerWorkspace} from './application/BrowserApi';
import fixture from './test/fixtures/detailed-workspace.json';
import {validDetailedReport} from './application/ReportValidation';

afterEach(()=>{vi.unstubAllGlobals();localStorage.clear();window.history.replaceState(null,'','/');});
const workspace=()=>structuredClone(fixture) as ServerWorkspace;
it('preserves exact editing history and submits a new version',async()=>{
 const w=workspace(),onSubmitted=vi.fn();
 const fetcher=vi.fn(async (_url:unknown,_init?:RequestInit)=>new Response(JSON.stringify({submission:{submissionId:'submission_new'}}),{status:202}));vi.stubGlobal('fetch',fetcher);
 render(<RedesignedReport report={w.presentation.report!} workspace={w} onSubmitted={onSubmitted} onRevise={()=>{}} onGuided={()=>{}}/>);
 expect(screen.getAllByRole('listitem')).toHaveLength(3);
 expect(screen.getByText('much people',{selector:'mark'})).toBeInTheDocument();
 const editor=screen.getByLabelText('重写作文正文'),original=w.source!.candidateScript;
 fireEvent.change(editor,{target:{value:original.replace('much people','many people')}});
 fireEvent.click(screen.getByRole('button',{name:'撤销修改'}));expect(editor).toHaveValue(original);
 fireEvent.click(screen.getByRole('button',{name:'重做修改'}));
 fireEvent.click(screen.getByRole('button',{name:'提交重写，深度批改'}));
 await waitFor(()=>expect(onSubmitted).toHaveBeenCalledWith('submission_new'));
 const sent=JSON.parse(fetcher.mock.calls[0][1]!.body as string);expect(sent.candidateScript).toContain('carry many people');expect(sent.question).toBe(w.source!.question);expect(sent.targetBand).toBe(7.5);
});
it('keyboard switches to every-paragraph analysis with only necessary corrections',()=>{
 const w=workspace();render(<RedesignedReport report={w.presentation.report!} workspace={w} onSubmitted={()=>{}} onRevise={()=>{}} onGuided={()=>{}}/>);
 fireEvent.keyDown(screen.getByRole('tab',{name:'根据修改建议重写'}),{key:'ArrowRight'});
 expect(screen.getByRole('tab',{name:'深度批改'})).toHaveFocus();
 expect(screen.getAllByRole('heading',{level:3,name:/^第 \d 段/})).toHaveLength(5);
 expect(screen.getAllByRole('table')).toHaveLength(2);
 expect(screen.getAllByText('这一段没有需要单独列出的词句修改。')).toHaveLength(3);
 expect(screen.getByRole('button',{name:'导出 PDF'})).toBeInTheDocument();
});
it('rejects malformed or cross-version reports before rendering',()=>{
 const w=workspace(),r=w.presentation.report!;expect(validDetailedReport(r,w.source)).toBe(true);
 r.detailedReport!.priorities[0].location.quote='invented quote';expect(validDetailedReport(r,w.source)).toBe(false);
 const w2=workspace();w2.presentation.report!.detailedReport!.lockedScoreSha256='b'.repeat(64);expect(validDetailedReport(w2.presentation.report!,w2.source)).toBe(false);
 const w3=workspace();Object.assign(w3.presentation.report!.detailedReport!,{mindMap:null});expect(validDetailedReport(w3.presentation.report!,w3.source)).toBe(false);
});
