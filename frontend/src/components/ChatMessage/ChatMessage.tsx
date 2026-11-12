import React, { memo, useMemo } from 'react';
import type { ChatMessage as ChatMessageType, ToolCall } from '../../types';
import { avatarColor, cn, formatTime, initials } from '../../utils';

export interface ChatMessageProps {
  message: ChatMessageType;
  /** 当前用户名称（用于头像 fallback） */
  currentUserName?: string;
  /** 是否显示头像 */
  showAvatar?: boolean;
  /** 是否显示时间戳 */
  showTimestamp?: boolean;
  /** 自定义工具调用渲染 */
  renderToolCall?: (toolCall: ToolCall) => React.ReactNode;
  onRetry?: (message: ChatMessageType) => void;
  className?: string;
}

// ---------------------------------------------------------------------------
// 轻量 Markdown 渲染（零依赖）：标题 / 粗斜体 / 行内代码 / 代码块 / 列表 / 引用 / 链接
// ---------------------------------------------------------------------------

type Block =
  | { kind: 'code'; lang: string; code: string }
  | { kind: 'heading'; level: number; text: string }
  | { kind: 'quote'; lines: string[] }
  | { kind: 'list'; ordered: boolean; items: string[] }
  | { kind: 'paragraph'; text: string };

function parseBlocks(markdown: string): Block[] {
  const lines = markdown.split('\n');
  const blocks: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.startsWith('```')) {
      const lang = line.slice(3).trim();
      const code: string[] = [];
      i += 1;
      while (i < lines.length && !lines[i].startsWith('```')) {
        code.push(lines[i]);
        i += 1;
      }
      i += 1; // 跳过结束围栏
      blocks.push({ kind: 'code', lang, code: code.join('\n') });
      continue;
    }

    const headingMatch = /^(#{1,4})\s+(.*)$/.exec(line);
    if (headingMatch) {
      blocks.push({ kind: 'heading', level: headingMatch[1].length, text: headingMatch[2] });
      i += 1;
      continue;
    }

    if (/^>\s?/.test(line)) {
      const quoteLines: string[] = [];
      while (i < lines.length && /^>\s?/.test(lines[i])) {
        quoteLines.push(lines[i].replace(/^>\s?/, ''));
        i += 1;
      }
      blocks.push({ kind: 'quote', lines: quoteLines });
      continue;
    }

    if (/^\s*([-*+]|\d+\.)\s+/.test(line)) {
      const ordered = /^\s*\d+\./.test(line);
      const items: string[] = [];
      while (i < lines.length && /^\s*([-*+]|\d+\.)\s+/.test(lines[i])) {
        items.push(lines[i].replace(/^\s*([-*+]|\d+\.)\s+/, ''));
        i += 1;
      }
      blocks.push({ kind: 'list', ordered, items });
      continue;
    }

    if (line.trim() === '') {
      i += 1;
      continue;
    }

    const para: string[] = [line];
    i += 1;
    while (
      i < lines.length &&
      lines[i].trim() !== '' &&
      !/^(#{1,4}\s|>\s?|\s*([-*+]|\d+\.)\s+|```)/.test(lines[i])
    ) {
      para.push(lines[i]);
      i += 1;
    }
    blocks.push({ kind: 'paragraph', text: para.join('\n') });
  }

  return blocks;
}

function renderInline(text: string, keyPrefix: string): React.ReactNode[] {
  // 顺序：行内代码 > 链接 > 粗体 > 斜体
  const pattern = /(`[^`]+`)|(\[[^\]]*\]\([^)]*\))|(\*\*[^*]+\*\*)|(\*[^*\n]+\*)/g;
  const nodes: React.ReactNode[] = [];
  let lastIndex = 0;
  let match: RegExpExecArray | null;
  let key = 0;

  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      nodes.push(text.slice(lastIndex, match.index));
    }
    const [token] = match;
    if (token.startsWith('`')) {
      nodes.push(
        <code
          key={`${keyPrefix}-${key++}`}
          className="rounded bg-slate-200/70 px-1 py-0.5 font-mono text-[0.85em] text-pink-600 dark:bg-slate-700/70 dark:text-pink-300"
        >
          {token.slice(1, -1)}
        </code>,
      );
    } else if (token.startsWith('[')) {
      const linkMatch = /^\[([^\]]*)\]\(([^)]*)\)$/.exec(token);
      if (linkMatch) {
        nodes.push(
          <a
            key={`${keyPrefix}-${key++}`}
            href={linkMatch[2]}
            target="_blank"
            rel="noopener noreferrer"
            className="text-brand-600 underline decoration-brand-300 underline-offset-2 hover:text-brand-700 dark:text-brand-400"
          >
            {linkMatch[1] || linkMatch[2]}
          </a>,
        );
      } else {
        nodes.push(token);
      }
    } else if (token.startsWith('**')) {
      nodes.push(
        <strong key={`${keyPrefix}-${key++}`} className="font-semibold">
          {renderInline(token.slice(2, -2), `${keyPrefix}-b${key}`)}
        </strong>,
      );
    } else if (token.startsWith('*')) {
      nodes.push(<em key={`${keyPrefix}-${key++}`}>{token.slice(1, -1)}</em>);
    }
    lastIndex = match.index + token.length;
  }

  if (lastIndex < text.length) {
    nodes.push(text.slice(lastIndex));
  }
  return nodes;
}

