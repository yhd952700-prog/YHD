import { memo, useCallback, useState } from 'react';
import type { SettingField, SettingSection } from '../../types';
import { cn } from '../../utils';

export interface SettingsPanelProps {
  sections: SettingSection[];
  onFieldChange?: (sectionId: string, field: SettingField, value: unknown) => void;
  onSave?: (section: SettingSection) => void;
  onReset?: (section: SettingSection) => void;
  saving?: boolean;
  className?: string;
}

interface FieldRendererProps {
  sectionId: string;
  field: SettingField;
  onChange?: (sectionId: string, field: SettingField, value: unknown) => void;
}

const inputBase =
  'w-full rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm text-slate-900 placeholder:text-slate-400 focus:border-brand-500 focus:outline-none focus:ring-1 focus:ring-brand-500 dark:border-slate-600 dark:bg-slate-800 dark:text-slate-100 dark:placeholder:text-slate-500';

const FieldRenderer = memo(function FieldRenderer({
  sectionId,
  field,
  onChange,
}: FieldRendererProps) {
  const [secretVisible, setSecretVisible] = useState(false);

  const emit = useCallback(
    (value: unknown) => {
      onChange?.(sectionId, field, value);
    },
    [onChange, sectionId, field],
  );

  const handleTextChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => {
      emit(event.target.value);
    },
    [emit],
  );

  const handleNumberChange = useCallback(
    (event: React.ChangeEvent<HTMLInputElement>) => {
      const next = event.target.value === '' ? '' : Number(event.target.value);
      emit(next);
    },
    [emit],
  );

  const handleBooleanChange = useCallback(() => {
    emit(!field.value);
  }, [emit, field.value]);

  const toggleSecret = useCallback(() => {
    setSecretVisible((prev) => !prev);
  }, []);

  const controlId = `setting-${sectionId}-${field.key}`;

  switch (field.type) {
    case 'boolean': {
      const checked = Boolean(field.value);
      return (
        <button
          id={controlId}
          type="button"
          role="switch"
          aria-checked={checked}
          onClick={handleBooleanChange}
          className={cn(
            'relative h-6 w-11 rounded-full transition-colors focus:outline-none focus-visible:ring-2 focus-visible:ring-brand-500',
            checked ? 'bg-brand-600' : 'bg-slate-300 dark:bg-slate-600',
          )}
        >
          <span
            aria-hidden
            className={cn(
              'absolute top-0.5 h-5 w-5 rounded-full bg-white shadow transition-transform',
              checked ? 'translate-x-[22px]' : 'translate-x-0.5',
            )}
          />
        </button>
      );
    }
    case 'number':
      return (
        <input
          id={controlId}
          type="number"
          className={cn(inputBase, 'max-w-[200px]')}
          value={field.value === undefined || field.value === null ? '' : String(field.value)}
          min={field.min}
          max={field.max}
          step={field.step}
          placeholder={field.placeholder}
          onChange={handleNumberChange}
        />
      );
    case 'select':
      return (
        <select
          id={controlId}
          className={cn(inputBase, 'max-w-[280px]')}
          value={String(field.value ?? '')}
          onChange={handleTextChange}
        >
          {field.placeholder && (
            <option value="" disabled>
              {field.placeholder}
            </option>
          )}
          {(field.options ?? []).map((option) => (
            <option key={option.value} value={option.value}>
              {option.label}
            </option>
          ))}
        </select>
      );
    case 'secret':
      return (
        <div className="flex max-w-[320px] items-center gap-2">
          <input
            id={controlId}
            type={secretVisible ? 'text' : 'password'}
            className={inputBase}
            value={String(field.value ?? '')}
            placeholder={field.placeholder ?? '请输入密钥'}
            autoComplete="off"
            onChange={handleTextChange}
          />
          <button
            type="button"
            onClick={toggleSecret}
            aria-label={secretVisible ? '隐藏密钥' : '显示密钥'}
            className="shrink-0 rounded-lg border border-slate-300 px-2 py-2 text-xs text-slate-500 transition-colors hover:bg-slate-100 dark:border-slate-600 dark:text-slate-400 dark:hover:bg-slate-700/60"
          >
            {secretVisible ? '🙈' : '👁'}
          </button>
        </div>
      );
    case 'string':
    default:
      return (
        <input
          id={controlId}
          type="text"
          className={cn(inputBase, 'max-w-[320px]')}
          value={String(field.value ?? '')}
          placeholder={field.placeholder}
          required={field.required}
          onChange={handleTextChange}
        />
      );
  }
});

interface SectionCardProps {
  section: SettingSection;
  onFieldChange?: (sectionId: string, field: SettingField, value: unknown) => void;
  onSave?: (section: SettingSection) => void;
  onReset?: (section: SettingSection) => void;
  saving?: boolean;
}

