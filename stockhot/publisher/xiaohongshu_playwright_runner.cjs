const fs = require('fs');
const { chromium } = require('playwright');

const payload = JSON.parse(fs.readFileSync(0, 'utf8'));

async function firstVisible(page, selectors) {
  for (const selector of selectors) {
    const locator = page.locator(selector).first();
    const count = await locator.count();
    if (count === 0) {
      continue;
    }
    try {
      if (await locator.isVisible()) {
        return locator;
      }
    } catch (_error) {
    }
  }
  return null;
}

async function firstPresent(page, selectors) {
  for (const selector of selectors) {
    const locator = page.locator(selector).first();
    const count = await locator.count();
    if (count > 0) {
      return locator;
    }
  }
  return null;
}

async function isLoginPage(page) {
  if (page.url().includes('/login')) {
    return true;
  }
  return await page.locator('text=短信登录').count() > 0;
}

async function waitForPublishConfirmation(page) {
  const successPattern = /(发布成功|笔记已发布|笔记发布成功|发布完成)/;

  const candidates = [
    {
      signal: 'success_text',
      wait: async () => {
        await page.waitForFunction(
          (pattern) => document.body && new RegExp(pattern).test(document.body.innerText),
          successPattern.source,
          { timeout: payload.actionTimeoutMs }
        );
        const bodyText = await page.locator('body').innerText();
        const match = bodyText.match(successPattern);
        return match ? match[0] : '';
      },
    },
    {
      signal: 'success_url',
      wait: async () => {
        await page.waitForURL(
          (url) => {
            const value = url.toString();
            return value.includes('/creator/home') || value.includes('/notes') || value.includes('/publish/success');
          },
          { timeout: payload.actionTimeoutMs }
        );
        return `success-url:${page.url()}`;
      },
    },
  ];

  const wrapped = candidates.map(({ signal, wait }) =>
    wait()
      .then(async (confirmationText) => ({
        confirmed: true,
        confirmationSignal: signal,
        currentUrl: page.url(),
        confirmationText: confirmationText || '',
      }))
      .catch(() => null)
  );

  const timeoutResult = page.waitForTimeout(payload.actionTimeoutMs).then(() => ({
    confirmed: false,
    confirmationSignal: null,
    currentUrl: page.url(),
    confirmationText: null,
  }));

  const winner = await Promise.race([...wrapped, timeoutResult]);
  if (winner) {
    return winner;
  }

  const settled = await Promise.all(wrapped);
  return settled.find(Boolean) || {
    confirmed: false,
    confirmationSignal: null,
    currentUrl: page.url(),
    confirmationText: null,
  };
}

async function ensureAuthenticated(context, page) {
  await page.goto(payload.publishUrl, {
    waitUntil: 'domcontentloaded',
    timeout: payload.actionTimeoutMs,
  });
  await page.waitForTimeout(2000);

  if (!(await isLoginPage(page))) {
    await context.storageState({ path: payload.storageStatePath });
    return { status: 'authenticated' };
  }

  if (payload.headless) {
    return {
      status: 'login_required',
      message: 'Headless mode cannot complete first login. Set XHS_HEADLESS=false and retry.',
      currentUrl: page.url(),
    };
  }

  await page.goto(payload.publishUrl, {
    waitUntil: 'domcontentloaded',
    timeout: payload.actionTimeoutMs,
  });

  try {
    await page.waitForURL(
      (url) => !url.toString().includes('/login'),
      { timeout: payload.loginTimeoutMs },
    );
  } catch (_error) {
    return {
      status: 'login_timeout',
      message: 'Login was not completed before timeout.',
      currentUrl: page.url(),
    };
  }

  await context.storageState({ path: payload.storageStatePath });
  return { status: 'authenticated' };
}

async function fillDraft(page) {
  const uploadInput = await firstPresent(page, [
    'input[type="file"]',
    '.upload-input input[type="file"]',
  ]);
  if (!uploadInput) {
    return { status: 'failed', error: 'Upload input not found', currentUrl: page.url() };
  }
  await uploadInput.setInputFiles(payload.images);

  const titleInput = await firstVisible(page, [
    'input[placeholder*="标题"]',
    'div.d-input input',
    'input.titleInput',
  ]);
  if (!titleInput) {
    return { status: 'failed', error: 'Title input not found', currentUrl: page.url() };
  }
  await titleInput.fill(payload.title);

  const contentEditor = await firstVisible(page, [
    '.ProseMirror',
    '[contenteditable="true"]',
    'textarea[placeholder*="正文"]',
  ]);
  if (!contentEditor) {
    return { status: 'failed', error: 'Content editor not found', currentUrl: page.url() };
  }

  await contentEditor.click();
  const tagName = await contentEditor.evaluate((element) => element.tagName.toLowerCase());
  if (tagName === 'textarea' || tagName === 'input') {
    await contentEditor.fill(payload.caption);
  } else {
    await page.keyboard.insertText(payload.caption);
  }

  await page.waitForTimeout(1000);
  if (!payload.autoSubmit) {
    return {
      status: 'draft_ready',
      autoSubmit: false,
      currentUrl: page.url(),
    };
  }

  const publishButton = await firstVisible(page, [
    'button:has-text("发布")',
    '.publish-page-publish-btn button',
  ]);
  if (!publishButton) {
    return { status: 'failed', error: 'Publish button not found', currentUrl: page.url() };
  }

  await publishButton.click();
  const confirmation = await waitForPublishConfirmation(page);
  return {
    status: 'submitted',
    autoSubmit: true,
    confirmed: confirmation.confirmed,
    confirmationSignal: confirmation.confirmationSignal,
    confirmationText: confirmation.confirmationText,
    currentUrl: confirmation.currentUrl,
  };
}

async function main() {
  const browser = await chromium.launch({ headless: payload.headless });
  const contextOptions = {};
  if (fs.existsSync(payload.storageStatePath)) {
    contextOptions.storageState = payload.storageStatePath;
  }

  const context = await browser.newContext(contextOptions);
  context.setDefaultTimeout(payload.actionTimeoutMs);
  const page = await context.newPage();

  try {
    const auth = await ensureAuthenticated(context, page);
    if (auth.status !== 'authenticated') {
      console.log(JSON.stringify(auth));
      return;
    }

    await page.goto(payload.publishUrl, {
      waitUntil: 'domcontentloaded',
      timeout: payload.actionTimeoutMs,
    });
    await page.waitForTimeout(2000);

    const result = await fillDraft(page);
    if (result.status === 'draft_ready' || result.status === 'submitted') {
      await context.storageState({ path: payload.storageStatePath });
    }
    console.log(JSON.stringify(result));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error && error.stack ? error.stack : String(error));
  process.exit(1);
});
