#!/usr/bin/env node
// PreToolUse hook: block agent edits to owner-only files (financial limits) and to .env files,
// and block shell commands that write to them. Exit code 2 = block with message to Claude.
let input = '';
process.stdin.on('data', (d) => (input += d));
process.stdin.on('end', () => {
  let evt; try { evt = JSON.parse(input); } catch { process.exit(0); }
  const ti = evt.tool_input ?? {};
  const PROTECTED = [/config\/financial-limits\.json$/, /(^|\/)\.env(\.(?!example$)[^/]*)?$/];
  const paths = [ti.file_path, ti.notebook_path, ti.path].filter(Boolean);
  const hitPath = paths.find((p) => PROTECTED.some((re) => re.test(p)));

  const cmd = typeof ti.command === 'string' ? ti.command : '';
  const F = String.raw`\S*financial-limits\.json`;
  const SHELL_WRITES = [
    new RegExp(String.raw`(>>?|>\|)\s*['"]?${F}`),                       // redirection into the file
    new RegExp(String.raw`\btee\b(\s+-\S+)*\s+['"]?${F}`),               // tee into the file
    new RegExp(String.raw`\b(sed|perl|ruby)\s+(-\S*\s+)*-i\S*\b[^\n]*${F}`), // in-place edit
    new RegExp(String.raw`(^|[;&|]\s*)(mv|cp|rm|truncate|ln)\b[^\n;&|]*${F}`, 'm'), // move/copy/delete
    new RegExp(String.raw`(writeFile|open\([^)]*['"]w)[^\n]*${F}`),      // scripted writes
    new RegExp(String.raw`git\s+(checkout|restore)\b[^\n]*${F}`),
  ];
  const hitCmd = SHELL_WRITES.some((re) => re.test(cmd));

  if (hitPath || hitCmd) {
    process.stderr.write(
      `BLOCKED by commerce-os guard: ${hitPath ?? 'shell write to financial-limits.json'} is owner-only ` +
      '(CLAUDE.md §5/§6). Add a request to APPROVAL_QUEUE.md instead.\n',
    );
    process.exit(2);
  }
  process.exit(0);
});
