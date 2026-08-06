# BOOKOFF Stock Monitor

BOOKOFF公式オンラインストアの商品ページを低頻度で確認し、現在「在庫あり」と判定できた場合にDiscordへ通知する個人用のCloudflare Workerです。

- 初期設定は「ねこくま、めしくま」
- JST 05:07〜22:57の間、10分間隔で確認
- JST 23:00〜04:59は停止
- Cloudflare Cron Triggerは1個だけ使用
- 状態管理なし
- GitHub Actions、Git書き込み、永続ストレージは使用しません
- 自動ログイン、CAPTCHA回避、自動購入は行いません
- 在庫確認は商品ページへの通常のHTTP GETのみです

## 通知の挙動

永続的な状態を保存しないため、各実行はその時点の在庫だけを判定します。

```text
在庫なし  → 通知しない
在庫あり  → Discordへ通知する
判定不能  → 通知せず実行を失敗させる
```

在庫が残っている間は、10分ごとの実行で繰り返し通知されます。購入できた後は、Cron Triggerを無効化するか、監視対象を削除してください。

## 構成

```text
.
├── src/
│   └── index.js
├── package.json
├── package-lock.json
├── wrangler.jsonc
└── README.md
```

## 必要なもの

- Cloudflareアカウント
- Node.js
- npm
- Discord Webhook URL

## 1. 依存関係をインストールする

```bash
npm install
```

## 2. Cloudflareへログインする

```bash
npx wrangler login
```

Cloudflare Workersを初めて使うアカウントでは、`workers.dev`サブドメインの初期登録が必要になる場合があります。

一度登録した後は、`wrangler.jsonc`で次を指定することで、このWorkerの公開URLを無効化できます。

```jsonc
"workers_dev": false,
"preview_urls": false
```

## 3. Discord WebhookをSecretとして登録する

```bash
npx wrangler secret put DISCORD_WEBHOOK_URL
```

プロンプトが表示されたら、Discord Webhook URLを貼り付けます。

Webhook URLを次の場所へ書かないでください。

- `wrangler.jsonc`
- `src/index.js`
- README
- Issue
- GitHub Actionsログ
- Gitコミット

ローカルテスト用には、Git管理対象外の`.dev.vars`を使えます。

```bash
cat > .dev.vars <<'EOV'
DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."
EOV
```

`.gitignore`には次を含めてください。

```gitignore
node_modules/
.wrangler/
.dev.vars
.dev.vars.*
.env
.env.*
```

## 4. Cron Triggerを設定する

`wrangler.jsonc`の例です。

```jsonc
{
  "$schema": "./node_modules/wrangler/config-schema.json",
  "name": "bookoff-stock-monitor",
  "main": "src/index.js",
  "compatibility_date": "2026-08-05",

  "workers_dev": false,
  "preview_urls": false,

  "observability": {
    "enabled": true
  },

  "triggers": {
    "crons": [
      "7,17,27,37,47,57 20-23,0-13 * * *"
    ]
  }
}
```

Cloudflare Cron TriggerはUTCで記述します。

上記は次のJST時刻に対応します。

```text
JST 05:07, 05:17, 05:27, ... 22:57
JST 23:00〜04:59は停止
```

1日の実行回数は108回です。

## 5. ローカルテスト

開発サーバーを起動します。

```bash
npm run dev
```

別のターミナルからCronハンドラーを呼び出します。

```bash
curl --get \
  --data-urlencode 'cron=7,17,27,37,47,57 20-23,0-13 * * *' \
  http://localhost:8787/__scheduled
```

在庫なしの場合は、概ね次のようなログが表示されます。

```json
{
  "event": "bookoff_stock_check",
  "status": "sold_out",
  "reason": "Found \"在庫なし\"",
  "httpStatus": 200
}
```

`GET /__scheduled 200 OK`になれば、Cronハンドラーは正常終了しています。

ローカルサーバーを起動したまま待っても、本番Cronは実行されません。ローカルでは`/__scheduled`へのリクエストが必要です。

## 6. デプロイする

```bash
npm run deploy
```

成功時は、概ね次のように表示されます。

```text
Deployed bookoff-stock-monitor triggers
  schedule: 7,17,27,37,47,57 20-23,0-13 * * *
```

`workers_dev: false`の場合、公開URLは表示されません。

## 7. 本番ログを確認する

```bash
npm run tail
```

実行時間帯に、概ね次のようなログが表示されれば正常です。

```json
{
  "event": "bookoff_stock_check",
  "cron": "7,17,27,37,47,57 20-23,0-13 * * *",
  "status": "sold_out"
}
```

Cloudflare Dashboardからも確認できます。

```text
Workers & Pages
→ bookoff-stock-monitor
→ Logs
```

Cron Triggerは次の場所で確認できます。

```text
Workers & Pages
→ bookoff-stock-monitor
→ Settings
→ Triggers
→ Cron Triggers
```

## 公開URLについて

このWorkerはCron専用です。

```jsonc
"workers_dev": false,
"preview_urls": false
```

を設定しているため、`workers.dev`の公開URLとPreview URLは無効です。

無効化されたURLへアクセスすると、Cloudflare側の404またはエラーコードが返ることがあります。これはWorkerのCron実行には影響しません。

## 監視対象を変更する

現在の実装では、`src/index.js`内の監視対象を編集します。

```javascript
const ITEM = Object.freeze({
  name: "ねこくま、めしくま",
  jan: "9784041064368",
  url: "https://shopping.bookoff.co.jp/used/0019040704",
});
```

変更後は再デプロイします。

```bash
npm run deploy
```

## 判定方法

- 対象の商品名とJANがページ内に存在することを確認
- 「在庫なし」があれば在庫なし
- 「在庫あり」とカート追加表示の両方があれば在庫あり
- 商品名またはJANがない場合は判定不能
- 在庫あり・在庫なしの両方が見つかった場合は判定不能
- ページ構造変更やアクセス制限が疑われる場合は通知しない

誤通知よりも、判定不能として停止することを優先します。

## 停止する

Cron Triggerだけを削除する場合、`wrangler.jsonc`を次のように変更します。

```jsonc
"triggers": {
  "crons": []
}
```

その後、再デプロイします。

```bash
npm run deploy
```

## 利用上の注意

このツールは購入を保証しません。通知時点で売り切れている可能性があります。

BOOKOFFの利用規約、サイト表示、アクセス制限を優先し、問題が生じた場合は監視を停止してください。

次の用途には使用しないでください。

- 自動購入
- 自動ログイン
- CAPTCHA回避
- 高頻度アクセス
- 大量商品の監視
- サイト全体のクロール
