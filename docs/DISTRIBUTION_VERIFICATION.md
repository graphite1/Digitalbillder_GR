# 配布・Windows portable 検証記録

確認日: 2026-09-06（日本時間）。09-05の記録は履歴として維持する。

## 2026-09-06: v1.0.5公開・旧v1.0.4からの更新確認

- 確定ソース `f946be2`。アプリv1.0.5 / code sequence 5、Windows v1.0.5-r1 / installer sequence 5 / build 1。署名済み旧版を差し替えず新しい配布番号で公開した。
- 処理中断、一覧配置・請求月配色、並行操作・状態表示、管理者用請求月リセット、D付き工種テンプレート・3桁入力、税額±1円調整、税込／税抜表示連動を収録。Python全体320テスト成功。
- 旧公開v1.0.4の署名済みコピーと合成台帳で、旧版の準備・適用→新v1.0.5の署名検証・ダウンロード・準備・適用・起動診断→通常のDB初期化を実行し成功。既存請求、振分金額、手動請求月、旧数字コードID・名称、設定を保持。更新前バックアップの内容も一致。診断は3回ともDBハッシュ不変。新列はDEFAULT 0として追加される。
- 公開サイトのlatestとZIPを実HTTP取得した更新経路も合格。手動更新ZIPの内包`update.zip`と署名manifestが公開コードと一致。公開取得試験の証跡: `作業補助/配布検証/v1.0.5/public-update-20260906T050414Z-2349aa92/result.json`。ローカル候補試験: 同親の`local-update-20260906T050128Z-96132042/result.json`。
- コードZIP: 799,265 bytes、SHA256 `194d45f28ff61a06878fac742428fb59dd1c96e611090f741cfd783c807b0358`。
- Windows ZIP: 88,878,405 bytes、SHA256 `b7eaf40a2a88ffaf3c6d8e87c5466d13fae23fca44ed41ef5c94b062a2a62222`。ビルダーの専用Python 3.14.3、同一runtime fingerprint、ブラウザーabout:blank、起動EXEの台帳初期化が成功。PDF部品はv1.0.4-r2と同じ検証済み専用DLLを維持。
- セットアップEXE: 99,840 bytes、SHA256 `c87792bcafee55b1ea1f10663f5b4299080e5773dc2a0b3797d086db1ea7c84c`。公開サイトからコードZIP・Windows ZIP・署名情報・EXEを取得して、署名済み候補との一致を確認。サイトの最新表示・セットアップリンクもv1.0.5へ更新した。今回の空Sandboxでの新規セットアップ実行は未実施。利用者が既に起動中の仮想環境v1.0.4で手動更新を試験する予定であり、その結果はまだ受領していない。
- 機械登録権限を公開後に無効化してサイトへ反映。同じトークンで管理APIが403となることを確認。実台帳・実VM・ログイン情報・署名済み旧版は変更していない。
- 互換性の範囲: 既存列を維持する加算変更に限りschema 1を維持。新機能使用後の旧版編集は対応外（端数調整した行を旧版で編集すると調整記録との不整合になるため）。詳細は[更新ガイド](UPDATE_GUIDE.md#v104からv105への台帳互換性)。Webの実取得中断、実編集保存、上位者への送信は今回の公開試験の対象外。

## 2026-09-06: Windows版v1.0.4-r2公開・PDF部品不足の修正確認

- アプリコードはv1.0.4・code sequence 4を維持。確定ソース `a8c631b` からWindows build 2・installer sequence 4を作成した。署名済みコードZIPは既存のものをそのまま使用し、公開済みファイルを再署名・置換していない。
- 原因は `msvcp140.dll` の同梱漏れ。同じSandbox・同じ旧配布物でこのDLLだけをアプリ専用runtimeへ補うと、PyMuPDFとPDF描画が成功した。
- 公式Microsoft VC再配布パッケージ14.51.36247.0から、同版の `msvcp140.dll`・`vcruntime140.dll`・`vcruntime140_1.dll` を同梱。ビルドでMicrosoft署名・x64・版一致・ハッシュを検証。DLLの配置・読込みはアプリ専用runtimeに限定し、共有ランタイムやPATHを変更しない。
- セットアップは本配置前にダミーPDFの生成・再読込み・描画と台帳初期化を検証する。失敗時は成功扱いにせず、既存の空でない保存先は上書きせず拒否する。
- Pythonテスト174件、C#セットアップテスト6件成功。候補版Sandboxで初期化・PDF描画・Tk・SQLite再読込み・専用DLL読込み元の検証に合格。再導入拒否の前後で既存全ファイルのハッシュ不変を確認した。
- 別アプリとの共存を確認するため、試験Sandboxだけに公式共有VCランタイムを導入。その状態でもアプリ専用DLLが使用され、PDF試験は合格し、共有DLL3点のハッシュは不変。これより新しい版・別アーキテクチャの共有ランタイムとの実機共存は未検証（ビルダーの異なる版・x86拒否は単体テスト済み）。
- 公開サイトから取得した署名manifestとZIPをアプリの検証処理で照合。ZIPは88,860,764 bytes、SHA256 `76382f58ce4feced1c3901d5b957529231a1e85294e7bb97fdcb97595dda211d`。
- 公開セットアップは99,840 bytes、SHA256 `363973f0c939716ae4666ed62cc89ce6e6020c57599e97fb02edbfeea1b505af`。これを空のSandboxで実行し、12:21に `install-and-runtime-smoke-passed` を確認。追加のアプリPDF描画メソッド試験もTk Canvasの画像1点・240×120・1/1ページ表示で合格した。
- 公開試験の証跡は `作業補助/WindowsTest/runs/20260906-122055-6be41a72/results/`。候補版・共存・再導入保護の証跡は `作業補助/WindowsTest/pdf-candidate/run/results/`。アプリ画面全体の業務操作・実請求・認証・更新復旧は今回の合格範囲に含めない。
- サイトのダウンロード案内とEXEを更新。旧Windows v1.0.4-r1（installer sequence 3）は配布停止し、archive取得404を確認。機械登録用の一時権限も無効化して反映し、同トークンで管理APIが403となることを確認した。
- 通常のコード更新はruntimeを変更しないため、既存環境へ今回のDLLを追加する手段ではない。既存台帳へ新しいZIPを上書きせず、既存環境の移行は別途検証する。今回、利用者の本番台帳・資格情報・開発PCの共有DLLは変更していない。

## 2026-09-06: 修正前のSandbox初回導入で検出した問題

- 空のSandboxで公開v1.0.4-r1セットアップを実行。セットアップ終了コード0。
- 同梱Python 3.14.3、導入構造、Tkウィンドウ、架空SQLite保存・再読込みは合格。PDF部品以外の対象importも成功。
- `fitz` / PyMuPDFは `_extra` 読込みで「指定されたモジュールが見つかりません」。ダミーPDF描画も失敗し、試験全体は不合格。実請求・資格情報は使用していない。
- ゲスト内で `vcruntime140.dll` と `vcruntime140_1.dll` はロード成功、`msvcp140.dll` はロード不可。原因候補だが、補充後の合格をまだ確認しておらず、原因確定とはしない。
- 証跡: `作業補助/WindowsTest/runs/20260906-113324-ee86dc3a/results/` の `install-result.json`、`smoke-result.json`、`pdf-dll-diagnosis.json`。
- WSBの関連付けからは起動できず、`WindowsSandbox.exe "<WSBの絶対パス>"` で起動。ゲストの結果JSONと実行中インスタンスで稼働を確認。
- アプリ画面全体・自動更新・実請求の業務操作は未試験。初回導入が完全に合格したとは扱わない。公開済み配布ファイルは変更していない。

この記録は、`C:/Users/rinnt/Documents/Codex/Digitalbuilder＿GR/作業補助/配布検証/` に保存した結果JSON、テスト結果、Windows画面での確認を集計しています。実請求書の名称・金額・メールアドレスは記載せず、件数と検証結果のみを扱います。実請求1件・PDF添付2点は隔離台帳へ登録しましたが、通常の主台帳は変更していません。

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
