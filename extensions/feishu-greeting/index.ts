import type { OpenClawPluginApi } from "openclaw/plugin-sdk";
import { sendGreetings, type GreetingConfig } from "./src/greeting.js";

const plugin = {
  id: "feishu-greeting",
  name: "Feishu Greeting",
  description: "Auto-send opening messages to Feishu users on gateway startup",
  configSchema: { type: "object" as const },
  register(api: OpenClawPluginApi) {
    const config = api.pluginConfig as GreetingConfig | undefined;
    if (!config?.message) {
      api.logger.debug("feishu-greeting: no message configured, skipping");
      return;
    }
    if (!config.targets || config.targets.length === 0) {
      api.logger.debug("feishu-greeting: no targets configured, skipping");
      return;
    }

    api.on("gateway_start", async () => {
      try {
        await sendGreetings({ api, config });
      } catch (err) {
        api.logger.warn(`feishu-greeting: failed: ${String(err)}`);
      }
    });
  },
};

export default plugin;
