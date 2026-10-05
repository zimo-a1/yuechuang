'use client';

import BrandMark from './BrandMark';
import ToastHost from './ui/ToastHost';
import {toast} from '@/lib/uiFeedback';

import { errorMessage, type SessionSummary } from '@/lib/workflowTypes';

import Link from 'next/link';
import { usePathname, useRouter } from 'next/navigation';
import { CheckCircle2, Clock, Home, LayoutGrid, BookOpen, Loader2, Settings, Trash2, ListTodo, X } from 'lucide-react';
import clsx from 'clsx';
import { useEffect, useRef, useState } from 'react';
import { clearTempCache, fetchSessions } from '@/lib/workflowApi';
import {projectProgress, STAGE_NAMES} from '@/lib/projects';
import {studioHref} from '@/lib/studio';
import {NOVEL_STEPS,type NovelAction} from '@/lib/novelApi';
import {canLeaveEditor} from '@/lib/unsavedChanges';

const NAV_ITEMS = [
  { href: '/', label: '首页', icon: Home },
  { href: '/projects', label: '项目', icon: LayoutGrid },
  { href: '/ip', label: 'IP 库', icon: BookOpen },
];



const TASK_STATUS_STYLE: Record<string, string> = {
  pending: 'bg-gray-100 text-gray-500',
  running: 'bg-blue-50 text-blue-600',
  waiting: 'bg-amber-50 text-amber-600',
  completed: 'bg-green-50 text-green-600',
  failed: 'bg-red-50 text-red-600',
  stopped: 'bg-gray-100 text-gray-500',
};

function statusText(status?: string) {
  if (status === 'pending') return '等待中';
  if (status === 'running') return '生成中';
  if (status === 'waiting') return '待确认';
  if (status === 'completed') return '已完成';
  if (status === 'failed') return '失败';
  if (status === 'stopped') return '已停止';
  return status || '未知';
}

type RunningTaskItem = {
  id: string;
  href: string;
  title: string;
  scope: string;
  status: string;
  progress: number;
};

const COMPLETED_TASK_DISMISS_KEY = 'yuechuang.dismissed-completed-tasks';


function projectTaskFromSession(session: SessionSummary): RunningTaskItem | null {
  if(session.novel_task&&['queued','running','stopping'].includes(session.novel_task.status)){const n=session.novel_task;return {id:`novel-${n.id}`,href:`/projects/${session.id}/adaptation`,title:session.title||'小说改编',scope:NOVEL_STEPS[n.action as NovelAction]||'小说改编',status:'running',progress:Math.round(n.requests/Math.max(1,n.max_requests)*100)};}
  const progress = projectProgress(session);
  if (progress.state !== 'running') return null;
  return {id: `project-${session.id}`, href: studioHref(session.id, progress.stage),
    title: session.title || session.idea || session.id, scope: STAGE_NAMES[progress.stage],
    status:'running', progress:Math.round(progress.completed / progress.total * 100)};
}

function projectReviewTaskFromSession(session: SessionSummary): RunningTaskItem | null {
  const progress = projectProgress(session);
  if(session.novel_task&&!['queued','running','stopping'].includes(session.novel_task.status)){const n=session.novel_task;return {id:`novel-review-${n.id}`,href:`/projects/${session.id}/adaptation`,title:session.title||'小说改编',scope:NOVEL_STEPS[n.action as NovelAction]||'小说改编',status:n.status==='completed'?'waiting':['stopped','interrupted'].includes(n.status)?'stopped':'failed',progress:n.status==='completed'?100:Math.round(n.requests/Math.max(1,n.max_requests)*100)};}
  if (!['review', 'attention', 'completed'].includes(progress.state)) return null;
  const status = progress.state === 'review' ? 'waiting' : progress.state === 'attention' ? (session.status[progress.stage] === 'stopped' ? 'stopped' : 'failed') : 'completed';
  return {id: `project-review-${session.id}-${progress.state === 'completed' ? 'completed' : progress.stage}${progress.state === 'attention' ? `-${status}` : ''}`,
    href: studioHref(session.id, progress.stage), title: session.title || session.idea || session.id,
    scope: STAGE_NAMES[progress.stage], status,
    progress:Math.round(progress.completed / progress.total * 100)};
}

function loadDismissedCompletedTasks(): Set<string> {
  if (typeof window === 'undefined') return new Set();
  try {
    const raw = window.localStorage.getItem(COMPLETED_TASK_DISMISS_KEY);
    const parsed = raw ? JSON.parse(raw) : [];
    return new Set(Array.isArray(parsed) ? parsed.filter((item): item is string => typeof item === 'string') : []);
  } catch {
    return new Set();
  }
}

