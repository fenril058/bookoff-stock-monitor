const ITEM = Object.freeze({
  name: "ねこくま、めしくま",
  jan: "9784041064368",
  url: "https://shopping.bookoff.co.jp/used/0019040704",
});

const CART_MARKERS = [
  "カートに追加する",
  "カートにいれる",
  "カートに追加",
];

/**
 * @param {string} html
 * @returns {{ status: "available" | "sold_out" | "unknown", reason: string }}
 */
export function detectStock(html) {
  const hasExpectedProduct =
    html.includes(ITEM.name) && html.includes(ITEM.jan);

  if (!hasExpectedProduct) {
    return {
      status: "unknown",
      reason: "Expected product name or JAN was not found",
    };
  }

  // エラーメッセージはカートボタンとして数えない
  const normalizedHtml = html.replaceAll(
    "カートに追加できませんでした",
    "",
  );

  const hasCartMarker = CART_MARKERS.some((marker) =>
    normalizedHtml.includes(marker),
  );

  if (hasCartMarker) {
    return {
      status: "available",
      reason: "Found cart marker",
    };
  }

  if (html.includes("在庫なし")) {
    return {
      status: "sold_out",
      reason: 'Found "在庫なし" without cart marker',
    };
  }

  return {
    status: "unknown",
    reason: "No recognized stock marker was found",
  };
}

async function fetchStock() {
  const response = await fetch(ITEM.url, {
    method: "GET",
    redirect: "follow",

    // 在庫監視なのでキャッシュされたHTMLを使わない
    cache: "no-store",

    headers: {
      Accept: "text/html,application/xhtml+xml",
      "Accept-Language": "ja-JP,ja;q=0.9",
      "User-Agent":
      "bookoff-stock-monitor/1.0 (+https://github.com/fenril058/bookoff-stock-monitor)",
    },
  });

  if (!response.ok) {
    throw new Error(
      `BOOKOFF returned HTTP ${response.status} ${response.statusText}`,
    );
  }

  const html = await response.text();
  const detection = detectStock(html);

  return {
    ...detection,
    httpStatus: response.status,
    responseLength: html.length,
  };
}

/**
 * @param {string} webhookUrl
 * @param {Date} checkedAt
 */
async function notifyDiscord(webhookUrl, checkedAt) {
  const url = new URL(webhookUrl);
  url.searchParams.set("wait", "true");

  const checkedAtJst = checkedAt.toLocaleString("ja-JP", {
    timeZone: "Asia/Tokyo",
    dateStyle: "medium",
    timeStyle: "medium",
  });

  const response = await fetch(url.toString(), {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      content: [
        "📚 **BOOKOFFで在庫を検知しました**",
        "",
        `**${ITEM.name}**`,
        ITEM.url,
        "",
        `検知時刻: ${checkedAtJst}`,
      ].join("\n"),

      // 商品名などにDiscordメンション文字列が入っても展開しない
      allowed_mentions: {
        parse: [],
      },
    }),
  });

  if (!response.ok) {
    const body = (await response.text()).slice(0, 500);
    throw new Error(
      `Discord returned HTTP ${response.status}: ${body}`,
    );
  }
}

/**
 * @param {{ DISCORD_WEBHOOK_URL?: string }} env
 * @param {{ cron?: string, scheduledAt?: string }} metadata
 */
async function runMonitor(env, metadata = {}) {
  if (!env.DISCORD_WEBHOOK_URL) {
    throw new Error("DISCORD_WEBHOOK_URL is not configured");
  }

  const checkedAt = new Date();
  const result = await fetchStock();

  console.log(
    JSON.stringify({
      event: "bookoff_stock_check",
      checkedAt: checkedAt.toISOString(),
      item: ITEM.name,
      ...metadata,
      ...result,
    }),
  );

  if (result.status === "available") {
    await notifyDiscord(env.DISCORD_WEBHOOK_URL, checkedAt);
    console.log("Discord notification sent");
    return;
  }

  if (result.status === "unknown") {
    throw new Error(`Stock status could not be determined: ${result.reason}`);
  }
}

export default {
  /**
   * Cloudflare Cron Trigger
   */
  async scheduled(controller, env) {
    await runMonitor(env, {
      cron: controller.cron,
      scheduledAt: new Date(controller.scheduledTime).toISOString(),
    });
  },

  /**
   * 稼働確認用。商品確認やDiscord通知は実行しない。
   */
  async fetch(request) {
    const url = new URL(request.url);

    if (url.pathname === "/health") {
      return Response.json({
        ok: true,
        service: "bookoff-stock-monitor",
      });
    }

    return new Response("Not Found", {
      status: 404,
    });
  },
};
