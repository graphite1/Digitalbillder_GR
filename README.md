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
