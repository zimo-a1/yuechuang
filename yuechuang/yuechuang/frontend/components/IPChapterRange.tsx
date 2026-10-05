'use client';

import {useMemo, useState} from 'react';
import {BookOpen, ChevronDown} from 'lucide-react';
import type {IPChapter} from '@/lib/ipApi';
import {isValidChapterRange, selectChapterEndpoint} from '@/lib/chapterSelection';
import IPChapterList from './IPChapterList';

export default function IPChapterRange({chapters, first, last, onChange, onPendingChange, disabled}: {
  chapters: IPChapter[]; first: number; last: number; onChange: (first: number, last: number) => void;
  onPendingChange: (pending: boolean) => void; disabled?: boolean;
}) {
  const [expanded, setExpanded] = useState(false);
  const [anchor, setAnchor] = useState<number | null>(null);
  const valid = isValidChapterRange(first, last, chapters.length);
  const characters = useMemo(() => valid ? chapters.slice(first - 1, last).reduce((sum, chapter) => sum + chapter.characters, 0) : 0, [chapters, first, last, valid]);
  function select(start: number, end: number) {setAnchor(null); onPendingChange(false); onChange(start, end);}
  function choose(ordinal: number) {
    const next = selectChapterEndpoint(anchor, ordinal, chapters.length);
    if (!next) return;
    setAnchor(next.anchor); onPendingChange(next.anchor !== null); onChange(next.first, next.last);
  }
  return <section className="ex-ip-range" aria-label="改编章节范围">
    <div className="ex-ip-range-heading"><h3>改编章节</h3><span>共 {chapters.length.toLocaleString()} 项</span></div>
    <div className="ex-ip-range-presets" role="group" aria-label="快捷章节范围">
      <button type="button" disabled={disabled || !chapters.length} aria-pressed={anchor === null && first === 1 && last === Math.min(10, chapters.length)} onClick={() => select(1, Math.min(10, chapters.length))}>开头 10 项</button>
      <button type="button" disabled={disabled || !valid} aria-pressed={anchor === null && first > 1 && last === Math.min(first + 9, chapters.length)} onClick={() => select(first, Math.min(first + 9, chapters.length))}>从起点选 10 项</button>
      <button type="button" disabled={disabled || !chapters.length} aria-pressed={anchor === null && first === 1 && last === chapters.length} onClick={() => select(1, chapters.length)}>整本</button>
    </div>
    <button type="button" className="ex-ip-range-summary" disabled={disabled || !chapters.length} aria-expanded={expanded} onClick={() => setExpanded(!expanded)}><BookOpen size={18}/><span><strong>{valid ? `已选 ${last - first + 1} 项 · ${characters.toLocaleString()} 字符` : '暂无可选章节'}</strong><small>{valid ? `目录 ${first}—${last} · 含起止章节` : '请先导入正文'}</small></span><span className="ex-ip-range-edit">{expanded ? '收起目录' : '调整范围'}<ChevronDown size={15}/></span></button>
    {valid && <p className="ex-ip-range-names"><span title={chapters[first - 1].title}>起 {chapters[first - 1].title}</span><span title={chapters[last - 1].title}>止 {chapters[last - 1].title}</span></p>}
    {expanded && <div className="ex-ip-range-expanded"><p className="ex-ip-range-instruction" role="status">{anchor === null ? '先点起始项，再点结束项，即可选中中间全部章节。' : `起点已选目录 ${anchor}，再点一项作为结束；也可再次点击起点只选这一项。`}</p><IPChapterList chapters={chapters} label="改编章节" range={{first, last}} onChoose={choose} disabled={disabled}/></div>}
    {anchor !== null && !expanded && <p role="status" className="ex-ip-range-instruction">范围未选完，请展开目录选择结束项，或使用快捷范围。</p>}
  </section>;
}
