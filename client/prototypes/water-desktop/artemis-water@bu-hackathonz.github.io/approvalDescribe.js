// Pure module: no imports, no GNOME. Says what an approval will do, word for word, so the dialog
// never asks the user to approve something it did not show (contracts.md §2 and §9).
// known:false means "this version can't show it": the dialog then only offers Reject.

export const UNKNOWN_MESSAGE = "Artemis sent an approval this version can't show. Rejecting it.";

const DESKTOP_TITLES = {
  open: 'Open',
  read_outside: 'Read a file',
  run_command: 'Run a command',
  type: 'Type into a window',
};

const isText = v => typeof v === 'string';

// Returns {title, lines, known}. lines are plain strings; desktop.text and desktop.target are
// copied unchanged.
export function describeApproval(approval) {
  const unknown = {title: 'Approval not shown', lines: [UNKNOWN_MESSAGE], known: false};
  if (!approval || typeof approval !== 'object') return unknown;

  if (approval.kind === 'write_files') {
    const files = Array.isArray(approval.files) ? approval.files : [];
    return {
      title: 'Review file changes',
      known: true,
      lines: files.map(f => `${f.path}: ${f.action}, ${f.lines_added} lines added, ${f.lines_removed} removed.`),
    };
  }

  if (approval.kind === 'run_program') {
    const run = approval.run;
    if (!run) return unknown; // nothing to show means nothing to approve
    return {
      title: 'Review program run',
      known: true,
      lines: [`Run ${run.entry} with ${run.preset}. Timeout: ${run.timeout_s} seconds.`],
    };
  }

  const title = Object.hasOwn(DESKTOP_TITLES, approval.kind) ? DESKTOP_TITLES[approval.kind] : null;
  const d = approval.desktop;
  if (!title || !d || typeof d !== 'object' || !isText(d.target)) return unknown;
  if (d.text != null && !isText(d.text)) return unknown;
  const hasText = isText(d.text);

  switch (approval.kind) {
    case 'open':
      return {title, known: true, lines: hasText ? [`Open: ${d.target}`, `With: ${d.text}`] : [`Open: ${d.target}`]};
    case 'read_outside':
      return {title, known: true, lines: [`File: ${d.target}`]};
    case 'run_command':
      if (!hasText) return unknown; // never approve a command we can't show
      return {title, known: true, lines: [`Command: ${d.text}`, `Folder: ${d.target}`]};
    default: // 'type'
      if (!hasText) return unknown;
      return {title, known: true, lines: [`Text: ${d.text}`, `Into: ${d.target}`]};
  }
}

// What a screen reader hears when the dialog opens: title, summary and the exact details.
export function accessibleName(approval) {
  const {title, lines, known} = describeApproval(approval);
  if (!known) return `${title}. ${UNKNOWN_MESSAGE}`;
  const summary = approval.summary ? `${approval.summary} ` : '';
  return `${title}. ${summary}${lines.join(' ')}`.replace(/\s+$/, '');
}
