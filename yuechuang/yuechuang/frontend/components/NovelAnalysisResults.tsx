import Link from 'next/link';
import {chapterProgress,NOVEL_STATUS,type NovelChunk} from '@/lib/novelApi';
import type {IPChapter} from '@/lib/ipApi';

export default function NovelAnalysisResults({chapters,chunks,sourceLink}:{
  chapters:IPChapter[];chunks:NovelChunk[];sourceLink:(chapterId:string)=>string;
}){
  const grouped=new Map<string,NovelChunk[]>();
  for(const chunk of chunks){
    const group=grouped.get(chunk.chapter_id)||[];group.push(chunk);grouped.set(chunk.chapter_id,group);
  }
  return <div className="yc-novel-results" aria-label="所选章节分析结果">
    {chapters.map(chapter=>{
      const parts=grouped.get(chapter.id)||[];
      const progress=chapterProgress(parts);
      const running=parts.some(c=>c.status==='running');
      const failed=parts.some(c=>['error','interrupted','stopped'].includes(c.status));
      const complete=parts.length>0&&progress.done===progress.total;
      const status=running?'running':complete?'completed':failed?'error':'pending';
      return <section className="yc-novel-result-chapter" key={chapter.id} aria-labelledby={`novel-${chapter.id}`}>
        <header>
          <div><h3 id={`novel-${chapter.id}`}>{chapter.title}</h3><span>{chapter.characters.toLocaleString()} 字{parts.length>1?` · ${progress.done}/${progress.total} 分片`:''}</span></div>
          <div><span className={`yc-novel-result-status is-${status}`}>{running?'分析中':complete?'已分析':failed?'待继续分析':'待分析'}</span><Link href={sourceLink(chapter.id)}>对照原文 ↗</Link></div>
        </header>
        {parts.map((chunk,index)=><div className="yc-novel-result-part" key={chunk.id}>
          {parts.length>1&&<h4>分片 {index+1} <small>{NOVEL_STATUS[chunk.status]}</small></h4>}
          {chunk.hard_split&&<small>此段较长，按连续字符拆分，正文完整保留。</small>}
          {chunk.result?<>
            <p>{chunk.result.summary}</p>
            {chunk.result.characters.length>0&&<div className="yc-novel-result-people"><h4>本章人物</h4>{chunk.result.characters.map(person=><p key={person.name}><b>{person.name}</b>{person.aliases.length>0&&<small>（{person.aliases.join('、')}）</small>} {person.description}</p>)}</div>}
            {chunk.result.relationships.length>0&&<div className="yc-novel-result-relations"><h4>人物关系</h4>{chunk.result.relationships.map((r,i)=><p key={i}><b>{r.source} → {r.target}</b> {r.description}</p>)}</div>}
            {chunk.result.events.length>0&&<div className="yc-novel-result-events"><h4>关键事件</h4><ol>{chunk.result.events.map((event,i)=><li key={i}><p>{event.description}</p><blockquote>{event.quote}</blockquote></li>)}</ol></div>}
          </>:<p className="yc-novel-result-placeholder">{chunk.status==='running'?'正在理解这一章，完成后会自动显示结果。':['error','interrupted','stopped'].includes(chunk.status)?'本章尚未完成，点击上方“继续未完成分析”即可接着处理。':'等待按所选范围依次分析。'}</p>}
        </div>)}
        {parts.length===0&&<p className="yc-novel-result-placeholder">配置文本模型后即可开始分析。</p>}
      </section>;
    })}
  </div>;
}
