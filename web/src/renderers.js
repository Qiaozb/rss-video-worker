import { state } from "./state.js?v=20260704-report-action-ui";
import { $, escapeHTML, fmt, formatDateTime, shortText, statusClass } from "./utils.js?v=20260704-report-action-ui";

export function reportCell(reportId) {
  if (!reportId) {
    return "-";
  }
  return `
    <div class="report-action-stack is-compact">
      <div class="report-action-line">
        <span class="report-action-id">${reportId}</span>
        <button class="link-button" data-publish-report="${reportId}" type="button" aria-label="发布报告 ${reportId}">发布</button>
      </div>
      <div class="report-action-line">
        ${renderEngineSelect(reportId, "remotion", "compact")}
        <button class="link-button" data-render-report="${reportId}" type="button" aria-label="渲染报告 ${reportId}">渲染</button>
      </div>
    </div>
  `;
}


function renderEngineSelect(reportId, selected = "remotion", variant = "compact") {
  const value = selected === "ffmpeg" ? "ffmpeg" : "remotion";
  const isDetail = variant === "detail";
  return `
    <label class="inline-render-engine-wrap ${isDetail ? "is-detail" : "is-compact"}">
      <span>${isDetail ? "渲染方式" : "方式"}</span>
      <select class="inline-render-engine ${isDetail ? "is-detail" : "is-compact"}" data-render-engine-for="${reportId}" aria-label="报告 ${reportId} 渲染引擎">
        <option value="remotion" ${value === "remotion" ? "selected" : ""}>Remotion</option>
        <option value="ffmpeg" ${value === "ffmpeg" ? "selected" : ""}>${isDetail ? "FFmpeg 模板" : "FFmpeg"}</option>
      </select>
    </label>
  `;
}



export function reportActions(report, variant = "table") {
  const download = report.video_download_url
    ? `<a class="button-link" href="${report.video_download_url}" target="_blank" rel="noreferrer">下载</a>`
    : "";
  const selectedEngine = report.video_render_engine || report.render_engine || "remotion";
  const isDetail = variant === "detail";
  return `
    <div class="report-action-stack ${isDetail ? "is-detail" : "is-compact"}">
      <div class="report-action-line">
        <button class="link-button" data-report-detail="${report.id}" type="button" aria-label="查看报告 ${report.id} 详情">查看</button>
        ${download}
      </div>
      <div class="report-action-line">
        ${renderEngineSelect(report.id, selectedEngine, isDetail ? "detail" : "compact")}
      </div>
      <div class="report-action-line">
        <button class="link-button" data-render-report="${report.id}" type="button" aria-label="渲染报告 ${report.id}">渲染</button>
        <button class="link-button" data-publish-report="${report.id}" type="button" aria-label="发布报告 ${report.id}">发布</button>
      </div>
    </div>
  `;
}



export function reportVideoLive(report) {
  const status = report.video_status || "pending";
  const progress = Number(report.video_progress_percent || 0).toFixed(0);
  const step = report.video_current_step || "";
  const error = report.video_error_message
    ? `<div class="form-error">${escapeHTML(report.video_error_message)}</div>`
    : "";
  return `
    <div class="video-live">
      <div>
        <span class="field-label">视频状态</span>
        <strong><span class="${statusClass(status)}">${escapeHTML(status)}</span></strong>
      </div>
      <div>
        <span class="field-label">视频进度</span>
        <strong>${progress}%</strong>
      </div>
      <div class="wide">
        <span class="field-label">当前步骤</span>
        <strong>${escapeHTML(step)}</strong>
      </div>
      ${error}
    </div>
  `;
}



export function ttsActions(item) {
  if (!item.tts_queue_id) {
    return "";
  }
  const audio =
    item.tts_status === "done"
      ? `<a class="button-link" href="/tts-items/${item.tts_queue_id}/audio" target="_blank" rel="noreferrer">试听</a>`
      : "";
  return `
    ${audio}
    <button class="link-button" data-generate-tts="${item.tts_queue_id}" type="button" aria-label="生成 TTS ${item.tts_queue_id}">生成</button>
    <button class="link-button" data-retry-tts="${item.tts_queue_id}" type="button" aria-label="重试 TTS ${item.tts_queue_id}">重试</button>
  `;
}



