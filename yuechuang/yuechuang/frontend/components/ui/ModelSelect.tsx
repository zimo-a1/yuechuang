'use client';
import {modelDisplayName} from '@/lib/modelLabel';
type Option = {id: string; label?: string; disabled?: boolean};
export default function ModelSelect({value, onChange, options, label, disabled, group, className}: {
  value: string; onChange: (value: string) => void; options: Option[]; label: string;
  disabled?: boolean; group?: string; className?: string;
}) {
  const unique = options.filter((item, index) => options.findIndex(other => other.id === item.id) === index);
  const choices = unique.map((item, index) => {
    const name = modelDisplayName(item.id, item.label);
    const duplicate = unique.filter(other => modelDisplayName(other.id, other.label) === name).length > 1;
    const connection = item.label?.includes(' · ') && !item.label.includes('svc/') ? item.label.split(' · ')[0] : item.id.startsWith('svc/') ? `服务连接 ${index + 1}` : '原供应商配置';
    return <option key={item.id} value={item.id} disabled={item.disabled}>{name}{duplicate ? ` · ${connection}` : ''}</option>;
  });
  return <select aria-label={label} title={modelDisplayName(value)} className={className} value={value} disabled={disabled} onChange={e => onChange(e.target.value)}>
    {!value && !unique.some(item => item.id === '') && <option value="">未配置模型</option>}
    {value && !unique.some(item => item.id === value) && <option value={value} disabled>{modelDisplayName(value)} · 当前不可用</option>}
    {group ? <optgroup label={group}>{choices}</optgroup> : choices}
  </select>;
}
