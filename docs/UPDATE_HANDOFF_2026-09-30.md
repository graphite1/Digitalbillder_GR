# 署名PCへの引継ぎ：過去実績の待機時間・並列数設定を更新配布する

## 依頼と現在の到達点

利用者は「待機時間を延ばして対応」「設定できるように」「並列数もマックス10まで」と依頼し、実装後に「アップデートできるように上げといて」と公開を指示した。このPCに既存署名鍵がないことを確認し、「署名鍵のあるPCで続ける」と回答している。署名PCでの配布作成・サイト公開を改めて承認確認する必要はない。

修正は通常アプリの `origin/main` に保存済み。実装コミットは `47084d5`。この引継書と最新のAGENTS.mdも取得すること。公開・署名・利用者アプリへの適用は未実施で、現在の公開版は変更していない。

## 確定した変更と検証

- 過去実績画面で画面の待機時間30・60・90・120・180・300秒、並列数1〜10を選び、次回も記憶する。未設定時は60秒・3並列。
- 通常の追加・変更確認と全件再検証に開始時の設定を渡す。待機はログイン・一覧・詳細画面の各段階に適用し、処理全体の制限ではない。CSV生成完了の待機は既存最低180秒を維持し、300秒設定時のみ延長する。
- 実行中は設定欄を無効にし、完了・失敗・中止後に戻す。単件直読と複数件の独立ブラウザー読取り、設定件数より対象が少ない場合に対応する。確認済み明細の再利用、工事別範囲、失敗時に既存履歴を維持する処理は継続する。
- 通常新着・振分確認・ブラウザー選択は従来どおり。待機延長で通信許可待ちや元の詳細エラーが解決したと断定しない。300秒設定時は同期画面待機が終わるまで中止の反映を待つ場合がある。
- 隔離台帳・模擬請求の関連71試験が成功。最大10同時読取り、設定の復元、両取得ボタンの設定伝播、30秒を超える表示待ち、期限超過、中止、失敗時の履歴保全を確認。画面幅880/1120で設定欄を確認し、独立レビューも実施した。
- 全体428試験（skip 4件）の初回は本変更外の更新試験3件が失敗。隔離用台帳指定の環境変数が欠損台帳試験へ影響したため指定を除き、失敗した3件を個別再実行してすべて成功した。署名破損試験とWindowsの移動拒否は原因未確定で、全体一括再実行の合格とは扱わない。詳しくはSTATUS_AND_ISSUES.mdを参照。
- 配布対象の列挙を実確認し、70ファイルに新しい `invoice_manager/services/history_import_options.py` と変更した画面・読取りサービスが含まれる。実台帳・実Webでの再取込はしていない。

## 今回このPCで公開できない理由

Windows資格情報ストアを読み取り専用で確認し、サービス `Digitalbuilder_GR/release-signing`、鍵ID `release-2026-01` は不在だった。主担当とGPT-6.1 Solの検証担当で独立に確認している。

`tools/build_release.py` の `load_or_create_signing_key()` は鍵不在時に別の鍵を自動作成する。`--public-key-only` もこの関数を呼ぶため、存在確認に使わない。既存鍵の存在と、派生した公開鍵が `updater/config.py` の `TRUSTED_PUBLIC_KEYS` に一致することを先に確認する。秘密鍵・資格情報をチャット、ログ、ファイル、Gitへ出力しない。

署名PCでも次の読取り確認を行い、不在または不一致ならビルダーを実行しない。

```powershell
$signingCheck = @'
from keyring.backends.Windows import WinVaultKeyring
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from updater.security import b64url_decode, b64url_encode
from updater.config import TRUSTED_PUBLIC_KEYS
stored = WinVaultKeyring().get_password("Digitalbuilder_GR/release-signing", "release-2026-01")
if not stored:
    raise SystemExit("既存署名鍵がありません。ビルダーを実行しないでください。")
private = Ed25519PrivateKey.from_private_bytes(b64url_decode(stored, "stored key"))
public = b64url_encode(private.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw))
if public != TRUSTED_PUBLIC_KEYS.get("release-2026-01"):
    raise SystemExit("既存利用者の信頼する鍵と一致しません。")
print("既存署名鍵あり・信頼する公開鍵との一致確認済み")
'@
& .\.venv\Scripts\python.exe -c $signingCheck
if ($LASTEXITCODE -ne 0) { throw "署名確認に失敗しました。公開処理は再開しないでください。" }
```

