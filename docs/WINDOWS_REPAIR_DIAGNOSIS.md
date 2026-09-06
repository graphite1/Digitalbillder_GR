# Windows修復エラーの特定結果

確認日: 2026-09-06。原因調査の後、利用者の修復承認を受けて実行。再起動は自動実行しない。

最新の修復結果（09:43）: 利用者の09:21の再起動後、残っていたHyper-V DLLの不整合1件をDISMで修復した。修復後のImageHealthStateはHealthy、SFCは終了コード0で「整合性違反を検出しませんでした」。Windowsの整合性修復は完了。Sandbox・Hyper-Vの有効化と起動確認は別途判定する。

修復後の機能有効化は、管理者確認が「ユーザーによって取り消されました」で終了したため未実行。次回は管理者PowerShellで `tools/windows_test_environment/Enable-HostFeatures.ps1` を実行し、機能の状態を確認する。修復済みのDISM・SFCを理由なく繰り返す必要はない。以前の `feature-status.json` は09:12の失敗記録のままで、修復後の有効化結果ではない。

再開用のローカル補助スクリプトで機能有効化を呼び出す相対パスに誤りがあったため修正した。Windows修復とSFCは完了していたので、それらを繰り返さず、有効化スクリプトだけ直接実行する。`post-reboot-status.json` の最終failedはこの呼出しエラーを表し、同記録の `health_after: Healthy` と `sfc_exit_code: 0` を区別する。

## 承認後の修復経過

- 08:52〜08:53: 検証済み修復元を指定したDISMを実行。CBSログで管理ファイル1件の修復成功を確認し、正常名のMUMが生成された。異常名の元ファイルは残している。
- 同じ実行で別のファイル内容の不整合160件を検出。ローカルの2ファイルだけでは修復できず、全体の終了コードは0x800f0915だった。したがって「修復元が全く使えなかった」わけではない。
- 生成されたMUMのSHA256を再照合。元ファイルの所有者・アクセス権を新しいMUMへ適用し、セキュリティ記述子の完全一致を確認。フォルダー全体の所有権・アクセス権は変更していない。
- 08:56〜09:10: Windows Updateを使うDISMを実行。CBSログで残り160件の修復成功、DISM終了コード0を確認。続くSFCも09:11に終了コード0。該当時間のCBS `[SR]` に `Cannot repair` はなく、`Repair complete` を確認した。
- 09:12: Sandbox有効化を再試行したが0x80073712で失敗。今度は `amd64_hyperv-vmuidevices_31bf3856ad364e35_10.0.26100.1742_none_1944447b72a92234/vmuidevices.dll` の `Unexpected compression state` が具体的原因として記録された。Hyper-V有効化にはまだ進んでいない。
- 09:14〜09:16: DISM ScanHealthを実行。検査コマンドは終了コード0だが、結果は「コンポーネント ストアは修復できます」。CBSで上記DLLの不整合1件、管理ファイルの不整合0件を確認した。終了コード0を「破損なし」と読み替えない。
- CBSの `RebootPending` キーの存在を確認。自動再起動はしていない。**現在は大部分修復済み・残り1件・再起動待ちであり、完全復旧やSandbox起動成功ではない。**

実行結果は `作業補助/WindowsTest/host/` の `targeted-repair-status.json`、`completion-repair-status.json`、`feature-status.json`、`feature-corruption-status.json` と対応ログに保存。

### 再起動後に再開する手順

1. 利用者が作業を保存し、Windowsを通常再起動する。
2. OSビルド、CBSの再起動待ち状態、残りの不整合を再確認する。必要なら、今回初めて検出されたDLLを対象に標準DISM修復・SFCを行う。
3. SandboxとHyper-Vの有効化を再試行し、必要な再起動後にSandboxの初回導入試験を行う。
4. 同じ圧縮状態エラーが再発する場合は、適合する完全なWindows修復元、またはアプリとファイルを保持するWindows修復インストールを検討する。圧縮フラグのレジストリ変更、WinSxSの手動削除、別版DLLの流用は行わない。

以下は初回調査時の証拠と検討案。実行状況は上記経過を優先する。

## 結論

**直接の未解決原因は、Windows管理ファイルの名前1文字の異常。中身が正常な候補は、このPC内に残っている。** 前回の「修復用ISOが必要」という説明は選択肢を狭めすぎていた。ISOを取得する前に、検証済み候補を使った局所的な修復を試す余地がある。

対象PCはWindows 11 Pro、25H2、x64、日本語、OSビルド26200.9168。レジストリのProductNameだけはWindows 10 Proと表示するが、Win32_OperatingSystemのCaptionと実ビルドを確認した。

## 確認できた証拠

| 段階 | 記録 | 意味 |
| --- | --- | --- |
| Sandbox有効化 | 08:06:17、0x80073712 | Windowsの追加機能コンテンツ展開に失敗 |
| DISM内部診断 | 08:13:10、0x800f0831 | 特定パッケージのmanifest（部品構成を示す管理ファイル）が見つからない |
| DISM修復結果 | 08:27:27、0x800f0915 | 修復コンテンツ不足で全体としては失敗 |
| 修復の内訳 | 検出3,600、修復3,599 | ログ上、ファイル内容2,622件と属性977件は修復。管理ファイル1件が残った |

根拠は `C:/Windows/Logs/CBS/CBS.log` と `C:/Windows/Logs/DISM/dism.log`。件数はCBSの不整合カウントであり、「3,600個のアプリが壊れた」という意味ではない。今回、修復後の再スキャンまでは行っていない。

