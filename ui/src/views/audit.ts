// Audit view: does this recording contain location clues? The first thing the app offers.

import { api, pickFile } from "../api";
import { button, clear, fmtClock, h, notice, pill, textInput } from "../dom";
import { emit, save, state, type Method } from "../state";
import type { AuditReport, Clue } from "../types";

function go(view: string): void {
  location.hash = `#/${view}`;
}

async function openPath(path: string, status: HTMLElement): Promise<void> {
  status.replaceChildren(notice("info", "Decoding…"));
  try {
    const info = await api.openRecording(path);
    state.recording = info;
    state.events.recording = { file: info.name, sha256: info.sha256 };
    state.runId = null;
    save();
    emit("recording");
    status.replaceChildren();
  } catch (err) {
    status.replaceChildren(notice("error", (err as Error).message));
  }
}

/** The file chooser shared by the audit and setup views. */
export function recordingPicker(onOpen?: () => void): HTMLElement {
  const status = h("div");
  const pathBox = textInput("", () => undefined, { placeholder: "C:\\path\\to\\recording.wav or .mp4", width: "28em" });
  const open = async (path: string) => {
    if (!path.trim()) return;
    await openPath(path.trim(), status);
    render();
    onOpen?.();
  };
  const container = h("div");
  const render = () => {
    clear(container);
    const rec = state.recording;
    container.append(
      rec
        ? h(
            "p",
            {},
            h("strong", {}, rec.name),
            ` · ${fmtClock(rec.duration_s)} (${rec.duration_s.toFixed(1)} s) · `,
            h("span", { class: "mono muted" }, rec.path),
          )
        : h("p", { class: "muted" }, "No recording open. Local files only; nothing leaves this computer."),
      h(
        "div",
        { class: "row" },
        button(rec ? "Choose another file…" : "Choose a file…", async () => {
          const picked = await pickFile();
          if (picked === undefined) {
            pathBox.focus();
            status.replaceChildren(notice("info", "Type the file's path and press Open (native dialogs need the desktop window)."));
            return;
          }
          if (picked) await open(picked);
        }, "btn primary"),
        pathBox,
        button("Open", () => void open(pathBox.value)),
      ),
      status,
    );
  };
  pathBox.addEventListener("keydown", (e) => {
    if (e.key === "Enter") void open(pathBox.value);
  });
  render();
  return container;
}

function clueBlock(clue: Clue): HTMLElement {
  const verdict = clue.confidence === "likely" ? pill("likely", "error") : clue.confidence === "weak" ? pill("weak", "warn") : pill("not found", "ok");
  const block = h(
    "div",
    { class: `clue ${clue.confidence}` },
    h("div", {}, h("strong", {}, clue.label), " ", verdict, " ", h("span", { class: "muted" }, clue.summary)),
  );
  if (clue.present) {
    block.append(h("p", {}, h("strong", {}, "What it can reveal: "), clue.leaks));
    block.append(h("p", {}, h("strong", {}, "What helps: "), clue.countermeasure));
  }
  const segments = clue.evidence.segments as { start_s: number; end_s: number; f_start_hz: number; f_end_hz: number; drop_pct: number }[] | undefined;
  if (segments?.length) {
    block.append(
      h(
        "p",
        { class: "mono muted" },
        segments
          .slice(0, 5)
          .map((s) => `${fmtClock(s.start_s)}–${fmtClock(s.end_s)}: ${s.f_start_hz.toFixed(0)}→${s.f_end_hz.toFixed(0)} Hz (${s.drop_pct.toFixed(0)}%)`)
          .join(" · "),
      ),
    );
  }
  const bursts = clue.evidence.burst_times_s as number[] | undefined;
  if (bursts?.length) {
    block.append(h("p", { class: "mono muted" }, "bursts at " + bursts.slice(0, 12).map(fmtClock).join(", ") + (bursts.length > 12 ? " …" : "")));
  }
  return block;
}

function renderReport(report: AuditReport, into: HTMLElement): void {
  clear(into);
  const file = report.file;
  const meta = report.metadata;
  into.append(
    h(
      "p",
      {},
      report.any_present
        ? notice("warn", "This recording contains at least one clue that can be matched against public records.")
        : notice("ok", "None of the clues this tool checks for were found. Other clues exist (see FUTURE_SOURCES.md), and faint signals can escape these heuristics."),
    ),
    ...report.clues.map(clueBlock),
    h("h3", {}, "File"),
    h(
      "p",
      { class: "mono muted" },
      `${String(file.name)} · ${Number(file.duration_s).toFixed(1)} s · ${String(file.size_bytes)} bytes · sha256 ${String(file.sha256).slice(0, 16)}…`,
    ),
    meta.available
      ? h(
          "p",
          { class: "mono muted" },
          `container ${String(meta.container ?? "?")} · codec ${String(meta.codec ?? "?")} · ${String(meta.sample_rate_hz ?? "?")} Hz · ${String(meta.channels ?? "?")} ch` +
            (meta.has_video ? " · has video" : ""),
        )
      : h("p", { class: "muted" }, "Container metadata not read: ffprobe (part of ffmpeg) is not installed."),
    h("h3", {}, "Next"),
    h(
      "div",
      { class: "row" },
      button("Find where it was recorded", () => go("locate"), "btn primary"),
      h("span", { class: "muted" }, "Say roughly when and where; the flyovers are measured for you."),
    ),
    h(
      "p",
      { class: "muted" },
      "Or set up a search by hand: ",
      h("a", { href: "#/setup", onclick: (e: Event) => { e.preventDefault(); chooseMethod("thunder"); } }, "thunder"),
      " · ",
      h("a", { href: "#/setup", onclick: (e: Event) => { e.preventDefault(); chooseMethod("aircraft"); } }, "aircraft Doppler"),
      ". By hand you trace each flyover yourself, which helps when the automatic measurement picks the wrong line.",
    ),
  );
}

function chooseMethod(method: Method): void {
  state.method = method;
  save();
  emit("method");
  go("setup");
}

export function mountAudit(container: HTMLElement): () => void {
  const reportBox = h("div");
  const runBtn = button("Run audit", () => void runAudit(), "btn primary");
  const intro = h(
    "div",
    { class: "card" },
    h("h2", {}, "Check your own recording for location clues"),
    h(
      "p",
      {},
      "Earwitness is a privacy-awareness tool. Background thunder and propeller aircraft can be matched against public lightning and ADS-B records to work out where a recording was made. ",
      "Start by auditing a recording of your own: the report says which clues it contains, what each could reveal, and what has been shown to help.",
    ),
    recordingPicker(() => {
      runBtn.disabled = !state.recording;
      clear(reportBox);
    }),
    h("div", { class: "row" }, runBtn),
    reportBox,
  );
  runBtn.disabled = !state.recording;

  async function runAudit(): Promise<void> {
    if (!state.recording) return;
    runBtn.disabled = true;
    reportBox.replaceChildren(notice("info", "Listening for clues… a few seconds for a long clip."));
    try {
      const report = await api.audit(state.recording.id);
      renderReport(report, reportBox);
    } catch (err) {
      reportBox.replaceChildren(notice("error", (err as Error).message));
    } finally {
      runBtn.disabled = !state.recording;
    }
  }

  container.append(intro);
  return () => undefined;
}
