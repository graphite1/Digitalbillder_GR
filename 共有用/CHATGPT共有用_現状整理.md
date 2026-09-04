# ChatGPT共有用 現状整理

> 2026年7月2日時点の履歴資料です。現行仕様ではありません。最新の内容はルートの `SPECIFICATIONS.md` と `README.md` を参照してください。

## 概要

対象は「Digital Billder請求書 補助台帳アプリ」です。
Windowsローカルで動作し、CSVとzip内PDF原本をローカル保存して管理する補助台帳アプリです。

外部通信、OCR、AI分類、LLM、PDF本文解析は使わない方針です。

## 現在までの実装状況

### 1. 工種マーク機能

- 請求詳細画面のPDFプレビュー上に、工種コード振分に対応するマークを配置できます。
- 既存マークはドラッグで移動できます。
- マーク一覧表示と個別削除ができます。
- マーク表示は、数値連番ではなく工種コードベースの色付きバッジ表示です。

### 2. 確認用PDF出力

- 請求詳細画面から、選択中PDFに対して「確認用PDF出力」ができます。
- 原本PDFは直接編集せず、`data/exports` 配下へ別ファイルとして出力します。
- 出力時にはPDF上へ工種コードバッジを重ねます。

### 3. 仕様書・方針書

- `README.md` 更新済み
- `SPECIFICATIONS.md` 更新済み
- `PDF_MARK_COORDINATE_PLAN.md` 追加済み

## 現在の課題

確認用PDF出力時に、PDFプレビュー上で配置したマーク位置と、出力PDF上のマーク位置にずれが出ることがあります。

現行実装は以下です。

- 画面側:
  - PDF画像プレビュー上で `x_ratio` / `y_ratio` を使って表示
- 出力側:
  - `x_ratio` / `y_ratio` をPDFページ座標へ再変換して描画

この構成だと、PDFの回転、CropBox / MediaBox、描画エンジン差、丸め誤差でずれが出やすいです。

## 次の実装方針

`PDF_MARK_COORDINATE_PLAN.md` に整理済みです。

方針の要点:

- 比率座標 `x_ratio` / `y_ratio` 中心の保存をやめる
- PDFページ上の実座標 `x_pt` / `y_pt` を保存する
- プレビュー表示も確認用PDF出力も、同じPDF座標を基準にする
- 既存データは互換変換で扱う

## 現時点のGit状態

直近コミット:

- `3418979 Remove local launch batch`
- `c14ac99 Add marked PDF export and coordinate plan`
- `e61888b Add PDF mark placement to invoice detail`

`main` と `origin/main` は同期済みです。

## ワークツリー状態

ソースコードの未コミット差分はありません。

未追跡で残っているのはビルド生成物です。

- `DigitalBuileder_GR.spec`
- `build/`
- `dist/`

## 配布物

PyInstaller による配布zip作成済み:

- `dist/DigitalBuileder_GR_20260612.zip`

## ChatGPTへ依頼したい内容の候補

- `PDF_MARK_COORDINATE_PLAN.md` の方針に沿って、PDF実座標保存へ移行する実装レビュー
- `x_pt` / `y_pt` 移行時のDBマイグレーション案の妥当性確認
- PDF回転ページやCropBox差分への対処方針レビュー
- 既存の `x_ratio` / `y_ratio` データ互換処理の設計レビュー
