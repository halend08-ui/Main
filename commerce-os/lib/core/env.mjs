// Minimal .env loader (no dependency). Never overrides variables already set in the environment.
import { existsSync, readFileSync } from 'node:fs';

export function loadDotEnv(path = '.env', env = process.env) {
  if (!existsSync(path)) return env;
  for (const raw of readFileSync(path, 'utf8').split(/\r?\n/)) {
    const line = raw.trim();
    if (!line || line.startsWith('#')) continue;
    const eq = line.indexOf('=');
    if (eq < 1) continue;
    const key = line.slice(0, eq).trim();
    let val = line.slice(eq + 1).replace(/\s+#.*$/, '').trim();
    if (/^(['"]).*\1$/.test(val)) val = val.slice(1, -1);
    if (env[key] === undefined) env[key] = val;
  }
  return env;
}

export function requireEnv(name, env = process.env) {
  const v = env[name];
  if (!v) throw new Error(`Missing required environment variable ${name} (see .env.example)`);
  return v;
}
