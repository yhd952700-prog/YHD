import {
  memo,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
} from 'react';
import { cn } from '../../utils';

export type CodeLanguage = 'javascript' | 'typescript' | 'json' | 'python' | 'plain';

export interface CodeEditorProps {
  value: string;
  language?: CodeLanguage;
  readOnly?: boolean;
  showLineNumbers?: boolean;
  placeholder?: string;
  tabSize?: number;
  maxHeight?: string;
  onChange?: (value: string) => void;
  className?: string;
}

// ---------------------------------------------------------------------------
// 内置轻量 Tokenizer（关键字 / 字符串 / 注释 / 数字 / 运算符）
// ---------------------------------------------------------------------------

type TokenType =
  | 'keyword'
  | 'string'
  | 'comment'
  | 'number'
  | 'operator'
  | 'plain';

interface Token {
  type: TokenType;
  value: string;
}

const KEYWORDS: Record<string, Set<string>> = {
  javascript: new Set([
    'const', 'let', 'var', 'function', 'return', 'if', 'else', 'for', 'while',
    'do', 'switch', 'case', 'break', 'continue', 'new', 'delete', 'typeof',
    'instanceof', 'in', 'of', 'class', 'extends', 'super', 'this', 'import',
    'export', 'from', 'default', 'try', 'catch', 'finally', 'throw', 'async',
    'await', 'yield', 'static', 'get', 'set', 'null', 'undefined', 'true',
    'false', 'void',
  ]),
  typescript: new Set([
    'const', 'let', 'var', 'function', 'return', 'if', 'else', 'for', 'while',
    'do', 'switch', 'case', 'break', 'continue', 'new', 'delete', 'typeof',
    'instanceof', 'in', 'of', 'class', 'extends', 'super', 'this', 'import',
    'export', 'from', 'default', 'try', 'catch', 'finally', 'throw', 'async',
    'await', 'yield', 'static', 'get', 'set', 'null', 'undefined', 'true',
    'false', 'void', 'interface', 'type', 'enum', 'namespace', 'implements',
    'public', 'private', 'protected', 'readonly', 'abstract', 'declare', 'as',
    'keyof', 'never', 'unknown', 'any', 'string', 'number', 'boolean',
  ]),
  json: new Set(['true', 'false', 'null']),
  python: new Set([
    'def', 'return', 'if', 'elif', 'else', 'for', 'while', 'break', 'continue',
    'class', 'import', 'from', 'as', 'try', 'except', 'finally', 'raise',
    'with', 'lambda', 'yield', 'pass', 'global', 'nonlocal', 'assert', 'del',
    'in', 'is', 'not', 'and', 'or', 'None', 'True', 'False', 'async', 'await',
    'print', 'self',
  ]),
  plain: new Set(),
};

const LINE_COMMENT: Record<string, string | undefined> = {
  javascript: '//',
  typescript: '//',
  json: undefined,
  python: '#',
  plain: undefined,
};

const OPERATOR_CHARS = new Set('+-*/%=<>!&|^~?:.,;()[]{}'.split(''));

function tokenizeLine(line: string, language: CodeLanguage): Token[] {
  const tokens: Token[] = [];
  const keywords = KEYWORDS[language] ?? KEYWORDS.plain;
  const lineComment = LINE_COMMENT[language];

  let index = 0;
  let buffer = '';
  let bufferType: TokenType = 'plain';

  const flush = () => {
    if (!buffer) return;
    tokens.push({ type: bufferType, value: buffer });
    buffer = '';
    bufferType = 'plain';
  };

  while (index < line.length) {
    const char = line[index];
    const rest = line.slice(index);

    // 行注释
    if (lineComment && rest.startsWith(lineComment)) {
      flush();
      tokens.push({ type: 'comment', value: rest });
      return tokens;
    }
    // 块注释开始（JS/TS 单行内处理）
    if (
      (language === 'javascript' || language === 'typescript') &&
      rest.startsWith('/*')
    ) {
      flush();
      const end = line.indexOf('*/', index + 2);
      const value = end === -1 ? rest : line.slice(index, end + 2);
      tokens.push({ type: 'comment', value });
      index += value.length;
      continue;
    }
    // 字符串
    if (char === '"' || char === "'" || char === '`') {
      flush();
      let end = index + 1;
      while (end < line.length) {
        if (line[end] === '\\') {
          end += 2;
          continue;
        }
        if (line[end] === char) {
          end += 1;
          break;
        }
        end += 1;
      }
      tokens.push({ type: 'string', value: line.slice(index, end) });
      index = end;
      continue;
    }
    // 数字
    if (/\d/.test(char) && bufferType !== 'plain') {
      // 数字不能紧跟标识符
    }
    if (/\d/.test(char) && !/^[A-Za-z_$][\w$]*$/.test(buffer)) {
      flush();
      let end = index;
      while (end < line.length && /[\d._a-fA-FxXbBoO]/.test(line[end])) {
        end += 1;
      }
      tokens.push({ type: 'number', value: line.slice(index, end) });
      index = end;
      continue;
    }
    // 标识符 / 关键字
    if (/[A-Za-z_$]/.test(char)) {
      let end = index;
      while (end < line.length && /[\w$]/.test(line[end])) {
        end += 1;
      }
      const word = line.slice(index, end);
      flush();
      tokens.push({
        type: keywords.has(word) ? 'keyword' : 'plain',
        value: word,
      });
      index = end;
      continue;
    }
    // 运算符
    if (OPERATOR_CHARS.has(char)) {
      flush();
      tokens.push({ type: 'operator', value: char });
      index += 1;
      continue;
    }
    // 空白与其它
    if (bufferType !== 'plain') flush();
    buffer += char;
    bufferType = 'plain';
    index += 1;
  }
  flush();
  return tokens;
}

