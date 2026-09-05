# Windows初回導入・更新テスト環境

2026-09-06作成。開発本体の `tools/windows_test_environment/` に再現用スクリプト、生成物・ログはGit対象外の `作業補助/WindowsTest/` に保存する。

## 用途と分離

- Windows Sandbox: 起動ごとに空のWindowsで公開セットアップの初回導入を試す。終了するとゲストの状態は消える。
- Hyper-V: Windowsを永続保存する仮想PC。更新前後・復旧の試験用。Windows 11 ISOと利用条件の確認、ゲストOSの導入が別途必要。
- 開発フォルダー、実台帳、請求PDF、資格情報は共有しない。Sandboxには検証済み公開EXE、配布情報、試験スクリプトだけを読取り専用で渡し、結果専用フォルダーのみ書込み可能にする。
- 軽量セットアップの部品取得のためSandboxの通信は有効。Digital Billder・OBIC7にはログインしない。

## ホスト準備

開発本体をカレントディレクトリとして、管理者PowerShellで実行する。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\windows_test_environment\Enable-HostFeatures.ps1
```

SandboxとHyper-Vを有効にし、`host/feature-status.json` に結果を書く。再起動は自動実行しない。`restart-required` の場合は作業を保存して利用者が再起動する。

今回の初回実行はDISMエラー `0x80073712`（Windowsコンポーネントストア破損）で失敗した。標準修復用の `Repair-HostComponents.ps1` を用意した。管理者で実行するとDISM RestoreHealthを行い、修復成功かつ再起動不要の場合に機能の有効化を再試行する。結果は `host/repair-status.json` と `repair-output.log`。修復失敗時にレジストリ削除やOS再インストールへ自動で進まない。

## Sandboxで試す

通常のPowerShellで次を実行する。

```powershell
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\windows_test_environment\Prepare-Lab.ps1
```

ローカル配布サイトの `lib/windows-setup.json` と配布検証フォルダーのEXEを照合し、サイズ・SHA256・配布期限を検証する。新しい `runs/日時-ID/` を作り、`初回導入テスト.wsb` を生成する。配布情報の期限切れは検証を止めるので、新しい検証済み公開版を準備する。

生成されたWSBをダブルクリックする。ゲスト内で専用インストール先へセットアップし、同梱Pythonだけで基本動作を検証する。完了は `results/install-result.json` の `install-and-runtime-smoke-passed` で判断し、各確認は `smoke-result.json` を見る。結果はSandbox終了後も残る。再試験は新しい実行フォルダーを生成する。

この自動試験は導入・部品読込み・Tk・ダミーPDF描画・独立SQLiteの確認まで。アプリ画面の一連の操作、実請求、認証、更新・電源断復旧の合格を意味しない。

## Hyper-Vで試す

管理者PowerShellでWindows 11 ISOの実際の場所を指定する。

```powershell
.\tools\windows_test_environment\New-TestVM.ps1 -IsoPath 'D:\ISO\Windows11.iso'
```

第2世代、メモリ4GB、CPU2個、可変容量64GB、Secure Boot・仮想TPM、Default Switchを設定する。同名VMや既存ディスクは上書きしない。作成途中に失敗した場合も自動削除せず、Hyper-Vマネージャーで状態を確認する。VM作成はOS導入完了ではなく、自動起動もしない。

Hyper-Vマネージャーから接続・起動し、Windowsの利用条件とライセンスを利用者が確認してOSを導入する。導入後に「OS初期状態」、アプリ導入後に「アプリ導入済み」、更新試験前に「更新前」のチェックポイントを作る。チェックポイントはバックアップの代わりではない。実データを持ち込まず、ダミーデータで更新前バックアップ・起動・失敗後復旧を確認する。この更新試験は未実施。

Windows ISOは未指定。入手先は [Microsoft Windows 11ダウンロード](https://www.microsoft.com/ja-jp/software-download/windows11)。

## 今回の検証記録

- 公開v1.0.4-r1セットアップ（99,328 bytes）のSHA256一致を確認し、WSBと入力4ファイルを生成済み。
- WSBのXML解析、共有先2件が実行専用フォルダー内であること、入力読取り専用・結果書込み可能を確認。
- PowerShell全5ファイルの構文検査、Pythonの検証テスト4件、ホストでのゲストスクリプト誤実行拒否を確認。
- ゲスト内の導入完走は未検証。Hyper-VのVM作成スクリプトは構文確認までで、OS導入済みVMはまだない。

## 参考資料

- [Windows Sandbox](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/)
- [WSB設定](https://learn.microsoft.com/en-us/windows/security/application-security/application-isolation/windows-sandbox/windows-sandbox-configure-using-wsb-file)
- [Windowsイメージの修復](https://learn.microsoft.com/en-us/windows-hardware/manufacture/desktop/repair-a-windows-image?view=windows-11)
- [Hyper-Vチェックポイント](https://learn.microsoft.com/en-us/windows-server/virtualization/hyper-v/checkpoints)