function saveDismissedCompletedTasks(ids: Set<string>) {
  if (typeof window === 'undefined') return;
  window.localStorage.setItem(COMPLETED_TASK_DISMISS_KEY, JSON.stringify(Array.from(ids)));
}

function TaskPanel({
  title,
  icon,
  loading,
  tasks,
  currentPath,
  emptyText,
  onTaskClick,
}: {
  title: string;
  icon: React.ReactNode;
  loading?: boolean;
  tasks: RunningTaskItem[];
  currentPath: string;
  emptyText: string;
  onTaskClick: (task: RunningTaskItem) => void;
}) {
  return (
    <section className="yc-task-panel h-[20vh] min-h-32 border-t border-gray-100 p-3">
      <div className="mb-2 flex items-center gap-2 px-1">
        {icon}
        <span className="text-xs font-medium text-gray-500">{title}</span>
        {loading && <Loader2 className="ml-auto h-3 w-3 animate-spin text-gray-300" />}
      </div>
      <div className="h-[calc(100%-26px)] overflow-y-auto pr-1">
        {tasks.length ? (
          <div className="space-y-1.5">
            {tasks.map(task => {
              const active = currentPath === task.href.split('?')[0];
              return (
                <button
                  key={task.id}
                  type="button"
                  onClick={() => onTaskClick(task)}
                  className={clsx(
                    'yc-task-item w-full rounded-lg border px-2.5 py-2 text-left transition-colors',
                    active ? 'border-blue-100 bg-blue-50/60' : 'border-gray-100 bg-white hover:border-blue-200 hover:bg-blue-50/40'
                  )}
                >
                  <div className="flex items-center gap-2">
                    <span className="min-w-0 flex-1 truncate text-xs font-medium text-gray-700">
                      {task.title.slice(0, 36)}
                    </span>
                    <span className={clsx('flex-shrink-0 rounded px-1.5 py-0.5 text-[10px]', TASK_STATUS_STYLE[task.status] || TASK_STATUS_STYLE.pending)}>
                      {statusText(task.status)}
                    </span>
                  </div>
                  <div className="mt-1 flex items-center gap-2">
                    <span className="truncate text-[10px] text-gray-400">{task.scope}</span>
                    <div className="h-1 min-w-10 flex-1 overflow-hidden rounded-full bg-gray-100">
                      <div className="h-full rounded-full bg-blue-500" style={{ width: `${task.progress || 0}%` }} />
                    </div>
                  </div>
                </button>
              );
            })}
          </div>
        ) : (
          <div className="flex h-full items-center justify-center rounded-lg border border-dashed border-gray-100 px-2 text-center text-xs text-gray-300">
            {emptyText}
          </div>
        )}
      </div>
    </section>
  );
}

function SidebarTaskPanels({ currentPath }: { currentPath: string }) {
  const router = useRouter();
  const [runningTasks, setRunningTasks] = useState<RunningTaskItem[]>([]);
  const [completedTasks, setCompletedTasks] = useState<RunningTaskItem[]>([]);
  const [dismissedCompleted, setDismissedCompleted] = useState<Set<string>>(() => loadDismissedCompletedTasks());
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let disposed = false;
    const load = async () => {
      const sessions = await fetchSessions().catch(() => []);
      if (disposed) return;
      setRunningTasks(sessions.map(projectTaskFromSession)
        .filter((task): task is RunningTaskItem => Boolean(task)));
      setCompletedTasks(sessions.map(projectReviewTaskFromSession)
        .filter((task): task is RunningTaskItem => Boolean(task))
        .filter(task => task.status !== 'completed' || !dismissedCompleted.has(task.id)));
      setLoading(false);
    };
    void load();
    const timer = window.setInterval(() => { void load(); }, 3000);
    return () => { disposed = true; window.clearInterval(timer); };
  }, [dismissedCompleted]);

  const handleRunningClick = (task: RunningTaskItem) => {
    if (!canLeaveEditor()) return;
    router.push(task.href);
  };

  const handleCompletedClick = (task: RunningTaskItem) => {
    if (!canLeaveEditor()) return;
    if (task.status === 'completed') {
      const next = new Set(dismissedCompleted);
      next.add(task.id);
      saveDismissedCompletedTasks(next);
      setDismissedCompleted(next);
      setCompletedTasks(current => current.filter(item => item.id !== task.id));
    }
    router.push(task.href);
  };

  return (
    <>
      <TaskPanel
        title="进行中任务"
        icon={<Clock className="h-3.5 w-3.5 text-gray-400" />}
        loading={loading}
        tasks={runningTasks}
        currentPath={currentPath}
        emptyText="还没有进行中的任务"
        onTaskClick={handleRunningClick}
      />
      <TaskPanel
        title="待确认与任务结果"
        icon={<CheckCircle2 className="h-3.5 w-3.5 text-gray-400" />}
        tasks={completedTasks}
        currentPath={currentPath}
        emptyText="没有待确认事项或任务结果"
        onTaskClick={handleCompletedClick}
      />
    </>
  );
}