## 公開状態と実行環境（2026-09-30に実確認）

- 署名付き最新情報の署名検証に成功。公開版はv2.0.3／コード配布番号12、公開日2026-09-08T23:47:00Z、有効期限2026-10-08T23:47:00Z、鍵ID `release-2026-01`。
- 公開コードの実行環境識別値は `python=3.14;platform=win32;arch=amd64;deps=f78b1cf645ef9cc938eae6f7`。この開発PCは `python=3.13;platform=win32;arch=amd64;deps=f78b1cf645ef9cc938eae6f7` で、Python版が異なる。開発PCの識別値のまま署名すると既存配布環境で更新が拒否される。署名PCで検証済みの配布runtimeを使い、その識別値を照合する。検証していない値の強制指定で互換性を装わない。
- 配布サイトは一般公開、現在の所有者アクセスあり、Sites版15。サイトIDは `appgprj_6a9bdd917ed0819184b4ad6872323e59`。
- 配布先: https://digitalbuilder-gr-updates.rinntyu2000.chatgpt.site
- 最新情報: https://digitalbuilder-gr-updates.rinntyu2000.chatgpt.site/api/releases/latest
- v2.0.4／コード配布番号13は次版候補にすぎない。非公開を含む配布履歴を署名PCで確認し、未使用の番号を確定する。Windows版専用番号も別途確認する。

## 署名PCでの再開手順

1. 未保存変更を保護したうえで通常アプリのGitを最新にする。本書、AGENTS.md、STATUS_AND_ISSUES.md、UPDATE_GUIDE.md、DISTRIBUTION_VERIFICATION.mdを読む。既存の公開物や署名を作り直して置換しない。
2. 上記の既存署名鍵確認と、検証済み配布runtimeの識別値確認を完了する。配布履歴を照合し、新版番号・コード番号・Windows版番号と変更履歴を確定する。
3. version.pyと公開準備の記録を更新し、必要な検証、コミット・プッシュを行う。確定ソースから既存ビルダーで署名コード更新を作成し、既存鍵で署名されたこと、ZIP内の許可対象と新モジュール、サイズ・SHA256を検査する。既存の合格試験を理由なく全件再反復しない。
4. UPDATE_GUIDE.mdに従い、検証済みの公式VC runtimeからWindows同梱版・軽量セットアップを作成する。開発の.venvや実台帳を含めない。配布環境のみで起動・新設定欄の表示・設定保持を確認する。
5. 同じ配布サイトへ新版のコード更新・Windows同梱版・セットアップ・変更履歴・案内を登録して公開する。管理操作は所有者限定、公開ダウンロードの範囲は維持する。Sitesのスキル手順に従い、サイトソースを復元するときも開発本体内の作業補助/配布サイトを拠点とする。
6. 公開された実ファイルを取得し、署名・サイズ・ハッシュを確認する。作業補助/配布検証内の一意な隔離先と合成台帳を使用し、更新準備、バックアップ、適用、実際のlauncher起動診断、起動版、設定保持を確認する。実Webの全件取得や実績履歴置換を配布検証に使わない。
7. STATUS_AND_ISSUES.mdとDISTRIBUTION_VERIFICATION.mdへ、実際の版・番号・ハッシュ・公開先・検証結果を記録して保存する。公開完了と、別PCの利用者アプリへの適用済みを区別して報告する。

合成台帳による検証は `verify_release_envelope` → `stage_update` → `application_lock` 内の `activate_pending` → `resolve_active_release` の順で行う。起動確認には `launcher.run_release_healthcheck` を使い、合成DBの確認用値、バックアップの `quick_check`、起動版・番号、pendingの削除を照合する。

## 署名PCで開始する際の依頼文

DBGRの更新配布を続けてください。Gitを最新にしてdocs/UPDATE_HANDOFF_2026-09-30.mdとAGENTS.mdを読み、実装済みの待機時間・最大10並列設定を既存署名鍵で新版として公開してください。公開は承認済みです。署名鍵と配布runtimeの一致を先に確認し、更新コード・Windows同梱版・セットアップ・配布サイトの変更履歴まで反映し、公開物の取得と隔離台帳での更新・起動を検証してください。今後の担当モデルはGPT-6.1 Solを基本、軽い作業はLuna、難しい判断はGPT-6.0 Astraです。
