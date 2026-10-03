import { test } from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const HOOK = fileURLToPath(new URL('../../.claude/hooks/guard-protected-files.mjs', import.meta.url));
const LIMITS = 'commerce-os/config/financial-limits' + '.json';
const run = (tool_input) => spawnSync('node', [HOOK], { input: JSON.stringify({ tool_input }) }).status;

test('blocks Edit/Write on financial limits and .env, allows .env.example', () => {
  assert.equal(run({ file_path: `/repo/${LIMITS}` }), 2);
  assert.equal(run({ file_path: '/repo/commerce-os/.env' }), 2);
  assert.equal(run({ file_path: '/repo/commerce-os/.env.production' }), 2);
  assert.equal(run({ file_path: '/repo/commerce-os/.env.example' }), 0);
  assert.equal(run({ file_path: '/repo/commerce-os/README.md' }), 0);
});

test('blocks shell writes to the limits file', () => {
  for (const command of [
    `sed -i s/0/500/ ${LIMITS}`,
    `echo {} > ${LIMITS}`,
    `echo x | tee -a ${LIMITS}`,
    `cp /tmp/x ${LIMITS}`,
    `git checkout HEAD~1 -- ${LIMITS}`,
    `node -e "require('fs').writeFileSync('${LIMITS}','{}')"`,
  ]) assert.equal(run({ command }), 2, command);
});

test('allows reads and mere mentions of the limits file', () => {
  for (const command of [
    `cat ${LIMITS}`,
    `cat > .github/CODEOWNERS <<EOF\n/${LIMITS} @owner\nEOF`,
    'git add -A && git commit -m "docs: mention limits"',
    'npm run check:limits',
  ]) assert.equal(run({ command }), 0, command);
});
