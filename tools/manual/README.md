# 操作マニュアル生成

`docs/電子請求書管理_操作マニュアル.docx` の生成元です。

## 更新手順

1. `py tools/manual/create_screenshots.py` で架空データの画面画像を更新します。
2. `py tools/manual/create_manual.py` でDOCXを生成します。
3. DOCXをPDF化し、全ページの余白、改ページ、文字切れを画像で確認します。

画面画像、切り抜き、確認用レンダーは `build/manual_work/` に生成され、Gitには含めません。
生成されたDOCXとPDFだけを `docs/` で管理します。