export function runActions(run) {
  const status = run.status || "";
  const canCancel = ["pending", "running"].includes(status);
  const canRetry = ["failed", "cancelled", "succeeded"].includes(status);
  return `
    <button class="link-button" data-run-events="${run.id}" type="button" aria-label="查看任务 ${run.id} 事件">事件</button>
    <button class="link-button" data-run-llm-logs="${run.id}" type="button" aria-label="查看任务 ${run.id} 模型日志">模型日志</button>
    ${
      canCancel
        ? `<button class="link-button" data-cancel-run="${run.id}" type="button" aria-label="取消任务 ${run.id}">取消</button>`
        : ""
    }
    ${
      canRetry
        ? `<button class="link-button" data-retry-run="${run.id}" type="button" aria-label="重试任务 ${run.id}">重试</button>`
        : ""
    }
  `;
}



export function isTerminalRun(status) {
  return ["succeeded", "failed", "cancelled"].includes(status || "");
}



export function isTerminalVideoJob(status) {
  return ["done", "failed", "cancelled"].includes(status || "");
}



export function usageText(item) {
  const total = item.total_tokens ?? "-";
  const prompt = item.prompt_tokens ?? "-";
  const completion = item.completion_tokens ?? "-";
  return `${total} / ${prompt}+${completion}`;
}



export function successRate(item) {
  const total = Number(item.total_calls || 0);
  if (!total) {
    return "0%";
  }
  return `${((Number(item.succeeded_calls || 0) / total) * 100).toFixed(1)}%`;
}



export function tokenText(item) {
  return fmt(item.total_tokens || 0);
}



export function formatMB(value) {
  const number = Number(value || 0);
  return `${number.toFixed(number >= 100 ? 0 : 2)} MB`;
}



export function serviceCard(title, service) {
  const status = service?.status || "unknown";
  const detail = service?.error || service?.body || service?.database || service?.url || "";
  return `
    <div class="maintenance-card">
      <span>${escapeHTML(title)}</span>
      <strong><span class="${statusClass(status)}">${escapeHTML(status)}</span></strong>
      <div class="muted">${escapeHTML(shortText(detail, 120))}</div>
    </div>
  `;
}



export function renderReportsTable() {
  const modelName = (id) => {
    if (!id) return "<span class='muted'>默认</span>";
    const m = (state.models || []).find((x) => String(x.id) === String(id));
    return m ? escapeHTML(m.name) : `#${id}`;
  };
  const promptName = (id) => {
    if (!id) return "<span class='muted'>默认</span>";
    const p = (state.prompts || []).find((x) => String(x.id) === String(id));
    return p ? escapeHTML(p.name) : `#${id}`;
  };
  const pipelineCell = (id) => {
    if (!id) return "<span class='muted'>-</span>";
    return `<button class="link-button" data-run-events="${id}" type="button" aria-label="查看流水线 ${id}">${id}</button>`;
  };
  const reportType = (item) => fmt(item.report_type) || "general";
  const reportCategory = (item) => fmt(item.rss_category) || "全部 RSS";
  const progress = (item) => Number(item.video_progress_percent || 0).toFixed(0);
  $("#reports-table").innerHTML = state.reports
    .map(
      (item) => `
        <tr
          class="selectable-row ${Number(state.selectedReportId) === Number(item.id) ? "is-selected" : ""}"
          data-report-row="${item.id}"
          tabindex="0"
          aria-label="查看报告 ${item.id} 详情"
        >
          <td class="report-main-cell" data-report-detail="${item.id}">
            <div class="run-title-line">
              <button class="link-button run-id-button" data-report-detail="${item.id}" type="button" aria-label="查看报告 ${item.id}">#${item.id}</button>
              <strong>${escapeHTML(item.title)}</strong>
            </div>
            <div class="run-meta-line">
              <span>${reportType(item)}</span>
              <span>${reportCategory(item)}</span>
            </div>
            <div class="report-pipeline">Pipeline ${pipelineCell(item.pipeline_run_id)}</div>
            <div class="report-trend">${escapeHTML(shortText(item.daily_trend, 110))}</div>
          </td>
          <td class="run-config-cell" data-report-detail="${item.id}">
            <div class="run-config-item">
              <span class="field-label">模型</span>
              <strong>${modelName(item.model_config_id)}</strong>
            </div>
            <div class="run-config-item">
              <span class="field-label">提示词</span>
              <strong>${promptName(item.prompt_version_id)}</strong>
            </div>
          </td>
          <td class="report-count-cell" data-report-detail="${item.id}">
            <div><strong>${fmt(item.key_news_count)}</strong><span>新闻</span></div>
            <div><strong>${fmt(item.tts_item_count)}</strong><span>TTS</span></div>
          </td>
          <td class="run-status-cell" data-report-detail="${item.id}">
            <span class="${statusClass(item.video_status || "pending")}">${fmt(item.video_status)}</span>
            <div class="run-progress" aria-label="视频进度 ${progress(item)}%">
              <span style="width: ${progress(item)}%"></span>
            </div>
            <div class="muted">${progress(item)}%</div>
          </td>
          <td class="report-publish-cell" data-report-detail="${item.id}">
            <strong>${fmt(item.published_count)}</strong>
            <span>已发布</span>
          </td>
          <td class="run-actions-cell">${reportActions(item)}</td>
        </tr>
      `,
    )
    .join("");
}



