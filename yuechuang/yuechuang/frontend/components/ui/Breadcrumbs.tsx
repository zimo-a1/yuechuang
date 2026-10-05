import Link from 'next/link';
export default function Breadcrumbs({items}: {items: {label: string; href?: string}[]}) {
  return <nav className="yc-breadcrumbs" aria-label="当前位置"><ol>{items.map((item, index) => <li key={`${index}-${item.label}`}><span aria-hidden="true" className="yc-breadcrumb-separator">/</span>{item.href ? <Link href={item.href} title={item.label}>{item.label}</Link> : <span aria-current="page" title={item.label}>{item.label}</span>}</li>)}</ol></nav>;
}
