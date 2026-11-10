/**
 * 极简 Markdown 渲染 —— 只服务于「产物预览」，不追求 CommonMark 完整度。
 *
 * 为什么不用第三方库
 * ------------------
 * 这轮约定不加依赖；而 AI 员工写出的产物绝大多数是标题 / 列表 / 代码块 /
 * 粗体 / 链接。这里只实现这几类，并且**全部走 React 节点**，不用
 * `dangerouslySetInnerHTML` —— 产物内容是模型生成的，不能当 HTML 执行。
 */

import { useMemo } from 'react'
import type { ReactNode } from 'react'

type Block =
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'code'; lang: string; text: string }
  | { kind: 'list'; ordered: boolean; items: string[] }
  | { kind: 'quote'; text: string }
  | { kind: 'hr' }
  | { kind: 'p'; text: string }

const HEADING = /^(#{1,6})\s+(.*)$/
const FENCE = /^```([\w+-]*)\s*$/
const UL_ITEM = /^\s*[-*+]\s+(.*)$/
const OL_ITEM = /^\s*\d+[.)]\s+(.*)$/
const QUOTE = /^\s*>\s?(.*)$/
const HR = /^\s*(?:-{3,}|\*{3,}|_{3,})\s*$/

export function parseMarkdown(source: string): Block[] {
  const lines = String(source ?? '').replace(/\r\n?/g, '\n').split('\n')
  const blocks: Block[] = []
  let index = 0
  while (index < lines.length) {
    const line = lines[index]
    if (!line.trim()) {
      index += 1
      continue
    }

    const fence = line.match(FENCE)
    if (fence) {
      const body: string[] = []
      index += 1
      while (index < lines.length && !/^```\s*$/.test(lines[index])) {
        body.push(lines[index])
        index += 1
      }
      // 跳过收尾的 ```（如果有）
      if (index < lines.length) index += 1
      blocks.push({ kind: 'code', lang: fence[1] ?? '', text: body.join('\n') })
      continue
    }

    const heading = line.match(HEADING)
    if (heading) {
      blocks.push({ kind: 'heading', level: heading[1].length, text: heading[2] })
      index += 1
      continue
    }

    if (HR.test(line)) {
      blocks.push({ kind: 'hr' })
      index += 1
      continue
    }

    if (QUOTE.test(line)) {
      const body: string[] = []
      while (index < lines.length) {
        const hit = lines[index].match(QUOTE)
        if (!hit) break
        body.push(hit[1])
        index += 1
      }
      blocks.push({ kind: 'quote', text: body.join('\n') })
      continue
    }

    if (UL_ITEM.test(line) || OL_ITEM.test(line)) {
      const ordered = OL_ITEM.test(line)
      const pattern = ordered ? OL_ITEM : UL_ITEM
      const items: string[] = []
      while (index < lines.length) {
        const hit = lines[index].match(pattern)
        if (!hit) break
        items.push(hit[1])
        index += 1
      }
      blocks.push({ kind: 'list', ordered, items })
      continue
    }

    // 普通段落：一直收到空行或下一个块级标记为止。
    const body: string[] = [line]
    index += 1
    while (index < lines.length) {
      const next = lines[index]
      if (
        !next.trim() ||
        FENCE.test(next) ||
        HEADING.test(next) ||
        HR.test(next) ||
        QUOTE.test(next) ||
        UL_ITEM.test(next) ||
        OL_ITEM.test(next)
      ) {
        break
      }
      body.push(next)
      index += 1
    }
    blocks.push({ kind: 'p', text: body.join('\n') })
  }
  return blocks
}

/** 行内语法：`code`、**bold**、[text](url)。非 http(s) 链接按纯文本渲染。 */
function inline(text: string, keyPrefix: string): ReactNode[] {
  const pattern = /`([^`]+)`|\*\*([^*]+)\*\*|\[([^\]]+)\]\(([^)\s]+)\)/g
  const nodes: ReactNode[] = []
  let last = 0
  let match: RegExpExecArray | null
  let seq = 0
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > last) nodes.push(text.slice(last, match.index))
    const key = `${keyPrefix}-${seq}`
    seq += 1
    if (match[1] !== undefined) {
      nodes.push(<code key={key}>{match[1]}</code>)
    } else if (match[2] !== undefined) {
      nodes.push(<strong key={key}>{match[2]}</strong>)
    } else if (/^https?:\/\//i.test(match[4] ?? '')) {
      nodes.push(
        <a key={key} href={match[4]} target="_blank" rel="noreferrer noopener">
          {match[3]}
        </a>,
      )
    } else {
      nodes.push(match[0])
    }
    last = match.index + match[0].length
  }
  if (last < text.length) nodes.push(text.slice(last))
  return nodes
}

export function Markdown({ text }: { text: string }) {
  const blocks = useMemo(() => parseMarkdown(text), [text])
  return (
    <div className="os-md">
      {blocks.map((block, index) => {
        const key = `md-${index}`
        switch (block.kind) {
          case 'heading': {
            const Tag = `h${Math.min(6, Math.max(1, block.level))}` as
              | 'h1'
              | 'h2'
              | 'h3'
              | 'h4'
              | 'h5'
              | 'h6'
            return <Tag key={key}>{inline(block.text, key)}</Tag>
          }
          case 'code':
            return (
              <pre key={key} className="os-pre" data-lang={block.lang || undefined}>
                <code>{block.text}</code>
              </pre>
            )
          case 'list': {
            const items = block.items.map((item, itemIndex) => (
              <li key={`${key}-${itemIndex}`}>{inline(item, `${key}-${itemIndex}`)}</li>
            ))
            return block.ordered ? <ol key={key}>{items}</ol> : <ul key={key}>{items}</ul>
          }
          case 'quote':
            return (
              <blockquote key={key}>
                {block.text.split('\n').map((line, lineIndex) => (
                  <p key={`${key}-${lineIndex}`}>{inline(line, `${key}-${lineIndex}`)}</p>
                ))}
              </blockquote>
            )
          case 'hr':
            return <hr key={key} />
          default:
            return (
              <p key={key}>
                {block.text.split('\n').map((line, lineIndex) => (
                  <span key={`${key}-${lineIndex}`}>
                    {lineIndex > 0 && <br />}
                    {inline(line, `${key}-${lineIndex}`)}
                  </span>
                ))}
              </p>
            )
        }
      })}
    </div>
  )
}