export function videoJobActions(job) {
  const download = job.video_download_url
    ? `<a class="button-link" href="${job.video_download_url}" target="_blank" rel="noreferrer">下载</a>`
    : "";
  const cover = job.cover_download_url
    ? `<a class="button-link" href="${job.cover_download_url}" target="_blank" rel="noreferrer">封面16:9</a>`
    : "";
  const cover4x3 = job.cover_4x3_download_url
    ? `<a class="button-link" href="${job.cover_4x3_download_url}" target="_blank" rel="noreferrer">封面4:3</a>`
    : "";
  const cancel = ["pending", "rendering"].includes(job.status || "")
    ? `<button class="link-button" data-cancel-video-job="${job.id}" type="button" aria-label="取消视频任务 ${job.id}">取消</button>`
    : "";
  return `
    <button class="link-button" data-watch-video-job="${job.id}" type="button" aria-label="查看视频任务 ${job.id} 实时进度">实时进度</button>
    <button class="link-button" data-generate-cover="${job.report_id}" type="button" aria-label="为报告 ${job.report_id} 生成封面">生成封面</button>
    <button class="link-button" data-delete-video-assets="${job.report_id}" type="button" aria-label="删除报告 ${job.report_id} 的视频产物">删除产物</button>
    ${download}
    ${cover}
    ${cover4x3}
    ${cancel}
  `;
}



export function renderVideosTable() {
  $("#videos-table").innerHTML = state.videoJobs
    .map(
      (item) => `
        <tr>
          <td>${item.id}</td>
          <td>
            <button class="link-button" data-report-detail="${item.report_id}" type="button" aria-label="查看报告 ${item.report_id} 详情">${item.report_id}</button>
            <div class="muted">${escapeHTML(shortText(item.report_title || item.video_title, 64))}</div>
            <div class="muted">${escapeHTML(shortText(item.report_daily_trend, 80))}</div>
          </td>
          <td><span class="${statusClass(item.status)}">${escapeHTML(item.status)}</span></td>
          <td>${escapeHTML(item.render_engine || "remotion")}</td>
          <td>
            ${Number(item.progress_percent || 0).toFixed(0)}%
            <div class="muted">${escapeHTML(item.current_step)}</div>
            <div class="muted">心跳 ${escapeHTML(item.heartbeat_at)}</div>
            <div class="muted">Worker ${escapeHTML(item.worker_id)}</div>
          </td>
          <td>${fmt(item.duration_seconds)}</td>
          <td>
            ${item.video_file_exists ? `${fmt(item.video_file_size_mb)} MB` : "-"}
            <div class="muted">${escapeHTML(shortText(item.video_file_path, 72))}</div>
          </td>
          <td>
            ${item.cover_file_exists ? `${fmt(item.cover_file_size_mb)} MB` : "-"}
            <div class="muted">${escapeHTML(shortText(item.cover_file_path, 72))}</div>
          </td>
          <td>${fmt(item.updated_at)}</td>
          <td>${videoJobActions(item)}</td>
        </tr>
      `,
    )
    .join("");
}


