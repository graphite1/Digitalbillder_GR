# Digital Billder請求書 補助台帳アプリ

Windowsローカルで動作する、Digital Billder請求書向けの補助台帳アプリです。
Digital Billderを置き換えず、CSVとzip内PDF原本をローカルで保存・検索・集計します。

## CLI

```bash
python app.py --init-db
python app.py --preview --csv path/to/sample.csv --zip path/to/sample.zip --month 2026-05
python app.py --import --csv path/to/sample.csv --zip path/to/sample.zip --month 2026-05
python app.py --export --month 2026-05
python app.py
```

## 固定方針

OCR、LLM、AI分類、外部AI API、PDF本文解析、外部通信は使いません。
Digital Billder側の登録状態・承認状態・支払状態は管理しません。

## 安全運用

`data/` 配下にはSQLite DB、PDF原本、Excel出力が保存されます。
請求情報や取引先情報を含むため、共有フォルダやGit管理対象には置かないでください。

CSV + zip取込では、zipサイズ、PDFサイズ、PDF件数、ファイル名の安全性を確認します。
PDF原本は `data/originals` 配下に保存し、アプリ内のPDF表示・PDFを開く操作もこの配下のPDFだけを対象にします。

Excel出力では、CSV由来の文字列がExcel数式として実行されないように無害化します。
