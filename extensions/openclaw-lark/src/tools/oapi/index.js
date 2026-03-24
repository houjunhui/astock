"use strict";
/**
 * Copyright (c) 2026 ByteDance Ltd. and/or its affiliates
 * SPDX-License-Identifier: MIT
 *
 * OAPI Tools Index
 *
 * This module registers all tools that directly use Feishu Open API (OAPI).
 * These tools are placed here to distinguish them from MCP-based tools.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.registerOapiTools = registerOapiTools;
const index_1 = require("./calendar/index");
const index_2 = require("./task/index");
const index_3 = require("./bitable/index");
const index_4 = require("./common/index");
// import { registerFeishuMailTools } from "./mail/index";
const index_5 = require("./search/index");
const index_6 = require("./drive/index");
const index_7 = require("./wiki/index");
const index_8 = require("../tat/im/index");
const index_9 = require("./sheets/index");
// import { registerFeishuOkrTools } from "./okr/index";
const index_10 = require("./chat/index");
const index_11 = require("./im/index");
function registerOapiTools(api) {
    // Common tools
    (0, index_4.registerGetUserTool)(api);
    (0, index_4.registerSearchUserTool)(api);
    // Chat tools
    (0, index_10.registerFeishuChatTools)(api);
    // IM tools (user identity)
    (0, index_11.registerFeishuImTools)(api);
    // Calendar tools
    (0, index_1.registerFeishuCalendarCalendarTool)(api);
    (0, index_1.registerFeishuCalendarEventTool)(api);
    (0, index_1.registerFeishuCalendarEventAttendeeTool)(api);
    (0, index_1.registerFeishuCalendarFreebusyTool)(api);
    // Task tools
    (0, index_2.registerFeishuTaskTaskTool)(api);
    (0, index_2.registerFeishuTaskTasklistTool)(api);
    (0, index_2.registerFeishuTaskCommentTool)(api);
    (0, index_2.registerFeishuTaskSubtaskTool)(api);
    // Bitable tools
    (0, index_3.registerFeishuBitableAppTool)(api);
    (0, index_3.registerFeishuBitableAppTableTool)(api);
    (0, index_3.registerFeishuBitableAppTableRecordTool)(api);
    (0, index_3.registerFeishuBitableAppTableFieldTool)(api);
    (0, index_3.registerFeishuBitableAppTableViewTool)(api);
    // Search tools
    (0, index_5.registerFeishuSearchTools)(api);
    // Drive tools
    (0, index_6.registerFeishuDriveTools)(api);
    // Wiki tools
    (0, index_7.registerFeishuWikiTools)(api);
    // Sheets tools
    (0, index_9.registerFeishuSheetsTools)(api);
    // IM tools (bot identity)
    (0, index_8.registerFeishuImTools)(api);
    api.logger.info?.('Registered all OAPI tools (calendar, task, bitable, search, drive, wiki, sheets, im)');
}