不足扱いのパッケージ:

```text
Microsoft-Windows-Kernel-Package-IsolatedUserMode-Common-Package~31bf3856ad364e35~amd64~~10.0.26100.4061
```

`C:/Windows/servicing/Packages/` 内で、期待する末尾は `amd64~~10.0.26100.4061.mum` だが、実際には `amd64~ቾ10.0.26100.4061.mum` が存在する。`~`（U+007E）の位置が `ቾ`（U+127E）になっている。正規名のMUMはなく、正規名のCAT（署名付き照合表）はある。同フォルダーで非ASCII文字を含むファイル名はこの1件だけだった。

候補MUMを読取り専用で調べた結果:

- XMLの部品名・版・アーキテクチャ・言語は、不足パッケージと一致。
- 大きさ926 bytes、SHA256は `243e39a8b37b2ad4a05521fe2fb0060d09666aeb2e5a3aa6cf5648980403fcbe`。
- 対応するCATのAuthenticode署名はValid、署名者はMicrosoft Windows。
- CATのメンバー識別子に、このMUMのSHA256が含まれることを確認。

これらは「内容は正規のものと一致し、名前の異常によって見つからない」という判断の根拠になる。なぜ名前が変わったか、過去の操作・ファイルシステム・ハードウェアのどれが原因かは未特定。

## 推奨する修復の順序

### 1. 検証済みの局所的な修復元を指定する

元ファイルを変更せず、MUMを正常名でコピーし、対応CATとともに次へ準備済み:

```text
作業補助/WindowsTest/host/repair-source-20260906-083731/Windows/servicing/Packages/
```

検証結果は同じ修復元フォルダーの `verification.json`。準備用スクリプトは `作業補助/WindowsTest/host/Prepare-VerifiedRepairSource.ps1`。候補ハッシュ・XML識別情報・CAT署名・ハッシュ収録を再確認してからコピーする。

次の試行候補は、管理者PowerShellでこのWindowsフォルダーをDISMの `/Source` に指定すること。下記は**未実行**。

```powershell
$repairSource = (Resolve-Path -LiteralPath '.\作業補助\WindowsTest\host\repair-source-20260906-083731\Windows').Path
DISM.exe /Online /Cleanup-Image /RestoreHealth "/Source:$repairSource" /LimitAccess /NoRestart
```

Microsoftは修復元のWindowsフォルダーを指定する仕組みを案内している。ただし、今回の2ファイルだけの修復元で解決するかは未検証。完全なマウント済みWindowsイメージと同等だとは扱わない。`/LimitAccess` でWindows Update経由の長い再取得を避け、局所的な修復元が使えるかを切り分ける。受理されなければ同じ処理を繰り返さず次の方法を選ぶ。[修復元の公式仕様](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/configure-a-windows-repair-source?view=windows-11)

Windowsの保護フォルダーの所有権変更、元ファイル削除、他バージョンのMUMの流用は行わない。正規名への直接復元は別途保護属性と復旧方法を含めて検討するもので、この手順には含めない。

### 2. 必要ならWindows Update経由の修復インストール

Windows 11の「設定 → システム → 回復 → Windows Updateで問題を解決 → 今すぐ再インストール」が利用できれば、同じWindowsの版を再導入し、アプリ・個人ファイル・設定を保持する方法がある。項目の表示はこのPCでは未確認。管理ポリシー等によって利用できない場合がある。再起動を伴う作業として利用者の作業終了後に行う。[Microsoftの手順](https://support.microsoft.com/en-us/windows/deployment/install-upgrade/fix-issues-by-reinstalling-the-current-version-of-windows)

### 3. 適合するWindows ISOを使う

上記が使えない場合の候補。DISM用には言語・アーキテクチャ・更新レベルと、必要な古い部品を含むことを確認する。新しいISOなら必ず今回の古い管理ファイルを含むとは限らない。別案として、Windows上からISOのsetup.exeを起動し、「個人ファイルとアプリを引き継ぐ」が選べることを確認して修復インストールする。初期化やUSB起動からの新規インストールとは区別する。[公式のインストールメディア再導入手順](https://support.microsoft.com/en-us/windows/reinstall-windows-with-the-installation-media-d8369486-3e33-7d9c-dccc-859e2b022fc7)

不足部品の版26100.4061はKB5058411のOSビルドと一致するが、現在のOSは26200.9168。古い更新プログラムをそのままインストールすれば直るとは断定せず、必要ファイルの供給元候補として扱う。[KB5058411](https://support.microsoft.com/fr-fr/servicing/os/windows-11/2025/05/may-13-2025-kb5058411-os-build-26100-4061)

## 修復完了の判定

DISM成功 → SFCでシステムファイル検証 → Sandbox・Hyper-Vの有効化 → 必要な再起動 → Sandbox初回導入テストの順に確認する。進捗100%やファイルコピーだけで修復完了とはしない。

## 補足: 原因の発生経緯

過去30日のSystemイベントにはdisk 51/153、stornvme 129の記録もあった。ただし確認した直近のdisk警告は当時のDisk 7、現在のCドライブはDisk 4。過去のディスク番号との対応は未確認で、今回のWindowsファイル名異常と結び付けられない。現在のCドライブはHealthy表示だったが、これだけで物理故障の有無は断定できない。必要なら再発有無とストレージ診断を別途調べる。
