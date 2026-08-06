const ITEM = Object.freeze({
  id: "0019040704",
  name: "ねこくま、めしくま",
  jan: "9784041064368",
  url: "https://shopping.bookoff.co.jp/used/0019040704",
});

/**
 * JSON-LD内の値を配列として扱う。
 *
 * @param {unknown} value
 * @returns {unknown[]}
 */
function toArray(value) {
  if (value === undefined || value === null) {
    return [];
  }

  return Array.isArray(value) ? value : [value];
}

/**
 * JSON-LDのトップレベルと@graph内のエントリを列挙する。
 *
 * @param {unknown} value
 * @returns {Record<string, unknown>[]}
 */
function collectJsonLdEntries(value) {
  const results = [];

  for (const entry of toArray(value)) {
    if (!entry || typeof entry !== "object" || Array.isArray(entry)) {
      continue;
    }

    results.push(entry);

    if (Array.isArray(entry["@graph"])) {
      results.push(...collectJsonLdEntries(entry["@graph"]));
    }
  }

  return results;
}

/**
 * schema.orgの在庫URLを末尾の名称へ正規化する。
 *
 * 例:
 *   https://schema.org/InStock  -> InStock
 *   http://schema.org/OutOfStock -> OutOfStock
 *
 * @param {unknown} value
 * @returns {string}
 */
function normalizeAvailability(value) {
  if (typeof value !== "string") {
    return "";
  }

  return value.split("/").filter(Boolean).at(-1) ?? "";
}

/**
 * 対象商品のProduct JSON-LDを使って在庫を判定する。
 *
 * @param {string} html
 * @returns {{ status: "available" | "sold_out" | "unknown", reason: string }}
 */