const SectionCard = memo(function SectionCard({
  section,
  onFieldChange,
  onSave,
  onReset,
  saving = false,
}: SectionCardProps) {
  const handleSave = useCallback(() => {
    onSave?.(section);
  }, [onSave, section]);

  const handleReset = useCallback(() => {
    onReset?.(section);
  }, [onReset, section]);

  return (
    <section
      id={`settings-section-${section.id}`}
      aria-labelledby={`settings-section-title-${section.id}`}
      className="scroll-mt-20 rounded-2xl border border-slate-200 bg-white p-5 dark:border-slate-700 dark:bg-slate-800/60"
    >
      <header className="mb-4">
        <h4
          id={`settings-section-title-${section.id}`}
          className="flex items-center gap-2 text-sm font-semibold text-slate-900 dark:text-slate-100"
        >
          {section.icon && <span aria-hidden>{section.icon}</span>}
          {section.title}
        </h4>
        {section.description && (
          <p className="mt-1 text-xs text-slate-500 dark:text-slate-400">
            {section.description}
          </p>
        )}
      </header>

      <dl className="divide-y divide-slate-100 dark:divide-slate-700/60">
        {section.fields.map((field) => (
          <div
            key={field.key}
            className="flex flex-col gap-2 py-3 first:pt-0 last:pb-0 sm:flex-row sm:items-center sm:justify-between"
          >
            <dt className="min-w-0">
              <label
                htmlFor={`setting-${section.id}-${field.key}`}
                className="text-sm font-medium text-slate-700 dark:text-slate-200"
              >
                {field.label}
                {field.required && (
                  <span className="ml-0.5 text-red-500" aria-hidden>
                    *
                  </span>
                )}
              </label>
              {field.description && (
                <p className="mt-0.5 text-xs text-slate-400 dark:text-slate-500">
                  {field.description}
                </p>
              )}
            </dt>
            <dd className="shrink-0">
              <FieldRenderer
                sectionId={section.id}
                field={field}
                onChange={onFieldChange}
              />
            </dd>
          </div>
        ))}
      </dl>

      {(onSave || onReset) && (
        <footer className="mt-4 flex items-center justify-end gap-2 border-t border-slate-100 pt-3 dark:border-slate-700/60">
          {onReset && (
            <button
              type="button"
              onClick={handleReset}
              className="rounded-lg px-3 py-1.5 text-xs font-medium text-slate-500 transition-colors hover:bg-slate-100 dark:text-slate-400 dark:hover:bg-slate-700/60"
            >
              恢复默认
            </button>
          )}
          {onSave && (
            <button
              type="button"
              onClick={handleSave}
              disabled={saving}
              className="lh-btn-primary rounded-lg px-4 py-1.5 text-xs font-medium disabled:cursor-not-allowed disabled:opacity-60"
            >
              {saving ? '保存中…' : '保存'}
            </button>
          )}
        </footer>
      )}
    </section>
  );
});

export const SettingsPanel = memo(function SettingsPanel({
  sections,
  onFieldChange,
  onSave,
  onReset,
  saving = false,
  className,
}: SettingsPanelProps) {
  const [activeSection, setActiveSection] = useState<string | undefined>(
    sections[0]?.id,
  );

  const handleNavClick = useCallback(
    (sectionId: string) => (event: React.MouseEvent<HTMLAnchorElement>) => {
      event.preventDefault();
      setActiveSection(sectionId);
      document
        .getElementById(`settings-section-${sectionId}`)
        ?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    },
    [],
  );

  return (
    <div className={cn('flex flex-col gap-6 lg:flex-row', className)}>
      {/* 锚点导航 */}
      <nav
        aria-label="设置分组"
        className="shrink-0 lg:sticky lg:top-4 lg:w-52 lg:self-start"
      >
        <ul className="flex gap-1 overflow-x-auto rounded-xl border border-slate-200 bg-white p-2 dark:border-slate-700 dark:bg-slate-800/60 lg:flex-col lg:overflow-visible">
          {sections.map((section) => (
            <li key={section.id}>
              <a
                href={`#settings-section-${section.id}`}
                onClick={handleNavClick(section.id)}
                aria-current={activeSection === section.id ? 'true' : undefined}
                className={cn(
                  'flex items-center gap-2 whitespace-nowrap rounded-lg px-3 py-2 text-sm transition-colors',
                  activeSection === section.id
                    ? 'bg-brand-50 font-medium text-brand-700 dark:bg-brand-500/15 dark:text-brand-300'
                    : 'text-slate-600 hover:bg-slate-100 dark:text-slate-300 dark:hover:bg-slate-700/60',
                )}
              >
                {section.icon && <span aria-hidden>{section.icon}</span>}
                {section.title}
              </a>
            </li>
          ))}
        </ul>
      </nav>

      {/* 分组内容 */}
      <div className="min-w-0 flex-1 space-y-5">
        {sections.map((section) => (
          <SectionCard
            key={section.id}
            section={section}
            onFieldChange={onFieldChange}
            onSave={onSave}
            onReset={onReset}
            saving={saving}
          />
        ))}
      </div>
    </div>
  );
});

export default SettingsPanel;
