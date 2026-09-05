# 配布・Windows portable 検証記録

確認日: 2026-09-05（日本時間）

この記録は、`C:/Users/rinnt/Documents/Codex/Digitalbuilder_GR-release-check/` に保存した結果JSON、テスト結果、Windows画面での確認を集計しています。実請求書の名称・金額・メールアドレスは記載せず、件数と検証結果のみを扱います。実請求1件・PDF添付2点は隔離台帳へ登録しましたが、通常の主台帳は変更していません。

## 公開版と更新

- 現在のコード公開版は v1.0.3、code sequence 3。
- v1.0.1・v1.0.2は、旧launcherが `__pycache__` を生成して署名台帳検証に失敗する問題のため配布停止。
- `app.py` 起動冒頭の `sys.dont_write_bytecode=True` と、launcherの `-B` / `PYTHONDONTWRITEBYTECODE` で修正。
- `manual-update-result.json` で、サイトからのv1.0.3取得、署名付き更新適用、バックアップ検証、隔離台帳保持を確認。
- システムPythonでkeyring等がない場合の資格情報保存`ModuleNotFoundError`を再現し、Python/Tk/依存パッケージ/Headless browser同梱で解消。
- メインテスト145件が成功。

## Windows portable r2

- 対象: Windows x64、v1.0.3、distribution sequence 2、code sequence 3。
- ZIPサイズ: 217,504,939 bytes。
- SHA-256: `2a70134e512689ccc5c6893ab5e48cb1285df05071178466431cc03dbb6c9bd1`。
- `portable-setup-result.json` で公開ZIP検証、ホストPythonをPATHから除外した起動、ホストPython環境の無視、新規台帳初期化を確認。
- Python、Tk、アプリ依存パッケージ、Headless browserを同梱する構成で、システムPythonに依存しない起動を検証。
- 公開ZIPの起動.batを通常起動し、Computer Useで同梱runtime/python.exeの「請求一覧」と登録済み1行を確認。起動時のPATHはSystem32だけとし、無効なホストPython環境変数が設定されていても起動できた。

## portable実測

`live-import-result.json` と `live-restart-result.json` は既存`.venv`を使った別試験の証跡であり、portableの成否の根拠には使用していません。portableの根拠は、以下の`portable-*result.json`だけです。

- `portable-live-import-result.json`: v1.0.3取得、資格情報保存・Vault読戻し、81候補行、CSV/ZIP取得、重複候補非表示、隔離台帳の実請求1件・添付2点、PDFプレビュー表示、主台帳不変を確認。`restart_check` はこの実行では `false`。
- `portable-live-restart-result.json`: 再起動後の保存資格情報読込、隔離台帳の1請求・添付2点、PDFプレビュー表示、主台帳不変、`restart_check=true` を確認。
- `portable-update-result.json`: 同梱runtimeから内蔵の「更新を確認」を実行し、公開サイトでv1.0.3最新版を検出。ブラウザー不要で完了。

## 範囲と未検証事項

- 実測はWin11 x64のこの1台のみ。別PC、ARM、企業の制限環境は未検証で、全PCでの動作を保証しない。
- 実測で使用したのはWin11 x64の1台のみで、全PCの動作を保証しない。