export function renderTTSVoicesTable() {
  const table = $("#tts-voices-table");
  if (!table) {
    return;
  }
  table.innerHTML = (state.ttsAudioAssets || [])
    .map(
      (item) => `
        <tr>
          <td>
            <div class="run-title-line">
              <button class="link-button run-id-button" data-report-detail="${item.report_id}" type="button" aria-label="查看报告 ${item.report_id}">#${item.id}</button>
              <strong>${escapeHTML(item.news_title)}</strong>
            </div>
            <div class="run-meta-line">
              <span>报告 ${fmt(item.report_id)}</span>
              <span>第 ${fmt(item.item_index)} 条</span>
              <span>${escapeHTML(item.importance || "-")}</span>
            </div>
            <div class="report-trend">${escapeHTML(shortText(item.narration_preview, 140))}</div>
          </td>
          <td><span class="${statusClass(item.tts_status)}">${escapeHTML(item.tts_status)}</span></td>
          <td>
            ${
              item.audio_download_url
                ? `<audio controls preload="none" src="${escapeHTML(item.audio_download_url)}"></audio>
                   <div class="muted">${fmt(item.audio_duration_seconds)}s · ${fmt(item.audio_file_size_mb)} MB</div>`
                : `<span class="muted">未生成本地音频</span>`
            }
          </td>
          <td>
            <div>${escapeHTML(item.report_title)}</div>
            <div class="muted">${escapeHTML(item.related_field || "-")}</div>
          </td>
          <td>${fmt(item.updated_at)}</td>
          <td>
            ${
              item.audio_download_url
                ? `<a class="button-link" href="${escapeHTML(item.audio_download_url)}" download>保存本地</a>`
                : ""
            }
            <button class="link-button" data-generate-tts="${item.id}" type="button" aria-label="生成语音 ${item.id}">生成</button>
            <button class="link-button" data-retry-tts="${item.id}" type="button" aria-label="重新生成语音 ${item.id}">重生成</button>
            ${
              item.audio_download_url
                ? `<button class="link-button danger-link" data-delete-tts-audio="${item.id}" type="button" aria-label="删除语音文件 ${item.id}">删除音频</button>`
                : ""
            }
          </td>
        </tr>
      `,
    )
    .join("");
}



export function renderAuthUsersTable() {
  const table = $("#auth-users-table");
  if (!table) {
    return;
  }
  table.innerHTML = state.authUsers
    .map(
      (item) => `
        <tr>
          <td>${item.id}</td>
          <td>${escapeHTML(item.username)}</td>
          <td>
            <select data-user-role="${item.id}">
              <option value="admin" ${item.role === "admin" ? "selected" : ""}>admin</option>
              <option value="editor" ${item.role === "editor" ? "selected" : ""}>editor</option>
              <option value="viewer" ${item.role === "viewer" ? "selected" : ""}>viewer</option>
            </select>
          </td>
          <td>${Number(item.enabled) ? "是" : "否"}</td>
          <td>${fmt(item.last_login_at)}</td>
          <td>
            <button class="link-button" data-save-user="${item.id}" type="button" aria-label="保存用户 ${item.id} 的角色">保存角色</button>
            <button class="link-button" data-toggle-user="${item.id}" data-enabled="${Number(item.enabled) ? "0" : "1"}" type="button" aria-label="${Number(item.enabled) ? "停用" : "启用"}用户 ${item.id}">
              ${Number(item.enabled) ? "停用" : "启用"}
            </button>
          </td>
        </tr>
      `,
    )
    .join("");
}



export function renderAuditLogsTable() {
  const table = $("#audit-logs-table");
  if (!table) {
    return;
  }
  table.innerHTML = state.auditLogs
    .map(
      (item) => `
        <tr>
          <td>${item.id}</td>
          <td>${fmt(item.created_at)}</td>
          <td>${escapeHTML(item.username)}</td>
          <td>${escapeHTML(item.role)}</td>
          <td>${escapeHTML(item.action)}</td>
          <td>
            <div>${escapeHTML(item.method)}</div>
            <div class="muted">${escapeHTML(shortText(item.path, 56))}</div>
          </td>
          <td>${fmt(item.status_code)}</td>
          <td>${escapeHTML(shortText(item.message, 80))}</td>
        </tr>
      `,
    )
    .join("");
}



