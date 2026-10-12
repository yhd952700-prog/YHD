import React, {
  forwardRef,
  memo,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
} from 'react';
import type { Agent, ChatMessage as ChatMessageType, ToolCall } from '../../types';
import { ChatMessage } from '../ChatMessage';
import { cn, genId } from '../../utils';

export interface ChatWindowHandle {
  /** 滚动到底部 */
  scrollToBottom: (smooth?: boolean) => void;
  /** 聚焦输入框 */
  focusInput: () => void;
}

export interface ChatWindowProps {
  messages: ChatMessageType[];
  /** 当前对话的 Agent（头部展示） */
  agent?: Agent | null;
  title?: string;
  /** 正在等待 AI 回复 */
  waiting?: boolean;
  /** 占位提示 */
  placeholder?: string;
  /** 最大输入行数 */
  maxRows?: number;
  currentUserName?: string;
  /** 是否禁用发送 */
  disabled?: boolean;
  headerActions?: React.ReactNode;
  renderToolCall?: (toolCall: ToolCall) => React.ReactNode;
  renderEmpty?: () => React.ReactNode;
  onSend?: (content: string) => void;
  onRetryMessage?: (message: ChatMessageType) => void;
  onStop?: () => void;
  onAttach?: () => void;
  className?: string;
}

const DEFAULT_PLACEHOLDER = '输入消息，Enter 发送，Shift+Enter 换行…';

/**
 * ChatWindow - 完整聊天窗口：消息列表 + 自动滚动 + 自适应输入框。
 */
