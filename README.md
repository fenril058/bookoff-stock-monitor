# BOOKOFF Stock Monitor

BOOKOFF公式オンラインストアの商品ページを低頻度で確認し、現在「在庫あり」と判定できた場合にDiscordへ通知する個人用GitHub Actionsです。

- 初期設定は「ねこくま、めしくま」
- 10分間隔
- 状態管理なし、リポジトリへの自動書き込みなし
- 1商品につき1回のページ取得（通信失敗時のみ最大1回再試行）
- 自動ログイン・CAPTCHA回避・自動購入は行いません
- 最大10商品に制限しています
- 外部Pythonパッケージは不要です

## 通知の挙動

永続的な状態を保存しないため、各実行はその時点の在庫だけを判定します。

```text
在庫なし  → 通知しない
在庫あり  → 通知する
判定不能  → 通知せずWorkflowを失敗させる
```

在庫が残っている間は、10分ごとの実行で繰り返し通知されます。購入できた後は、Actions画面でWorkflowを無効化するか、`items.json`から商品を削除してください。

## 1. リポジトリを作る

GitHubで新しいリポジトリを作成し、このフォルダの中身をそのままアップロードします。公開リポジトリでも、Discord Webhook URLをRepository Secretに保存すればコードには公開されません。

Workflowの権限は`contents: read`だけです。`actions/checkout`にも`persist-credentials: false`を設定しており、Gitの認証情報を後続ステップへ残しません。

## 2. Discord Webhookを作る

Discordの通知先サーバーで、対象チャンネルの設定からWebhookを作成し、Webhook URLをコピーします。

## 3. GitHub Secretを登録する

リポジトリで次を開きます。

`Settings` → `Secrets and variables` → `Actions` → `New repository secret`

| 項目 | 値 |
|---|---|
| Name | `DISCORD_WEBHOOK_URL` |
| Secret | DiscordでコピーしたWebhook URL |

Webhook URLを`items.json`、README、Issue、Actionsログへ貼り付けないでください。

## 4. テスト通知を送る

1. `Actions`タブを開く
2. `BOOKOFF stock monitor`を選択
3. `Run workflow`を押す
4. `Send a Discord test notification...`をオンにして実行

Discordにテスト通知が届けば設定完了です。

## 5. 監視商品を追加・変更する

`items.json`を編集します。

```json
[
  {
    "id": "0019040704",
    "name": "ねこくま、めしくま",
    "url": "https://shopping.bookoff.co.jp/used/0019040704"
  }
]
```

- `id`: リポジトリ内で重複しない識別子
- `name`: 商品ページに表示される商品名
- `url`: `https://shopping.bookoff.co.jp/`で始まる商品URL

複数商品を設定した場合、各商品へのアクセス間に1秒の間隔を入れます。サイト負荷を抑えるため、最大10商品です。

## 判定方法

1. 商品のJSON-LDに在庫情報があれば優先
2. なければ商品名の周辺にある「カートに入れる」または「在庫なし」等を判定
3. 商品名が見つからない、アクセス制限ページ、判定不能の場合は通知せずWorkflowを失敗させる

ページ構造が変わった場合、誤通知よりも「判定不能で停止」を優先します。

## 実行間隔を変える

`.github/workflows/monitor.yml`のcronを編集します。現在は毎時3、13、23、33、43、53分です。

```yaml
- cron: "3,13,23,33,43,53 * * * *"
```

高頻度化はサイト負荷や利用制限につながる可能性があります。5分未満にせず、対象商品数も必要最小限にしてください。

## 公開リポジトリの注意

公開リポジトリに長期間アクティビティがない場合、scheduled workflowが自動的に無効化されることがあります。Actionsタブから再度有効化してください。この構成は自動コミットを行わないため、リポジトリのアクティビティは増えません。

## ローカルテスト

```bash
python -m unittest discover -s tests -v
```

実際の通知テストには環境変数が必要です。

```bash
export DISCORD_WEBHOOK_URL='https://discord.com/api/webhooks/...'
python monitor.py --test-notification
```

## セキュリティチェック

Action参照はコミットSHAに固定しています。更新後は次を実行してください。

```bash
pinact run
zizmor -- .github/workflows/
```

## 利用上の注意

このツールは購入を保証しません。通知時点で売り切れている可能性があります。BOOKOFFの利用規約・サイト表示・アクセス制限を優先し、問題が生じた場合は監視を停止してください。自動購入、ログイン回避、CAPTCHA回避、大量クロールには使用しないでください。

## 参考（公式）

- BOOKOFF公式オンラインストア利用規約: https://shopping.bookoff.co.jp/policies/terms
- GitHub Actionsのscheduled workflow: https://docs.github.com/actions/reference/workflows-and-actions/events-that-trigger-workflows#schedule
- GitHub Actions Secrets: https://docs.github.com/actions/security-guides/using-secrets-in-github-actions