function detectFromJsonLd(html) {
  const scriptPattern =
    /<script\b[^>]*\btype\s*=\s*["']application\/ld\+json["'][^>]*>([\s\S]*?)<\/script>/gi;

  let foundExpectedProduct = false;
  let match;

  while ((match = scriptPattern.exec(html)) !== null) {
    const jsonText = match[1].trim();

    if (!jsonText) {
      continue;
    }

    let parsed;

    try {
      parsed = JSON.parse(jsonText);
    } catch {
      // 他用途の不正なJSON-LDがあっても、次のscript要素を確認する。
      continue;
    }

    const entries = collectJsonLdEntries(parsed);

    for (const entry of entries) {
      const types = toArray(entry["@type"]);
      const isProduct = types.includes("Product");

      if (!isProduct) {
        continue;
      }

      const name = String(entry.name ?? "");
      const gtin13 = String(entry.gtin13 ?? "");
      const productUrl = String(entry.url ?? entry["@id"] ?? "");

      const isExpectedProduct =
        name.includes(ITEM.name) &&
        gtin13 === ITEM.jan &&
        productUrl.includes(ITEM.id);

      if (!isExpectedProduct) {
        continue;
      }

      foundExpectedProduct = true;

      const offers = toArray(entry.offers).filter(
        (offer) => offer && typeof offer === "object",
      );

      const availabilityValues = offers
        .map((offer) => normalizeAvailability(offer.availability))
        .filter(Boolean);

      if (availabilityValues.includes("InStock")) {
        return {
          status: "available",
          reason: "JSON-LD availability is InStock",
        };
      }

      // LimitedAvailabilityも購入可能として扱う。
      if (availabilityValues.includes("LimitedAvailability")) {
        return {
          status: "available",
          reason: "JSON-LD availability is LimitedAvailability",
        };
      }

      if (
        availabilityValues.length > 0 &&
        availabilityValues.every((value) => value === "OutOfStock")
      ) {
        return {
          status: "sold_out",
          reason: "JSON-LD availability is OutOfStock",
        };
      }

      return {
        status: "unknown",
        reason:
          availabilityValues.length > 0
            ? `Unrecognized JSON-LD availability: ${availabilityValues.join(
                ", ",
              )}`
            : "Expected Product JSON-LD has no availability",
      };
    }
  }

  return {
    status: "unknown",
    reason: foundExpectedProduct
      ? "Expected Product JSON-LD could not be evaluated"
      : "Expected Product JSON-LD was not found",
  };
}

/**
 * 対象商品IDを持つ実際のカートボタンがHTML内にあるか確認する。
 *
 * ページ共通JavaScript内の「カートに追加」という文言は判定に使わない。
 * class属性とdata-item属性の順序には依存しない。
 *
 * @param {string} html
 * @returns {boolean}
 */
function hasProductSpecificCartButton(html) {
  const tagPattern = /<(?:a|button)\b[^>]*>/gi;
  let match;

  while ((match = tagPattern.exec(html)) !== null) {
    const tag = match[0];

    const classMatch = tag.match(/\bclass\s*=\s*["']([^"']*)["']/i);
    const itemMatch = tag.match(/\bdata-item\s*=\s*["']([^"']*)["']/i);

    if (!classMatch || !itemMatch) {
      continue;
    }

    const classes = classMatch[1].split(/\s+/);
    const itemId = itemMatch[1];

    if (classes.includes("jsBtn-cart") && itemId === ITEM.id) {
      return true;
    }
  }

  return false;
}

/**
 * JSON-LDが利用できない場合に、対象商品の在庫なし表示を探す。
 *
 * @param {string} html
 * @returns {boolean}
 */
function hasProductSoldOutMarker(html) {
  if (!html.includes(ITEM.name) || !html.includes(ITEM.jan)) {
    return false;
  }

  // BOOKOFFの商品詳細ページで使われている在庫なし表示。
  const soldOutPatterns = [
    /productInformation__stock--none[^>]*>\s*在庫なし\s*</i,
    /cartFloat__alert[^>]*>\s*在庫なし\s*</i,
  ];

  return soldOutPatterns.some((pattern) => pattern.test(html));
}

/**
 * @param {string} html
 * @returns {{ status: "available" | "sold_out" | "unknown", reason: string }}
 */
export function detectStock(html) {
  const hasExpectedIdentity =
    html.includes(ITEM.name) &&
    html.includes(ITEM.jan) &&
    html.includes(ITEM.id);

  if (!hasExpectedIdentity) {
    return {
      status: "unknown",
      reason: "Expected product name, JAN, or item ID was not found",
    };
  }

  /*
   * 1. 商品専用JSON-LDを最優先する。
   *
   * JSON-LDでInStockまたはOutOfStockを取得できた場合は、
   * HTML上の予備判定へ進まない。
   */
  const jsonLdResult = detectFromJsonLd(html);

  if (jsonLdResult.status !== "unknown") {
    return jsonLdResult;
  }

  /*
   * 2. JSON-LDがない、壊れている、未知の値だった場合のみ、
   *    対象商品ID付きの実カートボタンを予備判定に使う。
   */
  if (hasProductSpecificCartButton(html)) {
    return {
      status: "available",
      reason: "Found product-specific cart button",
    };
  }

  /*
   * 3. 対象商品の在庫なし表示が確認できればsold_out。
   */
  if (hasProductSoldOutMarker(html)) {
    return {
      status: "sold_out",
      reason: `Fallback sold-out marker found; JSON-LD result: ${jsonLdResult.reason}`,
    };
  }

  return {
    status: "unknown",
    reason: `No recognized stock marker was found; JSON-LD result: ${jsonLdResult.reason}`,
  };
}

/**
 * BOOKOFFの商品ページを取得して在庫を判定する。
 *
 * @returns {Promise<{
 *   status: "available" | "sold_out" | "unknown",
 *   reason: string,
 *   httpStatus: number,
 *   responseLength: number
 * }>}
 */
async function fetchStock() {
  const response = await fetch(ITEM.url, {
    method: "GET",
    redirect: "follow",

    // 在庫監視なのでCloudflareキャッシュを利用しない。
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
 * Discordへ在庫通知を送信する。
 *
 * @param {string} webhookUrl
 * @param {Date} checkedAt
 * @returns {Promise<void>}
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

      // 商品名などにDiscordメンション文字列が含まれても展開しない。
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
 * @returns {Promise<void>}
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
      itemId: ITEM.id,
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
    throw new Error(
      `Stock status could not be determined: ${result.reason}`,
    );
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
   * ローカルでの稼働確認用。
   *
   * 本番ではworkers_devとpreview_urlsを無効にしているため、
   * 公開HTTPエンドポイントとしては使用しない。
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
