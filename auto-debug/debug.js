/**
 * 自动调试脚本 - 捕获页面 console 错误、网络请求异常、页面崩溃等
 * 用法: node debug.js [url]
 * 示例: node debug.js http://localhost:9999
 */

const { chromium } = require('playwright');

const TARGET_URL = process.argv[2] || 'http://localhost:9999';

(async () => {
  console.log(`\n🚀 启动 Chrome 调试: ${TARGET_URL}\n`);

  const browser = await chromium.launch({
    channel: 'chrome',
    headless: false,
    devtools: true, // 自动打开 DevTools
  });

  const context = await browser.newContext();
  const page = await context.newPage();

  // ── Console 输出捕获 ──────────────────────────────────────
  page.on('console', (msg) => {
    const type = msg.type();
    const text = msg.text();
    const prefix = {
      error:   '❌ [console.error]',
      warning: '⚠️  [console.warn]',
      log:     '📝 [console.log]',
      info:    'ℹ️  [console.info]',
    }[type] || `[${type}]`;
    console.log(`${prefix} ${text}`);
  });

  // ── 页面未捕获异常 ───────────────────────────────────────
  page.on('pageerror', (err) => {
    console.error(`\n💥 [PageError] ${err.message}`);
    if (err.stack) console.error(err.stack);
  });

  // ── 网络请求失败 ─────────────────────────────────────────
  page.on('requestfailed', (request) => {
    console.error(`\n🔴 [RequestFailed] ${request.method()} ${request.url()}`);
    console.error(`   原因: ${request.failure()?.errorText}`);
  });

  // ── 响应状态码 >= 400 ────────────────────────────────────
  page.on('response', (response) => {
    const status = response.status();
    if (status >= 400) {
      console.error(`\n🟠 [HTTP ${status}] ${response.request().method()} ${response.url()}`);
    }
  });

  // ── 页面崩溃 ─────────────────────────────────────────────
  page.on('crash', () => {
    console.error('\n💀 [PageCrash] 页面已崩溃！');
  });

  // ── 导航到目标页面 ───────────────────────────────────────
  try {
    const response = await page.goto(TARGET_URL, {
      waitUntil: 'networkidle',
      timeout: 30000,
    });
    console.log(`\n✅ 页面加载完成，状态码: ${response?.status()}\n`);
  } catch (err) {
    console.error(`\n❌ 页面加载失败: ${err.message}`);
  }

  // ── 保持运行，持续监控 ───────────────────────────────────
  console.log('🔍 持续监控中（Ctrl+C 退出）...\n');
  await new Promise(() => {}); // 阻塞，保持浏览器开启
})();
