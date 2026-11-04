// Small DOM helpers; no framework, the views build their elements directly.

type Child = Node | string | null | undefined | false;

export function h<K extends keyof HTMLElementTagNameMap>(
  tag: K,
  attrs: Record<string, unknown> = {},
  ...children: Child[]
): HTMLElementTagNameMap[K] {
  const el = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs)) {
    if (value === null || value === undefined || value === false) continue;
    if (key === "class") el.className = String(value);
    else if (key === "style" && typeof value === "object") Object.assign(el.style, value as Record<string, string>);
    else if (key.startsWith("on") && typeof value === "function") {
      el.addEventListener(key.slice(2).toLowerCase(), value as EventListener);
    } else if (key === "value") (el as HTMLInputElement).value = String(value);
    else if (key === "checked") (el as HTMLInputElement).checked = Boolean(value);
    else if (key === "disabled") (el as HTMLButtonElement).disabled = Boolean(value);
    else if (key === "html") el.innerHTML = String(value);
    else el.setAttribute(key, String(value));
  }
  for (const child of children) {
    if (child === null || child === undefined || child === false) continue;
    el.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return el;
}

export function clear(el: HTMLElement): void {
  el.replaceChildren();
}

export function fmtClock(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  return `${m}:${String(s % 60).padStart(2, "0")}`;
}

export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || !Number.isFinite(seconds)) return "";
  if (seconds < 90) return `${seconds.toFixed(0)} s`;
  if (seconds < 5400) return `${(seconds / 60).toFixed(1)} min`;
  return `${(seconds / 3600).toFixed(1)} h`;
}

export function fmtNum(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return "";
  return value.toFixed(digits);
}

export function field(label: string, input: HTMLElement, hint?: string): HTMLElement {
  return h("label", { class: "field" }, h("span", { class: "field-label" }, label), input, hint ? h("span", { class: "hint" }, hint) : null);
}

interface NumberOpts {
  min?: number;
  max?: number;
  step?: number;
  width?: string;
}

export function numberInput(value: number, onChange: (v: number) => void, opts: NumberOpts = {}): HTMLInputElement {
  const input = h("input", {
    type: "number",
    value,
    min: opts.min,
    max: opts.max,
    step: opts.step ?? "any",
    style: opts.width ? { width: opts.width } : undefined,
  });
  input.addEventListener("change", () => {
    const v = Number(input.value);
    if (Number.isFinite(v)) onChange(v);
  });
  return input;
}

export function textInput(
  value: string,
  onChange: (v: string) => void,
  opts: { placeholder?: string; type?: string; width?: string } = {},
): HTMLInputElement {
  const input = h("input", {
    type: opts.type ?? "text",
    value,
    placeholder: opts.placeholder,
    style: opts.width ? { width: opts.width } : undefined,
  });
  input.addEventListener("change", () => onChange(input.value));
  return input;
}

export function select<T extends string>(options: [T, string][], value: T, onChange: (v: T) => void): HTMLSelectElement {
  const el = h("select", {}, ...options.map(([v, label]) => h("option", { value: v, selected: v === value ? "selected" : null }, label)));
  el.value = value;
  el.addEventListener("change", () => onChange(el.value as T));
  return el;
}

export function checkbox(label: string, checked: boolean, onChange: (v: boolean) => void): HTMLElement {
  const input = h("input", { type: "checkbox", checked });
  input.addEventListener("change", () => onChange(input.checked));
  return h("label", { class: "check" }, input, h("span", {}, label));
}

export function button(label: string, onClick: () => void, cls = "btn"): HTMLButtonElement {
  return h("button", { class: cls, type: "button", onclick: onClick }, label);
}

export function notice(kind: "info" | "error" | "ok" | "warn", text: string): HTMLElement {
  return h("div", { class: `notice ${kind}` }, text);
}

export function pill(text: string, kind = ""): HTMLElement {
  return h("span", { class: `pill ${kind}` }, text);
}

export function table(headers: string[], rows: (Node | string)[][], cls = "table"): HTMLTableElement {
  return h(
    "table",
    { class: cls },
    h("thead", {}, h("tr", {}, ...headers.map((t) => h("th", {}, t)))),
    h("tbody", {}, ...rows.map((cells) => h("tr", {}, ...cells.map((c) => h("td", {}, c))))),
  );
}

export function download(name: string, text: string, type = "application/json"): void {
  const blob = new Blob([text], { type });
  const url = URL.createObjectURL(blob);
  const a = h("a", { href: url, download: name });
  document.body.append(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export function debounce<A extends unknown[]>(fn: (...args: A) => void, ms: number): (...args: A) => void {
  let timer: number | undefined;
  return (...args: A) => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => fn(...args), ms);
  };
}