export const MarkdownContent: React.FC<{ content: string; className?: string }> = ({
  content,
  className,
}) => {
  const blocks = useMemo(() => parseBlocks(content), [content]);

  return (
    <div className={cn('space-y-2 text-sm leading-relaxed', className)}>
      {blocks.map((block, idx) => {
        const key = `blk-${idx}`;
        switch (block.kind) {
          case 'code':
            return (
              <div key={key} className="overflow-hidden rounded-lg border border-slate-200 dark:border-slate-700">
                {block.lang && (
                  <div className="border-b border-slate-200 bg-slate-100 px-3 py-1 text-[11px] font-mono text-slate-500 dark:border-slate-700 dark:bg-slate-800 dark:text-slate-400">
                    {block.lang}
                  </div>
                )}
                <pre className="overflow-x-auto bg-slate-50 p-3 font-mono text-xs text-slate-800 dark:bg-slate-950 dark:text-slate-200">
                  <code>{block.code}</code>
                </pre>
              </div>
            );
          case 'heading': {
            const sizes = ['text-lg', 'text-base', 'text-sm', 'text-sm'];
            return (
              <div className={cn('font-semibold text-slate-900 dark:text-slate-100', sizes[block.level - 1])} key={key}>
                {renderInline(block.text, key)}
              </div>
            );
          }
          case 'quote':
            return (
              <blockquote
                key={key}
                className="border-l-2 border-slate-300 pl-3 text-slate-500 dark:border-slate-600 dark:text-slate-400"
              >
                {block.lines.map((l, i) => (
                  <p key={i}>{renderInline(l, `${key}-${i}`)}</p>
                ))}
              </blockquote>
            );
          case 'list':
            return block.ordered ? (
              <ol key={key} className="list-decimal space-y-1 pl-5">
                {block.items.map((item, i) => (
                  <li key={i}>{renderInline(item, `${key}-${i}`)}</li>
                ))}
              </ol>
            ) : (
              <ul key={key} className="list-disc space-y-1 pl-5">
                {block.items.map((item, i) => (
                  <li key={i}>{renderInline(item, `${key}-${i}`)}</li>
                ))}
              </ul>
            );
          default:
            return (
              <p key={key} className="whitespace-pre-wrap">
                {renderInline(block.text, key)}
              </p>
            );
        }
      })}
    </div>
  );
};

const ToolCallBadge: React.FC<{ toolCall: ToolCall }> = ({ toolCall }) => {
  const statusMeta = {
    pending: { label: '等待', cls: 'bg-slate-100 text-slate-500 dark:bg-slate-800' },
    running: { label: '执行中', cls: 'bg-blue-100 text-blue-600 dark:bg-blue-950 dark:text-blue-400' },
    success: { label: '成功', cls: 'bg-emerald-100 text-emerald-600 dark:bg-emerald-950 dark:text-emerald-400' },
    error: { label: '失败', cls: 'bg-red-100 text-red-600 dark:bg-red-950 dark:text-red-400' },
  }[toolCall.status];

  return (
    <div className="flex items-center gap-2 rounded-lg border border-slate-200 bg-slate-50 px-2.5 py-1.5 text-xs dark:border-slate-700 dark:bg-slate-900">
      <svg className="h-3.5 w-3.5 text-slate-400" viewBox="0 0 20 20" fill="currentColor">
        <path
          fillRule="evenodd"
          d="M11.983 1.904a1.354 1.354 0 00-.524.121l-6.375 2.95a1.354 1.354 0 00-.8 1.237v7.576c0 .493.268.948.7 1.187l6.375 3.188a1.354 1.354 0 001.282 0l6.375-3.188a1.354 1.354 0 00.7-1.187V6.212c0-.502-.275-.963-.719-1.204l-6.375-3.1a1.354 1.354 0 00-.599-.004z"
          clipRule="evenodd"
        />
      </svg>
      <span className="font-mono font-medium text-slate-700 dark:text-slate-300">{toolCall.name}</span>
      <span className={cn('rounded px-1.5 py-0.5 text-[10px]', statusMeta.cls)}>{statusMeta.label}</span>
      {toolCall.durationMs !== undefined && (
        <span className="ml-auto tabular-nums text-slate-400">{toolCall.durationMs}ms</span>
      )}
    </div>
  );
};

const TypingIndicator: React.FC = () => (
  <div className="flex items-center gap-1 px-1 py-2">
    {[0, 1, 2].map((i) => (
      <span
        key={i}
        className="h-1.5 w-1.5 animate-typing rounded-full bg-slate-400"
        style={{ animationDelay: `${i * 0.18}s` }}
      />
    ))}
  </div>
);

/**
 * ChatMessage - 单条聊天消息气泡，支持 Markdown、工具调用与流式状态。
 */
