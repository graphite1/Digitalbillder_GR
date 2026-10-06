# Linuxクラウド開発・合成データQA

## 到達点と範囲（2026-10-02）

GitHubの`main`は`49c9bf7fb32bdae5c7a47c498556c8a9cb60f67c`（2026-09-30）を基準に確認した。会社PC側の現行開発本体・作業ツリーの保全確認後、専用ブランチでこの隔離QAを準備した。会社PC上のDB、請求PDF、業務資料、ブラウザープロファイル、ログイン情報、署名秘密鍵は入力として使用・移行していない。

確認できたのはdotのLinux作業環境での開発・検証である。Codex Cloudの保存済み環境作成・その環境での実行は別の工程で、ここでは完了扱いにしない。GitHub Actionsも実際のrun結果を確認するまで未検証とする。

既存のPython・Tk・venv・unittest・GitHub Actionsを使用する。依存ロックは評価用であり、Windows実運用・署名済み配布runtimeへ適用するものではない。評価した新しい開発ツールを本番・実運用へ導入する場合は、評価内容を「実装希望」として提示し、利用者の使用許可を得てから導入する。

## 検証環境

- Linux x86_64、Debian GNU/Linux 13
- CPython 3.13.5、SQLiteは同runtime内の標準ライブラリー
- Tk 8.6、TkDnD 2.10.2、実際のdot Linux表示1364×1024
- `tools/cloud/requirements-linux-py313.lock.txt`に19依存を版・公式PyPI wheelのSHA-256で固定
- 実行時はOSのネットワーク名前空間で親・子プロセスの通信を遮断
- import前にデータ・HOME・XDG各保存先・一時ファイル・ブラウザー保存先を`作業補助/cloud-qa/`内へ隔離
- 実ブラウザーの追加取得、Digital Billderログイン、実Webの読取り・書込み、実署名鍵の取得・生成は行わない

公開Windows runtimeは既存引継書でPython3.14、開発側は3.13と記録されている。本QAは3.13.5であり、3.14、Windows Vault、WindowsセットアップEXE、署名付き更新の実機検証を代替しない。

## Linux初期設定

ソースとテストだけの新しい作業用checkoutで実施する。Python3.13とTkが必要で、GUIテストには利用可能な表示またはXvfbが必要。

```bash
PYTHON=python3.13 bash tools/cloud/setup_linux.sh
```

このスクリプトは`.venv/`だけに、既存requirementsの依存を公式PyPIからハッシュ照合付きでインストールする。ブラウザーは取得しない。Linux x86_64・Python3.13以外の環境は停止する。Pythonの小さい更新版は3.13.5以外を未検証として扱う。

Debianイメージで`ensurepip`がない場合は、公式OSパッケージ`python3.13-venv`を用意する。既に利用可能な別Pythonのpipがある評価イメージでは、明示した次の方法も使用できる。本QAではこの方法でセットアップを検証した。

```bash
PYTHON=/usr/bin/python3.13 BOOTSTRAP_PYTHON=python3 \
  bash tools/cloud/setup_linux.sh
```

## 隔離テスト

利用可能なGUI表示があるLinux端末から:

```bash
unshare -Urn .venv/bin/python -B tools/cloud/run_checks.py
```

Xvfbを使う評価端末から:

```bash
unshare -Urn xvfb-run -a .venv/bin/python -B tools/cloud/run_checks.py
```

`unshare -Urn`がOSで許可されていない場合、隔離を省略して実行しない。GitHub Actionsではジョブ内だけの`sudo unshare --net`と`runuser`で通常ユーザーへ戻し、同じ通信遮断を行う。永続的なネットワーク・OSセキュリティ設定は変更しない。

runnerは通信インターフェースがloopbackだけで、外部ルートがないことを確認してからdiscoveryする。TkDnDは通常Tkへのfallbackで隠さず、直接構築・更新・終了を検査する。`tests.*`の補助importはこのcheckoutに固定する。結果JSONには件数、失敗全文、skip理由、依存版、GUI情報、Git commitとソースファイルのSHA-256を残す。結果と合成データはGit対象外の`作業補助/`だけに保存される。