export function renderLLMCallStats() {
  const data = state.llmCallStats || {};
  const summary = data.summary || {};
  $("#llm-stats-cards").innerHTML = `
    <div class="maintenance-card">
      <span>总调用</span>
      <strong>${fmt(summary.total_calls || 0)}</strong>
      <div class="muted">最近 ${fmt(data.days)} 天</div>
    </div>
    <div class="maintenance-card">
      <span>成功率</span>
      <strong>${fmt(summary.success_rate || 0)}%</strong>
      <div class="muted">成功 ${fmt(summary.succeeded_calls || 0)} / 失败 ${fmt(summary.failed_calls || 0)}</div>
    </div>
    <div class="maintenance-card">
      <span>平均耗时</span>
      <strong>${fmt(summary.avg_duration_ms || 0)} ms</strong>
      <div class="muted">最大 ${fmt(summary.max_duration_ms || 0)} ms</div>
    </div>
    <div class="maintenance-card">
      <span>Total Token</span>
      <strong>${fmt(summary.total_tokens || 0)}</strong>
      <div class="muted">Prompt ${fmt(summary.prompt_tokens || 0)} / Completion ${fmt(summary.completion_tokens || 0)}</div>
    </div>
  `;
  $("#llm-stats-model-table").innerHTML = (data.by_model || [])
    .map(
      (item) => `
        <tr>
          <td>${escapeHTML(item.model_name)}</td>
          <td>${fmt(item.total_calls)}</td>
          <td>${successRate(item)}</td>
          <td>${fmt(item.avg_duration_ms)} ms</td>
          <td>${tokenText(item)}</td>
          <td>${fmt(item.repair_calls || 0)}</td>
        </tr>
      `,
    )
    .join("");
  $("#llm-stats-purpose-table").innerHTML = (data.by_purpose || [])
    .map(
      (item) => `
        <tr>
          <td>${escapeHTML(item.purpose)}</td>
          <td>${fmt(item.total_calls)}</td>
          <td>${successRate(item)}</td>
          <td>${fmt(item.avg_duration_ms)} ms</td>
          <td>${tokenText(item)}</td>
          <td>${fmt(item.repair_calls || 0)}</td>
        </tr>
      `,
    )
    .join("");
  $("#llm-stats-day-table").innerHTML = (data.by_day || [])
    .map(
      (item) => `
        <tr>
          <td>${fmt(item.day)}</td>
          <td>${fmt(item.total_calls)}</td>
          <td>${fmt(item.succeeded_calls || 0)}</td>
          <td>${fmt(item.failed_calls || 0)}</td>
          <td>${fmt(item.avg_duration_ms)} ms</td>
          <td>${tokenText(item)}</td>
          <td>${fmt(item.repair_calls || 0)}</td>
        </tr>
      `,
    )
    .join("");
}



export function renderMaintenance() {
  const data = state.maintenance || {};
  const filesystem = data.filesystem || {};
  const services = data.services || {};
  $("#maintenance-services").innerHTML = `
    ${serviceCard("数据库", services.database)}
    ${serviceCard("TTS 服务", services.tts)}
    <div class="maintenance-card">
      <span>磁盘剩余</span>
      <strong>${formatMB(filesystem.free_mb)}</strong>
      <div class="muted">${escapeHTML(filesystem.free_percent)}% 可用 · ${escapeHTML(filesystem.path)}</div>
    </div>
    <div class="maintenance-card">
      <span>检查时间</span>
      <strong>${escapeHTML(data.checked_at)}</strong>
      <div class="muted">UTC 时间</div>
    </div>
  `;
  $("#maintenance-directories-table").innerHTML = (data.directories || [])
    .map(
      (item) => `
        <tr>
          <td>${escapeHTML(item.label)}</td>
          <td>${fmt(item.file_count)}</td>
          <td>${formatMB(item.total_mb)}</td>
          <td>${fmt(item.oldest_mtime)}</td>
          <td>${fmt(item.newest_mtime)}</td>
          <td class="muted">${escapeHTML(item.path)}</td>
        </tr>
      `,
    )
    .join("");
}



export function renderLLMCallLogsTable() {
  $("#llm-logs-table").innerHTML = state.llmCallLogs
    .map(
      (item) => `
        <tr>
          <td>${item.id}</td>
          <td>${item.pipeline_run_id ? `<button class="link-button" data-run-events="${item.pipeline_run_id}" type="button" aria-label="查看流水线 ${item.pipeline_run_id} 事件">${item.pipeline_run_id}</button>` : "-"}</td>
          <td>${escapeHTML(item.purpose)}</td>
          <td>
            <strong>${escapeHTML(item.model_name)}</strong>
            <div class="muted">配置 ${fmt(item.model_config_id)} / 提示词 ${fmt(item.prompt_version_id)}</div>
          </td>
          <td><span class="${statusClass(item.status)}">${escapeHTML(item.status)}</span></td>
          <td>${fmt(item.duration_ms)} ms</td>
          <td>${escapeHTML(usageText(item))}</td>
          <td>${fmt(item.repair_attempt)}</td>
          <td>${fmt(item.created_at)}</td>
          <td class="muted">${escapeHTML(shortText(item.error_message, 120))}</td>
        </tr>
      `,
    )
    .join("");
}



