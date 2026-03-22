import fs from "node:fs";
import path from "node:path";

const STATE_DIR = path.join(import.meta.dirname, "..", "..", "..", ".state");
const STATE_FILE = path.join(STATE_DIR, "feishu-greeting-sent.json");

export type GreetingState = Record<string, number>;

export function readState(): GreetingState {
  try {
    const raw = fs.readFileSync(STATE_FILE, "utf-8");
    const parsed = JSON.parse(raw);
    if (parsed && typeof parsed === "object" && !Array.isArray(parsed)) {
      return parsed as GreetingState;
    }
    return {};
  } catch {
    return {};
  }
}

export function markSent(state: GreetingState, userId: string): GreetingState {
  return { ...state, [userId]: Date.now() };
}

export function writeState(state: GreetingState): void {
  fs.mkdirSync(STATE_DIR, { recursive: true });
  fs.writeFileSync(STATE_FILE, JSON.stringify(state, null, 2), "utf-8");
}
