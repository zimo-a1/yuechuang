import Image from 'next/image';

/** Approved V5 outlines. The containing home link supplies the accessible name. */
export default function BrandMark({ large = false, tone = 'dark' }: { large?: boolean; tone?: 'dark' | 'light' }) {
  return <picture className={`yc-brand-logo${large ? ' yc-brand-logo-large' : ''}`}>
    <source media="(max-width: 600px)" srcSet={`/brand/yuechuang-chinese-${tone}-v5.svg`} />
    <Image src={`/brand/yuechuang-${tone}-v5.svg`} alt="" width={1112} height={312} unoptimized />
  </picture>;
}