export default function AppShell({ children }: { children: React.ReactNode }) {
  const pathname = usePathname();
  const taskDialog = useRef<HTMLDialogElement>(null);
  const [clearingCache, setClearingCache] = useState(false);
  const [cacheMessage, setCacheMessage] = useState('');
  const [tasksOpen, setTasksOpen] = useState(false);
  const [taskCount, setTaskCount] = useState(0);
  useEffect(() => {
    let live = true;
    const load = () => fetchSessions().then(sessions => {
      if (live) setTaskCount(sessions.reduce((count, session) => {
        const running = projectTaskFromSession(session);
        const result = projectReviewTaskFromSession(session);
        return count + (running ? 1 : 0) + (result && result.status !== 'completed' ? 1 : 0);
      }, 0));
    }).catch(() => {});
    void load(); const timer = window.setInterval(load, 10000);
    return () => {live = false; window.clearInterval(timer);};
  }, []);
  const closeTasks = () => { taskDialog.current?.close(); setTasksOpen(false); };
  const handleClearCache = async () => {
    setClearingCache(true); setCacheMessage('');
    try {
      const result = await clearTempCache();
      setCacheMessage(`缓存已清空，删除 ${result.deleted} 项，释放 ${Number(result.freed_mb || 0).toFixed(2)} MB。`);
      toast.success('临时缓存已清理');
    } catch (error) { const message = errorMessage(error, '清空缓存失败'); setCacheMessage(message); toast.error(message); }
    finally { setClearingCache(false); }
  };
  return <div className={`yc-shell hb-shell yc-experience-shell${pathname.startsWith('/studio/') ? ' yc-experience-studio-shell' : ''}`}>
    <header className="hb-header">
      <Link href="/" className="hb-brand" aria-label="悦创首页"><BrandMark /></Link>
      <nav className="hb-header-nav" aria-label="工作空间导航">
        {[...NAV_ITEMS, {href:'/settings', label:'设置',icon:Settings}].map(item => {
          const Icon=item.icon; const active=item.href==='/'?pathname==='/':pathname.startsWith(item.href)||(item.href==='/projects'&&pathname.startsWith('/studio/'));
          return <Link key={item.href} href={item.href} aria-current={active?'page':undefined} className={active?'active':''}><Icon size={15} strokeWidth={1.8}/><span>{item.label}</span></Link>;
        })}
      </nav>
      <div className="hb-header-actions"><span className="yc-workspace-label">个人工作空间</span><button type="button" className="yc-task-trigger" aria-label={`任务中心${taskCount ? `，${taskCount} 项待处理` : ''}`} title="查看生成任务与待确认事项" aria-haspopup="dialog" onClick={()=>{setTasksOpen(true);taskDialog.current?.showModal();}}><ListTodo size={17}/><span>任务中心</span>{taskCount>0&&<b>{taskCount}</b>}</button></div>
    </header>
    <div className="hb-content">{children}</div>
    <ToastHost/>
    <dialog ref={taskDialog} className="hb-task-dialog" aria-labelledby="hb-task-title" onClose={()=>setTasksOpen(false)} onClick={e=>{if(e.target===e.currentTarget) closeTasks();}}>
      <header><h2 id="hb-task-title">任务中心</h2><button type="button" className="hb-icon-button" aria-label="关闭任务中心" onClick={closeTasks}><X size={18}/></button></header>
      {tasksOpen&&<div onClick={e=>{if((e.target as HTMLElement).closest('.yc-task-item')) closeTasks();}}><SidebarTaskPanels currentPath={pathname}/></div>}
      <footer><button type="button" className="yc-secondary-button" disabled={clearingCache} onClick={handleClearCache}>{clearingCache?<Loader2 size={14} className="animate-spin"/>:<Trash2 size={14}/>}清空临时缓存</button><Link href="/settings" onClick={closeTasks}>工作空间设置</Link></footer>
      {cacheMessage&&<p role="status" className="hb-cache-result">{cacheMessage}</p>}
    </dialog>
  </div>;
}
