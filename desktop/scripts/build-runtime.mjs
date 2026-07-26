#!/usr/bin/env node

import { spawnSync } from "node:child_process";
import { fileURLToPath } from "node:url";

const scriptPath = fileURLToPath(new URL("../../tools/build_desktop_runtime.py", import.meta.url));
const forwardedArgs = process.argv.slice(2);

function pythonCandidates() {
  const configured = process.env.VOICE_CODE_PYTHON || process.env.PYTHON;
  const candidates = [];
  if (configured) {
    candidates.push([configured]);
  }
  if (process.platform === "win32") {
    candidates.push(["py", "-3"], ["python"], ["python3"]);
  } else {
    candidates.push(["python3"], ["python"]);
  }
  return candidates;
}

function commandExists(command) {
  const [program, ...args] = command;
  const result = spawnSync(program, [...args, "--version"], {
    encoding: "utf8",
    stdio: "pipe",
  });
  return result.status === 0;
}

function runWith(command) {
  const [program, ...args] = command;
  return spawnSync(program, [...args, scriptPath, ...forwardedArgs], {
    stdio: "inherit",
  });
}

for (const command of pythonCandidates()) {
  if (!commandExists(command)) {
    continue;
  }
  const result = runWith(command);
  if (result.error) {
    console.error(result.error.message);
    process.exit(1);
  }
  if (result.signal) {
    console.error(`Runtime build interrupted by ${result.signal}`);
    process.exit(1);
  }
  process.exit(result.status ?? 1);
}

console.error(
  "Could not find Python for desktop runtime build. Set VOICE_CODE_PYTHON or install python3/python.",
);
process.exit(127);
