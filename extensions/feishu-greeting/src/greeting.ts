import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
// 依赖 openclaw-lark
import { sendMessageFeishu } from "../../openclaw-lark/index.js";
import { normalizeFeishuTarget } from "../../openclaw-lark/src/core/targets.js";
import { readState, markSent, writeState } from "./state.js";

export type GreetingConfig = {
  message: string;
  channel?: string;
  accountId?: string;
  targets: string[];
};

export async function sendGreetings(params: {
  api: OpenClawPluginApi;
  config: GreetingConfig;
}): Promise<void> {
  const { api, config } = params;
  const { message, accountId, targets } = config;

  if (!targets || targets.length === 0) {
    api.logger.debug("feishu-greeting: no targets configured, skipping");
    return;
  }

  // Normalize targets — strip prefixes like "user:" and filter valid open_ids
  const resolvedTargets: string[] = [];
  for (const raw of targets) {
    const normalized = normalizeFeishuTarget(raw);
    if (normalized && normalized !== "*") {
      resolvedTargets.push(normalized);
    }
  }

  if (resolvedTargets.length === 0) {
    api.logger.debug("feishu-greeting: no valid targets after normalization");
    return;
  }

  // De-duplicate against previously sent
  const state = readState();
  const unsent = resolvedTargets.filter((t) => !(t in state));

  if (unsent.length === 0) {
    api.logger.info(`feishu-greeting: all ${resolvedTargets.length} targets already greeted`);
    return;
  }

  api.logger.info(`feishu-greeting: sending to ${unsent.length} new target(s)`);

  let updatedState = { ...state };
  for (const target of unsent) {
    try {
      await sendMessageFeishu({
        cfg: api.config,
        to: target,
        text: message,
        accountId,
      });
      updatedState = markSent(updatedState, target);
      writeState(updatedState);
      api.logger.info(`feishu-greeting: sent to ${target}`);
    } catch (err) {
      api.logger.warn(`feishu-greeting: failed to send to ${target}: ${String(err)}`);
    }
  }
}