export const ChatWindow = memo(
  forwardRef<ChatWindowHandle, ChatWindowProps>(function ChatWindow(
    {
      messages,
      agent,
      title,
      waiting = false,
      placeholder = DEFAULT_PLACEHOLDER,
      maxRows = 6,
      currentUserName,
      disabled = false,
      headerActions,
      renderToolCall,
      renderEmpty,
      onSend,
      onRetryMessage,
      onStop,
      onAttach,
      className,
    },
    ref,
  ) {
    const [draft, setDraft] = useState('');
    const [stickToBottom, setStickToBottom] = useState(true);
    const scrollRef = useRef<HTMLDivElement>(null);
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    const scrollToBottom = useCallback((smooth = true) => {
      const el = scrollRef.current;
      if (!el) return;
      el.scrollTo({ top: el.scrollHeight, behavior: smooth ? 'smooth' : 'auto' });
    }, []);

    useImperativeHandle(ref, () => ({
      scrollToBottom,
      focusInput: () => textareaRef.current?.focus(),
    }));

    // 新消息到达时，如果用户停留在底部则自动滚动
    useEffect(() => {
      if (stickToBottom) scrollToBottom(false);
    }, [messages, stickToBottom, scrollToBottom]);

    const handleScroll = useCallback(() => {
      const el = scrollRef.current;
      if (!el) return;
      const distance = el.scrollHeight - el.scrollTop - el.clientHeight;
      setStickToBottom(distance < 80);
    }, []);

    // 自适应输入框高度
    useEffect(() => {
      const ta = textareaRef.current;
      if (!ta) return;
      ta.style.height = 'auto';
      const lineHeight = 22;
      const maxHeight = lineHeight * maxRows + 16;
      ta.style.height = `${Math.min(ta.scrollHeight, maxHeight)}px`;
    }, [draft, maxRows]);

    const handleSend = useCallback(() => {
      const content = draft.trim();
      if (!content || disabled) return;
      onSend?.(content);
      setDraft('');
      setStickToBottom(true);
    }, [draft, disabled, onSend]);

    const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
        e.preventDefault();
        handleSend();
      }
    };

    // 演示用 id 生成器（避免 lint 未使用警告并展示 genId 用法）
    const _listId = useRef(genId('chat')).current;
    void _listId;

    return (
      <div className={cn('flex h-full flex-col overflow-hidden rounded-xl border border-slate-200 bg-white dark:border-slate-800 dark:bg-slate-900', className)}>
        {/* 头部 */}
        <header className="flex items-center gap-3 border-b border-slate-200 px-4 py-3 dark:border-slate-800">
          {agent && (
            <div className="relative shrink-0">
              {agent.avatarUrl ? (
                <img src={agent.avatarUrl} alt={agent.name} className="h-9 w-9 rounded-full object-cover" />
              ) : (
                <div className="flex h-9 w-9 items-center justify-center rounded-full bg-brand-100 text-sm font-semibold text-brand-600 dark:bg-brand-950 dark:text-brand-300">
                  {agent.name.slice(0, 2)}
                </div>
              )}
              <span
                className={cn(
                  'absolute -bottom-0.5 -right-0.5 h-2.5 w-2.5 rounded-full ring-2 ring-white dark:ring-slate-900',
                  agent.status === 'online'
                    ? 'bg-green-500'
                    : agent.status === 'busy'
                      ? 'bg-amber-500'
                      : agent.status === 'error'
                        ? 'bg-red-500'
                        : 'bg-slate-400',
                )}
              />
            </div>
          )}
          <div className="min-w-0 flex-1">
            <h2 className="truncate text-sm font-semibold text-slate-900 dark:text-slate-100">
              {title ?? agent?.name ?? '对话'}
            </h2>
            {agent && (
              <p className="truncate text-xs text-slate-400">
                {agent.model ?? agent.description ?? 'AI Agent'}
              </p>
            )}
          </div>
          {headerActions}
        </header>

        {/* 消息区 */}
        <div
          ref={scrollRef}
          onScroll={handleScroll}
          className="flex-1 overflow-y-auto px-4 py-4"
          role="log"
          aria-live="polite"
        >
          {messages.length === 0 ? (
            <div className="flex h-full flex-col items-center justify-center gap-3 text-slate-400">
              {renderEmpty ? (
                renderEmpty()
              ) : (
                <>
                  <svg className="h-12 w-12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.2">
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      d="M8.625 12a.375.375 0 11-.75 0 .375.375 0 01.75 0zm3.75 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zm3.75 0a.375.375 0 11-.75 0 .375.375 0 01.75 0zM21 12c0 4.556-4.03 8.25-9 8.25a9.764 9.764 0 01-2.555-.337A5.972 5.972 0 015.41 20.97a5.969 5.969 0 01-.474-.065 4.48 4.48 0 00.978-2.025c.09-.457-.133-.901-.467-1.226C3.93 16.178 3 14.189 3 12c0-4.556 4.03-8.25 9-8.25s9 3.694 9 8.25z"
                    />
                  </svg>
                  <p className="text-sm">开始与 {agent?.name ?? 'AI'} 对话</p>
                </>
              )}
            </div>
          ) : (
            <div className="flex flex-col gap-4">
              {messages.map((message) => (
                <ChatMessage
                  key={message.id}
                  message={message}
                  currentUserName={currentUserName}
                  renderToolCall={renderToolCall}
                  onRetry={onRetryMessage}
                />
              ))}

              {waiting && (
                <div className="flex items-center gap-2 text-xs text-slate-400">
                  <span className="h-1.5 w-1.5 animate-pulse-soft rounded-full bg-brand-500" />
                  {agent?.name ?? 'AI'} 正在思考…
                  {onStop && (
                    <button
                      type="button"
                      onClick={onStop}
                      className="rounded border border-slate-300 px-1.5 py-0.5 text-[10px] hover:bg-slate-100 dark:border-slate-700 dark:hover:bg-slate-800"
                    >
                      停止生成
                    </button>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* 回到底部按钮 */}
        {!stickToBottom && (
          <button
            type="button"
            onClick={() => scrollToBottom()}
            className="absolute bottom-24 right-6 rounded-full border border-slate-200 bg-white p-2 shadow-card-hover transition hover:bg-slate-50 dark:border-slate-700 dark:bg-slate-800"
            aria-label="回到底部"
          >
            <svg className="h-4 w-4 text-slate-500" viewBox="0 0 20 20" fill="currentColor">
              <path
                fillRule="evenodd"
                d="M10 3a.75.75 0 01.75.75v10.638l3.96-4.158a.75.75 0 111.08 1.04l-5.25 5.5a.75.75 0 01-1.08 0l-5.25-5.5a.75.75 0 111.08-1.04l3.96 4.158V3.75A.75.75 0 0110 3z"
                clipRule="evenodd"
              />
            </svg>
          </button>
        )}

        {/* 输入区 */}
        <footer className="border-t border-slate-200 p-3 dark:border-slate-800">
          <div className="flex items-end gap-2">
            {onAttach && (
              <button
                type="button"
                onClick={onAttach}
                className="lh-btn-ghost shrink-0 px-2"
                aria-label="添加附件"
              >
                <svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                  <path
                    fillRule="evenodd"
                    d="M15.621 4.379a3 3 0 00-4.242 0l-7 7a3 3 0 004.241 4.243h.001l.497-.5a.75.75 0 011.064 1.057l-.498.501-.002.002a4.5 4.5 0 01-6.364-6.364l7-7a4.5 4.5 0 016.368 6.36l-3.455 3.553A2.625 2.625 0 119.52 9.52l3.45-3.451a.75.75 0 111.061 1.06l-3.45 3.451a1.125 1.125 0 001.587 1.595l3.454-3.553a3 3 0 000-4.242z"
                    clipRule="evenodd"
                  />
                </svg>
              </button>
            )}
            <textarea
              ref={textareaRef}
              value={draft}
              onChange={(e) => setDraft(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder={placeholder}
              rows={1}
              disabled={disabled}
              className="lh-input max-h-40 flex-1 resize-none py-2.5 leading-[22px]"
            />
            <button
              type="button"
              onClick={handleSend}
              disabled={disabled || !draft.trim()}
              className="lh-btn-primary shrink-0 px-3.5"
              aria-label="发送"
            >
              <svg className="h-4 w-4" viewBox="0 0 20 20" fill="currentColor">
                <path d="M3.105 2.289a.75.75 0 00-.826.95l1.414 4.925A1.5 1.5 0 005.135 9.25h6.115a.75.75 0 010 1.5H5.135a1.5 1.5 0 00-1.442 1.086l-1.414 4.926a.75.75 0 00.826.95 28.896 28.896 0 0015.293-7.154.75.75 0 000-1.115A28.897 28.897 0 003.105 2.289z" />
              </svg>
            </button>
          </div>
          <p className="mt-1.5 px-1 text-[10px] text-slate-400">
            Enter 发送 · Shift+Enter 换行
          </p>
        </footer>
      </div>
    );
  }),
);

ChatWindow.displayName = 'ChatWindow';