const TOKEN_CLASS: Record<TokenType, string> = {
  keyword: 'text-fuchsia-600 dark:text-fuchsia-400',
  string: 'text-emerald-600 dark:text-emerald-400',
  comment: 'italic text-slate-400 dark:text-slate-500',
  number: 'text-amber-600 dark:text-amber-400',
  operator: 'text-sky-600 dark:text-sky-400',
  plain: 'text-slate-800 dark:text-slate-200',
};

// ---------------------------------------------------------------------------
// 组件
// ---------------------------------------------------------------------------

const EDITOR_FONT =
  'font-mono text-[13px] leading-6 [tab-size:2] whitespace-pre';

export const CodeEditor = memo(function CodeEditor({
  value,
  language = 'plain',
  readOnly = false,
  showLineNumbers = true,
  placeholder = '// 在此输入代码…',
  tabSize = 2,
  maxHeight = '480px',
  onChange,
  className,
}: CodeEditorProps) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const highlightRef = useRef<HTMLPreElement>(null);
  const gutterRef = useRef<HTMLDivElement>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [scrollLeft, setScrollLeft] = useState(0);

  const lines = useMemo(() => value.split('\n'), [value]);

  const tokenized = useMemo(
    () => lines.map((line) => tokenizeLine(line, language)),
    [lines, language],
  );

  const handleChange = useCallback(
    (event: React.ChangeEvent<HTMLTextAreaElement>) => {
      onChange?.(event.target.value);
    },
    [onChange],
  );

  const handleScroll = useCallback(
    (event: React.UIEvent<HTMLTextAreaElement>) => {
      setScrollTop(event.currentTarget.scrollTop);
      setScrollLeft(event.currentTarget.scrollLeft);
    },
    [],
  );

  // 同步高亮层与行号层滚动
  useEffect(() => {
    if (highlightRef.current) {
      highlightRef.current.scrollTop = scrollTop;
      highlightRef.current.scrollLeft = scrollLeft;
    }
    if (gutterRef.current) {
      gutterRef.current.scrollTop = scrollTop;
    }
  }, [scrollTop, scrollLeft]);

  // Tab 键插入空格
  const handleKeyDown = useCallback(
    (event: React.KeyboardEvent<HTMLTextAreaElement>) => {
      if (event.key !== 'Tab' || readOnly) return;
      event.preventDefault();
      const textarea = event.currentTarget;
      const { selectionStart, selectionEnd } = textarea;
      const indent = ' '.repeat(tabSize);
      const next = `${value.slice(0, selectionStart)}${indent}${value.slice(selectionEnd)}`;
      onChange?.(next);
      requestAnimationFrame(() => {
        textarea.selectionStart = textarea.selectionEnd =
          selectionStart + indent.length;
      });
    },
    [readOnly, tabSize, value, onChange],
  );

  return (
    <div
      className={cn(
        'flex overflow-hidden rounded-xl border border-slate-200 bg-slate-50 dark:border-slate-700 dark:bg-slate-900',
        className,
      )}
      style={{ maxHeight }}
    >
      {/* 行号 */}
      {showLineNumbers && (
        <div
          ref={gutterRef}
          aria-hidden
          className="shrink-0 select-none overflow-hidden border-r border-slate-200 bg-slate-100/70 py-3 pl-3 pr-2 text-right dark:border-slate-700 dark:bg-slate-800/60"
        >
          <div className={cn(EDITOR_FONT, 'text-slate-400 dark:text-slate-500')}>
            {lines.map((_, index) => (
              <div key={index}>{index + 1}</div>
            ))}
          </div>
        </div>
      )}

      {/* 编辑区：高亮层 + 透明 textarea */}
      <div className="relative min-w-0 flex-1">
        <pre
          ref={highlightRef}
          aria-hidden
          className={cn(
            'pointer-events-none absolute inset-0 overflow-auto py-3',
            EDITOR_FONT,
          )}
        >
          <code>
            {tokenized.map((tokens, lineIndex) => (
              <span key={lineIndex} className="block min-h-6">
                {tokens.length === 0
                  ? ' '
                  : tokens.map((token, tokenIndex) => (
                      <span
                        key={tokenIndex}
                        className={TOKEN_CLASS[token.type]}
                      >
                        {token.value}
                      </span>
                    ))}
                {'\n'}
              </span>
            ))}
          </code>
        </pre>
        <textarea
          ref={textareaRef}
          value={value}
          onChange={handleChange}
          onScroll={handleScroll}
          onKeyDown={handleKeyDown}
          readOnly={readOnly}
          placeholder={placeholder}
          spellCheck={false}
          aria-label={readOnly ? '代码（只读）' : '代码编辑器'}
          className={cn(
            'relative block h-full w-full resize-none overflow-auto bg-transparent py-3 outline-none',
            EDITOR_FONT,
            'text-transparent caret-brand-600 selection:bg-brand-500/20 placeholder:text-slate-400 dark:caret-brand-400 dark:placeholder:text-slate-600',
            readOnly && 'cursor-default',
          )}
          style={{ height: '100%', minHeight: '160px' }}
        />
      </div>
    </div>
  );
});

export default CodeEditor;
