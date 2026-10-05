import type {StatusTone} from '@/lib/projectPresentation';

export default function StatusTag({tone,label,description,className=''}:{tone:StatusTone;label:string;description?:string;className?:string}) {
  return <span className={`yc-status-tag ${className}`} data-tone={tone} title={description}><i aria-hidden="true"/>{label}</span>;
}
