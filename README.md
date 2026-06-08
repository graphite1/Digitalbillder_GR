# Digital Billder請求書 補助台帳アプリ

Windowsローカルで動作する、Digital Billder請求書向けの補助台帳アプリです。
Digital Billderを置き換えず、CSVとzip内PDF原本をローカルで保存・検索・集計します。

詳細な機能仕様、画面仕様、制限値は [SPECIFICATIONS.md](/C:/Users/s-yobimoto/Codex_Script/DigitalBuileder_GR/SPECIFICATIONS.md) を参照してください。

## アプリ方針

- Digital Billder側の登録状態、承認状態、支払状態は管理しません。
- OCR、LLM、AI分類、外部AI API、PDF本文解析、外部通信は使いません。
- CSVに含まれる請求情報と、zip内PDF原本をローカルで安全に扱います。
- 請求一覧、詳細確認、添付PDF参照、メモ、請求月管理、Excel出力を補助します。

## 請求月ルール

請求月は請求日から自動判定します。

- 毎月10日から翌月9日までを同じ請求月とします。
- 例: 2026-04-10 から 2026-05-09 までの請求日は 2026年5月請求です。
- 例: 2026-05-10 から 2026-06-09 までの請求日は 2026年6月請求です。
- CSV取込時は、各行の請求日ごとに請求月を判定します。
- CSV内に複数の請求月が混在していても、行単位で請求月を保存します。
- CSV取込では請求日を必須とし、空欄の場合は取込エラーとして請求月を自動記入しません。
- 例外対応のため、請求一覧画面の手動請求月変更機能は残します。

## 安全運用

`data/` 配下にはSQLite DB、PDF原本、Excel出力が保存されます。
請求情報や取引先情報を含むため、共有フォルダやGit管理対象には置かないでください。

CSV + zip取込では、zipサイズ、PDFサイズ、PDF件数、ファイル名の安全性を確認します。
PDF原本は `data/originals` 配下に保存し、アプリ内のPDF表示・PDFを開く操作もこの配下のPDFだけを対象にします。

Excel出力では、CSV由来の文字列がExcel数式として実行されないように無害化します。

## 画面方針

請求一覧画面は、毎月の確認作業を短時間で進めるための画面です。

- 表示件数と請求金額合計を確認できるようにします。
- 工事、請求月、取引先、請求日、並び順で絞り込みできるようにします。
- 行選択を前提とする操作は、未選択時に実行できないようにします。
- 詳細確認、メモ編集、請求月変更、添付PDF表示を行単位で実行します。
- 画面の大改造より、確認にかかる手数を減らす改善を優先します。

## CLI

```bash
python app.py --init-db
python app.py --preview --csv path/to/sample.csv --zip path/to/sample.zip
python app.py --import --csv path/to/sample.csv --zip path/to/sample.zip
python app.py --export --month 2026-05
python app.py
```

## 開発ルール

- 最小差分で修正します。
- 無関係な変更、リファクタリング、命名変更、勝手な最適化は行いません。
- 不明点がある場合は、実装前に確認します。
- 既存コードの動作を壊さないことを優先します。
- 小さなphaseが完了した時点でコミットします。
- 定期的にこのREADMEを確認し、アプリ方針と実装内容がずれていないか確認します。

## 開発時の確認

構文確認:

```bash
python -m compileall app.py invoice_manager
```

通常起動:

```bash
python app.py
```
