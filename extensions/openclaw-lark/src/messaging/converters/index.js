"use strict";
/**
 * Copyright (c) 2026 ByteDance Ltd. and/or its affiliates
 * SPDX-License-Identifier: MIT
 *
 * Content converter mapping for all Feishu message types.
 */
Object.defineProperty(exports, "__esModule", { value: true });
exports.converters = void 0;
const text_1 = require("./text");
const post_1 = require("./post");
const image_1 = require("./image");
const file_1 = require("./file");
const audio_1 = require("./audio");
const video_1 = require("./video");
const sticker_1 = require("./sticker");
const index_1 = require("./interactive/index");
const share_1 = require("./share");
const location_1 = require("./location");
const merge_forward_1 = require("./merge-forward");
const folder_1 = require("./folder");
const system_1 = require("./system");
const hongbao_1 = require("./hongbao");
const calendar_1 = require("./calendar");
const video_chat_1 = require("./video-chat");
const todo_1 = require("./todo");
const vote_1 = require("./vote");
const unknown_1 = require("./unknown");
exports.converters = new Map([
    ['text', text_1.convertText],
    ['post', post_1.convertPost],
    ['image', image_1.convertImage],
    ['file', file_1.convertFile],
    ['audio', audio_1.convertAudio],
    ['video', video_1.convertVideo],
    ['media', video_1.convertVideo],
    ['sticker', sticker_1.convertSticker],
    ['interactive', index_1.convertInteractive],
    ['share_chat', share_1.convertShareChat],
    ['share_user', share_1.convertShareUser],
    ['location', location_1.convertLocation],
    ['merge_forward', merge_forward_1.convertMergeForward],
    ['folder', folder_1.convertFolder],
    ['system', system_1.convertSystem],
    ['hongbao', hongbao_1.convertHongbao],
    ['share_calendar_event', calendar_1.convertShareCalendarEvent],
    ['calendar', calendar_1.convertCalendar],
    ['general_calendar', calendar_1.convertGeneralCalendar],
    ['video_chat', video_chat_1.convertVideoChat],
    ['todo', todo_1.convertTodo],
    ['vote', vote_1.convertVote],
    ['unknown', unknown_1.convertUnknown],
]);
