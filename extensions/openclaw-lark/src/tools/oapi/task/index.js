"use strict";
/**
 * Copyright (c) 2026 ByteDance Ltd. and/or its affiliates
 * SPDX-License-Identifier: MIT
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.registerFeishuTaskSubtaskTool = exports.registerFeishuTaskCommentTool = exports.registerFeishuTaskTasklistTool = exports.registerFeishuTaskTaskTool = void 0;
var task_1 = require("./task");
Object.defineProperty(exports, "registerFeishuTaskTaskTool", { enumerable: true, get: function () { return task_1.registerFeishuTaskTaskTool; } });
var tasklist_1 = require("./tasklist");
Object.defineProperty(exports, "registerFeishuTaskTasklistTool", { enumerable: true, get: function () { return tasklist_1.registerFeishuTaskTasklistTool; } });
var comment_1 = require("./comment");
Object.defineProperty(exports, "registerFeishuTaskCommentTool", { enumerable: true, get: function () { return comment_1.registerFeishuTaskCommentTool; } });
var subtask_1 = require("./subtask");
Object.defineProperty(exports, "registerFeishuTaskSubtaskTool", { enumerable: true, get: function () { return subtask_1.registerFeishuTaskSubtaskTool; } });
