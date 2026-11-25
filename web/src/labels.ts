// Display names for ids (e.g. subscription GUID -> name), remembered from API responses so filter chips can
// show a name even when the list they were picked from is no longer on screen. Labels never affect figures.
const cache = new Map<string, string>();

export function rememberLabel(value: string | null, label: string | null): void {
  if (value && label) cache.set(value, label);
}

export function labelFor(value: string): string {
  return cache.get(value) ?? value;
}
