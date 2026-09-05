# 配布・Windows portable 検証記録

確認日: 2026-09-05（日本時間）

この記録は、`C:/Users/rinnt/Documents/Codex/Digitalbuilder_GR-release-check/` に保存した結果JSON、テスト結果、Windows画面での確認を集計しています。実請求書の名称・金額・メールアドレスは記載せず、件数と検証結果のみを扱います。実請求1件・PDF添付2点は隔離台帳へ登録しましたが、通常の主台帳は変更していません。

## 公開版と更新

- 現在のコード公開版は v1.0.4、code sequence 4。以下のv1.0.3記録は旧同梱版の検証履歴。
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

## 軽量セットアップ v1.0.4 r1

- ソース: `0c8c95d031d22e182292f92e760b4f40c536834b`。GitHubへpush後に生成。
- コード sequence 4、Windows distribution sequence 3、build 1。
- セットアップEXE: 99,328 bytes（97 KiB）。SHA-256: `0b16b67bfae64178ea4b3af259a3d77fc085df52a5debb25f31a479ca3ddd66d`。
- 初回取得ZIP: 88,623,651 bytes（約84.5 MiB）。旧r2の217,504,939 bytesから約59.3%削減。
- ZIP SHA-256: `32fcd2f52c67d6b72d18707e6f83267514e81ceb69175ab0f2b3dc7403c7e0cc`。
- 公開サイトからEXEを取得し、固定ハッシュを確認して隔離フォルダーへ実導入。PythonをPATHから除き、無効なホストPython環境変数を設定した状態でも成功。
- `light-setup-result.json`: 導入exit 0、起動EXEによる台帳初期化、専用ショートカット、一時作業フォルダー・ZIPの削除、既存データがある導入先の拒否を確認。ショートカットのリンク先も実EXEと一致。検証スクリプトの「空のbrowserディレクトリーも不可」という誤った条件を修正し、導入済み環境で残りの確認を継続した。
- Computer Useでセットアップ画面の保存先・デスクトップ用ショートカット欄・導入ボタンを確認。
- `light-live-import-result.json`: 実導入した1.0.4の同梱Pythonと既存Edgeから、資格情報の保存・Vault読戻し、81候補、CSV/ZIP取得、1請求・添付2点、重複候補の非表示、PDFの内部描画を確認。Digital BillderへのCSV/ZIP取得は1回。
- `light-live-restart-result.json`: 別プロセスで資格情報・1請求・添付2点・PDF表示を再確認。両実行とも通常台帳のSHA-256前後一致。
- `light-update-result.json`: アプリの更新確認ボタンで公開v1.0.4を検出。ブラウザー画面は不要。
- `light-upgrade-result.json`: 別の隔離済み旧版1.0.3へサイトの署名済み1.0.4を適用。固定launcherで起動確認が成功し、更新前バックアップと台帳の内容一致を確認。
- Pythonテスト158件、セットアップC#試験5群、サイト署名9件・multipart完了試験・型検査・本番ビルドが成功。
- 公開用の一時管理トークンを無効値・過去の有効期限へ変更し、サイト再公開後に旧トークンが403になることを確認。

実行に必要なPython・依存部品・台帳は残す。削除対象はセットアップが所有する一時ZIPと作業フォルダー。Edgeが導入されていない場合は専用ブラウザーを追加取得するため、この85 MiBとは別の通信が発生する。

## 範囲と未検証事項

- 実測はWin11 x64のこの1台のみ。別PC、ARM、企業の制限環境は未検証で、全PCでの動作を保証しない。
- Edgeのない環境での追加ダウンロード分岐は単体試験で確認し、別PCでの実測は未実施。実機で使ったのは既存Edge。企業ポリシーでEdgeの自動操作が制限された環境は未確認。
- EXEへのAuthenticode署名は未導入。Windowsの配布元表示・SmartScreenの評価は配布先の環境に依存し、警告回避のためにOS設定を変更する処理はない。
