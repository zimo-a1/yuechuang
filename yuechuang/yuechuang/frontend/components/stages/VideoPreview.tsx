'use client';

import { useRef, useState } from 'react';

export default function VideoPreview({src, className, controls = true, label = '视频预览'}: {
  src: string; className?: string; controls?: boolean; label?: string;
}) {
  const video = useRef<HTMLVideoElement>(null);
  const [failedSource, setFailedSource] = useState<string | null>(null);
  return <div className="relative h-full w-full">
    <video ref={video} src={src} controls={controls} preload="metadata" playsInline aria-label={label}
      onError={() => setFailedSource(src)} className={className} />
    {failedSource === src && <div role="alert" className="absolute inset-0 flex flex-col items-center justify-center gap-2 bg-gray-950 px-4 text-center text-xs text-white">
      <p>视频暂时无法加载，已保存的文件不会因此删除。</p>
      <button type="button" className="min-h-11 rounded-lg border border-gray-500 px-4" onClick={event => {event.stopPropagation(); setFailedSource(null); video.current?.load();}}>重新加载视频</button>
    </div>}
  </div>;
}
