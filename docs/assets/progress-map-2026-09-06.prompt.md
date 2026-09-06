# 進度マップの生成記録

- 生成日: 2026-09-06
- 使用方法: 組込み `image_gen`（CLI/APIフォールバックは不使用）
- 出力: [progress-map-2026-09-06.png](progress-map-2026-09-06.png)
- 入力画像: なし。進度の説明から新規生成。
- 確認: イラスト内の日本語、状態区分、送信主体、OBIC7保留を目視確認。

## 最終プロンプト

```text
Use case: infographic-diagram. Create a polished Japanese illustrated development progress map for the desktop invoice and construction cost-management app Digitalbuilder GR. Landscape high resolution, spacious editorial infographic on warm white paper, navy crisp Japanese sans serif typography, soft flat illustrations with gentle depth: invoice sheets, PDF document, folders, calculator, small construction site crane and worker figurines, cost chart. Professional and friendly, easily readable for a Japanese construction manager. No screenshots, no photorealism. Exact large title: 「Digitalbuilder GR 開発進度マップ」. Subtitle: 「v1.0.4｜2026年9月6日時点」. Three clear main sections across the page, with status badges and icons, enough whitespace and large readable Japanese text. LEFT green completed zone heading 「実装済み」 checkmark badge, show invoice and magnifying glass illustrations. Text bullets exactly 「請求の取込・PDF確認」 「工種の手動振分」 「予算登録・実績集計」 「最終原価見込の基本機能」. Small note within left zone 「金額・集計の正確さには課題あり」. CENTER amber workbench zone heading 「次に固めること」 wrench/checklist illustrations, text 「税抜額の整合」 「誤請求の除外」 「工種対応の正確さ」. Put prominent small label 「優先候補」, never mark completed. RIGHT pale blue blueprint zone heading 「これから実装」 dotted outlines, text 「送信完了の確認」 「送信済みデータを原価へ反映」 「予算比較・最終着地へ連動」 「請求月ルールの個人設定」. All right features explicitly labeled 「未実装」. In a thin separate strip beneath those zones include two muted state cards: 「Webへの編集保存：無効・検証待ち」 and 「OBIC7への入力・連携：保留」 with pause icon. Bottom a clearly separated illustrated roadmap ribbon heading 「目指す運用」 and exact sequence 「アプリで提出準備」 → 「本人が最終確認・送信」 → 「送信済みを原価・着地へ反映」. Final connection is dotted future line; distinguish human-controlled sending with human hand/check icon. Small footer 「直近はテスト環境を整備中。アプリの新機能追加はなし。」. Requirements: faithful status, no invented progress percentages, no automatic submission or approval, do not imply planned features work now; OBIC7 shown as on hold not an active integration; balanced beautiful illustrative layout rather than dense spreadsheet.
```
