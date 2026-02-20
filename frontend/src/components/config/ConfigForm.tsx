import { useState, useCallback } from 'react';
import { Loader2, X, Plus } from 'lucide-react';
import { cn } from '@/lib/utils';

export interface ConfigField {
  key: string;
  label: string;
  type: 'text' | 'number' | 'password' | 'toggle' | 'select' | 'tags';
  options?: string[];
  description?: string;
}

interface ConfigFormProps {
  title: string;
  fields: ConfigField[];
  values: Record<string, any>;
  onSave: (values: Record<string, any>) => void;
  saving?: boolean;
}

const inputClasses = cn(
  'h-9 w-full rounded-md border border-input bg-background px-3 text-sm',
  'text-foreground placeholder:text-muted-foreground',
  'focus:outline-none focus:ring-1 focus:ring-ring',
);

const selectClasses = cn(
  'h-9 w-full rounded-md border border-input bg-background px-3 text-sm',
  'text-foreground focus:outline-none focus:ring-1 focus:ring-ring',
);

function TagsInput({
  value,
  onChange,
}: {
  value: string[];
  onChange: (tags: string[]) => void;
}) {
  const [draft, setDraft] = useState('');

  function addTag() {
    const trimmed = draft.trim();
    if (trimmed && !value.includes(trimmed)) {
      onChange([...value, trimmed]);
    }
    setDraft('');
  }

  function removeTag(tag: string) {
    onChange(value.filter((t) => t !== tag));
  }

  return (
    <div className="space-y-2">
      <div className="flex flex-wrap gap-1.5">
        {value.map((tag) => (
          <span
            key={tag}
            className={cn(
              'inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs',
              'bg-secondary text-secondary-foreground',
            )}
          >
            {tag}
            <button
              type="button"
              onClick={() => removeTag(tag)}
              className="rounded hover:bg-accent p-0.5"
            >
              <X className="h-3 w-3" />
            </button>
          </span>
        ))}
      </div>
      <div className="flex gap-2">
        <input
          type="text"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter') {
              e.preventDefault();
              addTag();
            }
          }}
          placeholder="Add tag..."
          className={inputClasses}
        />
        <button
          type="button"
          onClick={addTag}
          disabled={!draft.trim()}
          className={cn(
            'flex h-9 shrink-0 items-center gap-1 rounded-md px-3 text-sm',
            'bg-secondary text-secondary-foreground',
            'hover:bg-secondary/80 transition-colors',
            'disabled:opacity-50 disabled:pointer-events-none',
          )}
        >
          <Plus className="h-3.5 w-3.5" />
          Add
        </button>
      </div>
    </div>
  );
}

export function ConfigForm({ title, fields, values, onSave, saving }: ConfigFormProps) {
  const [local, setLocal] = useState<Record<string, any>>(() => ({ ...values }));

  const set = useCallback((key: string, val: any) => {
    setLocal((prev) => ({ ...prev, [key]: val }));
  }, []);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    onSave(local);
  }

  return (
    <form
      onSubmit={handleSubmit}
      className="rounded-lg border border-border bg-card p-5"
    >
      <h3 className="mb-4 text-lg font-semibold text-foreground">{title}</h3>

      <div className="space-y-4">
        {fields.map((field) => (
          <div key={field.key} className="space-y-1.5">
            <label
              htmlFor={field.key}
              className="block text-sm font-medium text-foreground"
            >
              {field.label}
            </label>

            {field.type === 'toggle' ? (
              <button
                id={field.key}
                type="button"
                role="switch"
                aria-checked={!!local[field.key]}
                onClick={() => set(field.key, !local[field.key])}
                className={cn(
                  'relative inline-flex h-6 w-11 shrink-0 cursor-pointer rounded-full',
                  'transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring',
                  local[field.key] ? 'bg-primary' : 'bg-input',
                )}
              >
                <span
                  className={cn(
                    'pointer-events-none block h-5 w-5 rounded-full bg-background shadow-lg ring-0',
                    'transition-transform mt-0.5',
                    local[field.key] ? 'translate-x-[22px]' : 'translate-x-0.5',
                  )}
                />
              </button>
            ) : field.type === 'select' ? (
              <select
                id={field.key}
                value={local[field.key] ?? ''}
                onChange={(e) => set(field.key, e.target.value)}
                className={selectClasses}
              >
                <option value="">-- Select --</option>
                {field.options?.map((opt) => (
                  <option key={opt} value={opt}>
                    {opt}
                  </option>
                ))}
              </select>
            ) : field.type === 'tags' ? (
              <TagsInput
                value={Array.isArray(local[field.key]) ? local[field.key] : []}
                onChange={(tags) => set(field.key, tags)}
              />
            ) : (
              <input
                id={field.key}
                type={field.type}
                value={local[field.key] ?? ''}
                onChange={(e) =>
                  set(
                    field.key,
                    field.type === 'number' ? Number(e.target.value) : e.target.value,
                  )
                }
                className={inputClasses}
              />
            )}

            {field.description && (
              <p className="text-xs text-muted-foreground">{field.description}</p>
            )}
          </div>
        ))}
      </div>

      <div className="mt-5 flex justify-end">
        <button
          type="submit"
          disabled={saving}
          className={cn(
            'flex items-center gap-2 rounded-md px-4 py-2 text-sm font-medium',
            'bg-primary text-primary-foreground',
            'hover:bg-primary/90 transition-colors',
            'disabled:opacity-50 disabled:pointer-events-none',
          )}
        >
          {saving && <Loader2 className="h-4 w-4 animate-spin" />}
          Save
        </button>
      </div>
    </form>
  );
}