dot環境では通常のshellから実表示に接続できなかったため、dot Linux自身の端末をCUAで開き、同じcheckoutの隔離コマンドを実行した。利用者PCの端末・表示は使用していない。

## テスト結果と既知の環境依存

修正前の基準ソースで429件を実行し、422成功・6skip・1失敗。失敗は`ManualUpdateTests.test_installation_rejects_missing_fixed_files_and_database`で、隔離用`DIGITALBUILDER_DATA_DIR`にDBが存在したため、テスト自身のローカルDBを削除しても例外が出なかった。

`tools/manual_update.py`は指定された外部データ保存先を優先する実装で、この挙動を変更していない。該当テストだけ環境指定を空にして個別再実行した結果は成功。同じ環境変数の干渉は2026-09-30の引継書にも記録されている。

今回のテスト修正は、ローカルDB欠損ケースの環境を明示して終了時に復元することと、次の2回帰ケースを追加することに限定した。

1. ローカルDBがなくても、指定外部DBが存在すればその保存先を使用する
2. ローカルDBがあっても、指定外部DBがなければエラーにし、暗黙にfallbackしない

最終一括検証の実数はブランチの検証記録・PRに記載する。修正前の失敗と、修正後の実行結果は分けて扱う。

### sparse checkoutでの6skip

- 旧公開1.0.4の互換性4件: 外部fixture`.updates/releases/4-1.0.4`が未提供
- Windows非上書きrename 1件: 既存のWindows限定skip
- 署名付きrelease builder 1件: アプリのICO/PNGをQA checkoutから除外したため明示skip。配布作成の合格とは扱わない

Windows setup/VC関連のPythonテストは合成ZIP/PEとモックで実行できる。一方、C#の`tests/windows_setup_tests.cs`、VM用PowerShell、Windows実資格情報ストアはこのunittest結果に含まれない。

## GUIで確認した内容

その場で作った架空の工事・取引先・請求3件と合成PDF3件を使用した。メールは`example.invalid`、電話は`000-0000`で、実案件・実取引先の情報は含めていない。

- 日本語の請求一覧、3件の税抜合計330,000円、行選択と詳細操作
- 請求詳細の金額と添付PDFのプレビュー描画
- 管理メニューの表示・終了
- 開発モードで「アプリの更新」が案内だけを表示し、更新取得を開始しないこと
- TkDnD 2.10.2を直接構築・更新・終了し、fallbackなしで成功
- アプリを通常の閉じる操作で終了し、端末へ正常に戻ること

請求一覧・PDF詳細のスクリーンショットはローカルの評価結果として保持する。実Web同期、実ブラウザー本体、Windows向けの外部PDFアプリ起動、実運用の認証・署名は未検証。

## GitHub Actionsと次の移行工程

`.github/workflows/linux-synthetic-qa.yml`は専用ブランチのpush、該当ソース変更のpull request、手動実行に限定し、10分で停止する。権限は`contents: read`、checkout認証情報は保存しない。公式`actions/checkout`と`actions/setup-python`は2026-10-02に確認したv6のcommit SHAへ固定し、ソース・テストのみをsparse checkoutする。

Codex Cloudの保存済み環境を利用する段階では、利用者が作成した環境を選び、会社PCの保全済みcommitとこのブランチを確認する。セットアップは本書の固定依存を使い、認証・署名鍵・実台帳を持ち込まず同じ合成QAを再実行する。Linux QAが成功しても、Windows配布への適用・署名・公開は別工程の条件と承認を満たしてから行う。

参照: [既存の更新引継書](UPDATE_HANDOFF_2026-09-30.md)、[公式checkout](https://github.com/actions/checkout)、[公式setup-python](https://github.com/actions/setup-python)
