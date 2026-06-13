# API通信先

## 目的

フロントエンドがどこへ通信するかを整理する。

## 確認済み通信先

| 種別 | 通信先 | 役割 | 状態 |
| --- | --- | --- | --- |
| GraphQL | `https://back.prd.digitalbillder.com/graphql` | メイン業務データ取得・更新 | 確認済み |
| GraphQL | `https://back.purchases.prd.digitalbillder.com/graphql` | 購買・関連機能 | 確認済み |
| 認証 | AWS Cognito OAuth2 | ログイン・認証 | 確認済み |
| 外部遷移 | `https://purchases.digitalbillder.com` | 別サービス画面表示 | 確認済み |
| 計測 | Google Analytics / GTM | アクセス計測 | 確認済み |
| 監視 | LogRocket / Sentry | エラー監視・操作記録 | 確認済み |

## 通信の大まかな役割

- 画面表示用データ取得
- 請求書検索
- 請求書詳細取得
- ユーザー設定更新
- MFA 設定
- 外部サービスへの画面遷移

## 次の確認ポイント

- `/applications` 表示時に最初に呼ばれる API
- 一覧検索時の GraphQL 操作名
- 詳細画面で追加取得するデータ
- 保存や更新時の mutation

## 実測結果

### 一覧画面 `/applications` で実測できた通信

#### 一覧取得の中心 GraphQL

- `InvoicesTotalCount`
- `SearchInvoicesList`

#### 一覧画面で周辺的に呼ばれていた GraphQL

- `LoginTemplate`
- `OptionTableSkeletons`
- `GetCompanyOutputSetting`
- `ElementOriginIds`
- `InvoiceCompanyViews`
- `usersByIds`
- `OfficesByIds`
- `InvoiceTagsByIds`
- `ConstructionTagsByIds`
- `InvoiceTagVisibilitySettings`

#### `SearchInvoicesList` から分かること

- 一覧は `offset` と `limit` を使うページング形式
- 実測時は `offset: 0`, `limit: 50`
- 並び順は `sortMode: submittedAtAsc`
- 検索条件は `SearchInvoicesInput` でまとめて送られる
- 実測時の条件には `condition: "userHandling"` が入っていた

#### `SearchInvoicesList` で取得している情報の傾向

- 請求書本体情報
- PDF リンク
- 工事情報
- 承認ルートログ
- ノート
- タグ
- 依頼情報
- body
- sheets
- 添付ファイルリンク

#### 一覧画面の通信から分かること

- `/applications` は軽い検索画面ではなく、一覧行表示に必要な関連情報も同時取得する
- 件数取得と一覧取得は分かれている
- 検索UIのマスタや表示設定も初期表示時にまとめて読み込んでいる

### 詳細画面再読み込み時に確認できた通信

| 種別 | 通信先 | 内容 |
| --- | --- | --- |
| HTML | `https://digitalbillder.com/invoices/{uuid}` | 詳細画面本体 |
| JS/CSS | `https://digitalbillder.com/assets/...` | フロントエンド本体 |
| GraphQL | `https://back.prd.digitalbillder.com/graphql` | 詳細データ取得の中心 |
| PDF取得 | `invoice-prd-invoice-timestamp.s3.ap-northeast-1.amazonaws.com/...pdf` | 請求書 PDF 原本 |
| 計測 | `googletagmanager.com`, `google-analytics.com` | アクセス計測 |

### 実測で確認できた GraphQL operation

#### 共通・初期化系

- `LoginTemplate`
- `OptionTableSkeletons`
- `GetInvoiceNumberingFormat`

#### 請求書詳細系

- `invoiceWithPdfAnnotations`
- `HasNewerInvoiceFormatLog`
- `getInvoiceEditLock`
- `CanCancelInvoiceForButton`

#### OCR・整合性チェック系

- `InvoiceSharedAttachedFileOcrResult`
- `OcrPriceConsistencyCheck`
- `OcrPaymentAmountCheck`

#### タグ・選択肢系

- `InvoiceTagEdit`
- `InvoiceTagView`
- `InvoiceTagVisibilitySettings`
- `OptionItems`
- `OptionItemAttributeMap`

#### 予実・予算系

- `JobDetailBudgetAllocations`
- `JobCategoryPreviousBudgetAmounts`
- `JobCategoryHierarchyNodes`
- `VisibleJobCategoriesOnPaymentTable`
- `ConstructionBudgetTable`
- `jobCategoryBudgetEditLogs`

### 実測から分かること

- 請求書詳細画面は 1 回の表示でかなり多くの GraphQL を呼ぶ
- 単純な明細画面ではなく、OCR、タグ、PDF注釈、予実管理、予算情報まで同時に読み込む
- PDF 原本は GraphQL のレスポンス本文ではなく、署名付き S3 URL 経由で取得している
- `予実管理` タブを開かなくても、関連データを先読みしている可能性が高い
- `ログ` `アクション` `付箋` `予実管理` の各タブ切替だけでは追加の GraphQL 通信は観測されなかった
- これらのタブ内容は詳細画面の初期表示時に先読みされている可能性が高い
- `ログ` `アクション` `付箋` `予実管理` の各タブ切替だけでは追加の GraphQL 通信は観測されなかった
- これらのタブ内容は詳細画面の初期表示時に先読みされている可能性が高い

### 現時点の整理

- 一覧画面の中心 API は `InvoicesTotalCount` と `SearchInvoicesList`
- 詳細画面の中心 API は `back.prd.digitalbillder.com/graphql`
- 画面表示の重さに影響しそうなのは、請求書本体情報よりも周辺機能の同時取得

## 未確認

- GraphQL の型定義詳細
- 添付ファイル関連のアップロード手順
- 権限制御の API 側仕様