export function renderRunsTable() {
  const modelName = (id) => {
    if (!id) return "<span class='muted'>默认</span>";
    const m = (state.models || []).find((x) => String(x.id) === String(id));
    return m ? escapeHTML(m.name) : `#${id}`;
  };
  const promptName = (id) => {
    if (!id) return "<span class='muted'>默认</span>";
    const p = (state.prompts || []).find((x) => String(x.id) === String(id));
    return p ? escapeHTML(p.name) : `#${id}`;
  };
  const runCategory = (item) => fmt(item.rss_category) || "<span class='muted'>全部 RSS</span>";
  const runType = (item) => fmt(item.task_type) || "<span class='muted'>未指定</span>";
  const runTrigger = (item) => fmt(item.trigger_type) || "<span class='muted'>-</span>";
  const progress = (item) => Number(item.progress_percent || 0).toFixed(0);
  const heartbeat = (item, compact = true) => formatDateTime(item.heartbeat_at, { compact });
  const metricRuns = $("#metric-runs");
  if (metricRuns) {
    metricRuns.textContent = state.runs.length;
  }
  const metricRunning = $("#metric-running");
  if (metricRunning) {
    metricRunning.textContent = state.runs.filter((item) =>
      ["pending", "running"].includes(item.status),
    ).length;
  }
  const runsTable = $("#runs-table");
  if (!runsTable) return;
  runsTable.innerHTML = state.runs
    .map(
      (item) => `
        <tr class="selectable-row ${Number(state.selectedRunId) === Number(item.id) ? "is-selected" : ""}" data-run-row="${item.id}">
          <td class="run-main-cell" data-run-events="${item.id}">
            <div class="run-title-line">
              <button class="link-button run-id-button" data-run-events="${item.id}" type="button" aria-label="查看任务 ${item.id} 事件">#${item.id}</button>
              <strong>${runType(item)}</strong>
            </div>
            <div class="run-meta-line">
              <span>${runTrigger(item)}</span>
              <span>${runCategory(item)}</span>
              <span>${fmt(item.report_type) || "general"}</span>
            </div>
          </td>
          <td class="run-config-cell" data-run-events="${item.id}">
            <div class="run-config-item">
              <span class="field-label">模型</span>
              <strong>${modelName(item.model_config_id)}</strong>
            </div>
            <div class="run-config-item">
              <span class="field-label">提示词</span>
              <strong>${promptName(item.prompt_version_id)}</strong>
            </div>
          </td>
          <td class="run-status-cell" data-run-events="${item.id}">
            <span class="${statusClass(item.status)}">${fmt(item.status)}</span>
            <div class="run-progress" aria-label="任务进度 ${progress(item)}%">
              <span style="width: ${progress(item)}%"></span>
            </div>
            <div class="muted">${progress(item)}%</div>
          </td>
          <td class="run-step-cell" data-run-events="${item.id}">
            <strong>${fmt(item.current_step) || "<span class='muted'>等待执行</span>"}</strong>
            <div class="run-step-meta run-time" title="心跳 ${escapeHTML(heartbeat(item, false))}">心跳 ${escapeHTML(heartbeat(item))}</div>
            <div class="run-step-meta">Worker ${fmt(item.worker_id)}</div>
          </td>
          <td class="run-report-cell">${reportCell(item.report_id)}</td>
          <td class="run-actions-cell">${runActions(item)}</td>
        </tr>
      `,
    )
    .join("");
}



export function renderRunEvents() {
  const events = state.selectedRunEvents || [];
  const empty = $("#events-empty");
  if (!state.selectedRunId) {
    empty.textContent = "选择一个任务查看事件。";
  } else if (!events.length) {
    empty.textContent = `任务 #${state.selectedRunId} 暂无事件。`;
  }
  empty.style.display = events.length ? "none" : "block";
  $("#events-list").innerHTML = events
    .map(
      (item) => `
        <li>
          <div class="event-meta">${escapeHTML(item.created_at)} · ${escapeHTML(item.level)} · ${escapeHTML(item.stage)} · ${escapeHTML(item.progress_percent)}%</div>
          <div>${escapeHTML(item.message)}</div>
        </li>
      `,
    )
    .join("");
}
