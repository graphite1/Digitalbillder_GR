# PDFマーク座標方式 変更方針

## 目的

確認用PDF出力時に、PDFプレビュー上で配置した工種マークと出力PDF上のマーク位置がずれる問題を減らします。

現行方式は、PDFを画像化したプレビュー上の表示サイズに対する比率座標を保存し、出力時にPDFページ座標へ再変換しています。
この方式では、PDFの回転、CropBox / MediaBox、画像化時の丸め、描画エンジン差によりずれが発生しやすくなります。

今後は、マーク位置をPDFページ上の実座標で保存し、プレビュー表示と確認用PDF出力の両方で同じPDF座標を基準にします。

## 変更方針

- PDFマークの保存座標を、比率座標中心からPDF実座標中心へ変更します。
- PDF実座標は、PyMuPDFのページ座標系に合わせたポイント単位で保存します。
- プレビュー表示時は、保存済みPDF座標を現在のズーム倍率と表示位置へ変換してCanvasに描画します。
- マーク作成・移動時は、Canvas上の位置をPDFページ座標へ変換してDBに保存します。
- 確認用PDF出力時は、保存済みPDF座標をそのまま使ってマークを描画します。
- 原本PDFは直接編集せず、確認用PDFは引き続き別ファイルとして出力します。

## DB方針

`pdf_marks` にPDF実座標用の列を追加します。

```sql
ALTER TABLE pdf_marks ADD COLUMN x_pt REAL;
ALTER TABLE pdf_marks ADD COLUMN y_pt REAL;
ALTER TABLE pdf_marks ADD COLUMN page_width_pt REAL;
ALTER TABLE pdf_marks ADD COLUMN page_height_pt REAL;
```

各列の意味:

- `x_pt`: PDFページ上のX座標
- `y_pt`: PDFページ上のY座標
- `page_width_pt`: 配置時点のPDFページ幅
- `page_height_pt`: 配置時点のPDFページ高さ

既存の `x_ratio` / `y_ratio` は互換用に残します。
既存データは初回表示またはマイグレーション時に、保存済み比率とページサイズから `x_pt` / `y_pt` を補完します。

## 座標変換方針

### プレビュー表示

1. PDFページをPyMuPDFで読み込みます。
2. ページサイズ `page.rect.width` / `page.rect.height` を取得します。
3. 保存済み `x_pt` / `y_pt` を使用します。
4. Canvas表示位置は以下で算出します。

```text
canvas_x = image_x + x_pt * zoom
canvas_y = image_y + y_pt * zoom
```

### マーク作成

1. Canvasクリック位置からPDF画像左上を引きます。
2. ズーム倍率で割ってPDF座標へ変換します。

```text
x_pt = (canvas_x - image_x) / zoom
y_pt = (canvas_y - image_y) / zoom
```

### マーク移動

移動後のCanvas上の中心座標を、作成時と同じ式でPDF座標へ変換して保存します。

### 確認用PDF出力

出力時は `x_pt` / `y_pt` を直接使います。
`x_ratio` / `y_ratio` からの再計算は、互換データ以外では行いません。

## 実装対象

- `invoice_manager/db.py`
  - `pdf_marks` の追加列をスキーマに追加
  - 既存DB向けマイグレーション追加

- `invoice_manager/repositories.py`
  - PDFマーク作成時に `x_pt` / `y_pt` を保存
  - PDFマーク位置更新時に `x_pt` / `y_pt` を更新
  - 既存マーク取得時に `x_pt` / `y_pt` が空の場合の補完方針を追加

- `invoice_manager/ui/invoice_detail_window.py`
  - Canvas座標とPDF座標の変換処理を追加
  - マーク描画を `x_pt` / `y_pt` 基準に変更
  - マーク作成・移動保存をPDF座標基準に変更

- `invoice_manager/services/export_marked_pdf.py`
  - 確認用PDF出力を `x_pt` / `y_pt` 基準に変更
  - 互換データのみ `x_ratio` / `y_ratio` から補完

- `SPECIFICATIONS.md`
  - PDFマーク座標はPDFページ実座標で扱う旨を反映

## 既存データ対応

既存の `pdf_marks` には `x_ratio` / `y_ratio` しかありません。
そのため、以下のどちらかで対応します。

### 案A: 読み込み時補完

マーク取得時に `x_pt` / `y_pt` が空なら、対象PDFページの幅・高さから一時的に計算します。

メリット:

- DB更新を最小化できる
- 既存データを壊しにくい

懸念:

- 取得処理がPDFページ情報に依存する
- Repository層だけではPDFサイズを取得しにくい

### 案B: マイグレーション補完

起動時または専用処理で、既存マークの `x_pt` / `y_pt` を一括補完します。

メリット:

- 以後の表示・出力処理が単純になる
- 互換分岐を減らせる

懸念:

- PDFファイルが存在しない既存データは補完できない
- 起動時処理が重くなる可能性がある

推奨は案Aです。
まず表示・出力時に互換変換を行い、マークを移動または保存したタイミングで `x_pt` / `y_pt` を確定保存します。

## 実装コスト

- DB列追加とマイグレーション: 小
- Repository修正: 中
- プレビュー座標変換修正: 中
- 確認用PDF出力修正: 小から中
- 既存データ互換処理: 中
- 実PDFでの確認: 中

合計見積もりは 2日から3日程度です。
PDF回転ページやCropBox差分まで確認対象に含める場合は、追加で1日程度見ます。

## 懸念点

- 回転ページではPyMuPDFのページ座標と見た目の向きの扱いを確認する必要があります。
- CropBoxとMediaBoxが異なるPDFでは、ページ座標の基準が想定とずれる可能性があります。
- 既存データのマークは、比率座標からの補完になるため、過去に発生していたずれを完全には復元できません。
- プレビュー上のバッジサイズとPDF出力上のバッジサイズは、座標とは別に調整が必要です。
- 文字描画はCanvasとPDFでエンジンが異なるため、位置中心は揃えられても文字幅は完全一致しない可能性があります。

## 段階的な進め方

1. DBにPDF実座標列を追加します。
2. PDFページ座標とCanvas座標の変換ヘルパーを追加します。
3. 新規マーク作成をPDF実座標保存へ変更します。
4. マーク移動をPDF実座標更新へ変更します。
5. プレビュー描画をPDF実座標基準へ変更します。
6. 確認用PDF出力をPDF実座標基準へ変更します。
7. 既存の `x_ratio` / `y_ratio` データの互換表示を確認します。
8. 実PDFで、プレビュー位置と確認用PDF出力位置を比較します。

## MVP完了条件

- 新規配置したマークが、確認用PDF出力で同じ位置に出ること。
- 移動したマークが、確認用PDF出力で移動後の位置に出ること。
- 既存の比率座標マークも表示・出力できること。
- 原本PDFに変更が入らないこと。
- `python -m compileall app.py invoice_manager` が成功すること。