export const ChatMessage = memo<ChatMessageProps>(function ChatMessage({
  message,
  currentUserName = '我',
  showAvatar = true,
  showTimestamp = true,
  renderToolCall,
  onRetry,
  className,
}) {
  const isUser = message.role === 'user';
  const isSystem = message.role === 'system';
  const senderName = isUser ? currentUserName : message.agentName ?? 'AI 助手';

  if (isSystem) {
    return (
      <div className={cn('flex justify-center py-1', className)}>
        <span className="rounded-full bg-slate-100 px-3 py-1 text-xs text-slate-500 dark:bg-slate-800 dark:text-slate-400">
          {message.content}
        </span>
      </div>
    );
  }

  return (
    <div
      className={cn(
        'group flex gap-2.5 animate-slide-up',
        isUser && 'flex-row-reverse',
        className,
      )}
    >
      {showAvatar && (
        <div className="shrink-0">
          {message.agentAvatarUrl ? (
            <img
              src={message.agentAvatarUrl}
              alt={senderName}
              className="h-8 w-8 rounded-full object-cover"
            />
          ) : (
            <div
              className={cn(
                'flex h-8 w-8 items-center justify-center rounded-full text-xs font-semibold text-white',
                isUser ? 'bg-brand-500' : avatarColor(senderName),
              )}
            >
              {initials(senderName)}
            </div>
          )}
        </div>
      )}

      <div className={cn('flex max-w-[78%] min-w-0 flex-col gap-1', isUser && 'items-end')}>
        <div className={cn('flex items-baseline gap-2 px-1', isUser && 'flex-row-reverse')}>
          <span className="text-xs font-medium text-slate-500 dark:text-slate-400">{senderName}</span>
          {showTimestamp && (
            <span className="text-[10px] text-slate-400 dark:text-slate-500">
              {formatTime(message.timestamp)}
            </span>
          )}
        </div>

        <div
          className={cn(
            'rounded-2xl px-3.5 py-2.5 shadow-sm',
            isUser
              ? 'rounded-tr-sm bg-brand-600 text-white'
              : 'rounded-tl-sm border border-slate-200 bg-white text-slate-800 dark:border-slate-700 dark:bg-slate-900 dark:text-slate-100',
            message.status === 'error' && 'border-red-300 dark:border-red-800',
          )}
        >
          {message.status === 'streaming' && !message.content ? (
            <TypingIndicator />
          ) : (
            <div className={cn(isUser && '[&_code]:bg-white/20 [&_code]:text-white [&_a]:text-white')}>
              <MarkdownContent content={message.content} />
            </div>
          )}

          {message.status === 'streaming' && message.content && (
            <span className="ml-0.5 inline-block h-4 w-1.5 animate-pulse-soft rounded-sm bg-current align-text-bottom opacity-60" />
          )}
        </div>

        {message.toolCalls && message.toolCalls.length > 0 && (
          <div className="flex w-full flex-col gap-1">
            {message.toolCalls.map((tc) =>
              renderToolCall ? (
                <React.Fragment key={tc.id}>{renderToolCall(tc)}</React.Fragment>
              ) : (
                <ToolCallBadge key={tc.id} toolCall={tc} />
              ),
            )}
          </div>
        )}

        {message.attachments && message.attachments.length > 0 && (
          <div className="flex flex-wrap gap-1.5">
            {message.attachments.map((att) => (
              <span
                key={att.id}
                className="inline-flex items-center gap-1 rounded-md bg-slate-100 px-2 py-1 text-[11px] text-slate-600 dark:bg-slate-800 dark:text-slate-300"
              >
                <svg className="h-3 w-3" viewBox="0 0 20 20" fill="currentColor">
                  <path
                    fillRule="evenodd"
                    d="M15.621 4.379a3 3 0 00-4.242 0l-7 7a3 3 0 004.241 4.243h.001l.497-.5a.75.75 0 011.064 1.057l-.498.501-.002.002a4.5 4.5 0 01-6.364-6.364l7-7a4.5 4.5 0 016.368 6.36l-3.455 3.553A2.625 2.625 0 119.52 9.52l3.45-3.451a.75.75 0 111.061 1.06l-3.45 3.451a1.125 1.125 0 001.587 1.595l3.454-3.553a3 3 0 000-4.242z"
                    clipRule="evenodd"
                  />
                </svg>
                {att.name}
              </span>
            ))}
          </div>
        )}

        {message.status === 'error' && (
          <div className="flex items-center gap-2 px-1 text-xs text-red-500">
            <span>{message.error ?? '发送失败'}</span>
            {onRetry && (
              <button
                type="button"
                onClick={() => onRetry(message)}
                className="font-medium underline underline-offset-2 hover:text-red-600"
              >
                重试
              </button>
            )}
          </div>
        )}

        {message.tokenCount !== undefined && (
          <span className="px-1 text-[10px] tabular-nums text-slate-400 opacity-0 transition-opacity group-hover:opacity-100">
            {message.tokenCount} tokens
          </span>
        )}
      </div>
    </div>
  );
});
